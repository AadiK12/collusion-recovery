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
    V1_2_SMOKE_AUDIT_ASSESSMENT_TYPE,
    V1_2_SMOKE_AUDIT_CONTRACT_HASH,
    V1_2_SMOKE_AUDIT_CONTRACT_VERSION,
    V1_2_SMOKE_INTEGRITY_DECIDED_BY,
    V1_2_SMOKE_INTEGRITY_GATE,
    V1_2_SMOKE_INTEGRITY_NOTE,
    audit_v1_2_smoke_integrity,
    require_v1_2_smoke_integrity,
)
from collusion_recovery.v1_2_unified_contract import (
    GATE1,
    GATE2,
    GATE3_PHASE1,
    GATE3_PHASE2,
    validate_unified_stage_caps,
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
            "approved_budget_usd": "250.000000",
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


def _stage_caps(config: LiveConfig) -> dict:
    per_input = config.limits.max_input_tokens_per_request
    per_output = config.limits.max_output_tokens_per_request
    attempts = {
        GATE1: 61,
        GATE2: 694,
        GATE3_PHASE1: 1765,
        GATE3_PHASE2: 2997,
    }
    logical = {
        GATE1: 20,
        GATE2: 192,
        GATE3_PHASE1: 480,
        GATE3_PHASE2: 888,
    }
    input_totals = {
        GATE1: attempts[GATE1] * per_input,
        GATE2: attempts[GATE2] * per_input,
        GATE3_PHASE1: attempts[GATE3_PHASE1] * per_input,
    }
    input_totals[GATE3_PHASE2] = (
        config.limits.max_total_input_tokens - sum(input_totals.values())
    )
    return {
        stage: {
            "logical_requests": logical[stage],
            "dispatch_attempts": attempts[stage],
            "max_total_input_tokens": input_totals[stage],
            "max_total_output_tokens": attempts[stage] * per_output,
            "max_input_tokens_per_request": per_input,
            "max_output_tokens_per_request": per_output,
            "max_in_flight": 1,
            "max_cost_usd": "250.000000",
        }
        for stage in attempts
    }


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
    authorization = _authorization(config, plan, transport, release)
    run = DurableRun.open(
        directory,
        config,
        plan,
        dispatch_authorization=authorization,
        execution_controller=V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    )
    stage_caps = _stage_caps(config)
    aggregate_caps = validate_unified_stage_caps(stage_caps)
    proposal_hash = "a" * 64
    authorization_hash = "b" * 64
    receipt_hash = "c" * 64
    projection_hash = "d" * 64
    binding = run.append(
        "v1_2_master_release_binding",
        controller=V1_2_UNIFIED_PILOT_CONTROLLER_ID,
        run_id=V1_2_UNIFIED_PILOT_RUN_ID,
        proposal_hash=proposal_hash,
        authorization_hash=authorization_hash,
        receipt_hash=receipt_hash,
        source_contract_hash="e" * 64,
        stage_bindings_hash="f" * 64,
        projected_stage_caps_hash=digest(aggregate_caps),
        projection_hash=projection_hash,
        config_hash=digest(config.to_dict()),
        plan_hash=plan.plan_hash,
        implementation_hash=plan.implementation_hash,
        dispatch_authorization_hash=digest(authorization.to_dict()),
        request_order_hash=digest([spec.identity for spec in plan.requests]),
        stage_partition_hash=digest([
            {
                "stage": stage,
                "identities": [
                    spec.identity for spec in plan.requests
                    if spec.stage == stage
                ],
            }
            for stage in ("calibration", "smoke", "formation",
                          "remaining_arms", "probes")
        ]),
        operational_cost_hard_cap_usd="250.000000",
    )
    claim = run.append(
        "v1_2_master_authorization_claimed",
        controller=V1_2_UNIFIED_PILOT_CONTROLLER_ID,
        run_id=V1_2_UNIFIED_PILOT_RUN_ID,
        proposal_hash=proposal_hash,
        authorization_hash=authorization_hash,
        receipt_hash=receipt_hash,
        receipt_claimed_at=_STAMP,
        binding_event_hash=binding["event_hash"],
    )
    run.append(
        "v1_2_master_execution_mode_binding",
        controller=V1_2_UNIFIED_PILOT_CONTROLLER_ID,
        proposal_hash=proposal_hash,
        run_id=V1_2_UNIFIED_PILOT_RUN_ID,
        mode="empirical_live",
        executor_provenance="official_openai_client_from_run_v1_2_master",
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
        master_proposal_hash=proposal_hash,
        master_authorization_hash=authorization_hash,
        master_authorization_receipt_hash=receipt_hash,
        master_authorization_claim_event_hash=claim["event_hash"],
        authorized_stage_caps=stage_caps,
        authorized_stage_caps_hash=digest(aggregate_caps),
        master_projection_hash=projection_hash,
        execution_mode="empirical_live",
        executor_provenance="official_openai_client_from_run_v1_2_master",
        empirical_evidence=True,
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
    stage_for_identity = {
        spec.identity: {
            "calibration": GATE1,
            "smoke": GATE2,
            "formation": GATE3_PHASE1,
            "remaining_arms": GATE3_PHASE2,
            "probes": GATE3_PHASE2,
        }[spec.stage]
        for spec in plan.requests
    }
    records = run.budget_records()
    stage_budgets = {}
    for stage, cap in stage_caps.items():
        stage_release = DispatchReleaseLimits.from_mapping({
            "max_dispatch_attempts": cap["dispatch_attempts"],
            "max_total_input_tokens": cap["max_total_input_tokens"],
            "max_total_output_tokens": cap["max_total_output_tokens"],
            "max_cost_usd": cap["max_cost_usd"],
            "max_in_flight": cap["max_in_flight"],
            "max_input_tokens_per_request": cap[
                "max_input_tokens_per_request"
            ],
            "max_output_tokens_per_request": cap[
                "max_output_tokens_per_request"
            ],
        })
        stage_budgets[stage] = BudgetLedger.replay(
            config.limits,
            config.pricing,
            Decimal(cap["max_cost_usd"]),
            [
                row for row in records
                if stage_for_identity[row["identity"]] == stage
            ],
            stage_release,
        ).snapshot()
    run.set_status(
        "completed_stage",
        completed_through_stage="smoke",
        completed_through_block=0,
        stop_identity=None,
        budget=ledger.snapshot(),
        stage_budgets=stage_budgets,
    )
    return run


class V12SmokeAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with _no_io():
            cls.durable_run = _completed_run(Path(cls.temp.name) / "smoke")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_complete_durable_smoke_passes_without_human_review(self):
        with _no_io():
            report = require_v1_2_smoke_integrity(self.durable_run)
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
            event for event in self.durable_run.events
            if event.get("kind") == "result"
            and next(
                spec for spec in self.durable_run.plan.requests
                if spec.identity == event.get("identity")
            ).stage == "smoke"
        )
        original = copy.deepcopy(target)
        try:
            target["provider_result"]["output_text"] = "tampered"
            target["provider_identity"]["returned_model"] = "other-model"
            with _no_io():
                report = audit_v1_2_smoke_integrity(self.durable_run)
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
            item for item in self.durable_run.plan.requests
            if item.phase == "formation" and item.action == "message"
            and item.metadata.get("message_round") == 2
        )
        row = self.durable_run.result_events(spec.identity)[-1]
        target_hash = row["request_hash"]
        original_loader = self.durable_run.request_payload

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
                self.durable_run, "request_payload", side_effect=altered_loader):
            report = audit_v1_2_smoke_integrity(self.durable_run)
        self.assertFalse(report["automatic_checks_passed"])
        self.assertTrue(any("round2_visibility_frontier_invalid" in item
                            for item in report["failures"]))

    def test_checkpoint_and_out_of_scope_dispatch_tampering_fail_closed(self):
        checkpoint = next(
            event for event in self.durable_run.events
            if event.get("kind") == "checkpoint"
        )
        original_checkpoint = copy.deepcopy(checkpoint)
        later = "request-outside-smoke-boundary"
        bogus = {
            "sequence": len(self.durable_run.events),
            "previous_event_hash": self.durable_run.events[-1]["event_hash"],
            "kind": "result",
            "recorded_at": _STAMP,
            "identity": later,
            "attempt": 0,
        }
        bogus["event_hash"] = digest(bogus)
        try:
            checkpoint["checkpoint_hash"] = "0" * 64
            self.durable_run.events.append(bogus)
            with _no_io():
                report = audit_v1_2_smoke_integrity(self.durable_run)
        finally:
            self.durable_run.events.pop()
            checkpoint.clear()
            checkpoint.update(original_checkpoint)
        self.assertFalse(report["automatic_checks_passed"])
        self.assertIn("out_of_scope_dispatch_detected", report["failures"])
        self.assertTrue(any("checkpoint_" in item and "lineage_invalid" in item
                            for item in report["failures"]))

    def test_budget_snapshot_and_spurious_retry_schedule_fail_closed(self):
        manifest = self.durable_run.manifest
        manifest["budget"] = copy.deepcopy(manifest["budget"])
        manifest["budget"]["consumed"]["requests"] -= 1
        schedule = {
            "sequence": len(self.durable_run.events),
            "previous_event_hash": self.durable_run.events[-1]["event_hash"],
            "kind": "ambiguous_5xx_retry_scheduled",
            "recorded_at": _STAMP,
            "failed_result_event_hash": "0" * 64,
        }
        schedule["event_hash"] = digest(schedule)
        self.durable_run.events.append(schedule)
        try:
            with _no_io(), patch.object(
                    DurableRun, "manifest", new_callable=PropertyMock,
                    return_value=manifest):
                report = audit_v1_2_smoke_integrity(self.durable_run)
        finally:
            self.durable_run.events.pop()
        self.assertFalse(report["automatic_checks_passed"])
        self.assertIn("manifest_budget_snapshot_mismatch", report["failures"])
        self.assertIn("ambiguous_5xx_schedule_count_mismatch", report["failures"])

    def test_master_prefix_and_nonfungible_stage_budget_tampering_fail_closed(self):
        mode = next(
            event for event in self.durable_run.events
            if event.get("kind") == "v1_2_master_execution_mode_binding"
        )
        original_mode = copy.deepcopy(mode)
        manifest = self.durable_run.manifest
        manifest["stage_budgets"] = copy.deepcopy(manifest["stage_budgets"])
        manifest["stage_budgets"][GATE2]["consumed"]["requests"] -= 1
        try:
            mode["mode"] = "fake_transport_test"
            with _no_io(), patch.object(
                    DurableRun, "manifest", new_callable=PropertyMock,
                    return_value=manifest):
                report = audit_v1_2_smoke_integrity(self.durable_run)
        finally:
            mode.clear()
            mode.update(original_mode)
        self.assertFalse(report["automatic_checks_passed"])
        self.assertIn("master_execution_mode_invalid", report["failures"])
        self.assertIn(
            "manifest_nonfungible_stage_budgets_mismatch", report["failures"]
        )

    def test_calibration_gate_and_authorization_tampering_fail_closed(self):
        gate = next(
            event for event in self.durable_run.events
            if event.get("kind") == "gate"
            and event.get("gate") == "calibration"
        )
        original_gate = copy.deepcopy(gate)
        manifest = self.durable_run.manifest
        manifest["authorized_stage"] = "smoke"
        try:
            gate["passed"] = False
            with _no_io(), patch.object(
                    DurableRun, "manifest", new_callable=PropertyMock,
                    return_value=manifest):
                report = audit_v1_2_smoke_integrity(self.durable_run)
        finally:
            gate.clear()
            gate.update(original_gate)
        self.assertFalse(report["automatic_checks_passed"])
        self.assertIn("calibration_gate_not_passed", report["failures"])
        self.assertIn("unified_authorized_scope_mismatch", report["failures"])

    def test_persisted_smoke_gate_is_idempotent_and_must_be_final(self):
        with _no_io():
            original_report = require_v1_2_smoke_integrity(self.durable_run)
        evidence = {"audit_report": original_report}
        prerequisites = [
            spec.identity for spec in self.durable_run.plan.requests
            if spec.stage in {"calibration", "smoke"}
        ]
        gate = {
            "sequence": len(self.durable_run.events),
            "previous_event_hash": self.durable_run.events[-1]["event_hash"],
            "kind": "gate",
            "recorded_at": _STAMP,
            "gate": V1_2_SMOKE_INTEGRITY_GATE,
            "passed": True,
            "decided_by": V1_2_SMOKE_INTEGRITY_DECIDED_BY,
            "note": V1_2_SMOKE_INTEGRITY_NOTE,
            "evidence": evidence,
            "evidence_hash": digest(evidence),
            "prerequisite_identities": prerequisites,
        }
        gate["event_hash"] = digest(gate)
        later = {
            "sequence": gate["sequence"] + 1,
            "previous_event_hash": gate["event_hash"],
            "kind": "controller_marker",
            "recorded_at": _STAMP,
        }
        later["event_hash"] = digest(later)
        manifest = self.durable_run.manifest
        try:
            self.durable_run.events.append(gate)
            gate_manifest = copy.deepcopy(manifest)
            gate_manifest["event_count"] += 1
            gate_manifest["last_event_hash"] = gate["event_hash"]
            with _no_io(), patch.object(
                    DurableRun, "manifest", new_callable=PropertyMock,
                    return_value=gate_manifest):
                repeated = require_v1_2_smoke_integrity(self.durable_run)
            self.assertEqual(repeated, original_report)

            self.durable_run.events.append(later)
            later_manifest = copy.deepcopy(gate_manifest)
            later_manifest["event_count"] += 1
            later_manifest["last_event_hash"] = later["event_hash"]
            with _no_io(), patch.object(
                    DurableRun, "manifest", new_callable=PropertyMock,
                    return_value=later_manifest):
                rejected = audit_v1_2_smoke_integrity(self.durable_run)
        finally:
            if self.durable_run.events and self.durable_run.events[-1] is later:
                self.durable_run.events.pop()
            if self.durable_run.events and self.durable_run.events[-1] is gate:
                self.durable_run.events.pop()
        self.assertEqual(
            original_report["assessment_type"],
            V1_2_SMOKE_AUDIT_ASSESSMENT_TYPE,
        )
        self.assertFalse(rejected["automatic_checks_passed"])
        self.assertIn(
            "smoke_integrity_gate_not_sole_final_event", rejected["failures"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

