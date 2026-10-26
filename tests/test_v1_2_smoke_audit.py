"""Offline tests for the exhaustive protocol-v1.2 smoke audit.

The fixture below creates a real ``DurableRun`` and its persisted artifacts by
deterministic local construction.  It never creates an SDK client, reads a
credential, invokes a provider, or opens a network socket.
"""

from contextlib import ExitStack
import copy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import PropertyMock, patch

from collusion_recovery.budget import BudgetLedger, DispatchReleaseLimits
from collusion_recovery.common import canonical, digest
from collusion_recovery.gates import calibration_assessment
from collusion_recovery.live_config import LiveConfig
from collusion_recovery.orchestration import (
    DispatchAuthorizationArtifact,
    DurableRun,
    ExecutionContext,
    V1_2_CALIBRATION_NAMESPACE,
    V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    V1_2_UNIFIED_PILOT_NAMESPACE,
    V1_2_UNIFIED_PILOT_RUN_ID,
    authorization_binding,
    build_request_plan,
)
from collusion_recovery.preflight import (
    PreflightRequestFactory,
    preflight_inventory,
)
from collusion_recovery.providers.openai import (
    OpenAIResponsesProvider,
    openai_sdk_transport_metadata,
)
from collusion_recovery.staged_requests import ScientificRequestFactory
from collusion_recovery.v1_2_smoke_audit import (
    SMOKE_LOGICAL_REQUESTS,
    V1_2_SMOKE_AUDIT_CONTRACT_HASH,
    V1_2_SMOKE_AUDIT_CONTRACT_VERSION,
    audit_v1_2_smoke_integrity,
    require_v1_2_smoke_integrity,
)


_STAMP = "2026-10-25T20:00:00+00:00"
_SDK_VERSION = "3.37.0-offline-test"


def _no_io():
    stack = ExitStack()
    for name in (
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
        "collusion_recovery.providers.openai.OpenAISDKTransport.send",
        "collusion_recovery.providers.openai.OpenAIResponsesProvider.complete",
    ):
        stack.enter_context(patch(
            name,
            side_effect=AssertionError("I/O and provider dispatch are forbidden"),
        ))
    stack.enter_context(patch("collusion_recovery.orchestration.os.fsync"))
    stack.enter_context(patch("collusion_recovery.orchestration._fsync_directory"))
    return stack


def _config() -> LiveConfig:
    return LiveConfig.from_mapping({
        "run_id": V1_2_UNIFIED_PILOT_RUN_ID,
        "protocol_version": "v1.2",
        "source": "empirical-live",
        "provider": "openai",
        "model": "gpt-test-smoke-snapshot",
        "model_context_tokens": 1_050_000,
        "model_settings": {
            "reasoning": {"effort": "low", "mode": "standard"},
            "service_tier": "default",
        },
        "pricing": {
            "source": "https://example.test/offline-smoke-audit-pricing",
            "verified_at": "2026-10-25T12:00:00-07:00",
            "input_usd_per_million": "0.01",
            "cached_input_usd_per_million": "0.01",
            "cache_write_input_usd_per_million": "0.01",
            "output_usd_per_million": "0.01",
        },
        "authorization": {
            "kind": "paid",
            "authorization_id": "offline-v1-2-smoke-audit-test",
            "approved_at": "2026-10-25T12:01:00-07:00",
            "approved_budget_usd": "100",
            "allow_paid_calls": True,
        },
        "limits": {
            "max_requests": 5517,
            "max_total_input_tokens": 5_770_850_368,
            "max_total_output_tokens": 22_597_632,
            "max_input_tokens_per_request": 1_045_904,
            "max_output_tokens_per_request": 4096,
            "max_in_flight": 1,
        },
        "retry_policy": {
            "transport_retries": 2,
            "message_overlength_retries": 1,
            "initial_backoff_seconds": "0",
            "maximum_backoff_seconds": "0",
        },
        "run_type": "pilot",
        "split": V1_2_UNIFIED_PILOT_NAMESPACE,
        "stage": "probes",
        "seed_namespace": V1_2_UNIFIED_PILOT_NAMESPACE,
        "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
        "blocks": 5,
    })


