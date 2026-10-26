"""Offline contract tests for the dedicated protocol-v1.2 Gate 1 controller.

These tests deliberately use only local fake transports.  They must never read
the repository credential file, construct a live SDK client, or use a network
socket.
"""

from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from collusion_recovery.common import canonical, digest
import collusion_recovery.orchestration as orchestration
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    StagedExecutor,
)
from collusion_recovery.providers.openai import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
)
from collusion_recovery.stage2 import STAGE2_SDK_VERSION, STAGE2_TIMEOUT_SECONDS
import collusion_recovery.v1_2_gate1 as v1_2_gate1
from collusion_recovery.v1_2_gate1 import (
    HUMAN_ACKNOWLEDGEMENT,
    V12Gate1Executor,
    V12Gate1ReadinessError,
    authorization_schema,
    build_fake_v1_2_gate1_executor_for_test,
    build_v1_2_gate1_proposal,
    claim_v1_2_gate1_authorization_once,
    complete_v1_2_gate1_human_authorization,
    load_v1_2_gate1_human_authorization,
    prepare_v1_2_gate1_run,
    run_v1_2_gate1,
    validate_v1_2_gate1_authorization_file,
    validate_v1_2_gate1_human_authorization,
)


TEST_NOW = datetime.fromisoformat("2026-10-25T12:00:00-07:00")
TEST_PRICING_RECORD = {
    "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
    "reverified_at": "2026-10-25T11:30:00-07:00",
    "standard_rates_usd_per_million": {
        "input": "2.00",
        "cached_input": "0.10",
        "cache_write_input": "2.50",
        "output": "10.00",
    },
}


def no_network():
    stack = ExitStack()
    for name in (
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
    ):
        stack.enter_context(
            patch(name, side_effect=AssertionError("network access forbidden"))
        )
    return stack


def _schema_external_fields(proposal, *, confirmed_at=None, auth_uuid=None):
    """Populate only human/external fields from the controller's exact schema."""
    schema = authorization_schema(proposal)
    auth_uuid = auth_uuid or str(uuid.uuid4())
    confirmed_at = confirmed_at or TEST_NOW.isoformat()
    derived = {
        "approved_gate1_config_hash",
        "approved_gate1_plan_hash",
        "approved_dispatch_authorization_hash",
        "authorization_hash",
    }
    special = {
        "authorization_uuid": auth_uuid,
        "authorization_id": f"gate1-v1-2-human-{auth_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "confirmed_by": "offline test human",
        "confirmed_at": confirmed_at,
    }
    fields = {}
    for name in schema["required"]:
        if name in derived:
            continue
        contract = schema["properties"][name]
        if "const" in contract:
            fields[name] = json.loads(canonical(contract["const"]))
        elif name in special:
            fields[name] = special[name]
        else:
            raise AssertionError(f"test helper needs an external value for {name}")
    return fields


def authorization(proposal, *, confirmed_at=None, auth_uuid=None):
    return complete_v1_2_gate1_human_authorization(
        _schema_external_fields(
            proposal, confirmed_at=confirmed_at, auth_uuid=auth_uuid
        ),
        proposal,
        _proposal_verified=True,
    )


class FakeResponses:
    """Local SDK-shaped Responses surface with no I/O implementation."""

    def __init__(self, model, steps=()):
        self.model = model
        self.steps = list(steps)
        self.payloads = []
        self.completed = 0

    def create(self, **payload):
        self.payloads.append(json.loads(canonical(payload)))
        step = self.steps.pop(0) if self.steps else "ok"
        if step == "500":
            raise SyntheticInternalServerError(
                f"req_v12_gate1_{len(self.payloads)}"
            )
        if step == "429":
            raise SyntheticRateLimitError(
                f"req_v12_gate1_{len(self.payloads)}"
            )
        identity = (
            step[1]
            if isinstance(step, tuple) and step[0] == "identity" else {}
        )
        index = self.completed
        self.completed += 1
        verdict = "ACCEPT" if index < 10 else "REJECT"
        text = canonical({
            "verdict": verdict,
            "evidence_record_ids": [],
            "brief_reason": "Deterministic offline v1.2 Gate 1 result.",
        })
        body = {
            "id": f"fake-v1-2-gate1-{index + 1}",
            "object": "response",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": identity.get("model", self.model),
            "service_tier": identity.get("service_tier", "default"),
            "created_at": 1792948800,
            "system_fingerprint": identity.get("system_fingerprint"),
            "output": [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            }],
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        }
        return body


class FakeSDKClient:
    base_url = "https://api.openai.com/v1/"

    def __init__(self, model, steps=()):
        self.responses = FakeResponses(model, steps)

    def with_options(self, **_kwargs):
        return self


class _HTTPResponse:
    def __init__(self, request_id):
        self.status_code = 500
        self.headers = {
            "x-request-id": request_id,
            "retry-after": "5",
        }


class SyntheticInternalServerError(Exception):
    def __init__(self, request_id):
        super().__init__("synthetic HTTP 500")
        self.status_code = 500
        self.request_id = request_id
        self.body = {
            "error": {
                "type": "server_error",
                "code": "internal_error",
                "message": "Synthetic provider error",
            }
        }
        self.response = _HTTPResponse(request_id)


class SyntheticRateLimitError(Exception):
    def __init__(self, request_id):
        super().__init__("synthetic HTTP 429")
        self.status_code = 429
        self.request_id = request_id
        self.body = {
            "error": {
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
                "message": "Synthetic rate limit",
            }
        }
        self.response = None


class AdvancingClock:
    def __init__(self):
        self.value = datetime(2100, 1, 1, tzinfo=timezone.utc)

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


