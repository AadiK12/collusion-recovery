"""Offline runner invariants for the prospectively versioned v1.2 5xx policy.

The tests use the real durable runner, request renderer, Responses adapter, and
SDK transport wrapper around a local duck-typed client.  No credential is read
and the client has no network implementation.
"""

from decimal import Decimal
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from collusion_recovery.budget import BudgetLedger, DispatchReleaseLimits
from collusion_recovery.common import canonical, digest
from collusion_recovery.live_config import LiveConfig
from collusion_recovery.orchestration import (
    DispatchAuthorizationArtifact,
    DurableRun,
    ExecutionContext,
    OrchestrationError,
    ResumeError,
    StagedExecutor,
    V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
    V1_2_GATE1_CONTROLLER_ID,
    V1_2_GATE1_NAMESPACE,
    V1_2_GATE1_RUN_ID,
    V1_2_GATE2_CONTROLLER_ID,
    V1_2_GATE2_NAMESPACE,
    V1_2_GATE2_RUN_ID,
    authorization_binding,
    build_request_plan,
)
from collusion_recovery.providers.openai import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
)
from collusion_recovery.preflight import PreflightRequestFactory, preflight_inventory
from collusion_recovery.staged_requests import ScientificRequestFactory


SDK_VERSION = "9.9.9-test"
TIMEOUT_SECONDS = 30


class _HTTPResponse:
    def __init__(self, status_code, headers):
        self.status_code = status_code
        self.headers = headers


class InternalServerError(Exception):
    def __init__(self, request_id, *, retry_after=5):
        super().__init__("synthetic provider HTTP 500")
        self.status_code = 500
        self.request_id = request_id
        self.body = {
            "error": {
                "type": "server_error",
                "code": "internal_error",
                "message": "Synthetic provider error",
            },
        }
        self.response = _HTTPResponse(
            500,
            {"x-request-id": request_id, "retry-after": str(retry_after)},
        )


class _LocalResponses:
    def __init__(self, model, steps):
        self.model = model
        self.steps = list(steps)
        self.calls = []

    def create(self, **kwargs):
        # Round-trip so later caller mutation cannot change the test's exact
        # local dispatch record.
        self.calls.append(json.loads(canonical(kwargs)))
        step = self.steps.pop(0) if self.steps else "ok"
        call_number = len(self.calls)
        if step == "500":
            raise InternalServerError(f"req_500_{call_number}")
        if isinstance(step, tuple) and step[0] == "500":
            raise InternalServerError(
                f"req_500_{call_number}", retry_after=step[1]
            )

        mode = kwargs["text"]["format"]["type"]
        output_text = (
            "x" * 201
            if step == "overlength"
            else "Synthetic local message."
            if mode == "text"
            else canonical({
                "verdict": "REJECT",
                "evidence_record_ids": [],
                "brief_reason": "Synthetic local verdict.",
            })
        )
        return {
            "id": f"resp_test_{call_number}",
            "model": self.model,
            "service_tier": "default",
            "system_fingerprint": "fp_test",
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": output_text}],
            }],
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 12,
            },
        }


class _LocalSDKClient:
    """The minimum SDK-shaped boundary, deliberately incapable of I/O."""

    def __init__(self, model, steps):
        self.base_url = "https://api.openai.com/v1/"
        self.responses = _LocalResponses(model, steps)
        self.options = None

    def with_options(self, **kwargs):
        self.options = kwargs
        return self