def _release(config: LiveConfig) -> DispatchReleaseLimits:
    return DispatchReleaseLimits.from_mapping({
        "max_dispatch_attempts": 5517,
        "max_total_input_tokens": config.limits.max_total_input_tokens,
        "max_total_output_tokens": config.limits.max_total_output_tokens,
        "max_cost_usd": str(config.authorization.approved_budget_usd),
        "max_in_flight": 1,
        "max_input_tokens_per_request": (
            config.limits.max_input_tokens_per_request
        ),
        "max_output_tokens_per_request": (
            config.limits.max_output_tokens_per_request
        ),
    })


def _authorization(config, plan, transport, release):
    transport_hash = digest(transport)
    inventory_hash = preflight_inventory(PreflightRequestFactory(
        config.model,
        config.limits.max_output_tokens_per_request,
        dict(config.model_settings),
    ))["inventory_hash"]
    binding = authorization_binding(
        config,
        plan,
        confirmed_at="2026-10-25T12:02:00-07:00",
        transport_hash=transport_hash,
        max_stage="probes",
        max_block=4,
        release_limits=release,
        preflight_inventory_hash=inventory_hash,
    )
    return DispatchAuthorizationArtifact(
        authorization_id=config.authorization.authorization_id,
        confirmed_at="2026-10-25T12:02:00-07:00",
        binding_hash=binding,
        approved_budget_usd=str(config.authorization.approved_budget_usd),
        transport_hash=transport_hash,
        max_stage="probes",
        max_block=4,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=inventory_hash,
    )


def _provider_result(config, spec, index):
    if spec.action == "message":
        output = "Apply the required evidence-verification rule."
        parsed = output
    elif spec.phase == "calibration":
        parsed = {
            "verdict": (
                "ACCEPT" if spec.metadata["fixture_index"] < 10 else "REJECT"
            ),
            "evidence_record_ids": [],
            "brief_reason": "Deterministic offline calibration fixture.",
        }
        output = canonical(parsed)
    else:
        parsed = {
            "verdict": "REJECT",
            "evidence_record_ids": [],
            "brief_reason": "Deterministic offline audit fixture.",
        }
        output = canonical(parsed)
    raw_usage = {
        "input_tokens": 10,
        "input_tokens_details": {
            "cached_tokens": 0,
            "cache_write_tokens": 0,
        },
        "output_tokens": 2,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 12,
    }
    raw = {
        "id": f"resp_offline_smoke_{index:03d}",
        "object": "response",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "model": config.model,
        "service_tier": "default",
        "created_at": 1792987200,
        "system_fingerprint": "fp_offline_smoke",
        "output": [{
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": output}],
        }],
        "usage": raw_usage,
    }
    return {
        "status": "ok",
        "raw_response": raw,
        "output_text": output,
        "parsed_output": parsed,
        "refusal": None,
        "error": None,
        "provider_request_id": raw["id"],
        "returned_model": config.model,
        "model_metadata": {
            "model": config.model,
            "service_tier": "default",
            "created_at": 1792987200,
            "system_fingerprint": "fp_offline_smoke",
        },
        "usage": {
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 2,
            "reasoning_tokens": 0,
            "total_tokens": 12,
            "raw": raw_usage,
        },
        "timing": {
            "started_at": _STAMP,
            "ended_at": _STAMP,
            "elapsed_seconds": 0.0,
        },
        "ambiguous": False,
        "known_not_completed": False,
        "transport_evidence": {},
    }


def _provider_identity(config):
    return {
        "requested_model": config.model,
        "returned_model": config.model,
        "metadata_model": config.model,
        "requested_service_tier": "default",
        "returned_service_tier": "default",
        "system_fingerprint": "fp_offline_smoke",
    }


def _append_attempt(run, ledger, factory, spec, index):
    request = factory(spec, ExecutionContext(run, 0, ()))
    request_hash, _ = run.save_request(request)
    wire_hash = run.save_wire_payload(
        OpenAIResponsesProvider.build_payload(request)
    )
    reservation = ledger.reserve(
        spec.identity,
        0,
        input_tokens=request.conservative_input_token_bound,
        output_tokens=request.max_output_tokens,
    )
    run.append(
        "reservation",
        identity=spec.identity,
        attempt=0,
        request_hash=request_hash,
        wire_payload_hash=wire_hash,
        reservation=reservation.to_dict(),
    )
    provider_result = _provider_result(run.config, spec, index)
    accounting = ledger.preview_reconciliation(
        reservation.reservation_id, provider_result["usage"]
    )
    run.append(
        "result",
        identity=spec.identity,
        attempt=0,
        reservation_id=reservation.reservation_id,
        request_hash=request_hash,
        wire_payload_hash=wire_hash,
        status="ok",
        ambiguous=False,
        known_not_completed=False,
        usage=provider_result["usage"],
        budget_accounting=accounting,
        provider_identity=_provider_identity(run.config),
        provider_result=provider_result,
    )
    ledger.reconcile(
        reservation.reservation_id,
        provider_result["usage"],
        expected_accounting=accounting,
    )


