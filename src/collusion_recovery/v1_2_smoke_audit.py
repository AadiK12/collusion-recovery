"""Read-only, exhaustive integrity audit for the protocol-v1.2 smoke boundary.

This module contains no provider construction, credential loading, dispatch
entry point, or filesystem mutation.  It consumes an already-opened
``DurableRun`` and independently reconstructs the complete block-0 smoke
evidence before a controller may release later stages.

The audit is intentionally stronger than the historical manual checklist.  It
checks every scheduled identity and every attempt, the exact saved scientific
and wire payloads, raw Responses derivatives, retry lineage, provider identity,
two-round visibility/timing, checkpoints, scientific artifacts, and budget
replay.  Any missing or malformed item produces a deterministic failing report.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import json
import math
from typing import Any, Iterable, Mapping

from .budget import BudgetLedger, BudgetStateError, DispatchReleaseLimits
from .common import canonical, digest
from .design import ARMS, ROLES, block_design
from .orchestration import (
    OBSERVATION_STATUSES,
    STAGE_ORDER,
    V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
    V1_2_CALIBRATION_NAMESPACE,
    V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    V1_2_UNIFIED_PILOT_NAMESPACE,
    V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
    V1_2_UNIFIED_PILOT_RUN_ID,
    V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
    DurableRun,
    ExecutionContext,
    ScheduledRequest,
    _provider_identity_candidate,
    _valid_provider_identity,
    build_gate2_smoke_fixture_inventory,
    validate_canonical_plan,
)
from .providers.openai import (
    OpenAIResponsesProvider,
    provider_result_matches_raw_response,
)
from .staged_requests import ScientificRequestFactory


SMOKE_LOGICAL_REQUESTS = 192
SMOKE_MAX_DISPATCH_ATTEMPTS = 694
SMOKE_PHASE_COUNTS = {"formation": 120, "evaluation": 72}
SMOKE_ACTION_COUNTS = {"message": 116, "verdict": 76}
V1_2_SMOKE_AUDIT_CONTRACT_VERSION = "v1"
V1_2_SMOKE_AUDIT_CONTRACT = {
    "contract_version": V1_2_SMOKE_AUDIT_CONTRACT_VERSION,
    "run_id": V1_2_UNIFIED_PILOT_RUN_ID,
    "controller_id": V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    "namespace": V1_2_UNIFIED_PILOT_NAMESPACE,
    "protocol_version": "v1.2",
    "logical_requests": SMOKE_LOGICAL_REQUESTS,
    "maximum_dispatch_attempts": SMOKE_MAX_DISPATCH_ATTEMPTS,
    "phase_counts": SMOKE_PHASE_COUNTS,
    "action_counts": SMOKE_ACTION_COUNTS,
    "allowed_dispatch_through_stage": "smoke",
    "required_prior_gate": "calibration",
    "required_checkpoint_histories": ["C", "U"],
    "required_scientific_artifacts": {
        "checkpoint_snapshot": 2,
        "evaluation_sequence_end": 3,
    },
    "provider_result_policy": "all_attempts_raw_or_typed_transport_evidence",
    "payload_policy": "all_attempts_exact_request_and_wire_reconstruction",
    "outcome_blind": True,
    "human_dispatch_review_required": False,
}
V1_2_SMOKE_AUDIT_CONTRACT_HASH = digest(V1_2_SMOKE_AUDIT_CONTRACT)
_FINAL_STATUSES = frozenset(OBSERVATION_STATUSES)
_RETRYABLE_INTERMEDIATE_STATUSES = frozenset({
    "overlength", "retryable_error", "ambiguous_timeout",
})
_PROVIDER_RESULT_FIELDS = frozenset({
    "status", "raw_response", "output_text", "parsed_output", "refusal",
    "error", "provider_request_id", "returned_model", "model_metadata",
    "usage", "timing", "ambiguous", "known_not_completed",
    "transport_evidence",
})


class V12SmokeIntegrityError(RuntimeError):
    """The durable v1.2 block-0 smoke evidence failed closed."""


class _Findings:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def require(self, condition: bool, code: str) -> bool:
        if not condition and code not in self.failures:
            self.failures.append(code)
        return condition

    def capture(self, code: str, operation):
        try:
            return operation()
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self.require(False, f"{code}:{type(exc).__name__}")
            return None


def _aware_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _event_body(event: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "event_hash"}


def _verify_journal_chain(run: DurableRun, findings: _Findings) -> None:
    previous = None
    for sequence, event in enumerate(run.events):
        valid = (
            isinstance(event, Mapping)
            and event.get("sequence") == sequence
            and event.get("previous_event_hash") == previous
            and event.get("event_hash") == digest(_event_body(event))
            and _aware_time(event.get("recorded_at")) is not None
        )
        findings.require(valid, f"journal_chain_invalid_at_{sequence}")
        if not isinstance(event, Mapping):
            return
        previous = event.get("event_hash")
    manifest = findings.capture("manifest_unreadable", lambda: run.manifest)
    if isinstance(manifest, Mapping):
        findings.require(
            manifest.get("event_count") == len(run.events),
            "manifest_event_count_mismatch",
        )
        findings.require(
            manifest.get("last_event_hash") == previous,
            "manifest_journal_tail_mismatch",
        )


def _smoke_specs(plan) -> list[ScheduledRequest]:
    return [spec for spec in plan.requests if spec.stage == "smoke"]


def _through_smoke_identities(plan) -> set[str]:
    ceiling = STAGE_ORDER.index("smoke")
    return {
        spec.identity for spec in plan.requests
        if STAGE_ORDER.index(spec.stage) <= ceiling
    }


def _verify_plan_and_inventory(run: DurableRun, findings: _Findings) -> tuple[
        list[ScheduledRequest], dict[str, Any]]:
    config, plan = run.config, run.plan
    findings.require(config.protocol_version == "v1.2", "protocol_not_v1_2")
    findings.require(config.source == "empirical-live", "source_not_empirical_live")
    findings.require(config.provider == "openai", "provider_not_openai")
    findings.require(config.run_type == "pilot", "run_type_not_pilot")
    findings.require(
        config.run_id == V1_2_UNIFIED_PILOT_RUN_ID,
        "run_id_not_unified_v1_2_pilot",
    )
    findings.require(
        config.split == V1_2_UNIFIED_PILOT_NAMESPACE
        and config.seed_namespace == V1_2_UNIFIED_PILOT_NAMESPACE,
        "unified_v1_2_namespace_mismatch",
    )
    findings.require(
        config.calibration_namespace == V1_2_CALIBRATION_NAMESPACE,
        "unified_v1_2_calibration_namespace_mismatch",
    )
    findings.require(config.stage == "probes" and config.blocks == 5,
                     "unified_v1_2_scope_mismatch")
    findings.capture(
        "canonical_plan_invalid", lambda: validate_canonical_plan(config, plan)
    )

    specs = _smoke_specs(plan)
    request_order_hash = digest([spec.identity for spec in plan.requests])
    stage_partition_hash = digest([
        {
            "stage": stage,
            "identities": [
                spec.identity for spec in plan.requests if spec.stage == stage
            ],
        }
        for stage in STAGE_ORDER
        if any(spec.stage == stage for spec in plan.requests)
    ])
    findings.require(
        request_order_hash == V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
        "unified_request_order_hash_mismatch",
    )
    findings.require(
        stage_partition_hash == V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        "unified_stage_partition_hash_mismatch",
    )
    findings.require(len(specs) == SMOKE_LOGICAL_REQUESTS,
                     "smoke_schedule_not_192")
    findings.require(
        len({spec.identity for spec in specs}) == SMOKE_LOGICAL_REQUESTS,
        "smoke_identity_duplicate",
    )
    findings.require(
        Counter(spec.phase for spec in specs) == Counter(SMOKE_PHASE_COUNTS),
        "smoke_phase_inventory_drift",
    )
    findings.require(
        Counter(spec.action for spec in specs) == Counter(SMOKE_ACTION_COUNTS),
        "smoke_action_inventory_drift",
    )
    findings.require(
        all(spec.metadata.get("block") == 0 for spec in specs),
        "smoke_contains_nonzero_block",
    )
    findings.require(
        {spec.metadata.get("history") for spec in specs
         if spec.phase == "formation"} == {"U", "C"},
        "smoke_formation_history_inventory_drift",
    )
    findings.require(
        {spec.metadata.get("arm") for spec in specs
         if spec.phase == "evaluation"} == {"A", "B", "F"},
        "smoke_evaluation_arm_inventory_drift",
    )

    design = findings.capture(
        "block0_design_invalid",
        lambda: block_design(config.seed_namespace, 0, config.protocol_version),
    )
    expected_inventory = (
        {} if design is None else
        build_gate2_smoke_fixture_inventory(config, design)
    )
    actual_inventory = {
        name: value for name, value in plan.fixture_inventory.items()
        if name.startswith("block/0/formation/")
        or name.startswith("block/0/evaluation/")
    }
    findings.require(
        canonical(actual_inventory) == canonical(expected_inventory),
        "block0_fixture_inventory_drift",
    )
    findings.require(len(actual_inventory) == 32,
                     "block0_fixture_inventory_not_32")
    findings.require(
        len({item.get("packet_hash") for item in actual_inventory.values()
             if isinstance(item, Mapping)}) == len(actual_inventory),
        "block0_fixture_packet_hash_duplicate",
    )

    manifest = findings.capture("manifest_unreadable", lambda: run.manifest)
    if isinstance(manifest, Mapping):
        findings.require(
            manifest.get("execution_controller")
            == V1_2_UNIFIED_PILOT_CONTROLLER_ID,
            "unified_execution_controller_mismatch",
        )
        findings.require(manifest.get("config_hash") == plan.config_hash,
                         "manifest_config_hash_mismatch")
        findings.require(manifest.get("plan_hash") == plan.plan_hash,
                         "manifest_plan_hash_mismatch")
        findings.require(manifest.get("protocol_hash") == plan.protocol_hash,
                         "manifest_protocol_hash_mismatch")
        findings.require(
            manifest.get("implementation_hash") == plan.implementation_hash,
            "manifest_implementation_hash_mismatch",
        )
        findings.require(
            canonical(manifest.get("planned_counts"))
            == canonical(plan.planned_counts()),
            "manifest_planned_counts_mismatch",
        )
        transport = manifest.get("transport")
        transport_hash = digest(transport) if isinstance(transport, Mapping) else None
        findings.require(
            isinstance(transport, Mapping)
            and transport.get("transport")
            == "official-openai-python-sdk-injected"
            and transport.get("base_url") == "https://api.openai.com/v1"
            and transport.get("sdk_package") == "openai"
            and isinstance(transport.get("sdk_version"), str)
            and bool(transport["sdk_version"].strip())
            and transport.get("sdk_max_retries") == 0,
            "empirical_transport_identity_invalid",
        )
        findings.require(
            manifest.get("transport_hash") == transport_hash
            == manifest.get("authorized_transport_hash"),
            "empirical_transport_hash_mismatch",
        )
        runtime = manifest.get("runtime")
        dependencies = (
            runtime.get("third_party_dependencies")
            if isinstance(runtime, Mapping) else None
        )
        sdk_version = (
            transport.get("sdk_version") if isinstance(transport, Mapping)
            else None
        )
        findings.require(
            isinstance(dependencies, list) and len(dependencies) == 1
            and dependencies[0] == {
                "name": "openai", "version": sdk_version
            },
            "empirical_runtime_dependency_mismatch",
        )
    return specs, actual_inventory


def _events_by_kind(run: DurableRun, kind: str) -> list[Mapping[str, Any]]:
    return [event for event in run.events if event.get("kind") == kind]


def _reservation_result_indexes(run: DurableRun, findings: _Findings):
    reservations: dict[tuple[str, int], Mapping[str, Any]] = {}
    results: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    reservation_ids: set[str] = set()
    for event in run.events:
        kind = event.get("kind")
        if kind == "reservation":
            key = (event.get("identity"), event.get("attempt"))
            reservation = event.get("reservation")
            reservation_id = (
                reservation.get("reservation_id")
                if isinstance(reservation, Mapping) else None
            )
            findings.require(
                isinstance(key[0], str) and type(key[1]) is int and key not in reservations,
                "duplicate_or_malformed_reservation_identity_attempt",
            )
            findings.require(
                isinstance(reservation_id, str)
                and reservation_id not in reservation_ids,
                "duplicate_or_malformed_reservation_id",
            )
            if isinstance(key[0], str) and type(key[1]) is int:
                reservations[key] = event
            if isinstance(reservation_id, str):
                reservation_ids.add(reservation_id)
        elif kind == "result":
            identity = event.get("identity")
            if isinstance(identity, str):
                results[identity].append(event)
            else:
                findings.require(False, "malformed_result_identity")
    return reservations, results


def _verify_calibration_gate(
        run: DurableRun,
        reservations: Mapping[tuple[str, int], Mapping[str, Any]],
        results: Mapping[str, list[Mapping[str, Any]]],
        smoke_specs: list[ScheduledRequest], findings: _Findings) -> dict[str, Any]:
    """Verify the exact in-run calibration decision that released smoke.

    The unified pilot has no cross-run inheritance at this boundary.  Its
    twenty calibration identities and the standard durable ``gate`` event are
    therefore part of the smoke audit's lineage.  This check recomputes the
    predeclared capability assessment but never interprets smoke outcomes.
    """
    calibration_specs = [
        spec for spec in run.plan.requests if spec.stage == "calibration"
    ]
    calibration_ids = [spec.identity for spec in calibration_specs]
    findings.require(
        len(calibration_specs) == 20
        and len(set(calibration_ids)) == 20
        and all(spec.phase == "calibration" and spec.action == "verdict"
                for spec in calibration_specs),
        "calibration_identity_inventory_not_exact_20",
    )
    findings.require(
        [spec.metadata.get("fixture_index") for spec in calibration_specs]
        == list(range(20)),
        "calibration_fixture_order_invalid",
    )
    missing = [identity for identity in calibration_ids if not results.get(identity)]
    findings.require(not missing, "calibration_results_missing")
    findings.require(
        all(results.get(identity, [{}])[-1].get("status") == "ok"
            for identity in calibration_ids),
        "calibration_final_status_not_ok",
    )
    findings.require(
        all((identity, row.get("attempt")) in reservations
            for identity in calibration_ids
            for row in results.get(identity, ())),
        "calibration_reservation_lineage_incomplete",
    )

    gates = [
        event for event in run.events
        if event.get("kind") == "gate" and event.get("gate") == "calibration"
    ]
    if not findings.require(len(gates) == 1,
                            "calibration_gate_not_exactly_once"):
        return {
            "scheduled": len(calibration_specs),
            "completed": len(calibration_specs) - len(missing),
            "gate_event_hash": None,
        }
    gate = gates[0]
    evidence = gate.get("evidence")
    findings.require(gate.get("passed") is True,
                     "calibration_gate_not_passed")
    findings.require(
        isinstance(evidence, Mapping)
        and gate.get("evidence_hash") == digest(evidence),
        "calibration_gate_evidence_hash_invalid",
    )
    findings.require(
        gate.get("prerequisite_identities") == calibration_ids,
        "calibration_gate_prerequisite_identities_invalid",
    )
    findings.require(
        isinstance(gate.get("decided_by"), str)
        and bool(gate["decided_by"].strip())
        and isinstance(gate.get("note"), str),
        "calibration_gate_decision_metadata_invalid",
    )

    computed = findings.capture(
        "calibration_assessment_reconstruction_failed",
        lambda: __import__(
            "collusion_recovery.gates", fromlist=["calibration_assessment"]
        ).calibration_assessment(run),
    )
    if isinstance(computed, Mapping):
        findings.require(computed.get("passed") is True,
                         "calibration_assessment_not_passed")
        findings.require(canonical(evidence) == canonical(computed),
                         "calibration_gate_evidence_mismatch")

    calibration_result_sequences = [
        row.get("sequence")
        for identity in calibration_ids for row in results.get(identity, ())
        if type(row.get("sequence")) is int
    ]
    smoke_reservation_sequences = [
        event.get("sequence")
        for spec in smoke_specs
        for (identity, _attempt), event in reservations.items()
        if identity == spec.identity and type(event.get("sequence")) is int
    ]
    findings.require(
        bool(calibration_result_sequences)
        and type(gate.get("sequence")) is int
        and max(calibration_result_sequences) < gate["sequence"],
        "calibration_gate_precedes_calibration_completion",
    )
    findings.require(
        bool(smoke_reservation_sequences)
        and type(gate.get("sequence")) is int
        and gate["sequence"] < min(smoke_reservation_sequences),
        "smoke_dispatch_precedes_calibration_gate",
    )
    return {
        "scheduled": len(calibration_specs),
        "completed": len(calibration_specs) - len(missing),
        "gate_event_hash": gate.get("event_hash"),
    }


def _valid_timing(provider_result: Mapping[str, Any]) -> bool:
    timing = provider_result.get("timing")
    if not isinstance(timing, Mapping) or set(timing) != {
            "started_at", "ended_at", "elapsed_seconds"}:
        return False
    started = _aware_time(timing.get("started_at"))
    ended = _aware_time(timing.get("ended_at"))
    elapsed = timing.get("elapsed_seconds")
    return bool(
        started is not None and ended is not None and started <= ended
        and not isinstance(elapsed, bool) and isinstance(elapsed, (int, float))
        and math.isfinite(elapsed) and elapsed >= 0
    )


def _eligible_ambiguous_5xx(row: Mapping[str, Any]) -> bool:
    provider = row.get("provider_result")
    evidence = (
        provider.get("transport_evidence")
        if isinstance(provider, Mapping) else None
    )
    return bool(
        row.get("status") == "ambiguous_timeout"
        and row.get("ambiguous") is True
        and row.get("known_not_completed") is False
        and isinstance(evidence, Mapping)
        and evidence.get("schema_version") == "v1"
        and evidence.get("failure_class") == "sdk_http_error"
        and evidence.get("http_status") in {500, 502, 503, 504}
        and evidence.get("response_http_status") == evidence.get("http_status")
        and evidence.get("provider_request_id")
        == provider.get("provider_request_id")
        and evidence.get("x_request_id") == provider.get("provider_request_id")
        and evidence.get("request_id_consistent") is True
        and evidence.get("response_received") is True
        and evidence.get("output_absent") is True
        and evidence.get("usage_absent") is True
        and evidence.get("retry_eligible") is True
        and provider.get("usage") is None
        and provider.get("output_text") is None
        and isinstance(provider.get("raw_response"), Mapping)
        and "output" not in provider["raw_response"]
        and "usage" not in provider["raw_response"]
    )


def _valid_known_not_completed_retry(row: Mapping[str, Any]) -> bool:
    provider = row.get("provider_result")
    return bool(
        row.get("status") == "retryable_error"
        and row.get("ambiguous") is False
        and row.get("known_not_completed") is True
        and row.get("usage") is None
        and isinstance(provider, Mapping)
        and provider.get("status") == "retryable_error"
        and provider.get("ambiguous") is False
        and provider.get("known_not_completed") is True
        and provider.get("usage") is None
        and provider.get("output_text") is None
        and provider.get("parsed_output") is None
        and provider.get("refusal") is None
        and isinstance(provider.get("error"), str)
        and bool(provider["error"].strip())
    )


def _verify_provider_result(
        run: DurableRun, row: Mapping[str, Any], request_payload: Mapping[str, Any],
        expected_identity: Mapping[str, Any], findings: _Findings,
        prefix: str) -> None:
    provider = row.get("provider_result")
    if not findings.require(isinstance(provider, Mapping),
                            f"{prefix}_provider_result_missing"):
        return
    findings.require(set(provider) == _PROVIDER_RESULT_FIELDS,
                     f"{prefix}_provider_result_schema_drift")
    findings.require(row.get("status") == provider.get("status"),
                     f"{prefix}_status_derivative_mismatch")
    findings.require(row.get("ambiguous") is provider.get("ambiguous"),
                     f"{prefix}_ambiguity_derivative_mismatch")
    findings.require(
        row.get("known_not_completed") is provider.get("known_not_completed"),
        f"{prefix}_completion_derivative_mismatch",
    )
    findings.require(
        canonical(row.get("usage")) == canonical(provider.get("usage")),
        f"{prefix}_usage_derivative_mismatch",
    )
    findings.require(_valid_timing(provider), f"{prefix}_timing_invalid")

    status = row.get("status")
    if status in _FINAL_STATUSES or status == "overlength":
        matches = provider_result_matches_raw_response(
            provider,
            response_mode=request_payload.get("response_mode"),
            message_max_codepoints=request_payload.get(
                "message_max_codepoints", 200),
            message_retry_history_max_utf8_bytes=request_payload.get(
                "message_retry_history_max_utf8_bytes", 2048),
        )
        findings.require(matches, f"{prefix}_raw_response_derivative_mismatch")
        candidate = _provider_identity_candidate(run.config, status, provider)
        findings.require(candidate == expected_identity,
                         f"{prefix}_provider_identity_drift")
        findings.require(row.get("provider_identity") == candidate,
                         f"{prefix}_provider_identity_not_journal_bound")
    elif status == "retryable_error":
        findings.require(_valid_known_not_completed_retry(row),
                         f"{prefix}_retryable_evidence_invalid")
        findings.require(row.get("provider_identity") is None,
                         f"{prefix}_transport_failure_has_provider_identity")
    elif status == "ambiguous_timeout":
        findings.require(_eligible_ambiguous_5xx(row),
                         f"{prefix}_ambiguous_5xx_evidence_invalid")
        findings.require(row.get("provider_identity") is None,
                         f"{prefix}_transport_failure_has_provider_identity")
    else:
        findings.require(False, f"{prefix}_unsupported_status_{status}")


def _current_payload(saved_request: Mapping[str, Any]) -> Mapping[str, Any] | None:
    turns = saved_request.get("turns")
    if not isinstance(turns, list) or not turns:
        return None
    content = turns[-1].get("content") if isinstance(turns[-1], Mapping) else None
    if not isinstance(content, str):
        return None
    try:
        tagged = json.loads(content.rpartition("\n")[2])
        current = tagged["current"]
    except (KeyError, TypeError, ValueError):
        return None
    return current if isinstance(current, Mapping) else None


def _verify_round_visibility(
        spec: ScheduledRequest, saved_request: Mapping[str, Any],
        findings: _Findings) -> None:
    if spec.phase != "formation":
        return
    prefix = f"visibility_{spec.identity}_{saved_request.get('attempt')}"
    current = _current_payload(saved_request)
    if not findings.require(current is not None, f"{prefix}_current_unreadable"):
        return
    communication = current.get("communication")
    if spec.action == "message" and spec.metadata.get("message_round") == 1:
        findings.require(current.get("message_round") == 1,
                         f"{prefix}_round1_label_missing")
        findings.require(communication is None,
                         f"{prefix}_round1_sees_current_messages")
        return
    if spec.action == "message" and spec.metadata.get("message_round") == 2:
        rounds = communication.get("message_rounds") if isinstance(
            communication, Mapping) else None
        findings.require(current.get("message_round") == 2,
                         f"{prefix}_round2_label_missing")
        findings.require(isinstance(rounds, Mapping) and set(rounds) == {"round_1"},
                         f"{prefix}_round2_visibility_frontier_invalid")
        return
    rounds = communication.get("message_rounds") if isinstance(
        communication, Mapping) else None
    findings.require(
        isinstance(rounds, Mapping) and set(rounds) == {"round_1", "round_2"},
        f"{prefix}_verdict_visibility_frontier_invalid",
    )
    findings.require("peer_verdict" not in current and "verdicts" not in current,
                     f"{prefix}_verdict_leakage")


def _verify_attempts(
        run: DurableRun, specs: list[ScheduledRequest],
        reservations: Mapping[tuple[str, int], Mapping[str, Any]],
        results: Mapping[str, list[Mapping[str, Any]]],
        findings: _Findings) -> dict[str, Any]:
    factory = findings.capture(
        "scientific_request_factory_invalid",
        lambda: ScientificRequestFactory(run.config, run.plan),
    )
    manifest = findings.capture("manifest_unreadable", lambda: run.manifest) or {}
    expected_identity = manifest.get("observed_provider_identity")
    findings.require(_valid_provider_identity(expected_identity),
                     "observed_provider_identity_invalid")

    raw_verified = payloads_verified = wires_verified = 0
    retry_statuses: Counter[str] = Counter()
    final_statuses: Counter[str] = Counter()
    ambiguous_identities: list[str] = []
    total_attempts = 0
    final_rows: dict[str, Mapping[str, Any]] = {}
    first_reservations: dict[str, Mapping[str, Any]] = {}

    for spec in specs:
        rows = list(results.get(spec.identity, ()))
        prefix = f"attempts_{spec.identity}"
        findings.require(bool(rows), f"{prefix}_missing")
        findings.require(
            [row.get("attempt") for row in rows] == list(range(len(rows))),
            f"{prefix}_ordinals_not_contiguous",
        )
        total_attempts += len(rows)
        if rows:
            final_rows[spec.identity] = rows[-1]
            final_statuses[str(rows[-1].get("status"))] += 1
            findings.require(rows[-1].get("status") in _FINAL_STATUSES,
                             f"{prefix}_final_not_observation")
        overlength = retryable = ambiguous = 0
        for index, row in enumerate(rows):
            attempt = row.get("attempt")
            key = (spec.identity, attempt)
            reservation_event = reservations.get(key)
            item_prefix = f"{spec.identity}_attempt_{attempt}"
            if index < len(rows) - 1:
                findings.require(
                    row.get("status") in _RETRYABLE_INTERMEDIATE_STATUSES,
                    f"{item_prefix}_nonfinal_status_not_retryable",
                )
            if row.get("status") == "overlength":
                overlength += 1
            elif row.get("status") == "retryable_error":
                retryable += 1
            elif row.get("status") == "ambiguous_timeout":
                ambiguous += 1
            if index < len(rows) - 1:
                retry_statuses[str(row.get("status"))] += 1

            if not findings.require(
                    isinstance(reservation_event, Mapping),
                    f"{item_prefix}_reservation_missing"):
                continue
            if index == 0:
                first_reservations[spec.identity] = reservation_event
            reservation = reservation_event.get("reservation")
            findings.require(
                isinstance(reservation, Mapping)
                and reservation.get("identity") == spec.identity
                and reservation.get("attempt") == attempt
                and row.get("reservation_id") == reservation.get("reservation_id")
                and reservation_event.get("sequence", -1) < row.get("sequence", -1),
                f"{item_prefix}_reservation_result_lineage_invalid",
            )
            findings.require(
                row.get("request_hash") == reservation_event.get("request_hash")
                and row.get("wire_payload_hash")
                == reservation_event.get("wire_payload_hash"),
                f"{item_prefix}_payload_hash_lineage_invalid",
            )

            expected_request = None
            if factory is not None and type(attempt) is int:
                expected_request = findings.capture(
                    f"{item_prefix}_request_reconstruction_failed",
                    lambda spec=spec, attempt=attempt, rows=rows, index=index: factory(
                        spec, ExecutionContext(run, attempt, tuple(rows[:index]))
                    ),
                )
            actual_request = findings.capture(
                f"{item_prefix}_request_payload_unreadable",
                lambda row=row: run.request_payload(row.get("request_hash")),
            )
            if expected_request is not None and isinstance(actual_request, Mapping):
                exact_request = expected_request.to_dict()
                verified = canonical(actual_request) == canonical(exact_request)
                findings.require(verified, f"{item_prefix}_request_payload_mismatch")
                if verified:
                    payloads_verified += 1
                findings.require(
                    reservation.get("input_tokens")
                    == expected_request.conservative_input_token_bound
                    and reservation.get("output_tokens")
                    == expected_request.max_output_tokens,
                    f"{item_prefix}_reservation_shape_mismatch",
                )
                _verify_round_visibility(spec, actual_request, findings)
                expected_wire = OpenAIResponsesProvider.build_payload(expected_request)
                actual_wire = findings.capture(
                    f"{item_prefix}_wire_payload_unreadable",
                    lambda row=row: run.wire_payload(row.get("wire_payload_hash")),
                )
                wire_verified = (
                    isinstance(actual_wire, Mapping)
                    and canonical(actual_wire) == canonical(expected_wire)
                    and digest(actual_wire) == row.get("wire_payload_hash")
                )
                findings.require(wire_verified, f"{item_prefix}_wire_payload_mismatch")
                if wire_verified:
                    wires_verified += 1
                before = len(findings.failures)
                _verify_provider_result(
                    run, row, actual_request, expected_identity, findings,
                    item_prefix,
                )
                if len(findings.failures) == before:
                    raw_verified += 1

        findings.require(overlength <= (1 if spec.action == "message" else 0),
                         f"{prefix}_overlength_retry_cap_exceeded")
        findings.require(
            retryable <= run.config.retry_policy.transport_retries,
            f"{prefix}_transport_retry_cap_exceeded",
        )
        findings.require(ambiguous <= 1,
                         f"{prefix}_ambiguous_5xx_retry_cap_exceeded")
        if ambiguous:
            ambiguous_identities.append(spec.identity)

    findings.require(total_attempts <= SMOKE_MAX_DISPATCH_ATTEMPTS,
                     "smoke_dispatch_attempt_ceiling_exceeded")
    findings.require(len(ambiguous_identities) <= 2,
                     "smoke_ambiguous_5xx_identity_cap_exceeded")
    findings.require(len(final_rows) == SMOKE_LOGICAL_REQUESTS,
                     "smoke_final_observation_count_not_192")
    return {
        "factory": factory,
        "total_attempts": total_attempts,
        "payloads_verified": payloads_verified,
        "wires_verified": wires_verified,
        "raw_results_verified": raw_verified,
        "retry_status_counts": dict(sorted(retry_statuses.items())),
        "final_status_counts": dict(sorted(final_statuses.items())),
        "ambiguous_5xx_identities": ambiguous_identities,
        "final_rows": final_rows,
        "first_reservations": first_reservations,
    }


def _verify_retry_schedule(
        run: DurableRun, specs: list[ScheduledRequest],
        results: Mapping[str, list[Mapping[str, Any]]],
        reservations: Mapping[tuple[str, int], Mapping[str, Any]],
        findings: _Findings) -> None:
    schedules = _events_by_kind(run, "ambiguous_5xx_retry_scheduled")
    by_result_hash: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in schedules:
        by_result_hash[str(event.get("failed_result_event_hash"))].append(event)
    eligible_rows = [
        row for spec in specs for row in results.get(spec.identity, ())
        if _eligible_ambiguous_5xx(row)
    ]
    findings.require(len(schedules) == len(eligible_rows),
                     "ambiguous_5xx_schedule_count_mismatch")
    eligible_hashes = {row.get("event_hash") for row in eligible_rows}
    findings.require(
        all(event.get("failed_result_event_hash") in eligible_hashes
            for event in schedules),
        "ambiguous_5xx_schedule_has_unknown_result",
    )
    for row in eligible_rows:
        matches = by_result_hash.get(str(row.get("event_hash")), [])
        prefix = f"ambiguous_schedule_{row.get('identity')}_{row.get('attempt')}"
        if not findings.require(len(matches) == 1, f"{prefix}_not_exactly_once"):
            continue
        event = matches[0]
        next_attempt = row.get("attempt") + 1
        next_reservation = reservations.get((row.get("identity"), next_attempt))
        recorded = _aware_time(event.get("recorded_at"))
        not_before = _aware_time(event.get("not_before"))
        delay = event.get("delay_seconds")
        findings.require(
            event.get("identity") == row.get("identity")
            and event.get("failed_attempt") == row.get("attempt")
            and event.get("next_attempt") == next_attempt
            and event.get("provider_request_id")
            == row.get("provider_result", {}).get("provider_request_id")
            and event.get("http_status")
            == row.get("provider_result", {}).get("transport_evidence", {}).get(
                "http_status")
            and event.get("request_hash") == row.get("request_hash")
            and event.get("wire_payload_hash") == row.get("wire_payload_hash")
            and event.get("retry_index") == 1
            and event.get("per_identity_retry_cap") == 1
            and event.get("release_affected_identity_cap") == 2
            and event.get("retry_policy_hash")
            == V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
            f"{prefix}_binding_invalid",
        )
        findings.require(
            not isinstance(delay, bool) and isinstance(delay, (int, float))
            and math.isfinite(delay) and 5 <= delay <= 60
            and recorded is not None and not_before is not None
            and (not_before - recorded).total_seconds() == delay,
            f"{prefix}_delay_invalid",
        )
        findings.require(
            isinstance(next_reservation, Mapping)
            and row.get("sequence", -1) < event.get("sequence", -1)
            < next_reservation.get("sequence", -1)
            and _aware_time(next_reservation.get("recorded_at")) >= not_before,
            f"{prefix}_timing_invalid",
        )
        findings.require(
            next_reservation.get("wire_payload_hash") == row.get("wire_payload_hash"),
            f"{prefix}_wire_changed",
        )


def _select(
        specs: Iterable[ScheduledRequest], phase: str, action: str,
        **metadata: Any) -> ScheduledRequest | None:
    matches = [
        spec for spec in specs
        if spec.phase == phase and spec.action == action
        and all(spec.metadata.get(key) == value for key, value in metadata.items())
    ]
    return matches[0] if len(matches) == 1 else None


def _verify_two_round_timing(
        specs: list[ScheduledRequest],
        final_rows: Mapping[str, Mapping[str, Any]],
        first_reservations: Mapping[str, Mapping[str, Any]],
        findings: _Findings) -> None:
    for history in ("U", "C"):
        previous_verdicts: list[ScheduledRequest] = []
        for episode in range(10):
            round_one = [
                _select(specs, "formation", "message", block=0,
                        history=history, episode=episode, role=role,
                        message_round=1)
                for role in ROLES
            ]
            round_two = [
                _select(specs, "formation", "message", block=0,
                        history=history, episode=episode, role=role,
                        message_round=2)
                for role in ROLES
            ]
            verdicts = [
                _select(specs, "formation", "verdict", block=0,
                        history=history, episode=episode, role=role)
                for role in ROLES
            ]
            prefix = f"formation_{history}_{episode}"
            if not findings.require(
                    all(item is not None for item in (*round_one, *round_two, *verdicts)),
                    f"{prefix}_identity_inventory_invalid"):
                continue
            round_one = list(round_one)  # type: ignore[arg-type]
            round_two = list(round_two)  # type: ignore[arg-type]
            verdicts = list(verdicts)  # type: ignore[arg-type]
            findings.require(
                set(round_one[0].dependencies)
                == {item.identity for item in previous_verdicts}
                and round_one[0].dependencies == round_one[1].dependencies,
                f"{prefix}_round1_dependency_frontier_invalid",
            )
            findings.require(
                all(set(item.dependencies)
                    == {other.identity for other in round_one}
                    for item in round_two),
                f"{prefix}_round2_dependency_frontier_invalid",
            )
            findings.require(
                all(set(item.dependencies)
                    == {other.identity for other in (*round_one, *round_two)}
                    for item in verdicts),
                f"{prefix}_verdict_dependency_frontier_invalid",
            )
            try:
                round_one_end = max(final_rows[item.identity]["sequence"]
                                    for item in round_one)
                round_two_start = min(first_reservations[item.identity]["sequence"]
                                      for item in round_two)
                round_two_end = max(final_rows[item.identity]["sequence"]
                                    for item in round_two)
                verdict_start = min(first_reservations[item.identity]["sequence"]
                                    for item in verdicts)
            except (KeyError, TypeError, ValueError):
                findings.require(False, f"{prefix}_durable_timing_missing")
            else:
                findings.require(round_one_end < round_two_start,
                                 f"{prefix}_round2_started_before_round1_complete")
                findings.require(round_two_end < verdict_start,
                                 f"{prefix}_verdict_started_before_round2_complete")
                if previous_verdicts:
                    previous_end = max(
                        final_rows[item.identity]["sequence"]
                        for item in previous_verdicts
                    )
                    current_start = min(
                        first_reservations[item.identity]["sequence"]
                        for item in round_one
                    )
                    findings.require(previous_end < current_start,
                                     f"{prefix}_started_before_prior_episode_complete")
            previous_verdicts = verdicts


def _verify_checkpoints_and_artifacts(
        run: DurableRun, specs: list[ScheduledRequest],
        final_rows: Mapping[str, Mapping[str, Any]],
        first_reservations: Mapping[str, Mapping[str, Any]],
        factory: ScientificRequestFactory | None,
        findings: _Findings) -> None:
    checkpoint_specs = [item for item in run.plan.checkpoints if item.block == 0]
    findings.require(len(checkpoint_specs) == 2,
                     "block0_checkpoint_spec_count_not_2")
    findings.require({item.history for item in checkpoint_specs} == {"U", "C"},
                     "block0_checkpoint_history_inventory_drift")
    findings.require(all(len(item.contributors) == 60 for item in checkpoint_specs),
                     "block0_checkpoint_contributor_count_drift")

    checkpoint_events = _events_by_kind(run, "checkpoint")
    findings.require(len(checkpoint_events) == 2,
                     "durable_checkpoint_event_count_not_2")
    by_id = defaultdict(list)
    for event in checkpoint_events:
        by_id[event.get("checkpoint_id")].append(event)
    for checkpoint in checkpoint_specs:
        matches = by_id.get(checkpoint.checkpoint_id, [])
        prefix = f"checkpoint_{checkpoint.history}"
        if not findings.require(len(matches) == 1, f"{prefix}_not_exactly_once"):
            continue
        event = matches[0]
        try:
            rows = [final_rows[identity] for identity in checkpoint.contributors]
        except KeyError:
            findings.require(False, f"{prefix}_contributor_result_missing")
            continue
        result_hashes = [digest(row) for row in rows]
        expected_hash = digest({
            "spec": checkpoint.to_dict(), "result_hashes": result_hashes,
        })
        findings.require(
            event.get("checkpoint_hash") == expected_hash
            and event.get("block") == 0
            and event.get("history") == checkpoint.history
            and event.get("contributors") == list(checkpoint.contributors)
            and event.get("result_hashes") == result_hashes,
            f"{prefix}_lineage_invalid",
        )
        findings.require(
            max(row.get("sequence", -1) for row in rows)
            < event.get("sequence", -1),
            f"{prefix}_recorded_before_contributors_complete",
        )
        consumers = [
            spec for spec in specs
            if spec.phase == "evaluation"
            and spec.parent_checkpoint == checkpoint.checkpoint_id
        ]
        findings.require(bool(consumers), f"{prefix}_has_no_smoke_consumers")
        for spec in consumers:
            reservation = first_reservations.get(spec.identity)
            findings.require(
                isinstance(reservation, Mapping)
                and event.get("sequence", -1) < reservation.get("sequence", -1),
                f"{prefix}_consumer_started_before_checkpoint",
            )

    artifact_events = _events_by_kind(run, "scientific_artifact")
    actual: dict[str, Mapping[str, Any]] = {}
    for event in artifact_events:
        artifact = event.get("artifact")
        artifact_id = event.get("artifact_id")
        valid = (
            isinstance(artifact, Mapping) and isinstance(artifact_id, str)
            and artifact.get("artifact_id") == artifact_id
            and event.get("artifact_hash") == digest(artifact)
            and artifact_id not in actual
        )
        findings.require(valid, "scientific_artifact_lineage_invalid")
        if valid:
            actual[artifact_id] = artifact
    if factory is None:
        findings.require(False, "scientific_artifact_reconstruction_unavailable")
        return
    expected = findings.capture(
        "scientific_artifact_reconstruction_failed",
        lambda: factory.durable_artifacts(run),
    )
    if expected is None:
        return
    expected_by_id = {item["artifact_id"]: item for item in expected}
    findings.require(len(expected_by_id) == 5,
                     "smoke_scientific_artifact_count_not_5")
    findings.require(
        Counter(item.get("artifact_kind") for item in expected) == Counter({
            "checkpoint_snapshot": 2,
            "evaluation_sequence_end": 3,
        }),
        "smoke_scientific_artifact_kind_inventory_drift",
    )
    findings.require(canonical(actual) == canonical(expected_by_id),
                     "smoke_scientific_artifacts_mismatch")


def _verify_budget(
        run: DurableRun, reservations: Mapping[tuple[str, int], Mapping[str, Any]],
        results: Mapping[str, list[Mapping[str, Any]]],
        findings: _Findings) -> dict[str, Any] | None:
    manifest = findings.capture("manifest_unreadable", lambda: run.manifest)
    if not isinstance(manifest, Mapping):
        return None
    release_value = manifest.get("authorized_release_limits")
    release = None
    if release_value is not None:
        release = findings.capture(
            "release_limits_invalid",
            lambda: DispatchReleaseLimits.from_mapping(release_value),
        )
    findings.require(
        release is None or release.max_in_flight == 1,
        "released_max_in_flight_not_1",
    )
    try:
        approved = Decimal(str(manifest.get("authorized_budget_usd")))
    except (TypeError, ValueError):
        findings.require(False, "authorized_budget_invalid")
        return None
    ledger = findings.capture(
        "budget_replay_failed",
        lambda: BudgetLedger.replay(
            run.config.limits, run.config.pricing, approved,
            run.budget_records(), release,
        ),
    )
    if ledger is None:
        return None
    snapshot = ledger.snapshot()
    findings.require(not ledger.active, "budget_has_unresolved_reservation")
    findings.require(
        snapshot["consumed"]["requests"] == len(reservations)
        == sum(len(rows) for rows in results.values()),
        "budget_attempt_inventory_mismatch",
    )
    findings.require(canonical(manifest.get("budget")) == canonical(snapshot),
                     "manifest_budget_snapshot_mismatch")
    findings.require(snapshot["consumed"]["requests"]
                     <= snapshot["approved"]["requests"],
                     "budget_request_ceiling_exceeded")
    findings.require(snapshot["consumed"]["input_tokens"]
                     <= snapshot["approved"]["input_tokens"],
                     "budget_input_ceiling_exceeded")
    findings.require(snapshot["consumed"]["output_tokens"]
                     <= snapshot["approved"]["output_tokens"],
                     "budget_output_ceiling_exceeded")
    findings.require(Decimal(snapshot["consumed"]["cost_usd"])
                     <= Decimal(snapshot["approved"]["cost_usd"]),
                     "budget_cost_ceiling_exceeded")
    return snapshot


def audit_v1_2_smoke_integrity(run: DurableRun) -> dict[str, Any]:
    """Return a deterministic, exhaustive, read-only Gate-2 audit report.

    A passing report is sufficient for the mechanical/human-integrity boundary:
    no separate transcript spot-check is needed.  Scientific qualification is
    deliberately out of scope; the later formation marker remains a separate
    conditional gate.
    """
    if not isinstance(run, DurableRun):
        raise TypeError("v1.2 smoke audit requires an opened DurableRun")
    findings = _Findings()
    _verify_journal_chain(run, findings)
    specs, inventory = _verify_plan_and_inventory(run, findings)
    reservations, results = _reservation_result_indexes(run, findings)
    calibration = _verify_calibration_gate(
        run, reservations, results, specs, findings
    )

    allowed = _through_smoke_identities(run.plan)
    dispatched = {
        event.get("identity") for event in run.events
        if event.get("kind") in {"reservation", "result"}
    }
    out_of_scope = sorted(
        identity if isinstance(identity, str) else repr(identity)
        for identity in dispatched
        if not isinstance(identity, str) or identity not in allowed
    )
    findings.require(not out_of_scope, "out_of_scope_dispatch_detected")

    attempt_audit = _verify_attempts(
        run, specs, reservations, results, findings
    )
    _verify_retry_schedule(run, specs, results, reservations, findings)
    _verify_two_round_timing(
        specs,
        attempt_audit["final_rows"],
        attempt_audit["first_reservations"],
        findings,
    )
    _verify_checkpoints_and_artifacts(
        run, specs,
        attempt_audit["final_rows"],
        attempt_audit["first_reservations"],
        attempt_audit["factory"],
        findings,
    )
    budget = _verify_budget(run, reservations, results, findings)

    smoke_ids = {spec.identity for spec in specs}
    smoke_reservations = {
        key: event for key, event in reservations.items() if key[0] in smoke_ids
    }
    unused_reservations = sorted(
        f"{identity}:{attempt}" for identity, attempt in reservations
        if not any(
            row.get("attempt") == attempt
            for row in results.get(identity, ())
        )
    )
    findings.require(not unused_reservations, "reservation_without_result")
    findings.require(
        len(smoke_reservations) == attempt_audit["total_attempts"],
        "smoke_reservation_attempt_count_mismatch",
    )

    manifest = findings.capture("manifest_unreadable", lambda: run.manifest) or {}
    report = {
        "assessment_type": "v1_2_block_0_smoke_exhaustive_integrity_v1",
        "audit_contract_version": V1_2_SMOKE_AUDIT_CONTRACT_VERSION,
        "audit_contract_hash": V1_2_SMOKE_AUDIT_CONTRACT_HASH,
        "outcome_blind": True,
        "protocol_version": run.config.protocol_version,
        "source": run.config.source,
        "run_id": run.config.run_id,
        "plan_hash": run.plan.plan_hash,
        "fixture_inventory_hash": digest(inventory),
        "journal_tail": manifest.get("last_event_hash"),
        "journal_event_count": len(run.events),
        "scope": {
            "calibration": calibration,
            "scheduled_smoke_identities": len(specs),
            "completed_smoke_identities": len(attempt_audit["final_rows"]),
            "smoke_dispatch_attempts": attempt_audit["total_attempts"],
            "maximum_smoke_dispatch_attempts": SMOKE_MAX_DISPATCH_ATTEMPTS,
            "out_of_scope_identities": out_of_scope,
            "unused_reservations": unused_reservations,
        },
        "lineage": {
            "request_payloads_verified": attempt_audit["payloads_verified"],
            "wire_payloads_verified": attempt_audit["wires_verified"],
            "raw_results_verified": attempt_audit["raw_results_verified"],
            "provider_identity": manifest.get("observed_provider_identity"),
            "retry_status_counts": attempt_audit["retry_status_counts"],
            "final_status_counts": attempt_audit["final_status_counts"],
            "ambiguous_5xx_identities": attempt_audit[
                "ambiguous_5xx_identities"
            ],
        },
        "budget": budget,
        "human_review_required": False,
        "later_stage_release_permitted": not findings.failures,
        "failures": sorted(findings.failures),
        "automatic_checks_passed": not findings.failures,
    }
    report["report_hash"] = digest(report)
    return report


def require_v1_2_smoke_integrity(run: DurableRun) -> dict[str, Any]:
    """Return the passing report or raise before any later-stage release."""
    report = audit_v1_2_smoke_integrity(run)
    if not report["automatic_checks_passed"]:
        raise V12SmokeIntegrityError(
            "v1.2 smoke integrity failed: " + ", ".join(report["failures"])
        )
    return report