def _config(protocol_version="v1.2", *, run_id=None):
    if protocol_version == "v1.2":
        run_id = V1_2_GATE1_RUN_ID
        prefix = V1_2_GATE1_NAMESPACE
        calibration = "fallback-two-round/v1.2/calibration"
        run_type = "pilot"
        stage = "calibration"
        blocks = 5
    else:
        run_id = run_id or "http-5xx-runner-v1-1-test"
        prefix = "confirm/v1.1/http-5xx-runner-test"
        calibration = "calibration/v1.1"
        run_type = "confirmation"
        stage = "formation"
        blocks = 1
    return LiveConfig.from_mapping({
        "run_id": run_id,
        "protocol_version": protocol_version,
        "source": "empirical-live",
        "provider": "openai",
        "model": "gpt-test-snapshot",
        "model_context_tokens": 100_000,
        "model_settings": {
            "reasoning": {"effort": "low"},
            "service_tier": "default",
        },
        "pricing": {
            "source": "https://example.test/offline-pricing-fixture",
            "verified_at": "2026-10-24T12:00:00-07:00",
            "input_usd_per_million": "0.01",
            "cached_input_usd_per_million": "0.01",
            "cache_write_input_usd_per_million": "0.01",
            "output_usd_per_million": "0.01",
        },
        "authorization": {
            "kind": "paid",
            "authorization_id": "offline-test-authorization",
            "approved_at": "2026-10-24T12:01:00-07:00",
            "approved_budget_usd": "100",
            "allow_paid_calls": True,
        },
        "limits": {
            "max_requests": 1_000,
            "max_total_input_tokens": 50_000_000,
            "max_total_output_tokens": 100_000,
            "max_input_tokens_per_request": 50_000,
            "max_output_tokens_per_request": 100,
            "max_in_flight": 1,
        },
        "retry_policy": {
            "transport_retries": 0,
            "message_overlength_retries": 1,
            "initial_backoff_seconds": "0",
            "maximum_backoff_seconds": "0",
        },
        "run_type": run_type,
        "split": prefix,
        "stage": stage,
        "seed_namespace": prefix,
        "calibration_namespace": calibration,
        "blocks": blocks,
    })


def _provider(config, steps):
    client = _LocalSDKClient(config.model, steps)
    transport = OpenAISDKTransport(
        client,
        timeout_seconds=TIMEOUT_SECONDS,
        sdk_version=SDK_VERSION,
    )
    return client, OpenAIResponsesProvider(transport)


def _gate2_config():
    values = _config().to_dict()
    values.update({
        "run_id": V1_2_GATE2_RUN_ID,
        "split": V1_2_GATE2_NAMESPACE,
        "seed_namespace": V1_2_GATE2_NAMESPACE,
        "stage": "smoke",
    })
    return LiveConfig.from_mapping(values)


def _release(config, max_attempts):
    return DispatchReleaseLimits.from_mapping({
        "max_dispatch_attempts": max_attempts,
        "max_total_input_tokens": config.limits.max_total_input_tokens,
        "max_total_output_tokens": config.limits.max_total_output_tokens,
        "max_cost_usd": str(config.authorization.approved_budget_usd),
        "max_in_flight": config.limits.max_in_flight,
        "max_input_tokens_per_request": config.limits.max_input_tokens_per_request,
        "max_output_tokens_per_request": config.limits.max_output_tokens_per_request,
    })


def _artifact(config, plan, provider, release):
    transport_hash = digest(provider.transport.audit_metadata)
    confirmed_at = "2026-10-24T12:02:00-07:00"
    preflight_hash = None
    if config.run_type == "pilot":
        preflight_hash = preflight_inventory(PreflightRequestFactory(
            config.model,
            config.limits.max_output_tokens_per_request,
            dict(config.model_settings),
        ))["inventory_hash"]
    binding = authorization_binding(
        config,
        plan,
        confirmed_at=confirmed_at,
        transport_hash=transport_hash,
        max_stage=config.stage,
        max_block=0,
        release_limits=release,
        preflight_inventory_hash=preflight_hash,
    )
    return DispatchAuthorizationArtifact(
        authorization_id=config.authorization.authorization_id,
        confirmed_at=confirmed_at,
        binding_hash=binding,
        approved_budget_usd=str(config.authorization.approved_budget_usd),
        transport_hash=transport_hash,
        max_stage=config.stage,
        max_block=0,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=preflight_hash,
    )