def _materialize_checkpoints(run):
    for checkpoint in run.plan.checkpoints:
        if checkpoint.block != 0:
            continue
        rows = [run.result_events(identity)[-1]
                for identity in checkpoint.contributors]
        hashes = [digest(row) for row in rows]
        run.append(
            "checkpoint",
            checkpoint_id=checkpoint.checkpoint_id,
            checkpoint_hash=digest({
                "spec": checkpoint.to_dict(), "result_hashes": hashes,
            }),
            block=checkpoint.block,
            history=checkpoint.history,
            contributors=list(checkpoint.contributors),
            result_hashes=hashes,
        )


def _completed_run(directory: Path) -> DurableRun:
    config = _config()
    plan = build_request_plan(config)
    transport = openai_sdk_transport_metadata(
        timeout_seconds=120,
        sdk_version=_SDK_VERSION,
    )
    release = _release(config)
    run = DurableRun.open(
        directory,
        config,
        plan,
        dispatch_authorization=_authorization(config, plan, transport, release),
        execution_controller=V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    )
    runtime = dict(run.manifest["runtime"])
    runtime["third_party_dependencies"] = [
        {"name": "openai", "version": _SDK_VERSION}
    ]
    run.set_status(
        "running",
        transport=transport,
        transport_hash=digest(transport),
        runtime=runtime,
        observed_provider_identity=_provider_identity(config),
    )
    ledger = BudgetLedger(
        config.limits,
        config.pricing,
        Decimal(config.authorization.approved_budget_usd),
        release,
    )
    factory = ScientificRequestFactory(config, plan)
    calibration = [spec for spec in plan.requests if spec.stage == "calibration"]
    formation = [spec for spec in plan.requests
                 if spec.stage == "smoke" and spec.phase == "formation"]
    evaluations = [spec for spec in plan.requests
                   if spec.stage == "smoke" and spec.phase == "evaluation"]
    for index, spec in enumerate(calibration):
        _append_attempt(run, ledger, factory, spec, index)
    evidence = calibration_assessment(run)
    decision = run._record_gate_decision_unlocked(
        "calibration", passed=True, evidence=evidence,
        decided_by="offline-deterministic-test",
        note="Exact reconstructed calibration assessment.",
    )
    if decision.get("passed") is not True:
        raise AssertionError("offline calibration fixture failed")
    for index, spec in enumerate(formation):
        _append_attempt(run, ledger, factory, spec, index + len(calibration))
    _materialize_checkpoints(run)
    for index, spec in enumerate(
            evaluations, start=len(calibration) + len(formation)):
        _append_attempt(run, ledger, factory, spec, index)
    for artifact in factory.durable_artifacts(run):
        run.append(
            "scientific_artifact",
            artifact_id=artifact["artifact_id"],
            artifact=artifact,
            artifact_hash=digest(artifact),
        )
    run.set_status(
        "completed_stage",
        completed_through_stage="smoke",
        completed_through_block=0,
        stop_identity=None,
        budget=ledger.snapshot(),
    )
    return run