class V12Gate1ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed = build_v1_2_gate1_proposal(
            verify_prior_lineage=False,
            pricing_record=TEST_PRICING_RECORD,
        )
        cls.pricing_patcher = patch.object(
            v1_2_gate1, "V1_2_PRICING_RECORD", TEST_PRICING_RECORD
        )
        cls.lineage_patcher = patch.object(
            v1_2_gate1,
            "verify_gate1_passed_lineage",
            return_value=seed["capability_bridge"]["predecessor_gate1"],
        )
        cls.pricing_patcher.start()
        cls.lineage_patcher.start()
        cls.addClassCleanup(cls.lineage_patcher.stop)
        cls.addClassCleanup(cls.pricing_patcher.stop)
        with no_network():
            cls.proposal = build_v1_2_gate1_proposal()

    @staticmethod
    def _write_owner_only_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(value) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def _prepared(self, root: Path, *, steps=(), clock=None):
        root.mkdir(parents=True, exist_ok=True)
        auth = authorization(self.proposal)
        run_path = root / auth["gate1_run_directory"]
        run, config, plan, dispatch, inventory = prepare_v1_2_gate1_run(
            run_path,
            self.proposal,
            auth,
            workspace_root=root,
            _proposal_verified=True,
        )
        registry = root.parent / f"owner-state-{uuid.uuid4()}" / "gate1-v1.2"
        with patch(
            "collusion_recovery.v1_2_gate1.AUTHORIZATION_RECEIPT_DIRECTORY",
            registry,
        ):
            receipt = claim_v1_2_gate1_authorization_once(
                run,
                self.proposal,
                auth,
                receipt_dir=registry,
                now=TEST_NOW,
            )
        client = FakeSDKClient(config.model, steps)
        transport = OpenAISDKTransport(
            client,
            timeout_seconds=STAGE2_TIMEOUT_SECONDS,
            sdk_version=STAGE2_SDK_VERSION,
            ambient_openai_environment="rejected",
            http_client_trust_env=False,
            http_client_follow_redirects=False,
        )
        controller = build_fake_v1_2_gate1_executor_for_test(
            run,
            OpenAIResponsesProvider(transport),
            self.proposal,
            inventory,
            authorization_receipt=receipt,
            sleep=None if clock is None else clock.sleep,
            now=None if clock is None else clock.now,
        )
        return (
            auth, run, config, plan, dispatch, inventory, receipt,
            client, controller, registry,
        )

    def test_proposal_and_authorization_bind_exact_20_request_scope(self):
        proposal = self.proposal
        # The proposal never grants dispatch by itself; only the external,
        # one-shot authorization can release its exact fixed scope.
        self.assertFalse(proposal["dispatch_enabled"])
        self.assertTrue(proposal["execution_ready"])
        self.assertTrue(proposal["live_controller_implemented"])
        self.assertTrue(proposal["pricing_reverified_for_v1_2"])
        self.assertTrue(
            proposal["external_authorization"]["schema_artifact_created"]
        )
        self.assertEqual(
            proposal["external_authorization"]["schema_artifact"],
            "release/GATE1_V1_2_HUMAN_AUTHORIZATION_SCHEMA.json",
        )
        self.assertEqual(proposal["scope"]["logical_requests"], 20)
        self.assertEqual(proposal["scope"]["maximum_dispatch_attempts"], 61)
        self.assertFalse(proposal["scope"]["preflight_authorized"])
        self.assertFalse(proposal["scope"]["smoke_authorized"])
        self.assertFalse(
            proposal["scope"]["hypothesis_bearing_requests_authorized"]
        )
        self.assertEqual(proposal["scope"]["later_stages_authorized"], [])
        self.assertEqual(proposal["exact_caps"]["max_dispatch_attempts"], 61)
        self.assertEqual(
            set(proposal["assessment_contract"]),
            {
                "assessment_type",
                "scheduled",
                "schema_valid_verdicts",
                "request_payloads_verified",
                "wire_payloads_verified",
                "provider_audits_complete",
                "correct_packet_approvals",
                "faulty_packet_rejections",
                "decision_values",
                "decision_immutable",
                "transport_outcome_sensitivity_required",
            },
        )
        self.assertTrue(
            proposal["assessment_contract"][
                "transport_outcome_sensitivity_required"
            ]
        )
        schema = authorization_schema(proposal)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["proposal_hash"]["const"],
            proposal["proposal_hash"],
        )
        self.assertEqual(
            schema["properties"]["approved_scope"]["const"],
            proposal["scope"],
        )
        self.assertEqual(
            schema["properties"]["approved_caps"]["const"],
            proposal["exact_caps"],
        )
        self.assertEqual(
            schema["properties"]["human_acknowledgement"]["const"],
            HUMAN_ACKNOWLEDGEMENT,
        )
        auth = authorization(proposal)
        validate_v1_2_gate1_human_authorization(
            auth, proposal, _proposal_verified=True
        )
        tampered = json.loads(canonical(auth))
        tampered["approved_scope"]["logical_requests"] = 21
        body = dict(tampered)
        body.pop("authorization_hash")
        tampered["authorization_hash"] = digest(body)
        with self.assertRaises(V12Gate1ReadinessError):
            validate_v1_2_gate1_human_authorization(
                tampered, proposal, _proposal_verified=True
            )

    def test_unset_source_pricing_stays_draft_and_cannot_issue_authorization(self):
        with patch.object(v1_2_gate1, "V1_2_PRICING_RECORD", None), no_network():
            draft = build_v1_2_gate1_proposal()
            self.assertFalse(draft["execution_ready"])
            self.assertFalse(draft["authorization_issuance_enabled"])
            self.assertFalse(draft["pricing_reverified_for_v1_2"])
            self.assertIn("awaiting_pricing", draft["status"])
            with self.assertRaises(V12Gate1ReadinessError):
                complete_v1_2_gate1_human_authorization(
                    _schema_external_fields(draft),
                    draft,
                    _proposal_verified=True,
                )

    def test_external_authorization_must_be_owner_only_and_outside_repo(self):
        auth = authorization(self.proposal)
        with tempfile.TemporaryDirectory(dir=Path.home()) as td, no_network():
            root = Path(td)
            proposal_path = root / "proposal.json"
            self._write_owner_only_json(proposal_path, self.proposal)
            external = root / "external.json"
            external.write_text(canonical(auth) + "\n", encoding="utf-8")
            external.chmod(0o644)
            with patch.object(
                v1_2_gate1, "_validate_static_authorization_schema"
            ), self.assertRaisesRegex(V12Gate1ReadinessError, "owner-only"):
                    validate_v1_2_gate1_authorization_file(
                        external, proposal_path=proposal_path, now=TEST_NOW
                    )
            external.chmod(0o600)
            repo_auth = Path("temporary-v1-2-authorization-test.json")
            try:
                self._write_owner_only_json(repo_auth, auth)
                with patch.object(
                    v1_2_gate1, "_validate_static_authorization_schema"
                ), self.assertRaisesRegex(
                    V12Gate1ReadinessError, "outside the repository"
                ):
                        validate_v1_2_gate1_authorization_file(
                            repo_auth, proposal_path=proposal_path, now=TEST_NOW
                        )
            finally:
                repo_auth.unlink(missing_ok=True)

    def test_receipt_allows_exact_resume_but_blocks_replay(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as td, no_network():
            base = Path(td)
            prepared = self._prepared(base / "workspace-a")
            auth, run, *_middle, receipt, _transport, _controller, registry = prepared
            with patch(
                "collusion_recovery.v1_2_gate1.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ):
                repeated = claim_v1_2_gate1_authorization_once(
                    run,
                    self.proposal,
                    auth,
                    receipt_dir=registry,
                    now=TEST_NOW,
                )
            self.assertEqual(repeated, receipt)

            other = base / "workspace-b"
            other.mkdir()
            other_run, *_ = prepare_v1_2_gate1_run(
                other / auth["gate1_run_directory"],
                self.proposal,
                auth,
                workspace_root=other,
                _proposal_verified=True,
            )
            with patch(
                "collusion_recovery.v1_2_gate1.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ), self.assertRaisesRegex(
                V12Gate1ReadinessError, "already claimed|different"
            ):
                claim_v1_2_gate1_authorization_once(
                    other_run,
                    self.proposal,
                    auth,
                    receipt_dir=registry,
                    now=TEST_NOW,
                )

    def test_claim_happens_before_secret_sdk_or_client_construction(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            auth = authorization(self.proposal)
            sentinel = V12Gate1ReadinessError("claim sentinel")
            with (
                patch(
                    "collusion_recovery.v1_2_gate1.load_v1_2_gate1_proposal",
                    return_value=self.proposal,
                ),
                patch(
                    "collusion_recovery.v1_2_gate1.load_v1_2_gate1_human_authorization",
                    return_value=auth,
                ),
                patch("collusion_recovery.v1_2_gate1.ROOT", workspace),
                patch(
                    "collusion_recovery.v1_2_gate1.claim_v1_2_gate1_authorization_once",
                    side_effect=sentinel,
                ) as claim,
                patch(
                    "collusion_recovery.v1_2_gate1._load_local_openai_key",
                    side_effect=AssertionError("secret read before claim"),
                ) as key_read,
                patch(
                    "collusion_recovery.v1_2_gate1.OpenAISDKTransport",
                    side_effect=AssertionError("transport built before claim"),
                ) as transport,
            ):
                with self.assertRaisesRegex(
                    V12Gate1ReadinessError, "claim sentinel"
                ):
                    v1_2_gate1._run_v1_2_gate1(
                        root / "auth.json", now=TEST_NOW
                    )
            claim.assert_called_once()
            key_read.assert_not_called()
            transport.assert_not_called()

    def test_fake_e2e_persists_immutable_hard_stop_and_report(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            (_auth, run, _config, plan, _dispatch, _inventory, _receipt,
             client, controller, _registry) = prepared
            report = controller.execute()
            self.assertEqual(
                report["status"], "completed_v1_2_gate1_passed_hard_stop"
            )
            self.assertTrue(report["assessment"]["passed"])
            self.assertEqual(report["assessment"]["scheduled"], 20)
            self.assertEqual(report["logical_requests"], 20)
            self.assertEqual(report["dispatch_attempts"], 20)
            self.assertEqual(len(plan.requests), 20)
            self.assertEqual(len(client.responses.payloads), 20)
            self.assertFalse(report["experiment_started"])
            self.assertFalse(report["next_stage_authorized"])
            before = (
                run.manifest["event_count"],
                run.manifest["last_event_hash"],
                run.manifest["status"],
            )
            self.assertEqual(controller.execute(), report)
            self.assertEqual(
                (
                    run.manifest["event_count"],
                    run.manifest["last_event_hash"],
                    run.manifest["status"],
                ),
                before,
            )
            report_path = run.path / "gate1_v1_2_calibration_report.json"
            self.assertTrue(report_path.exists())
            forged = json.loads(report_path.read_text(encoding="utf-8"))
            forged["next_stage_authorized"] = True
            report_path.chmod(0o600)
            report_path.write_text(canonical(forged) + "\n", encoding="utf-8")
            with self.assertRaises(Exception):
                controller.execute()

    def test_eligible_500_recovers_once_and_reports_claim_sensitivity(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            clock = AdvancingClock()
            prepared = self._prepared(
                Path(td) / "workspace", steps=("500",), clock=clock
            )
            (_auth, run, _config, plan, _dispatch, _inventory, _receipt,
             client, controller, _registry) = prepared
            report = controller.execute()
            first_identity = plan.requests[0].identity
            results = run.result_events(first_identity)
            self.assertEqual(
                [row["status"] for row in results],
                ["ambiguous_timeout", "ok"],
            )
            self.assertEqual(
                results[0]["wire_payload_hash"],
                results[1]["wire_payload_hash"],
            )
            self.assertEqual(len(client.responses.payloads), 21)
            self.assertEqual(report["dispatch_attempts"], 21)
            sensitivity = report["transport_outcome_sensitivity"]
            self.assertEqual(sensitivity["affected_identities"], [first_identity])
            self.assertEqual(sensitivity["recovered_identities"], [first_identity])
            self.assertEqual(sensitivity["unresolved_identities"], [])
            self.assertEqual(
                sensitivity["recovered_outcomes_treated_as_unresolved"]["passed"],
                False,
            )
            self.assertTrue(sensitivity["retained_outcomes"]["passed"])
            schedules = [
                event for event in run.events
                if event.get("kind") == "ambiguous_5xx_retry_scheduled"
            ]
            self.assertEqual(len(schedules), 1)
            self.assertEqual(schedules[0]["retry_index"], 1)
            self.assertEqual(schedules[0]["delay_seconds"], 5.0)

    def test_generic_executor_cannot_bypass_dedicated_controller(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            (_auth, run, _config, _plan, _dispatch, _inventory, _receipt,
             client, _controller, _registry) = prepared
            from collusion_recovery.staged_requests import ScientificRequestFactory
            with self.assertRaises(Exception):
                generic = StagedExecutor(
                    run,
                    OpenAIResponsesProvider(OpenAISDKTransport(
                        client,
                        timeout_seconds=STAGE2_TIMEOUT_SECONDS,
                        sdk_version=STAGE2_SDK_VERSION,
                        ambient_openai_environment="rejected",
                        http_client_trust_env=False,
                        http_client_follow_redirects=False,
                    )),
                    ScientificRequestFactory(run.config, run.plan),
                )
                generic.execute("calibration", through_block=0)
            self.assertEqual(client.responses.payloads, [])

    def test_provider_identity_must_exactly_match_reviewed_model_tier_and_fingerprint(self):
        drifts = {
            "model": {"model": "wrong-model"},
            "service": {"service_tier": "priority"},
            "fingerprint": {"system_fingerprint": "fp_unreviewed"},
        }
        for label, identity in drifts.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as td, no_network():
                prepared = self._prepared(
                    Path(td) / "workspace",
                    steps=(("identity", identity),),
                )
                (_auth, run, _config, _plan, _dispatch, _inventory, _receipt,
                 client, controller, _registry) = prepared
                with self.assertRaisesRegex(
                    V12Gate1ReadinessError, "identity|stopped"
                ):
                    controller.execute()
                self.assertEqual(
                    run.manifest["status"], "paused_model_drift"
                )
                self.assertEqual(len(client.responses.payloads), 1)

    def test_completed_verifier_rejects_rehashed_identity_drift(self):
        for field in ("model", "service_tier", "system_fingerprint"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td, no_network():
                prepared = self._prepared(Path(td) / "workspace")
                (auth, run, _config, _plan, _dispatch, _inventory, receipt,
                 _client, controller, _registry) = prepared
                controller.execute()
                row = run.result_events()[0]
                result = row["provider_result"]
                identity = row["provider_identity"]
                if field == "model":
                    result["returned_model"] = "wrong-model"
                    result["model_metadata"]["model"] = "wrong-model"
                    identity["returned_model"] = "wrong-model"
                    identity["metadata_model"] = "wrong-model"
                elif field == "service_tier":
                    result["model_metadata"]["service_tier"] = "priority"
                    identity["returned_service_tier"] = "priority"
                else:
                    result["model_metadata"]["system_fingerprint"] = "fp_wrong"
                    identity["system_fingerprint"] = "fp_wrong"
                with self.assertRaisesRegex(
                    V12Gate1ReadinessError, "identity|provider"
                ):
                    v1_2_gate1._verify_completed_v1_2_gate1(
                        run,
                        self.proposal,
                        auth,
                        receipt,
                        require_empirical=False,
                    )

    def test_completed_verifier_rejects_nonfinal_provider_contract_tampering(self):
        cases = (
            "extra-transport-evidence-key",
            "raw-usage-despite-absence",
            "unknown-knc-provider-result-key",
            "retry-reservation-before-not-before",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td, no_network():
                steps = ("429", "ok") if "knc" in case else ("500",)
                prepared = self._prepared(
                    Path(td) / "workspace",
                    steps=steps,
                    clock=AdvancingClock(),
                )
                (auth, run, _config, plan, _dispatch, _inventory, receipt,
                 _client, controller, _registry) = prepared
                controller.execute()
                first = run.result_events(plan.requests[0].identity)[0]
                if case == "extra-transport-evidence-key":
                    first["provider_result"]["transport_evidence"][
                        "unexpected"
                    ] = "forbidden"
                elif case == "raw-usage-despite-absence":
                    first["provider_result"]["raw_response"]["usage"] = {
                        "total_tokens": 0
                    }
                elif case == "unknown-knc-provider-result-key":
                    self.assertEqual(first["status"], "retryable_error")
                    first["provider_result"]["unexpected"] = "forbidden"
                else:
                    schedule = next(
                        event for event in run.events
                        if event.get("kind")
                        == "ambiguous_5xx_retry_scheduled"
                    )
                    retry_reservation = next(
                        event for event in run.events
                        if event.get("kind") == "reservation"
                        and event.get("identity") == plan.requests[0].identity
                        and event.get("attempt") == 1
                    )
                    retry_reservation["recorded_at"] = schedule["recorded_at"]
                with self.assertRaises(V12Gate1ReadinessError):
                    v1_2_gate1._verify_completed_v1_2_gate1(
                        run,
                        self.proposal,
                        auth,
                        receipt,
                        require_empirical=False,
                    )

    def test_crash_after_wrong_model_result_stops_before_second_dispatch(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(
                Path(td) / "workspace",
                steps=(("identity", {"model": "wrong-model"}),),
            )
            (_auth, run, _config, _plan, _dispatch, _inventory, _receipt,
             client, controller, _registry) = prepared
            with patch.object(
                controller,
                "_bind_or_check_provider_identity",
                side_effect=RuntimeError("simulated crash before identity bind"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                    controller.execute()
            self.assertEqual(len(client.responses.payloads), 1)
            self.assertEqual(len(run.result_events()), 1)
            with self.assertRaisesRegex(
                V12Gate1ReadinessError, "before dispatch"
            ):
                controller.execute()
            self.assertEqual(len(client.responses.payloads), 1)
            self.assertEqual(run.manifest["status"], "paused_model_drift")

    def test_fake_and_empirical_provenance_are_capability_bound(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            self.assertFalse(
                hasattr(v1_2_gate1, "_RUN_V1_2_GATE1_CAPABILITY")
            )
            self.assertFalse(
                hasattr(v1_2_gate1, "_EMPIRICAL_EXECUTOR_CAPABILITY")
            )
            self.assertFalse(
                hasattr(v1_2_gate1, "_build_empirical_v1_2_gate1_executor")
            )
            self.assertFalse(
                hasattr(orchestration, "_V1_2_GATE1_STAGED_CAPABILITY")
            )
            root = Path(td)
            fake = self._prepared(root / "fake")
            (fake_auth, fake_run, _config, _plan, _dispatch, _inventory,
             fake_receipt, _client, fake_controller, _registry) = fake
            fake_report = fake_controller.execute()
            self.assertEqual(fake_report["source"], "fake-transport-test")
            self.assertIn("NOT EMPIRICAL", fake_report["warning"])
            with self.assertRaisesRegex(
                V12Gate1ReadinessError, "empirical"
            ):
                v1_2_gate1._verify_completed_v1_2_gate1(
                    fake_run,
                    self.proposal,
                    fake_auth,
                    fake_receipt,
                    require_empirical=True,
                )

            empirical = self._prepared(
                root / "empirical", clock=AdvancingClock()
            )
            (_auth, _run, _config, _plan, _dispatch, _inventory, _receipt,
             _client, empirical_controller, _registry) = empirical
            empirical_controller.fake_transport_test = False
            # The mode was already durably bound during construction, so a
            # caller cannot relabel a fake controller after the fact.
            with self.assertRaisesRegex(Exception, "mode"):
                empirical_controller.execute()

    def test_fake_sdk_export_cannot_mint_empirical_provenance(self):
        import openai

        with tempfile.TemporaryDirectory(dir=Path.home()) as td, no_network():
            base = Path(td)
            workspace = base / "workspace"
            workspace.mkdir()
            auth = authorization(self.proposal)
            run, _config, _plan, _dispatch, _inventory = (
                prepare_v1_2_gate1_run(
                    workspace / auth["gate1_run_directory"],
                    self.proposal,
                    auth,
                    workspace_root=workspace,
                    _proposal_verified=True,
                )
            )
            registry = base / "owner-state" / "gate1-v1.2"
            with patch.object(
                v1_2_gate1, "AUTHORIZATION_RECEIPT_DIRECTORY", registry
            ):
                claim_v1_2_gate1_authorization_once(
                    run,
                    self.proposal,
                    auth,
                    receipt_dir=registry,
                    now=TEST_NOW,
                )
            proposal_path = base / "proposal.json"
            auth_path = base / "authorization.json"
            self._write_owner_only_json(proposal_path, self.proposal)
            self._write_owner_only_json(auth_path, auth)
            clean_environment = {
                key: value for key, value in os.environ.items()
                if not key.upper().startswith("OPENAI_")
            }
            with (
                patch.dict(os.environ, clean_environment, clear=True),
                patch.object(
                    v1_2_gate1, "AUTHORIZATION_RECEIPT_DIRECTORY", registry
                ),
                patch.object(
                    v1_2_gate1, "_validate_static_authorization_schema"
                ),
                patch.object(
                    v1_2_gate1.importlib_metadata,
                    "version",
                    return_value=STAGE2_SDK_VERSION,
                ),
                patch.object(
                    v1_2_gate1,
                    "_load_local_openai_key",
                    return_value="offline-test-placeholder",
                ),
                patch.object(openai, "OpenAI", FakeSDKClient),
                self.assertRaisesRegex(
                    V12Gate1ReadinessError,
                    "official OpenAI SDK class identities",
                ),
            ):
                v1_2_gate1._run_v1_2_gate1(
                    auth_path,
                    proposal_path=proposal_path,
                    now=TEST_NOW,
                    workspace_root=workspace,
                )
            inspected = type(run).inspect(
                run.path, probe_installed_dependencies=False
            )
            self.assertFalse(any(
                event.get("kind")
                == v1_2_gate1.V1_2_GATE1_EXECUTION_MODE_EVENT
                for event in inspected.events
            ))
            self.assertEqual(inspected.result_events(), [])

            # Reproduce the remaining direct construction attack: manually
            # claim empirical strings, allocate the exact wrapper without its
            # fake-only constructor, and pass the only importable staged test
            # capability.  The generic engine must reject it because the
            # immediate caller is not the actual loaded live entrypoint.
            run.append(
                v1_2_gate1.V1_2_GATE1_EXECUTION_MODE_EVENT,
                controller=v1_2_gate1.V1_2_GATE1_CONTROLLER_ID,
                proposal_hash=self.proposal["proposal_hash"],
                run_id=v1_2_gate1.V1_2_GATE1_RUN_ID,
                mode=v1_2_gate1.EMPIRICAL_EXECUTION_MODE,
                executor_provenance=v1_2_gate1.EMPIRICAL_EXECUTOR_PROVENANCE,
            )
            fake_client = FakeSDKClient(run.config.model)
            fake_provider = OpenAIResponsesProvider(OpenAISDKTransport(
                fake_client,
                timeout_seconds=STAGE2_TIMEOUT_SECONDS,
                sdk_version=STAGE2_SDK_VERSION,
                ambient_openai_environment="rejected",
                http_client_trust_env=False,
                http_client_follow_redirects=False,
            ))
            adversarial = object.__new__(V12Gate1Executor)
            from collusion_recovery.staged_requests import ScientificRequestFactory
            with self.assertRaisesRegex(
                AuthorizationRequiredError, "exact official run caller"
            ):
                StagedExecutor.__init__(
                    adversarial,
                    run,
                    fake_provider,
                    ScientificRequestFactory(run.config, run.plan),
                    dedicated_controller=v1_2_gate1.V1_2_GATE1_CONTROLLER_ID,
                    _dedicated_capability=(
                        orchestration._V1_2_GATE1_STAGED_TEST_CAPABILITY
                    ),
                )
            self.assertEqual(fake_client.responses.payloads, [])

    def test_subclass_and_public_controller_string_cannot_mint_capability(self):
        class AdversarialSubclass(V12Gate1Executor):
            pass

        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            (_auth, run, _config, _plan, _dispatch, inventory, receipt,
             client, _controller, _registry) = prepared
            provider = OpenAIResponsesProvider(OpenAISDKTransport(
                client,
                timeout_seconds=STAGE2_TIMEOUT_SECONDS,
                sdk_version=STAGE2_SDK_VERSION,
                ambient_openai_environment="rejected",
                http_client_trust_env=False,
                http_client_follow_redirects=False,
            ))
            with self.assertRaises(Exception):
                AdversarialSubclass(
                    run,
                    provider,
                    self.proposal,
                    inventory,
                    authorization_receipt=receipt,
                    _executor_capability=v1_2_gate1._FAKE_EXECUTOR_CAPABILITY,
                )
            with self.assertRaises(Exception):
                V12Gate1Executor(
                    run,
                    provider,
                    self.proposal,
                    inventory,
                    authorization_receipt=receipt,
                )
            from collusion_recovery.staged_requests import ScientificRequestFactory
            with self.assertRaises(Exception):
                StagedExecutor(
                    run,
                    provider,
                    ScientificRequestFactory(run.config, run.plan),
                    dedicated_controller=v1_2_gate1.V1_2_GATE1_CONTROLLER_ID,
                )
            self.assertEqual(client.responses.payloads, [])

    def test_authorization_is_opened_once_nofollow_with_safe_parent_and_size_cap(self):
        auth = authorization(self.proposal)
        with tempfile.TemporaryDirectory(dir=Path.home()) as td, no_network():
            root = Path(td)
            safe_parent = root / "safe"
            safe_parent.mkdir(mode=0o700)
            auth_path = safe_parent / "authorization.json"
            self._write_owner_only_json(auth_path, auth)
            real_open = os.open
            opens = []

            def audited_open(path, flags, *args, **kwargs):
                if Path(path) == auth_path:
                    opens.append(flags)
                return real_open(path, flags, *args, **kwargs)

            with patch.object(v1_2_gate1.os, "open", side_effect=audited_open):
                loaded = load_v1_2_gate1_human_authorization(
                    auth_path,
                    self.proposal,
                    _proposal_verified=True,
                )
            self.assertEqual(loaded, auth)
            self.assertEqual(len(opens), 1)
            self.assertTrue(opens[0] & os.O_NOFOLLOW)

            unsafe_parent = root / "unsafe"
            unsafe_parent.mkdir(mode=0o777)
            unsafe_parent.chmod(0o777)
            unsafe_path = unsafe_parent / "authorization.json"
            self._write_owner_only_json(unsafe_path, auth)
            with self.assertRaisesRegex(
                V12Gate1ReadinessError, "parent|writable|owner"
            ):
                load_v1_2_gate1_human_authorization(
                    unsafe_path,
                    self.proposal,
                    _proposal_verified=True,
                )

            oversized = safe_parent / "oversized.json"
            oversized.write_text(
                canonical(auth) + " " * (64 * 1024), encoding="utf-8"
            )
            oversized.chmod(0o600)
            with self.assertRaisesRegex(V12Gate1ReadinessError, "large|size"):
                load_v1_2_gate1_human_authorization(
                    oversized,
                    self.proposal,
                    _proposal_verified=True,
                )

    def test_gate_to_manifest_and_final_result_to_gate_crashes_resume_without_dispatch(self):
        for point in ("after_gate", "before_gate"):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as td, no_network():
                prepared = self._prepared(Path(td) / "workspace")
                (_auth, run, _config, _plan, _dispatch, _inventory, _receipt,
                 client, controller, _registry) = prepared
                original = controller._gate_decision

                def crash_after_gate():
                    original()
                    raise RuntimeError("simulated crash after gate")

                crashing = (
                    crash_after_gate
                    if point == "after_gate" else
                    lambda: (_ for _ in ()).throw(
                        RuntimeError("simulated crash before gate")
                    )
                )
                with patch.object(controller, "_gate_decision", crashing):
                    with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                        controller.execute()
                self.assertEqual(len(client.responses.payloads), 20)
                if point == "after_gate":
                    self.assertEqual(set(run.gate_events()), {"calibration"})
                else:
                    self.assertEqual(run.gate_events(), {})
                report = controller.execute()
                self.assertEqual(
                    report["status"],
                    "completed_v1_2_gate1_passed_hard_stop",
                )
                self.assertEqual(len(client.responses.payloads), 20)

    def test_full_61_attempt_shape_and_unmatched_reservation_fail_closed(self):
        steps = ["429", "429", "500", "ok"]
        for _ in range(19):
            steps.extend(["429", "429", "ok"])
        with tempfile.TemporaryDirectory() as td, no_network():
            clock = AdvancingClock()
            prepared = self._prepared(
                Path(td) / "max-shape", steps=steps, clock=clock
            )
            (_auth, run, _config, plan, _dispatch, _inventory, _receipt,
             client, controller, _registry) = prepared
            report = controller.execute()
            self.assertEqual(report["dispatch_attempts"], 61)
            self.assertEqual(len(client.responses.payloads), 61)
            self.assertEqual(len(run.result_events(plan.requests[0].identity)), 4)
            self.assertTrue(all(
                len(run.result_events(spec.identity)) == 3
                for spec in plan.requests[1:]
            ))
            self.assertEqual(
                sum(
                    row["status"] == "retryable_error"
                    for row in run.result_events()
                ),
                40,
            )

        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "unmatched")
            (_auth, run, _config, _plan, _dispatch, _inventory, _receipt,
             client, controller, _registry) = prepared
            original_append = run.append

            def crash_before_result(kind, **data):
                if kind == "result":
                    raise KeyboardInterrupt("simulated process death")
                return original_append(kind, **data)

            with patch.object(run, "append", side_effect=crash_before_result):
                with self.assertRaises(KeyboardInterrupt):
                    controller.execute()
            self.assertEqual(len(run.budget_records()), 1)
            with self.assertRaises(V12Gate1ReadinessError):
                controller.execute()
            self.assertEqual(len(client.responses.payloads), 1)
            self.assertEqual(len(run.result_events()), 1)
            self.assertEqual(
                run.result_events()[0]["status"], "ambiguous_timeout"
            )

    def test_completed_receipt_resume_precedes_stale_pricing_and_secret_access(self):
        with tempfile.TemporaryDirectory(dir=Path.home()) as td, no_network():
            base = Path(td)
            workspace = base / "workspace"
            prepared = self._prepared(workspace, clock=AdvancingClock())
            (auth, _run, _config, _plan, _dispatch, _inventory, _receipt,
             client, controller, registry) = prepared
            report = controller.execute()
            proposal_path = base / "proposal.json"
            auth_path = base / "authorization.json"
            self._write_owner_only_json(proposal_path, self.proposal)
            self._write_owner_only_json(auth_path, auth)
            stale = TEST_NOW + timedelta(days=2)
            with (
                patch.object(
                    v1_2_gate1,
                    "AUTHORIZATION_RECEIPT_DIRECTORY",
                    registry,
                ),
                patch.object(
                    v1_2_gate1, "_validate_static_authorization_schema"
                ),
                # Empirical lineage cannot be synthesized by an injected fake
                # provider anymore.  This unit isolates the early completed
                # boundary branch; verifier behavior is covered separately.
                patch.object(
                    v1_2_gate1,
                    "_recover_v1_2_gate1_completion_boundary",
                    return_value=report,
                ) as recover,
                patch.object(
                    v1_2_gate1,
                    "_load_local_openai_key",
                    side_effect=AssertionError("completed resume read secret"),
                ) as secret,
            ):
                repeated = v1_2_gate1._run_v1_2_gate1(
                    auth_path,
                    proposal_path=proposal_path,
                    now=stale,
                    workspace_root=workspace,
                )
            self.assertEqual(repeated, report)
            self.assertTrue(recover.called)
            self.assertTrue(recover.call_args.kwargs["require_empirical"])
            secret.assert_not_called()
            self.assertEqual(len(client.responses.payloads), 20)

    def test_private_run_recovers_each_post_dispatch_boundary_without_provider_access(self):
        for boundary in (
                "all_results_no_gate",
                "gate_incomplete_manifest",
                "completed_manifest_missing_report"):
            with (
                self.subTest(boundary=boundary),
                tempfile.TemporaryDirectory(dir=Path.home()) as td,
                no_network(),
            ):
                base = Path(td)
                workspace = base / "workspace"
                workspace.mkdir()
                auth = authorization(self.proposal)
                run_path = workspace / auth["gate1_run_directory"]
                run, config, _plan, _dispatch, inventory = (
                    prepare_v1_2_gate1_run(
                        run_path,
                        self.proposal,
                        auth,
                        workspace_root=workspace,
                        _proposal_verified=True,
                    )
                )
                registry = base / "owner-state" / "gate1-v1.2"
                with patch.object(
                    v1_2_gate1,
                    "AUTHORIZATION_RECEIPT_DIRECTORY",
                    registry,
                ):
                    receipt = claim_v1_2_gate1_authorization_once(
                        run,
                        self.proposal,
                        auth,
                        receipt_dir=registry,
                        now=TEST_NOW,
                    )
                client = FakeSDKClient(config.model)
                transport = OpenAISDKTransport(
                    client,
                    timeout_seconds=STAGE2_TIMEOUT_SECONDS,
                    sdk_version=STAGE2_SDK_VERSION,
                    ambient_openai_environment="rejected",
                    http_client_trust_env=False,
                    http_client_follow_redirects=False,
                )
                controller = build_fake_v1_2_gate1_executor_for_test(
                    run,
                    OpenAIResponsesProvider(transport),
                    self.proposal,
                    inventory,
                    authorization_receipt=receipt,
                )
                original_gate = controller._gate_decision

                if boundary == "all_results_no_gate":
                    def interrupt_completion():
                        raise RuntimeError("simulated crash before gate")
                elif boundary == "gate_incomplete_manifest":
                    def interrupt_completion():
                        original_gate()
                        raise RuntimeError("simulated crash after gate")
                else:
                    interrupt_completion = None

                if interrupt_completion is None:
                    controller.execute()
                    (run.path / "gate1_v1_2_calibration_report.json").unlink()
                else:
                    with patch.object(
                        controller, "_gate_decision", interrupt_completion
                    ):
                        with self.assertRaisesRegex(
                            RuntimeError, "simulated crash"
                        ):
                            controller.execute()

                self.assertEqual(len(client.responses.payloads), 20)
                with (
                    patch.object(
                        v1_2_gate1,
                        "_load_local_openai_key",
                        side_effect=AssertionError(
                            "post-dispatch recovery read a credential"
                        ),
                    ) as secret,
                ):
                    with run.execution_lock():
                        recovered = (
                            v1_2_gate1._recover_v1_2_gate1_completion_boundary(
                                run,
                                self.proposal,
                                auth,
                                receipt,
                                require_empirical=False,
                            )
                        )

                self.assertEqual(
                    recovered["status"],
                    "completed_v1_2_gate1_passed_hard_stop",
                )
                self.assertEqual(len(client.responses.payloads), 20)
                secret.assert_not_called()
                recovered_run = type(run).inspect(
                    run.path, probe_installed_dependencies=False
                )
                self.assertEqual(len(recovered_run.result_events()), 20)
                self.assertEqual(
                    set(recovered_run.gate_events()), {"calibration"}
                )
                self.assertEqual(
                    recovered_run.manifest["status"],
                    "completed_v1_2_gate1_passed_hard_stop",
                )
                self.assertTrue(
                    (run.path / "gate1_v1_2_calibration_report.json").is_file()
                )

    def test_public_execute_api_exposes_no_clock_override(self):
        parameters = inspect.signature(run_v1_2_gate1).parameters
        self.assertNotIn("now", parameters)
        self.assertNotIn("_now", parameters)

    def test_rehashed_next_stage_report_cannot_become_passed_lineage(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            base = Path(td)
            root = base / "workspace"
            root.mkdir()
            auth = authorization(self.proposal)
            run, config, _plan, _dispatch, inventory = prepare_v1_2_gate1_run(
                root / auth["gate1_run_directory"],
                self.proposal,
                auth,
                workspace_root=root,
                _proposal_verified=True,
            )
            registry = base / "owner" / "gate1-v1.2"
            with patch.object(
                v1_2_gate1, "AUTHORIZATION_RECEIPT_DIRECTORY", registry
            ):
                receipt = claim_v1_2_gate1_authorization_once(
                    run,
                    self.proposal,
                    auth,
                    receipt_dir=registry,
                    now=TEST_NOW,
                )
            client = FakeSDKClient(config.model)
            transport = OpenAISDKTransport(
                client,
                timeout_seconds=STAGE2_TIMEOUT_SECONDS,
                sdk_version=STAGE2_SDK_VERSION,
                ambient_openai_environment="rejected",
                http_client_trust_env=False,
                http_client_follow_redirects=False,
            )
            build_fake_v1_2_gate1_executor_for_test(
                run,
                OpenAIResponsesProvider(transport),
                self.proposal,
                inventory,
                authorization_receipt=receipt,
            ).execute()
            report_path = run.path / "gate1_v1_2_calibration_report.json"
            forged = json.loads(report_path.read_text(encoding="utf-8"))
            forged["next_stage_authorized"] = True
            body = dict(forged)
            body.pop("report_hash")
            forged["report_hash"] = digest(body)
            report_path.write_text(canonical(forged) + "\n", encoding="utf-8")
            with self.assertRaises(V12Gate1ReadinessError):
                v1_2_gate1._verify_completed_v1_2_gate1(
                    run,
                    self.proposal,
                    auth,
                    receipt,
                    require_empirical=False,
                )

    def test_static_authorization_schema_requires_exact_canonical_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "schema.json"
            expected = authorization_schema(self.proposal)
            path.write_text(canonical(expected) + "\n", encoding="utf-8")
            v1_2_gate1._validate_static_authorization_schema(
                self.proposal, path
            )
            path.write_text(
                json.dumps(expected, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(V12Gate1ReadinessError):
                v1_2_gate1._validate_static_authorization_schema(
                    self.proposal, path
                )

    def test_exclusive_json_publish_is_torn_write_safe_and_concurrent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            torn = root / "torn.json"
            with patch.object(
                v1_2_gate1.os,
                "link",
                side_effect=OSError("simulated crash before publication"),
            ):
                with self.assertRaisesRegex(OSError, "simulated crash"):
                    v1_2_gate1._publish_exclusive_json(torn, {"value": 1})
            self.assertFalse(torn.exists())
            self.assertEqual(list(root.glob(".*.publish-*")), [])

            target = root / "concurrent.json"
            barrier = threading.Barrier(2)

            def publish():
                barrier.wait()
                try:
                    v1_2_gate1._publish_exclusive_json(
                        target, {"stable": True}
                    )
                    return "published"
                except FileExistsError:
                    return "already-published"

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(publish) for _ in range(2)]
                outcomes = sorted(future.result() for future in futures)
            self.assertEqual(
                outcomes, ["already-published", "published"]
            )
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                canonical({"stable": True}) + "\n",
            )

            immutable = root / "immutable-report.json"
            barrier = threading.Barrier(2)

            def write_same_report():
                barrier.wait()
                v1_2_gate1._write_immutable_json(
                    immutable, {"report": "same"}, "test report"
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(write_same_report) for _ in range(2)]
                for future in futures:
                    future.result()
            self.assertEqual(
                immutable.read_text(encoding="utf-8"),
                canonical({"report": "same"}) + "\n",
            )

            # The publisher removes its private hard-link name after exposing
            # the target.  That cleanup changes inode ctime/link count while a
            # concurrent identical loser may already be reading the stable
            # target.  Reproduce that exact window deterministically.
            source = root / "publisher-private-link.json"
            target = root / "published-link.json"
            self._write_owner_only_json(source, {"stable": "bytes"})
            os.link(source, target)
            real_read = os.read
            cleaned = False

            def cleanup_then_read(descriptor, amount):
                nonlocal cleaned
                if not cleaned:
                    cleaned = True
                    source.unlink()
                return real_read(descriptor, amount)

            with patch.object(
                v1_2_gate1.os, "read", side_effect=cleanup_then_read
            ):
                self.assertEqual(
                    v1_2_gate1._read_immutable_json_once(
                        target, "concurrent published value"
                    ),
                    {"stable": "bytes"},
                )

            receipt_registry = root / "receipt-owner" / "gate1-v1.2"
            receipt_registry.mkdir(parents=True, mode=0o700)
            receipt_registry.parent.chmod(0o700)
            receipt_registry.chmod(0o700)
            receipt_body = {"artifact_type": "race-test-receipt"}
            receipt = {
                **receipt_body,
                "receipt_hash": digest(receipt_body),
            }
            receipt_source = receipt_registry / "publisher-private-link.json"
            receipt_target = v1_2_gate1._authorization_receipt_path(
                receipt_registry
            )
            self._write_owner_only_json(receipt_source, receipt)
            os.link(receipt_source, receipt_target)
            cleaned = False

            def cleanup_receipt_then_read(descriptor, amount):
                nonlocal cleaned
                if not cleaned:
                    cleaned = True
                    receipt_source.unlink()
                return real_read(descriptor, amount)

            with (
                patch.object(
                    v1_2_gate1,
                    "AUTHORIZATION_RECEIPT_DIRECTORY",
                    receipt_registry,
                ),
                patch.object(
                    v1_2_gate1.os,
                    "read",
                    side_effect=cleanup_receipt_then_read,
                ),
            ):
                self.assertEqual(
                    v1_2_gate1._load_fixed_authorization_receipt(),
                    receipt,
                )


if __name__ == "__main__":
    unittest.main()