def _prepared_run(path, steps, *, protocol_version="v1.2", max_attempts=20,
                  run_id="http-5xx-runner-test"):
    config = _config(protocol_version, run_id=run_id)
    plan = build_request_plan(config)
    client, provider = _provider(config, steps)
    release = _release(config, max_attempts)
    artifact = _artifact(config, plan, provider, release)
    run = DurableRun.open(
        path,
        config,
        plan,
        dispatch_authorization=artifact,
        execution_controller=(
            V1_2_GATE1_CONTROLLER_ID if protocol_version == "v1.2" else None),
    )
    # The test targets transport execution, not the separately tested protocol
    # freeze ceremony.  A direct synthetic event keeps this fixture local while
    # preserving the executor's normal gate lookup.
    run.append(
        "gate",
        gate=("transport_preflight" if protocol_version == "v1.2"
              else "protocol_freeze"),
        passed=True,
        decided_by="offline-unit-test",
        note="Synthetic local runner test only.",
        evidence={"fixture": "offline-only"},
    )
    factory = ScientificRequestFactory(config, plan)
    return config, plan, client, provider, release, artifact, run, factory


class _InstantClock:
    def __init__(self):
        self.value = datetime(2100, 1, 1, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


def _executor(run, provider, factory, *, sleep=None):
    clock = _InstantClock()
    return StagedExecutor(
        run,
        provider,
        factory,
        sleep=clock.sleep if sleep is None else sleep,
        now=clock.now,
        dedicated_controller=(
            V1_2_GATE1_CONTROLLER_ID
            if run.config.protocol_version == "v1.2" else None),
    )


def _execute(executor):
    with patch(
        "collusion_recovery.orchestration.importlib_metadata.version",
        return_value=SDK_VERSION,
    ):
        return executor.execute(executor.run.config.stage, through_block=0)


class V12Ambiguous5xxRunnerTests(unittest.TestCase):
    def test_short_or_noop_sleeper_cannot_dispatch_before_not_before(self):
        with tempfile.TemporaryDirectory() as td:
            (_config_value, plan, client, provider, _release_limits, _artifact_value,
             run, factory) = _prepared_run(Path(td) / "run", ["500", "ok"])
            with self.assertRaisesRegex(ResumeError, "delay has not elapsed"):
                _execute(_executor(
                    run, provider, factory, sleep=lambda _seconds: None))
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(
                [row["status"] for row in run.result_events(plan.requests[0].identity)],
                ["ambiguous_timeout"],
            )

    def test_submillisecond_early_wake_cannot_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            (_config_value, plan, client, provider, _release_limits, _artifact_value,
             run, factory) = _prepared_run(Path(td) / "run", ["500", "ok"])
            clock = _InstantClock()

            def wake_early(seconds):
                clock.value += timedelta(seconds=seconds - 0.0005)

            executor = StagedExecutor(
                run,
                provider,
                factory,
                sleep=wake_early,
                now=clock.now,
                dedicated_controller=V1_2_GATE1_CONTROLLER_ID,
            )
            with self.assertRaisesRegex(ResumeError, "delay has not elapsed"):
                _execute(executor)
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(
                [row["status"] for row in run.result_events(plan.requests[0].identity)],
                ["ambiguous_timeout"],
            )

    def test_eligible_500_then_ok_is_one_observation_with_full_first_reservation(self):
        with tempfile.TemporaryDirectory() as td:
            (config, plan, client, provider, _release_limits, artifact,
             run, factory) = _prepared_run(
                Path(td) / "run", ["500", "ok"], max_attempts=2
            )
            report = _execute(_executor(
                run, provider, factory
            ))

            first = plan.requests[0]
            rows = run.result_events(first.identity)
            self.assertEqual([row["status"] for row in rows], [
                "ambiguous_timeout", "ok",
            ])
            self.assertEqual(len(client.responses.calls), 2)
            self.assertEqual(rows[0]["budget_accounting"]["accounting"],
                             "full_reservation")
            self.assertEqual(sum(
                row["status"] in {"ok", "refusal", "invalid_response"}
                for row in rows
            ), 1)
            self.assertIsNotNone(rows[0]["wire_payload_hash"])
            self.assertEqual(rows[0]["wire_payload_hash"],
                             rows[1]["wire_payload_hash"])
            self.assertNotEqual(rows[0]["request_hash"], rows[1]["request_hash"])

            recovered = rows[1]["provider_result"]
            self.assertTrue(recovered["transport_recovered"])
            self.assertEqual(recovered["ambiguous_5xx_retry_count"], 1)
            self.assertEqual(
                recovered["prior_ambiguous_result_event_hashes"],
                [rows[0]["event_hash"]],
            )
            self.assertEqual(recovered["retry_policy_hash"],
                             V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH)
            self.assertRegex(recovered["dependency_closure_hash"], r"^[a-f0-9]{64}$")
            schedules = [
                event for event in run.events
                if event["kind"] == "ambiguous_5xx_retry_scheduled"
            ]
            self.assertEqual(len(schedules), 1)
            self.assertEqual(schedules[0]["failed_result_event_hash"],
                             rows[0]["event_hash"])
            self.assertEqual(schedules[0]["wire_payload_hash"],
                             rows[0]["wire_payload_hash"])
            self.assertEqual(schedules[0]["next_attempt"], 1)
            self.assertEqual(report.status, "paused_budget_exhausted")

            # A completed recovered identity and its schedule are immutable on
            # resume; the exhausted release permits no additional dispatch.
            resume_client, resume_provider = _provider(config, ["ok"])
            with patch(
                "collusion_recovery.orchestration.importlib_metadata.version",
                return_value=SDK_VERSION,
            ):
                resumed_run = DurableRun.open(
                    Path(td) / "run",
                    config,
                    plan,
                    resume=True,
                    dispatch_authorization=artifact,
                    execution_controller=V1_2_GATE1_CONTROLLER_ID,
                )
                resumed = _executor(
                    resumed_run,
                    resume_provider,
                    ScientificRequestFactory(config, plan),
                ).execute(config.stage, through_block=0)
            self.assertEqual(resumed.status, "paused_budget_exhausted")
            self.assertEqual(resume_client.responses.calls, [])
            self.assertEqual(sum(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in resumed_run.events
            ), 1)

    def test_overlength_then_eligible_500_reuses_exact_retry_wire(self):
        with tempfile.TemporaryDirectory() as td:
            config = _gate2_config()
            plan = build_request_plan(config)
            first = plan.requests[0]
            self.assertEqual(first.action, "message")
            client, provider = _provider(
                config, ["overlength", "500", "ok"]
            )
            release = _release(config, 3)
            artifact = _artifact(config, plan, provider, release)
            run = DurableRun.open(
                Path(td) / "run",
                config,
                plan,
                dispatch_authorization=artifact,
                execution_controller=V1_2_GATE2_CONTROLLER_ID,
            )
            run.append(
                "gate",
                gate="calibration",
                passed=True,
                decided_by="offline-unit-test",
                note="Synthetic local runner test only.",
                evidence={"fixture": "offline-only"},
            )
            factory = ScientificRequestFactory(config, plan)
            clock = _InstantClock()

            # Gate 2 remains deliberately projection-only.  This scoped patch
            # bypasses only that constructor guard so the lower-level retry
            # state machine can be regression-tested against its real message
            # schedule without creating a live controller.
            with patch.multiple(
                "collusion_recovery.orchestration",
                V1_2_GATE1_RUN_ID=V1_2_GATE2_RUN_ID,
                V1_2_GATE2_RUN_ID="projection-only-test-sentinel",
            ):
                executor = StagedExecutor(
                    run,
                    provider,
                    factory,
                    sleep=clock.sleep,
                    now=clock.now,
                    dedicated_controller=V1_2_GATE2_CONTROLLER_ID,
                )
                report = _execute(executor)

            rows = run.result_events(first.identity)
            self.assertEqual(
                [row["status"] for row in rows],
                ["overlength", "ambiguous_timeout", "ok"],
            )
            self.assertEqual(len(client.responses.calls), 3)
            self.assertNotEqual(
                rows[0]["wire_payload_hash"], rows[1]["wire_payload_hash"]
            )
            self.assertEqual(
                rows[1]["wire_payload_hash"], rows[2]["wire_payload_hash"]
            )
            self.assertNotEqual(rows[1]["request_hash"], rows[2]["request_hash"])
            self.assertTrue(rows[2]["provider_result"]["transport_recovered"])
            schedules = [
                event for event in run.events
                if event["kind"] == "ambiguous_5xx_retry_scheduled"
            ]
            self.assertEqual(len(schedules), 1)
            self.assertEqual(schedules[0]["failed_attempt"], 1)
            self.assertEqual(schedules[0]["next_attempt"], 2)
            self.assertEqual(
                schedules[0]["wire_payload_hash"], rows[1]["wire_payload_hash"]
            )
            self.assertEqual(report.status, "paused_budget_exhausted")

    def test_two_eligible_500s_stop_after_exactly_one_retry(self):
        with tempfile.TemporaryDirectory() as td:
            (_config_value, plan, client, provider, _release_limits, _artifact_value,
             run, factory) = _prepared_run(Path(td) / "run", ["500", "500"])
            report = _execute(_executor(
                run, provider, factory
            ))

            rows = run.result_events(plan.requests[0].identity)
            self.assertEqual(report.status,
                             "paused_ambiguous_5xx_retry_exhausted")
            self.assertEqual(len(client.responses.calls), 2)
            self.assertEqual([row["status"] for row in rows],
                             ["ambiguous_timeout", "ambiguous_timeout"])
            self.assertTrue(all(
                row["budget_accounting"]["accounting"] == "full_reservation"
                for row in rows
            ))
            self.assertEqual(sum(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in run.events
            ), 1)

    def test_v1_1_preserves_terminal_ambiguous_behavior_for_same_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            (_config_value, plan, client, provider, _release_limits, _artifact_value,
             run, factory) = _prepared_run(
                Path(td) / "run",
                ["500", "ok"],
                protocol_version="v1.1",
                max_attempts=2,
                run_id="http-5xx-runner-v1-1-test",
            )
            report = _execute(_executor(
                run, provider, factory
            ))

            rows = run.result_events(plan.requests[0].identity)
            self.assertEqual(report.status, "paused_ambiguous_timeout")
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["provider_result"]["transport_evidence"][
                "retry_eligible"
            ])
            self.assertFalse(any(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in run.events
            ))

    def test_retry_after_above_ceiling_stops_without_a_retry(self):
        with tempfile.TemporaryDirectory() as td:
            (_config_value, plan, client, provider, _release_limits, _artifact_value,
             run, factory) = _prepared_run(
                Path(td) / "run", [("500", 61), "ok"]
            )
            report = _execute(_executor(
                run, provider, factory
            ))

            self.assertEqual(report.status,
                             "paused_ambiguous_5xx_retry_after_exceeded")
            self.assertEqual(len(client.responses.calls), 1)
            self.assertEqual(len(run.result_events(plan.requests[0].identity)), 1)
            self.assertFalse(any(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in run.events
            ))

    def test_per_identity_cap_does_not_reset_after_overlength(self):
        identity = "request-test-identity"
        eligible = {
            "identity": identity,
            "status": "ambiguous_timeout",
            "provider_result": {"transport_evidence": {
                "retry_eligible": True,
                "http_status": 500,
                "response_http_status": 500,
                "request_id_consistent": True,
                "response_received": True,
                "output_absent": True,
                "usage_absent": True,
                "retry_after_seconds": 5,
            }},
        }
        rows = [eligible, {"identity": identity, "status": "overlength"}, eligible]
        runner = object.__new__(StagedExecutor)
        runner.run = SimpleNamespace(
            config=SimpleNamespace(protocol_version="v1.2"),
            plan=SimpleNamespace(requests=[object()] * 20),
            result_events=lambda requested=None: (
                rows if requested is None or requested == identity else []),
        )
        spec = SimpleNamespace(identity=identity)
        self.assertEqual(
            runner._v1_2_5xx_retry_state(spec, rows),
            ("paused_ambiguous_5xx_retry_exhausted", None),
        )

    def test_release_affected_identity_cap_stops_identity_k_plus_one(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(run_id="http-5xx-release-cap-test")
            plan = build_request_plan(config)
            cap = max(1, math.ceil(len(plan.requests) / 100))
            steps = []
            for _ in range(cap):
                steps.extend(("500", "ok"))
            steps.extend(("500", "ok"))
            client, provider = _provider(config, steps)
            release = _release(config, 2 * cap + 2)
            artifact = _artifact(config, plan, provider, release)
            run = DurableRun.open(
                Path(td) / "run",
                config,
                plan,
                dispatch_authorization=artifact,
                execution_controller=V1_2_GATE1_CONTROLLER_ID,
            )
            run.append("gate", gate="transport_preflight", passed=True,
                       decided_by="offline-unit-test", note="Synthetic gate.",
                       evidence={"fixture": "offline-only"})
            report = _execute(_executor(
                run,
                provider,
                ScientificRequestFactory(config, plan),
            ))

            self.assertEqual(report.status,
                             "paused_ambiguous_5xx_release_cap")
            self.assertEqual(len(client.responses.calls), 2 * cap + 1)
            affected = []
            for row in run.result_events():
                evidence = row["provider_result"].get("transport_evidence")
                if isinstance(evidence, dict) and evidence.get("retry_eligible"):
                    if row["identity"] not in affected:
                        affected.append(row["identity"])
            self.assertEqual(len(affected), cap + 1)
            for identity in affected[:cap]:
                self.assertEqual(
                    [row["status"] for row in run.result_events(identity)],
                    ["ambiguous_timeout", "ok"],
                )
            self.assertEqual(
                [row["status"] for row in run.result_events(affected[-1])],
                ["ambiguous_timeout"],
            )
            self.assertEqual(sum(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in run.events
            ), cap)

    def test_unmatched_reservation_is_terminal_and_never_redispatched(self):
        with tempfile.TemporaryDirectory() as td:
            (config, plan, _client, provider, release, artifact,
             run, factory) = _prepared_run(
                Path(td) / "run", [], max_attempts=1,
                run_id="http-5xx-unmatched-test",
            )
            first = plan.requests[0]
            request = factory(first, ExecutionContext(run, 0, ()))
            wire_payload = provider.build_payload(request)
            request_hash, _ = run.save_request(request)
            wire_payload_hash = run.save_wire_payload(wire_payload)
            ledger = BudgetLedger(
                config.limits,
                config.pricing,
                Decimal(artifact.approved_budget_usd),
                release,
            )
            reservation = ledger.reserve(
                first.identity,
                0,
                input_tokens=request.conservative_input_token_bound,
                output_tokens=request.max_output_tokens,
            )
            run.append(
                "reservation",
                identity=first.identity,
                attempt=0,
                request_hash=request_hash,
                wire_payload_hash=wire_payload_hash,
                reservation=reservation.to_dict(),
            )

            resume_client, resume_provider = _provider(config, ["ok"])
            with patch(
                "collusion_recovery.orchestration.importlib_metadata.version",
                return_value=SDK_VERSION,
            ):
                resumed_run = DurableRun.open(
                    Path(td) / "run",
                    config,
                    plan,
                    resume=True,
                    dispatch_authorization=artifact,
                    execution_controller=V1_2_GATE1_CONTROLLER_ID,
                )
                report = _executor(
                    resumed_run,
                    resume_provider,
                    ScientificRequestFactory(config, plan),
                ).execute(config.stage, through_block=0)

            self.assertEqual(report.status, "paused_ambiguous_timeout")
            self.assertEqual(report.dispatched_attempts, 0)
            self.assertEqual(resume_client.responses.calls, [])
            rows = resumed_run.result_events(first.identity)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "ambiguous_timeout")
            self.assertEqual(rows[0]["wire_payload_hash"], wire_payload_hash)
            self.assertFalse(rows[0]["provider_result"].get("transport_evidence"))
            self.assertFalse(any(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in resumed_run.events
            ))

            second_client, second_provider = _provider(config, ["ok"])
            with patch(
                "collusion_recovery.orchestration.importlib_metadata.version",
                return_value=SDK_VERSION,
            ):
                second_run = DurableRun.open(
                    Path(td) / "run",
                    config,
                    plan,
                    resume=True,
                    dispatch_authorization=artifact,
                    execution_controller=V1_2_GATE1_CONTROLLER_ID,
                )
                second_report = _executor(
                    second_run,
                    second_provider,
                    ScientificRequestFactory(config, plan),
                ).execute(config.stage, through_block=0)
            self.assertEqual(second_report.status, "paused_ambiguous_timeout")
            self.assertEqual(second_client.responses.calls, [])
            self.assertEqual(len(second_run.result_events(first.identity)), 1)

    def test_pending_schedule_resumes_once_and_duplicate_schedule_is_rejected(self):
        class SimulatedCrash(RuntimeError):
            pass

        def interrupt(_seconds):
            raise SimulatedCrash("interrupt after durable retry schedule")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "resume"
            (config, plan, _client, provider, _release_limits, artifact,
             run, factory) = _prepared_run(
                path, ["500"], max_attempts=2,
                run_id="http-5xx-schedule-resume-test",
            )
            with self.assertRaises(SimulatedCrash):
                _execute(_executor(run, provider, factory, sleep=interrupt))
            self.assertEqual(sum(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in run.events
            ), 1)

            resume_client, resume_provider = _provider(config, ["ok"])
            with patch(
                "collusion_recovery.orchestration.importlib_metadata.version",
                return_value=SDK_VERSION,
            ):
                resumed_run = DurableRun.open(
                    path, config, plan, resume=True,
                    dispatch_authorization=artifact,
                    execution_controller=V1_2_GATE1_CONTROLLER_ID,
                )
                resumed = _executor(
                    resumed_run,
                    resume_provider,
                    ScientificRequestFactory(config, plan),
                ).execute(config.stage, through_block=0)
            rows = resumed_run.result_events(plan.requests[0].identity)
            self.assertEqual(resumed.status, "paused_budget_exhausted")
            self.assertEqual(len(resume_client.responses.calls), 1)
            self.assertEqual([row["attempt"] for row in rows], [0, 1])
            self.assertEqual(rows[0]["wire_payload_hash"],
                             rows[1]["wire_payload_hash"])
            self.assertEqual(sum(
                event["kind"] == "ambiguous_5xx_retry_scheduled"
                for event in resumed_run.events
            ), 1)

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "duplicate"
            (config, plan, _client, provider, _release_limits, artifact,
             run, factory) = _prepared_run(
                path, ["500"], max_attempts=2,
                run_id="http-5xx-schedule-duplicate-test",
            )
            with self.assertRaises(SimulatedCrash):
                _execute(_executor(run, provider, factory, sleep=interrupt))
            schedule = next(
                event for event in run.events
                if event["kind"] == "ambiguous_5xx_retry_scheduled"
            )
            metadata = {
                key: value for key, value in schedule.items()
                if key not in {
                    "kind", "sequence", "recorded_at", "previous_event_hash", "event_hash",
                }
            }
            run.append("ambiguous_5xx_retry_scheduled", **metadata)

            _resume_client, resume_provider = _provider(config, ["ok"])
            with patch(
                "collusion_recovery.orchestration.importlib_metadata.version",
                return_value=SDK_VERSION,
            ):
                duplicate_run = DurableRun.open(
                    path, config, plan, resume=True,
                    dispatch_authorization=artifact,
                    execution_controller=V1_2_GATE1_CONTROLLER_ID,
                )
                with self.assertRaisesRegex(
                    ResumeError, "scheduled more than once"
                ):
                    _executor(
                        duplicate_run,
                        resume_provider,
                        ScientificRequestFactory(config, plan),
                    ).execute(config.stage, through_block=0)

    def test_existing_schedule_revalidates_all_metadata_and_deadline(self):
        class SimulatedCrash(RuntimeError):
            pass

        def interrupt(_seconds):
            raise SimulatedCrash("interrupt after durable retry schedule")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "schedule-validation"
            (config, plan, _client, provider, _release_limits, _artifact,
             run, factory) = _prepared_run(
                path, ["500"], max_attempts=2,
                run_id="http-5xx-schedule-validation-test",
            )
            with self.assertRaises(SimulatedCrash):
                _execute(_executor(run, provider, factory, sleep=interrupt))

            spec = plan.requests[0]
            results = run.result_events(spec.identity)
            schedule = next(
                event for event in run.events
                if event["kind"] == "ambiguous_5xx_retry_scheduled"
            )
            original = dict(schedule)
            recorded_at = datetime.fromisoformat(schedule["recorded_at"])
            self.assertEqual(
                schedule["not_before"],
                (recorded_at + timedelta(seconds=schedule["delay_seconds"])).isoformat(),
            )

            _resume_client, resume_provider = _provider(config, ["ok"])
            executor = _executor(
                run, resume_provider, ScientificRequestFactory(config, plan)
            )
            mutations = {
                "identity": "mutated-identity",
                "failed_attempt": 99,
                "next_attempt": 99,
                "failed_result_event_hash": "0" * 64,
                "provider_request_id": "req_mutated",
                "http_status": 503,
                "request_hash": "1" * 64,
                "wire_payload_hash": "2" * 64,
                "retry_index": 2,
                "per_identity_retry_cap": 2,
                "release_affected_identity_cap": 99,
                "release_affected_identity_index": 99,
                "delay_seconds": 6.0,
                "retry_policy_hash": "3" * 64,
                "not_before": (recorded_at - timedelta(seconds=1)).isoformat(),
                "recorded_at": (recorded_at - timedelta(seconds=1)).isoformat(),
            }
            for field, value in mutations.items():
                with self.subTest(field=field):
                    schedule.clear()
                    schedule.update(original)
                    schedule[field] = value
                    with self.assertRaisesRegex(ResumeError, "schedule was mutated"):
                        executor._v1_2_5xx_schedule_event(spec, results)

            schedule.clear()
            schedule.update(original)
            schedule.pop("provider_request_id")
            with self.assertRaisesRegex(ResumeError, "schedule was mutated"):
                executor._v1_2_5xx_schedule_event(spec, results)

            schedule.clear()
            schedule.update(original)
            schedule["unexpected"] = True
            with self.assertRaisesRegex(ResumeError, "schedule was mutated"):
                executor._v1_2_5xx_schedule_event(spec, results)

            early = dict(original)
            early["not_before"] = (recorded_at - timedelta(seconds=1)).isoformat()
            with self.assertRaisesRegex(ResumeError, "schedule was mutated"):
                executor._wait_for_v1_2_5xx_retry(early)

            with self.assertRaisesRegex(OrchestrationError, "reserved event metadata"):
                run.append("forged", recorded_at=recorded_at.isoformat())

            schedule.clear()
            schedule.update(original)


if __name__ == "__main__":
    unittest.main()