class V12SmokeAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with _no_io():
            cls.run = _completed_run(Path(cls.temp.name) / "smoke")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_complete_durable_smoke_passes_without_human_review(self):
        with _no_io():
            report = require_v1_2_smoke_integrity(self.run)
        self.assertTrue(report["automatic_checks_passed"])
        self.assertEqual(
            report["audit_contract_version"], V1_2_SMOKE_AUDIT_CONTRACT_VERSION
        )
        self.assertEqual(
            report["audit_contract_hash"], V1_2_SMOKE_AUDIT_CONTRACT_HASH
        )
        self.assertTrue(report["outcome_blind"])
        self.assertFalse(report["human_review_required"])
        self.assertTrue(report["later_stage_release_permitted"])
        self.assertEqual(report["scope"]["scheduled_smoke_identities"], 192)
        self.assertEqual(report["scope"]["completed_smoke_identities"], 192)
        self.assertEqual(report["scope"]["smoke_dispatch_attempts"], 192)
        self.assertEqual(report["lineage"]["request_payloads_verified"], 192)
        self.assertEqual(report["lineage"]["wire_payloads_verified"], 192)
        self.assertEqual(report["lineage"]["raw_results_verified"], 192)
        self.assertEqual(report["failures"], [])
        body = dict(report)
        report_hash = body.pop("report_hash")
        self.assertEqual(report_hash, digest(body))

    def test_raw_result_and_provider_identity_tampering_fail_closed(self):
        target = next(
            event for event in self.run.events
            if event.get("kind") == "result"
        )
        original = copy.deepcopy(target)
        try:
            target["provider_result"]["output_text"] = "tampered"
            target["provider_identity"]["returned_model"] = "other-model"
            with _no_io():
                report = audit_v1_2_smoke_integrity(self.run)
        finally:
            target.clear()
            target.update(original)
        self.assertFalse(report["automatic_checks_passed"])
        self.assertTrue(any("raw_response_derivative_mismatch" in item
                            for item in report["failures"]))
        self.assertTrue(any("provider_identity_not_journal_bound" in item
                            for item in report["failures"]))

    def test_round_two_visibility_tampering_fails_closed(self):
        spec = next(
            item for item in self.run.plan.requests
            if item.phase == "formation" and item.action == "message"
            and item.metadata.get("message_round") == 2
        )
        row = self.run.result_events(spec.identity)[-1]
        target_hash = row["request_hash"]
        original_loader = self.run.request_payload

        def altered_loader(request_hash):
            value = original_loader(request_hash)
            if request_hash != target_hash:
                return value
            value = copy.deepcopy(value)
            turn = value["turns"][-1]
            prefix, _, encoded = turn["content"].rpartition("\n")
            tagged = json.loads(encoded)
            tagged["current"]["communication"]["message_rounds"]["round_2"] = {}
            turn["content"] = prefix + "\n" + canonical(tagged)
            return value

        with _no_io(), patch.object(
                self.run, "request_payload", side_effect=altered_loader):
            report = audit_v1_2_smoke_integrity(self.run)
        self.assertFalse(report["automatic_checks_passed"])
        self.assertTrue(any("round2_visibility_frontier_invalid" in item
                            for item in report["failures"]))

    def test_checkpoint_and_out_of_scope_dispatch_tampering_fail_closed(self):
        checkpoint = next(
            event for event in self.run.events
            if event.get("kind") == "checkpoint"
        )
        original_checkpoint = copy.deepcopy(checkpoint)
        later = "request-outside-smoke-boundary"
        bogus = {
            "sequence": len(self.run.events),
            "previous_event_hash": self.run.events[-1]["event_hash"],
            "kind": "result",
            "recorded_at": _STAMP,
            "identity": later,
            "attempt": 0,
        }
        bogus["event_hash"] = digest(bogus)
        try:
            checkpoint["checkpoint_hash"] = "0" * 64
            self.run.events.append(bogus)
            with _no_io():
                report = audit_v1_2_smoke_integrity(self.run)
        finally:
            self.run.events.pop()
            checkpoint.clear()
            checkpoint.update(original_checkpoint)
        self.assertFalse(report["automatic_checks_passed"])
        self.assertIn("out_of_scope_dispatch_detected", report["failures"])
        self.assertTrue(any("checkpoint_" in item and "lineage_invalid" in item
                            for item in report["failures"]))

    def test_budget_snapshot_and_spurious_retry_schedule_fail_closed(self):
        manifest = self.run.manifest
        manifest["budget"] = copy.deepcopy(manifest["budget"])
        manifest["budget"]["consumed"]["requests"] -= 1
        schedule = {
            "sequence": len(self.run.events),
            "previous_event_hash": self.run.events[-1]["event_hash"],
            "kind": "ambiguous_5xx_retry_scheduled",
            "recorded_at": _STAMP,
            "failed_result_event_hash": "0" * 64,
        }
        schedule["event_hash"] = digest(schedule)
        self.run.events.append(schedule)
        try:
            with _no_io(), patch.object(
                    DurableRun, "manifest", new_callable=PropertyMock,
                    return_value=manifest):
                report = audit_v1_2_smoke_integrity(self.run)
        finally:
            self.run.events.pop()
        self.assertFalse(report["automatic_checks_passed"])
        self.assertIn("manifest_budget_snapshot_mismatch", report["failures"])
        self.assertIn("ambiguous_5xx_schedule_count_mismatch", report["failures"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

