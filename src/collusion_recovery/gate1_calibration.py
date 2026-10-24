"""Fresh v1.1 Gate 1 capability-calibration release controller.

Gate 1 is a distinct, immutable release: exactly twenty isolated full-evidence
verdict reviews under ``calibration/v1.1``.  It is not a resume or relabeling
of the completed v1.0 Stage 2 run, and it has no preflight, smoke, formation,
evaluation, probe, checkpoint, or later-stage dispatch path.

Inspection and authorization packaging are credential-free.  Empirical
execution requires a fresh external owner-only human authorization, claimed
once in a durable external receipt before any key is read or client is built.
After all twenty observed results, the controller persists the exact frozen
calibration assessment and an immutable passed/failed gate decision, then
hard-stops before smoke.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import time
from typing import Any, Mapping
import uuid

from .budget import (
    BudgetExhausted,
    BudgetLedger,
    DispatchReleaseLimits,
    LONG_CONTEXT_INPUT_MULTIPLIER,
    LONG_CONTEXT_OUTPUT_MULTIPLIER,
    LONG_CONTEXT_THRESHOLD_INPUT_TOKENS,
    pricing_multipliers,
)
from .common import canonical, digest, utc_now
from .gate2_readiness import (
    Gate2ReadinessError,
    PRICING_REVERIFIED_AT,
    PRICING_SOURCE,
    STAGE2_RUN_DIR,
    verify_stage2_lineage,
)
from .gates import calibration_assessment
from .live_config import LiveConfig
from .orchestration import (
    AuthorizationRequiredError,
    GATE1_CALIBRATION_BINDING_EVENT,
    GATE1_CALIBRATION_CONTROLLER_ID,
    GATE1_CALIBRATION_RUN_ID,
    KNOWN_NOT_COMPLETED,
    OBSERVATION_STATUSES,
    PERSISTENT_GLOBAL_STOPS,
    PROVIDER_IDENTITY_STATUSES,
    STOP_STATUSES,
    DispatchAuthorizationArtifact,
    DurableRun,
    ExecutionContext,
    ResumeError,
    ScheduledRequest,
    _object_dict,
    _provider_identity_candidate,
    _status_text,
    authorization_binding,
    build_request_plan,
    validate_dispatch_authorization,
)
from .providers.openai import OpenAIResponsesProvider, OpenAISDKTransport
from .stage2 import (
    STAGE2_ENV_FILE,
    STAGE2_PREFLIGHT_INVENTORY_HASH,
    STAGE2_SDK_VERSION,
    STAGE2_TIMEOUT_SECONDS,
    _load_local_openai_key,
    _stage2_transport_metadata,
)
from .stage2_lineage_snapshot import (
    Stage2LineageSnapshotError,
    load_stage2_lineage_snapshot,
    verify_stage2_lineage_snapshot_against_raw,
)
from .staged_requests import ScientificRequestFactory
from .visibility import ROOT


CONTROLLER_ID = GATE1_CALIBRATION_CONTROLLER_ID
EXECUTION_MODE_BINDING_EVENT = "gate1_execution_mode_binding"
AUTHORIZATION_CLAIM_EVENT = "gate1_authorization_claimed"
EMPIRICAL_EXECUTION_MODE = "empirical_live"
FAKE_EXECUTION_MODE = "fake_transport_test"
GATE1_PROTOCOL_VERSION = "v1.1"
GATE1_RUN_ID = GATE1_CALIBRATION_RUN_ID
GATE1_SPLIT = f"pilot/v1.1/gate1-{GATE1_RUN_ID}"
GATE1_SEED_NAMESPACE = f"pilot/v1.1/gate1-{GATE1_RUN_ID}"
GATE1_CALIBRATION_NAMESPACE = "calibration/v1.1"
GATE1_RUN_DIRECTORY = f"runs/empirical/gate1-calibration-v1.1-{GATE1_RUN_ID}"
AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS = 15 * 60
AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS = 60
PRICING_MAX_AGE_SECONDS = 24 * 60 * 60
PROPOSAL_PATH = ROOT / "configs/gate1_calibration_proposal.json"
AUTHORIZATION_SCHEMA_PATH = ROOT / "release/GATE1_HUMAN_AUTHORIZATION_SCHEMA.json"
AUTHORIZATION_RECEIPT_DIRECTORY = (
    Path(pwd.getpwuid(os.getuid()).pw_dir)
    / ".collusion-recovery-authorization-receipts"
    / "gate1-v1.1"
)
HUMAN_ACKNOWLEDGEMENT = (
    "I authorize exactly the Gate 1 v1.1 capability-calibration scope and "
    "exact ceilings bound by this proposal. I understand that this release "
    "hard-stops before smoke and does not authorize the experiment."
)
_AUTH_FIELDS = {
    "artifact_type",
    "authorization_uuid",
    "authorization_id",
    "authorization_nonce",
    "gate1_run_id",
    "gate1_run_directory",
    "confirmed_by",
    "confirmed_at",
    "proposal_hash",
    "approved_scope",
    "approved_caps",
    "approved_pricing_contract_hash",
    "approved_transport_hash",
    "approved_provider_identity",
    "approved_protocol_bridge_hash",
    "approved_gate1_config_hash",
    "approved_gate1_plan_hash",
    "approved_dispatch_authorization_hash",
    "approved_calibration_inventory_hash",
    "allow_paid_calls",
    "human_acknowledgement",
    "authorization_hash",
}


class Gate1CalibrationError(RuntimeError):
    """The Gate 1 proposal, authorization, run, or evidence is unsafe."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise Gate1CalibrationError(f"{label} is unexpectedly large")
        value = json.loads(raw.decode("utf-8"))
    except Gate1CalibrationError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise Gate1CalibrationError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise Gate1CalibrationError(f"{label} must be a JSON object")
    return value


def _write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(canonical(dict(value)) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def _write_immutable_json(path: Path, value: Mapping[str, Any], label: str) -> None:
    if Path(path).exists():
        if canonical(_read_json(Path(path), label)) != canonical(dict(value)):
            raise Gate1CalibrationError(f"existing {label} differs from durable evidence")
        return
    _write_exclusive_json(Path(path), value)


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise Gate1CalibrationError(f"{label} must be a nonempty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Gate1CalibrationError(f"{label} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Gate1CalibrationError(f"{label} requires an explicit UTC offset")
    return parsed


def _utc_now_datetime(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise Gate1CalibrationError("authorization clock requires an explicit UTC offset")
    return value.astimezone(timezone.utc)


def _validate_pricing_window(
        proposal: Mapping[str, Any], *, now: datetime | None = None) -> datetime:
    current = _utc_now_datetime(now)
    pricing = _parse_timestamp(
        proposal["pricing_contract"]["reverified_at"],
        "pricing reverification",
    ).astimezone(timezone.utc)
    if pricing > current + timedelta(seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS):
        raise Gate1CalibrationError(
            "pricing reverification timestamp is implausibly far in the future"
        )
    if current - pricing > timedelta(seconds=PRICING_MAX_AGE_SECONDS):
        raise Gate1CalibrationError(
            "reviewed pricing is stale; regenerate and reauthorize the proposal"
        )
    return pricing


def _validate_first_claim_window(
        authorization: Mapping[str, Any], proposal: Mapping[str, Any], *,
        now: datetime | None = None) -> None:
    current = _utc_now_datetime(now)
    confirmed = _parse_timestamp(
        authorization.get("confirmed_at"), "confirmed_at"
    ).astimezone(timezone.utc)
    pricing = _validate_pricing_window(proposal, now=current)
    if confirmed > current + timedelta(seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS):
        raise Gate1CalibrationError(
            "human authorization timestamp is implausibly far in the future"
        )
    if confirmed < pricing:
        raise Gate1CalibrationError(
            "human authorization predates the pricing and proposal reverification"
        )
    if current - confirmed > timedelta(
            seconds=AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS):
        raise Gate1CalibrationError(
            "unused human authorization expired before its first claim"
        )


def _projection_config_values(
        lineage: Mapping[str, Any], limits: Mapping[str, Any]) -> dict[str, Any]:
    values = lineage["config"].to_dict()
    values.update({
        "run_id": GATE1_RUN_ID,
        "protocol_version": GATE1_PROTOCOL_VERSION,
        "split": GATE1_SPLIT,
        "seed_namespace": GATE1_SEED_NAMESPACE,
        "calibration_namespace": GATE1_CALIBRATION_NAMESPACE,
        "stage": "calibration",
    })
    values["pricing"]["verified_at"] = PRICING_REVERIFIED_AT
    values["limits"] = dict(limits)
    values["authorization"] = {
        "kind": "paid",
        "authorization_id": "proposal-only-not-a-dispatch-release",
        "approved_at": PRICING_REVERIFIED_AT,
        "approved_budget_usd": "999999999",
        "allow_paid_calls": True,
    }
    return values


def _audit_calibration_inventory(
        config: LiveConfig, plan: Any) -> dict[str, Any]:
    if (config.run_id != GATE1_RUN_ID
            or config.protocol_version != GATE1_PROTOCOL_VERSION
            or config.calibration_namespace != GATE1_CALIBRATION_NAMESPACE):
        raise Gate1CalibrationError("Gate 1 inventory requires the fixed v1.1 config")
    specs = list(plan.requests)
    if (len(specs) != 20 or plan.checkpoints
            or any(
                spec.stage != "calibration"
                or spec.phase != "calibration"
                or spec.action != "verdict"
                or spec.dependencies
                or spec.parent_checkpoint is not None
                for spec in specs
            )):
        raise Gate1CalibrationError(
            "Gate 1 plan must contain exactly 20 independent calibration verdicts"
        )
    factory = ScientificRequestFactory(config, plan)
    rows = []
    strict_cost = Decimal("0")
    nominal_cost = Decimal("0")
    most_expensive_input_rate = max(
        config.pricing.input_usd_per_million,
        config.pricing.cached_input_usd_per_million,
        config.pricing.cache_write_input_usd_per_million,
    )
    for spec in specs:
        request, rendered = factory.build(spec, ExecutionContext(None, 0, ()))
        wire = OpenAIResponsesProvider.build_payload(request)
        row = {
            "identity": spec.identity,
            "ordinal": spec.ordinal,
            "fixture_index": spec.metadata["fixture_index"],
            "fixture_namespace": spec.metadata["fixture_namespace"],
            "request_hash": digest(request.to_dict()),
            "prompt_hash": rendered.prompt_hash,
            "wire_payload_hash": digest(wire),
            "wire_payload_utf8_bytes": len(canonical(wire).encode("utf-8")),
            "conservative_input_token_bound": request.conservative_input_token_bound,
            "max_output_tokens": request.max_output_tokens,
        }
        rows.append(row)
        input_multiplier, output_multiplier = pricing_multipliers(
            config.pricing, request.conservative_input_token_bound
        )
        strict_cost += (
            Decimal(request.conservative_input_token_bound)
            * most_expensive_input_rate * input_multiplier
            + Decimal(request.max_output_tokens)
            * config.pricing.output_usd_per_million * output_multiplier
        ) / Decimal(1_000_000)
        nominal_cost += (
            Decimal(request.conservative_input_token_bound)
            * config.pricing.input_usd_per_million * input_multiplier
            + Decimal(request.max_output_tokens)
            * config.pricing.output_usd_per_million * output_multiplier
        ) / Decimal(1_000_000)
    defect_counts: dict[str, int] = {}
    for index in range(10, 20):
        defect = plan.fixture_inventory[f"calibration/{index}/peer"]["defect"]
        defect_counts[defect] = defect_counts.get(defect, 0) + 1
    input_sum = sum(row["conservative_input_token_bound"] for row in rows)
    output_sum = sum(row["max_output_tokens"] for row in rows)
    longest = max(rows, key=lambda row: row["conservative_input_token_bound"])
    transport_retry_attempts = (
        len(rows) * config.retry_policy.transport_retries
    )
    return {
        "inventory_kind": "v1.1_gate1_calibration_no_dispatch_projection_v1",
        "projection_only": True,
        "dispatch_performed": False,
        "protocol_version": plan.protocol_version,
        "protocol_hash": plan.protocol_hash,
        "implementation_hash": plan.implementation_hash,
        "plan_hash": plan.plan_hash,
        "fixture_inventory_hash": digest(dict(plan.fixture_inventory)),
        "logical_request_count": len(rows),
        "verdict_request_count": len(rows),
        "maximum_billable_attempt_shapes": len(rows),
        "maximum_known_not_completed_transport_retry_attempts": transport_retry_attempts,
        "maximum_dispatch_attempts": len(rows) + transport_retry_attempts,
        "logical_identity_hash": digest([row["identity"] for row in rows]),
        "inventory_hash": digest(rows),
        "calibration_namespace": config.calibration_namespace,
        "clean_fixture_count": 10,
        "faulty_fixture_count": 10,
        "faulty_defect_counts": dict(sorted(defect_counts.items())),
        "conservative_input_token_bound_sum": input_sum,
        "output_token_allowance_sum": output_sum,
        "long_context_projected_shape_count": sum(
            row["conservative_input_token_bound"]
            > LONG_CONTEXT_THRESHOLD_INPUT_TOKENS for row in rows
        ),
        "longest": longest,
        "within_model_context": (
            longest["conservative_input_token_bound"]
            + longest["max_output_tokens"] <= config.model_context_tokens
        ),
        "pricing_projection": {
            "strict_all_shapes_reservation_ceiling_usd": str(
                strict_cost.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)
            ),
            "nominal_uncached_all_shapes_ceiling_usd": str(
                nominal_cost.quantize(Decimal("0.000001"))
            ),
        },
        "requests": rows,
    }


def _caps_from_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "max_dispatch_attempts": inventory["maximum_dispatch_attempts"],
        "max_total_input_tokens": inventory[
            "conservative_input_token_bound_sum"
        ],
        "max_total_output_tokens": inventory["output_token_allowance_sum"],
        "max_cost_usd": inventory["pricing_projection"][
            "strict_all_shapes_reservation_ceiling_usd"
        ],
        "max_in_flight": 1,
        "max_input_tokens_per_request": inventory["longest"][
            "conservative_input_token_bound"
        ],
        "max_output_tokens_per_request": max(
            row["max_output_tokens"] for row in inventory["requests"]
        ),
    }


def _build_gate1_calibration_proposal_from_snapshot(
        snapshot: Mapping[str, Any], *,
        raw_stage2_ids: set[str] | None = None) -> dict[str, Any]:
    """Deterministically render the whole proposal from the pinned snapshot."""
    historical_config = LiveConfig.from_mapping(snapshot["historical_config"])
    lineage = {
        "config": historical_config,
        "run_id": snapshot["run_id"],
        "config_hash": snapshot["config_hash"],
        "plan_hash": snapshot["plan_hash"],
        "semantic_plan_hash": snapshot["semantic_plan_hash"],
        "implementation_hash": snapshot["implementation_hash"],
        "authorization_package_hash": snapshot["authorization_package_hash"],
        "report_hash": snapshot["report_hash"],
        "journal_tail": snapshot["journal_tail"],
        "journal_event_count": snapshot["journal_event_count"],
        "dispatch_authorization_hash": snapshot["dispatch_authorization_hash"],
        "transport_hash": snapshot["transport_hash"],
        "provider_identity": snapshot["provider_identity"],
        "plan": {"protocol_hash": snapshot["protocol_hash"]},
    }
    transport = _stage2_transport_metadata(sdk_version=STAGE2_SDK_VERSION)
    if digest(transport) != lineage["transport_hash"]:
        raise Gate1CalibrationError(
            "locked transport differs from completed Stage 2 capability evidence"
        )
    output_cap = lineage["config"].limits.max_output_tokens_per_request
    provisional_limits = {
        "max_requests": 1_000,
        "max_total_input_tokens": lineage["config"].model_context_tokens * 1_000,
        "max_total_output_tokens": output_cap * 1_000,
        "max_input_tokens_per_request": (
            lineage["config"].model_context_tokens - output_cap
        ),
        "max_output_tokens_per_request": output_cap,
        "max_in_flight": 1,
    }
    projection_config = LiveConfig.from_mapping(
        _projection_config_values(lineage, provisional_limits)
    )
    projection_plan = build_request_plan(projection_config)
    inventory = _audit_calibration_inventory(projection_config, projection_plan)
    if (inventory["logical_request_count"] != 20
            or inventory["verdict_request_count"] != 20
            or inventory["maximum_billable_attempt_shapes"] != 20
            or inventory["maximum_known_not_completed_transport_retry_attempts"] != 40
            or inventory["maximum_dispatch_attempts"] != 60
            or inventory["clean_fixture_count"] != 10
            or inventory["faulty_fixture_count"] != 10
            or inventory["faulty_defect_counts"]
            != {"add": 3, "omit": 4, "substitute": 3}
            or inventory["within_model_context"] is not True):
        raise Gate1CalibrationError(
            "v1.1 Gate 1 inventory differs from the reviewed 20-verdict scope"
        )
    caps = _caps_from_inventory(inventory)
    exact_limits = {
        "max_requests": caps["max_dispatch_attempts"],
        "max_total_input_tokens": caps["max_total_input_tokens"],
        "max_total_output_tokens": caps["max_total_output_tokens"],
        "max_input_tokens_per_request": caps["max_input_tokens_per_request"],
        "max_output_tokens_per_request": caps["max_output_tokens_per_request"],
        "max_in_flight": caps["max_in_flight"],
    }
    projection_config = LiveConfig.from_mapping(
        _projection_config_values(lineage, exact_limits)
    )
    projection_plan = build_request_plan(projection_config)
    inventory = _audit_calibration_inventory(projection_config, projection_plan)
    if _caps_from_inventory(inventory) != caps:
        raise Gate1CalibrationError("Gate 1 exact caps did not converge")

    template = projection_config.to_dict()
    template["authorization"] = {
        "kind": "paid",
        "authorization_id": "EXTERNAL_UUID_REQUIRED",
        "approved_at": "EXTERNAL_TIMESTAMP_REQUIRED",
        "approved_budget_usd": caps["max_cost_usd"],
        "allow_paid_calls": True,
    }
    pricing_contract = {
        "source": PRICING_SOURCE,
        "reverified_at": PRICING_REVERIFIED_AT,
        "standard_rates_usd_per_million": {
            "input": str(projection_config.pricing.input_usd_per_million),
            "cached_input": str(
                projection_config.pricing.cached_input_usd_per_million
            ),
            "cache_write_input": str(
                projection_config.pricing.cache_write_input_usd_per_million
            ),
            "output": str(projection_config.pricing.output_usd_per_million),
        },
        "long_context_threshold_input_tokens": LONG_CONTEXT_THRESHOLD_INPUT_TOKENS,
        "long_context_input_and_cache_multiplier": str(
            LONG_CONTEXT_INPUT_MULTIPLIER
        ),
        "long_context_output_multiplier": str(LONG_CONTEXT_OUTPUT_MULTIPLIER),
        "classification_basis": (
            "per-attempt conservative input bound for reservations; reported "
            "input usage for completed-attempt reconciliation"
        ),
    }
    gate1_ids = [spec.identity for spec in projection_plan.requests]
    if raw_stage2_ids is not None and set(gate1_ids) & raw_stage2_ids:
        raise Gate1CalibrationError(
            "fresh v1.1 Gate 1 identities overlap completed Stage 2"
        )
    bridge_body = {
        "artifact_type": "stage2_v1_0_to_gate1_v1_1_capability_bridge_v1",
        "predecessor_role": (
            "completed v1.0 transport/model capability evidence only; not "
            "calibration semantic identity or a v1.1 Gate 1 decision"
        ),
        "from": {
            "protocol_version": lineage["config"].protocol_version,
            "protocol_hash": lineage["plan"]["protocol_hash"],
            "run_id": lineage["run_id"],
            "config_hash": lineage["config_hash"],
            "plan_hash": lineage["plan_hash"],
            "implementation_hash": lineage["implementation_hash"],
            "split": lineage["config"].split,
            "seed_namespace": lineage["config"].seed_namespace,
            "calibration_namespace": lineage["config"].calibration_namespace,
            "calibration_identity_hash": snapshot[
                "calibration_identity_hash"
            ],
        },
        "to": {
            "protocol_version": projection_plan.protocol_version,
            "protocol_hash": projection_plan.protocol_hash,
            "run_id": projection_plan.run_id,
            "projection_config_hash": digest(projection_config.to_dict()),
            "projection_plan_hash": projection_plan.plan_hash,
            "implementation_hash": projection_plan.implementation_hash,
            "split": projection_config.split,
            "seed_namespace": projection_config.seed_namespace,
            "calibration_namespace": projection_config.calibration_namespace,
            "calibration_identity_hash": digest(gate1_ids),
            "calibration_inventory_hash": inventory["inventory_hash"],
        },
        "changed_dimensions": {
            "protocol_version": [
                lineage["config"].protocol_version,
                projection_plan.protocol_version,
            ],
            "protocol_hash": [
                lineage["plan"]["protocol_hash"], projection_plan.protocol_hash
            ],
            "run_id": [lineage["run_id"], projection_plan.run_id],
            "split": [lineage["config"].split, projection_config.split],
            "seed_namespace": [
                lineage["config"].seed_namespace,
                projection_config.seed_namespace,
            ],
            "calibration_namespace": [
                lineage["config"].calibration_namespace,
                projection_config.calibration_namespace,
            ],
            "calibration_identity_hash": [
                snapshot["calibration_identity_hash"],
                digest(gate1_ids),
            ],
        },
        "preserved_capability_dimensions": {
            "source": projection_config.source,
            "provider": projection_config.provider,
            "model": projection_config.model,
            "model_settings": dict(projection_config.model_settings),
            "model_context_tokens": projection_config.model_context_tokens,
            "transport_hash": lineage["transport_hash"],
            "required_provider_identity": lineage["provider_identity"],
        },
    }
    bridge = {**bridge_body, "bridge_hash": digest(bridge_body)}
    scope = {
        "stage": "calibration",
        "stop_before_stage": "smoke",
        "logical_request_count": 20,
        "maximum_dispatch_attempts": 60,
        "logical_identity_hash": inventory["logical_identity_hash"],
        "permitted_projected_shape_inventory_hash": inventory["inventory_hash"],
        "calibration_namespace": GATE1_CALIBRATION_NAMESPACE,
        "later_stages_authorized": [],
        "preflight_authorized": False,
        "smoke_authorized": False,
    }
    body = {
        "artifact_type": "gate_1_v1_1_calibration_proposal_v1",
        "controller": CONTROLLER_ID,
        "prepared_at": PRICING_REVERIFIED_AT,
        "status": "awaiting_external_human_authorization",
        "dispatch_enabled": False,
        "credentials_required_for_inspection": False,
        "experiment_started": False,
        "stage2_capability_lineage": {
            "run_id": lineage["run_id"],
            "config_hash": lineage["config_hash"],
            "plan_hash": lineage["plan_hash"],
            "semantic_plan_hash": lineage["semantic_plan_hash"],
            "implementation_hash_at_stage2": lineage["implementation_hash"],
            "authorization_package_hash": lineage["authorization_package_hash"],
            "report_hash": lineage["report_hash"],
            "journal_tail": lineage["journal_tail"],
            "journal_event_count": lineage["journal_event_count"],
            "dispatch_authorization_hash": lineage[
                "dispatch_authorization_hash"
            ],
            "transport_hash": lineage["transport_hash"],
            "provider_identity": lineage["provider_identity"],
            "protocol_version": lineage["config"].protocol_version,
            "protocol_hash": lineage["plan"]["protocol_hash"],
            "preflight_inventory_hash": STAGE2_PREFLIGHT_INVENTORY_HASH,
            "verified_gates_hash": snapshot["verified_gates_hash"],
            "portability_snapshot_hash": snapshot["snapshot_hash"],
        },
        "protocol_bridge": bridge,
        "current_implementation_hash": projection_plan.implementation_hash,
        "gate1_protocol": {
            "protocol_version": projection_plan.protocol_version,
            "protocol_hash": projection_plan.protocol_hash,
            "run_id": projection_plan.run_id,
            "run_directory": GATE1_RUN_DIRECTORY,
            "split": projection_config.split,
            "seed_namespace": projection_config.seed_namespace,
            "calibration_namespace": projection_config.calibration_namespace,
            "config_template_hash": digest(template),
            "projection_config_hash": digest(projection_config.to_dict()),
            "projection_plan_hash": projection_plan.plan_hash,
            "fixture_inventory_hash": digest(
                dict(projection_plan.fixture_inventory)
            ),
        },
        "calibration_scope": scope,
        "calibration_projection": {
            key: inventory[key] for key in (
                "inventory_kind",
                "inventory_hash",
                "logical_identity_hash",
                "logical_request_count",
                "verdict_request_count",
                "maximum_billable_attempt_shapes",
                "maximum_known_not_completed_transport_retry_attempts",
                "maximum_dispatch_attempts",
                "clean_fixture_count",
                "faulty_fixture_count",
                "faulty_defect_counts",
                "calibration_namespace",
                "conservative_input_token_bound_sum",
                "output_token_allowance_sum",
                "long_context_projected_shape_count",
                "longest",
                "within_model_context",
                "pricing_projection",
                "protocol_version",
                "protocol_hash",
                "fixture_inventory_hash",
            )
        },
        "assessment_contract": {
            "assessment_type": "capability_calibration_v1",
            "scheduled": 20,
            "schema_valid_verdicts": "20_of_20",
            "request_payloads_verified": "20_of_20",
            "wire_payloads_verified": "20_of_20",
            "provider_audits_complete": "20_of_20",
            "correct_packet_approvals": "at_least_9_of_10",
            "faulty_packet_rejections": "at_least_9_of_10",
            "decision_values": ["passed", "failed"],
            "decision_immutable": True,
        },
        "attempt_accounting": {
            "logical_identity_count": 20,
            "billable_content_shape_count": 20,
            "transport_retries_per_logical_identity": (
                projection_config.retry_policy.transport_retries
            ),
            "known_not_completed_retry_slots": 40,
            "maximum_dispatch_attempts": 60,
            "known_not_completed_transport_retries_reconcile_to_zero": True,
            "ambiguous_or_usage_bearing_transport_failure_is_retried": False,
        },
        "transport": transport,
        "transport_hash": digest(transport),
        "required_provider_identity": lineage["provider_identity"],
        "pricing_contract": pricing_contract,
        "pricing_contract_hash": digest(pricing_contract),
        "exact_caps": caps,
        "gate1_config_template": template,
        "external_authorization": {
            "required": True,
            "must_be_outside_repository": True,
            "owner_only_file_required": True,
            "fresh_authorization_uuid_v4_required": True,
            "fresh_64_hex_nonce_required": True,
            "single_use_receipt_required": True,
            "receipt_registry_scope": "stable_per_user_controller_and_run_id",
            "receipt_registry_key_hash": digest({
                "controller": CONTROLLER_ID,
                "run_id": GATE1_RUN_ID,
            }),
            "replacement_authorization_cannot_reuse_fixed_release": True,
            "expired_unclaimed_prepared_run_requires_new_versioned_release": True,
            "maximum_unclaimed_age_seconds": (
                AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS
            ),
            "maximum_future_skew_seconds": (
                AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS
            ),
            "maximum_pricing_age_seconds": PRICING_MAX_AGE_SECONDS,
            "validity_enforced_at": "first atomic authorization claim",
            "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
            "schema_artifact": str(AUTHORIZATION_SCHEMA_PATH.relative_to(ROOT)),
        },
        "hard_stop": {
            "after_stage": "calibration",
            "before_stage": "smoke",
            "preflight_dispatch_possible": False,
            "smoke_dispatch_possible": False,
            "later_stage_dispatch_possible": False,
            "generic_staged_executor_allowed": False,
        },
    }
    return {**body, "proposal_hash": digest(body)}


def build_gate1_calibration_proposal(
        stage2_run_dir: Path | None = None) -> dict[str, Any]:
    """Build only after the pinned snapshot exactly cross-checks raw Stage 2."""
    stage2_path = STAGE2_RUN_DIR if stage2_run_dir is None else Path(stage2_run_dir)
    try:
        raw = verify_stage2_lineage(stage2_path)
        snapshot = verify_stage2_lineage_snapshot_against_raw(
            load_stage2_lineage_snapshot(), stage2_path
        )
    except (Gate2ReadinessError, Stage2LineageSnapshotError) as exc:
        raise Gate1CalibrationError(
            "complete raw Stage 2 lineage is required and must exactly match "
            "the pinned portability snapshot"
        ) from exc
    raw_ids = {row["identity"] for row in raw["plan"]["requests"]}
    return _build_gate1_calibration_proposal_from_snapshot(
        snapshot, raw_stage2_ids=raw_ids
    )


def validate_gate1_calibration_proposal(
        value: Mapping[str, Any], *,
        stage2_run_dir: Path | None = None,
        require_raw_lineage: bool = True) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Gate1CalibrationError("Gate 1 proposal must be an object")
    proposal = json.loads(canonical(dict(value)))
    snapshot = load_stage2_lineage_snapshot()
    expected = (
        build_gate1_calibration_proposal(stage2_run_dir)
        if require_raw_lineage else
        _build_gate1_calibration_proposal_from_snapshot(snapshot)
    )
    if canonical(proposal) != canonical(expected):
        raise Gate1CalibrationError(
            "Gate 1 proposal differs from the complete deterministic current contract"
        )
    return expected


def load_gate1_calibration_proposal(
        path: Path = PROPOSAL_PATH, *,
        require_raw_lineage: bool = True) -> dict[str, Any]:
    return validate_gate1_calibration_proposal(
        _read_json(Path(path), "Gate 1 calibration proposal"),
        require_raw_lineage=require_raw_lineage,
    )


def gate1_authorization_schema(proposal: Mapping[str, Any]) -> dict[str, Any]:
    proposal = validate_gate1_calibration_proposal(proposal)
    uuid_pattern = (
        r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:collusion-recovery:gate1-calibration-authorization:v1.1",
        "title": "Gate 1 v1.1 calibration human authorization",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_AUTH_FIELDS),
        "properties": {
            "artifact_type": {
                "const": "gate_1_calibration_human_authorization_v1_1"
            },
            "authorization_uuid": {"type": "string", "pattern": uuid_pattern},
            "authorization_id": {
                "type": "string",
                "pattern": r"^gate1-human-[0-9a-f-]{36}$",
            },
            "authorization_nonce": {
                "type": "string",
                "pattern": r"^[0-9a-f]{64}$",
            },
            "gate1_run_id": {"const": proposal["gate1_protocol"]["run_id"]},
            "gate1_run_directory": {
                "const": proposal["gate1_protocol"]["run_directory"]
            },
            "confirmed_by": {"type": "string", "minLength": 1, "maxLength": 200},
            "confirmed_at": {"type": "string", "format": "date-time"},
            "proposal_hash": {"const": proposal["proposal_hash"]},
            "approved_scope": {"const": proposal["calibration_scope"]},
            "approved_caps": {"const": proposal["exact_caps"]},
            "approved_pricing_contract_hash": {
                "const": proposal["pricing_contract_hash"]
            },
            "approved_transport_hash": {"const": proposal["transport_hash"]},
            "approved_provider_identity": {
                "const": proposal["required_provider_identity"]
            },
            "approved_protocol_bridge_hash": {
                "const": proposal["protocol_bridge"]["bridge_hash"]
            },
            "approved_calibration_inventory_hash": {
                "const": proposal["calibration_projection"]["inventory_hash"]
            },
            "approved_gate1_config_hash": {
                "type": "string", "pattern": r"^[a-f0-9]{64}$"
            },
            "approved_gate1_plan_hash": {
                "type": "string", "pattern": r"^[a-f0-9]{64}$"
            },
            "approved_dispatch_authorization_hash": {
                "type": "string", "pattern": r"^[a-f0-9]{64}$"
            },
            "allow_paid_calls": {"const": True},
            "human_acknowledgement": {"const": HUMAN_ACKNOWLEDGEMENT},
            "authorization_hash": {
                "type": "string", "pattern": r"^[a-f0-9]{64}$"
            },
        },
        "$comment": (
            "authorization_hash is sha256 of canonical JSON after removing "
            "authorization_hash. Human-supplied UUID, nonce, identity, time, "
            "scope, caps, lineage, pricing, transport, provider identity, and "
            "acknowledgement are mandatory. Unclaimed authorization expires "
            "after 15 minutes and permits at most 60 seconds future clock skew."
        ),
    }


def _validate_external_file(path: Path) -> None:
    try:
        resolved = Path(path).resolve(strict=True)
        details = Path(path).lstat()
    except OSError as exc:
        raise Gate1CalibrationError(
            "human authorization is unavailable or invalid"
        ) from exc
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise Gate1CalibrationError(
            "human authorization must be outside the repository"
        )
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise Gate1CalibrationError(
            "human authorization must be a regular non-symlink file"
        )
    if details.st_uid != os.getuid() or details.st_mode & 0o077:
        raise Gate1CalibrationError(
            "human authorization must be owned by the current user and owner-only"
        )


def _authorization_receipt_directory(
        _authorization_path: Path | None = None) -> Path:
    """Return the stable per-user registry, independent of auth-file copies."""
    return AUTHORIZATION_RECEIPT_DIRECTORY


def _authorization_receipt_path(receipt_dir: Path) -> Path:
    """Key consumption by the fixed release, not a replaceable auth UUID."""
    return Path(receipt_dir) / f"{CONTROLLER_ID}--{GATE1_RUN_ID}.json"


def _validate_receipt_registry_path(receipt_dir: Path) -> Path:
    """Require the fixed registry and reject redirects below its home anchor."""
    value = Path(os.path.abspath(receipt_dir))
    expected = Path(os.path.abspath(AUTHORIZATION_RECEIPT_DIRECTORY))
    if value != expected:
        raise Gate1CalibrationError(
            "authorization receipt registry is not the fixed per-user release registry"
        )
    # The fixed live layout is HOME/.collusion-recovery.../gate1-v1.1.
    # Treat HOME as the trusted anchor and inspect every existing descendant
    # lexically; starting at filesystem root would incorrectly reject normal
    # platform aliases such as macOS /var -> /private/var in isolated tests.
    trusted_home = expected.parents[1]
    current = trusted_home
    for part in (None, *value.relative_to(trusted_home).parts):
        if part is not None:
            current = current / part
        if current.is_symlink():
            raise Gate1CalibrationError(
                "authorization receipt registry cannot contain symlink components"
            )
        if current.exists():
            details = current.lstat()
            if (not stat.S_ISDIR(details.st_mode)
                    or details.st_uid != os.getuid()
                    or details.st_mode & 0o022):
                raise Gate1CalibrationError(
                    "authorization receipt registry components must be "
                    "current-user-owned non-writable directories"
                )
    return value


def validate_gate1_human_authorization(
        value: Mapping[str, Any], proposal: Mapping[str, Any], *,
        _proposal_verified: bool = False,
        _verify_derived_contract: bool = True) -> dict[str, Any]:
    if not _proposal_verified:
        proposal = validate_gate1_calibration_proposal(proposal)
    if not isinstance(value, Mapping) or set(value) != _AUTH_FIELDS:
        raise Gate1CalibrationError(
            f"human authorization must contain exactly {sorted(_AUTH_FIELDS)}"
        )
    auth = json.loads(canonical(dict(value)))
    body = dict(auth)
    authorization_hash = body.pop("authorization_hash", None)
    if (not isinstance(authorization_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", authorization_hash) is None
            or authorization_hash != digest(body)):
        raise Gate1CalibrationError("human authorization self-hash is invalid")
    try:
        authorization_uuid = uuid.UUID(auth["authorization_uuid"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise Gate1CalibrationError(
            "authorization_uuid must be a lowercase UUIDv4"
        ) from exc
    uuid_text = str(authorization_uuid)
    if authorization_uuid.version != 4 or uuid_text != auth["authorization_uuid"]:
        raise Gate1CalibrationError(
            "authorization_uuid must be a lowercase UUIDv4"
        )
    if auth["authorization_id"] != f"gate1-human-{uuid_text}":
        raise Gate1CalibrationError("authorization_id must bind the fresh UUID")
    nonce = auth["authorization_nonce"]
    if (not isinstance(nonce, str)
            or re.fullmatch(r"[a-f0-9]{64}", nonce) is None
            or len(set(nonce)) < 8):
        raise Gate1CalibrationError(
            "authorization nonce must be a fresh 64-hex value"
        )
    confirmer = auth["confirmed_by"]
    if (not isinstance(confirmer, str) or not confirmer.strip()
            or len(confirmer) > 200):
        raise Gate1CalibrationError("confirmed_by must name the human authorizer")
    _parse_timestamp(auth["confirmed_at"], "confirmed_at")
    exact = {
        "artifact_type": "gate_1_calibration_human_authorization_v1_1",
        "gate1_run_id": proposal["gate1_protocol"]["run_id"],
        "gate1_run_directory": proposal["gate1_protocol"]["run_directory"],
        "proposal_hash": proposal["proposal_hash"],
        "approved_scope": proposal["calibration_scope"],
        "approved_caps": proposal["exact_caps"],
        "approved_pricing_contract_hash": proposal["pricing_contract_hash"],
        "approved_transport_hash": proposal["transport_hash"],
        "approved_provider_identity": proposal["required_provider_identity"],
        "approved_protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "approved_calibration_inventory_hash": proposal[
            "calibration_projection"
        ]["inventory_hash"],
        "allow_paid_calls": True,
        "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
    }
    if any(auth.get(name) != expected for name, expected in exact.items()):
        raise Gate1CalibrationError(
            "human authorization does not bind the exact Gate 1 scope and lineage"
        )
    for name in (
            "approved_gate1_config_hash",
            "approved_gate1_plan_hash",
            "approved_dispatch_authorization_hash"):
        if (not isinstance(auth.get(name), str)
                or re.fullmatch(r"[a-f0-9]{64}", auth[name]) is None):
            raise Gate1CalibrationError(
                "human authorization contains a malformed derived contract hash"
            )
    if _verify_derived_contract:
        config, plan, dispatch, inventory = _gate1_config_and_plan(
            proposal,
            auth,
            _proposal_verified=True,
            _authorization_verified=True,
        )
        derived = {
            "approved_gate1_config_hash": digest(config.to_dict()),
            "approved_gate1_plan_hash": plan.plan_hash,
            "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
            "approved_calibration_inventory_hash": inventory["inventory_hash"],
        }
        if any(auth.get(name) != expected for name, expected in derived.items()):
            raise Gate1CalibrationError(
                "human authorization differs from the exact derived Gate 1 contract"
            )
    return auth


def complete_gate1_human_authorization(
        externally_supplied_fields: Mapping[str, Any],
        proposal: Mapping[str, Any], *,
        _proposal_verified: bool = False) -> dict[str, Any]:
    """Deterministically package human-supplied approval; never create approval."""
    proposal = (
        dict(proposal) if _proposal_verified
        else validate_gate1_calibration_proposal(proposal)
    )
    derived_names = {
        "approved_gate1_config_hash",
        "approved_gate1_plan_hash",
        "approved_dispatch_authorization_hash",
        "authorization_hash",
    }
    required = _AUTH_FIELDS - derived_names
    if (not isinstance(externally_supplied_fields, Mapping)
            or set(externally_supplied_fields) != required):
        raise Gate1CalibrationError(
            "authorization packaging requires exactly the external human fields"
        )
    draft = json.loads(canonical(dict(externally_supplied_fields)))
    draft.update({
        name: "0" * 64
        for name in derived_names - {"authorization_hash"}
    })
    draft["authorization_hash"] = digest({
        key: value for key, value in draft.items()
        if key != "authorization_hash"
    })
    envelope = validate_gate1_human_authorization(
        draft,
        proposal,
        _proposal_verified=True,
        _verify_derived_contract=False,
    )
    config, plan, dispatch, _inventory = _gate1_config_and_plan(
        proposal,
        envelope,
        _proposal_verified=True,
        _authorization_verified=True,
    )
    completed = dict(envelope)
    completed.update({
        "approved_gate1_config_hash": digest(config.to_dict()),
        "approved_gate1_plan_hash": plan.plan_hash,
        "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
    })
    body = dict(completed)
    body.pop("authorization_hash", None)
    completed["authorization_hash"] = digest(body)
    return validate_gate1_human_authorization(
        completed,
        proposal,
        _proposal_verified=True,
        _verify_derived_contract=False,
    )


def load_gate1_human_authorization(
        path: Path, proposal: Mapping[str, Any], *,
        require_external: bool = True,
        _proposal_verified: bool = False) -> dict[str, Any]:
    path = Path(path)
    if require_external:
        _validate_external_file(path)
    return validate_gate1_human_authorization(
        _read_json(path, "Gate 1 human authorization"),
        proposal,
        _proposal_verified=_proposal_verified,
    )


def _gate1_config_and_plan(
        proposal: Mapping[str, Any], authorization: Mapping[str, Any], *,
        _proposal_verified: bool = False,
        _authorization_verified: bool = False,
        ) -> tuple[LiveConfig, Any, DispatchAuthorizationArtifact, dict[str, Any]]:
    if not _proposal_verified:
        proposal = validate_gate1_calibration_proposal(proposal)
    auth = (
        json.loads(canonical(dict(authorization)))
        if _authorization_verified else
        validate_gate1_human_authorization(
            authorization,
            proposal,
            _proposal_verified=True,
            _verify_derived_contract=False,
        )
    )
    values = json.loads(canonical(proposal["gate1_config_template"]))
    if digest(values) != proposal["gate1_protocol"]["config_template_hash"]:
        raise Gate1CalibrationError("Gate 1 configuration template hash differs")
    values["authorization"] = {
        "kind": "paid",
        "authorization_id": auth["authorization_id"],
        "approved_at": auth["confirmed_at"],
        "approved_budget_usd": proposal["exact_caps"]["max_cost_usd"],
        "allow_paid_calls": True,
    }
    config = LiveConfig.from_mapping(values)
    plan = build_request_plan(config)
    protocol = proposal["gate1_protocol"]
    if (config.run_id != GATE1_RUN_ID
            or config.protocol_version != GATE1_PROTOCOL_VERSION
            or config.stage != "calibration"
            or config.split != protocol["split"]
            or config.seed_namespace != protocol["seed_namespace"]
            or config.calibration_namespace != GATE1_CALIBRATION_NAMESPACE
            or plan.run_id != protocol["run_id"]
            or plan.protocol_version != protocol["protocol_version"]
            or plan.protocol_hash != protocol["protocol_hash"]
            or plan.implementation_hash != proposal["current_implementation_hash"]
            or digest(dict(plan.fixture_inventory))
            != protocol["fixture_inventory_hash"]):
        raise Gate1CalibrationError(
            "current implementation differs from the authorized Gate 1 proposal"
        )
    inventory = _audit_calibration_inventory(config, plan)
    expected_projection = proposal["calibration_projection"]
    if (inventory["inventory_hash"] != expected_projection["inventory_hash"]
            or inventory["logical_request_count"] != 20
            or inventory["maximum_dispatch_attempts"] != 60
            or inventory["within_model_context"] is not True
            or digest([spec.identity for spec in plan.requests])
            != proposal["calibration_scope"]["logical_identity_hash"]):
        raise Gate1CalibrationError(
            "runtime Gate 1 inventory differs from the human-reviewed scope"
        )
    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    binding = authorization_binding(
        config,
        plan,
        confirmed_at=auth["confirmed_at"],
        transport_hash=proposal["transport_hash"],
        approved_budget_usd=proposal["exact_caps"]["max_cost_usd"],
        max_stage="calibration",
        max_block=0,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
    )
    dispatch = DispatchAuthorizationArtifact(
        authorization_id=auth["authorization_id"],
        confirmed_at=auth["confirmed_at"],
        binding_hash=binding,
        approved_budget_usd=proposal["exact_caps"]["max_cost_usd"],
        transport_hash=proposal["transport_hash"],
        max_stage="calibration",
        max_block=0,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
    )
    validate_dispatch_authorization(config, plan, dispatch, require_initial=True)
    if not _authorization_verified:
        derived = {
            "approved_gate1_config_hash": digest(config.to_dict()),
            "approved_gate1_plan_hash": plan.plan_hash,
            "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
            "approved_calibration_inventory_hash": inventory["inventory_hash"],
        }
        if any(auth.get(name) != expected for name, expected in derived.items()):
            raise Gate1CalibrationError(
                "human authorization differs from the exact Gate 1 contract"
            )
    return config, plan, dispatch, inventory


def _binding_event_value(
        proposal: Mapping[str, Any], authorization: Mapping[str, Any],
        config: LiveConfig, plan: Any,
        dispatch: DispatchAuthorizationArtifact) -> dict[str, Any]:
    return {
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": authorization["authorization_hash"],
        "gate1_run_id": authorization["gate1_run_id"],
        "stage2_report_hash": proposal["stage2_capability_lineage"]["report_hash"],
        "stage2_authorization_package_hash": proposal[
            "stage2_capability_lineage"
        ]["authorization_package_hash"],
        "stage2_journal_tail": proposal["stage2_capability_lineage"]["journal_tail"],
        "stage2_event_count": proposal["stage2_capability_lineage"][
            "journal_event_count"
        ],
        "protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "gate1_protocol_version": proposal["gate1_protocol"]["protocol_version"],
        "gate1_protocol_hash": proposal["gate1_protocol"]["protocol_hash"],
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "implementation_hash": proposal["current_implementation_hash"],
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "transport_hash": proposal["transport_hash"],
        "required_provider_identity": proposal["required_provider_identity"],
        "pricing_contract_hash": proposal["pricing_contract_hash"],
        "exact_caps": proposal["exact_caps"],
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "scope": proposal["calibration_scope"],
    }


def _validated_gate1_run_path(
        run_dir: Path, authorization: Mapping[str, Any], *,
        workspace_root: Path | None = None) -> tuple[Path, bool]:
    """Return the exact non-symlinked authorized path and existence flag."""
    run_path = Path(run_dir)
    root = ROOT if workspace_root is None else Path(workspace_root)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise Gate1CalibrationError(
            "Gate 1 workspace root must already exist"
        ) from exc
    if not root_resolved.is_dir():
        raise Gate1CalibrationError("Gate 1 workspace root is not a directory")
    relative = Path(authorization["gate1_run_directory"])
    if (relative.is_absolute() or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise Gate1CalibrationError(
            "Gate 1 run directory must be a safe workspace-relative path"
        )
    root_lexical = Path(os.path.abspath(root))
    expected_lexical = root_lexical.joinpath(*relative.parts)
    supplied_lexical = Path(os.path.abspath(run_path))
    if supplied_lexical != expected_lexical:
        raise Gate1CalibrationError(
            "Gate 1 run path differs from the human authorization"
        )
    try:
        expected_lexical.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise Gate1CalibrationError(
            "Gate 1 run path resolves outside the authorized workspace"
        ) from exc
    current = root_lexical
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise Gate1CalibrationError(
                "Gate 1 run path cannot contain symlink components"
            )
    exists = run_path.exists()
    if exists and not run_path.is_dir():
        raise Gate1CalibrationError("Gate 1 run path is not a directory")
    return run_path, exists


def prepare_gate1_calibration_run(
        run_dir: Path, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], *,
        workspace_root: Path | None = None,
        _proposal_verified: bool = False) -> tuple[
            DurableRun, LiveConfig, Any, DispatchAuthorizationArtifact,
            dict[str, Any]
        ]:
    """Create or exactly resume Gate 1 without provider or credential I/O."""
    config, plan, dispatch, inventory = _gate1_config_and_plan(
        proposal, authorization, _proposal_verified=_proposal_verified
    )
    run_path, exists = _validated_gate1_run_path(
        run_dir, authorization, workspace_root=workspace_root
    )
    run = DurableRun.open(
        run_path,
        config,
        plan,
        resume=exists,
        dispatch_authorization=dispatch,
        execution_controller=CONTROLLER_ID,
    )
    binding = _binding_event_value(proposal, authorization, config, plan, dispatch)
    with run.execution_lock():
        existing = [
            event for event in run.events
            if event.get("kind") == GATE1_CALIBRATION_BINDING_EVENT
        ]
        if len(existing) > 1:
            raise ResumeError("Gate 1 binding was recorded more than once")
        created_binding = not existing
        if existing:
            durable = {key: existing[0].get(key) for key in binding}
            if canonical(durable) != canonical(binding):
                raise ResumeError("durable Gate 1 binding differs")
            nonce = existing[0].get("run_instance_nonce")
            if (not isinstance(nonce, str)
                    or re.fullmatch(r"[a-f0-9]{64}", nonce) is None):
                raise ResumeError("durable Gate 1 run-instance nonce is invalid")
        else:
            nonce = secrets.token_hex(32)
            run.append(
                GATE1_CALIBRATION_BINDING_EVENT,
                **binding,
                run_instance_nonce=nonce,
            )
        prepared_fields = {
            "execution_controller": CONTROLLER_ID,
            "gate1_proposal_hash": proposal["proposal_hash"],
            "gate1_human_authorization_hash": authorization[
                "authorization_hash"
            ],
            "gate1_run_id": authorization["gate1_run_id"],
            "gate1_run_instance_hash": digest(nonce),
            "required_provider_identity": proposal["required_provider_identity"],
            "preflight_dispatch_forbidden": True,
            "smoke_dispatch_forbidden": True,
            "later_stage_dispatch_forbidden": True,
        }
        missing = {name for name in prepared_fields if name not in run.manifest}
        mismatched = {
            name for name, expected in prepared_fields.items()
            if name in run.manifest and run.manifest[name] != expected
        }
        if mismatched:
            raise ResumeError("durable Gate 1 manifest binding differs")
        if created_binding or missing:
            if not created_binding and run.manifest.get("status") != "created":
                raise ResumeError("durable Gate 1 manifest binding is incomplete")
            run.set_status(
                "prepared_gate1_calibration",
                **prepared_fields,
                budget=BudgetLedger.replay(
                    config.limits,
                    config.pricing,
                    Decimal(dispatch.approved_budget_usd),
                    run.budget_records(),
                    dispatch.release_limits,
                ).snapshot(),
            )
    return run, config, plan, dispatch, inventory


def _gate1_claim_event_fields(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any],
        receipt: Mapping[str, Any]) -> dict[str, Any]:
    bindings = [
        event for event in run.events
        if event.get("kind") == GATE1_CALIBRATION_BINDING_EVENT
    ]
    if len(bindings) != 1:
        raise Gate1CalibrationError(
            "durable Gate 1 run-instance binding is absent"
        )
    receipt_body = dict(receipt)
    receipt_hash = receipt_body.pop("receipt_hash", None)
    if (not isinstance(receipt_hash, str)
            or receipt_hash != digest(receipt_body)):
        raise Gate1CalibrationError(
            "authorization receipt self-hash is invalid"
        )
    return {
        "controller": CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "receipt_hash": receipt_hash,
        "receipt_claimed_at": receipt_body.get("claimed_at"),
        "gate1_run_id": authorization["gate1_run_id"],
        "run_directory": str(run.path.resolve()),
        "run_instance_event_hash": bindings[0]["event_hash"],
        "run_instance_hash": digest(bindings[0]["run_instance_nonce"]),
    }


def _require_gate1_claim_event(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any],
        receipt: Mapping[str, Any]) -> dict[str, Any]:
    expected = _gate1_claim_event_fields(
        run, proposal, authorization, receipt
    )
    claims = [
        event for event in run.events
        if event.get("kind") == AUTHORIZATION_CLAIM_EVENT
    ]
    if (len(claims) != 1
            or any(claims[0].get(key) != value
                   for key, value in expected.items())
            or any(
                event.get("kind") in {
                    EXECUTION_MODE_BINDING_EVENT, "reservation", "result"
                }
                and event.get("sequence", -1) < claims[0].get("sequence", -1)
                for event in run.events
            )):
        raise Gate1CalibrationError(
            "a singular journal-bound Gate 1 authorization claim is required"
        )
    _parse_timestamp(
        claims[0].get("receipt_claimed_at"),
        "authorization claim receipt_claimed_at",
    )
    return claims[0]


def claim_gate1_authorization_once(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], *,
        receipt_dir: Path,
        now: datetime | None = None) -> dict[str, Any]:
    """Claim the external authorization once, allowing only exact-run resume."""
    if run.manifest.get("execution_controller") != CONTROLLER_ID:
        raise Gate1CalibrationError("Gate 1 controller marker is absent")
    if (run.manifest.get("gate1_proposal_hash") != proposal["proposal_hash"]
            or run.manifest.get("gate1_human_authorization_hash")
            != authorization["authorization_hash"]
            or run.manifest.get("gate1_run_id")
            != authorization["gate1_run_id"]):
        raise Gate1CalibrationError(
            "durable Gate 1 run differs from the authorization being claimed"
        )
    receipt_dir = _validate_receipt_registry_path(Path(receipt_dir))
    try:
        receipt_dir.resolve(strict=False).relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise Gate1CalibrationError(
            "authorization receipt registry must remain outside the repository"
        )
    if receipt_dir.exists():
        details = receipt_dir.lstat()
        if (not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode)
                or details.st_uid != os.getuid() or details.st_mode & 0o077):
            raise Gate1CalibrationError(
                "authorization receipt registry must be owner-only"
            )
    else:
        receipt_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        _validate_receipt_registry_path(receipt_dir)
        if receipt_dir.lstat().st_mode & 0o077:
            raise Gate1CalibrationError(
                "authorization receipt registry was not created owner-only"
            )
    bindings = [
        event for event in run.events
        if event.get("kind") == GATE1_CALIBRATION_BINDING_EVENT
    ]
    if len(bindings) != 1:
        raise Gate1CalibrationError("durable Gate 1 run-instance binding is absent")
    expected = {
        "artifact_type": "gate_1_authorization_consumption_receipt_v1_1",
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "proposal_hash": proposal["proposal_hash"],
        "protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "gate1_run_id": authorization["gate1_run_id"],
        "run_directory": str(run.path.resolve()),
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "dispatch_authorization_hash": run.manifest[
            "dispatch_authorization_hash"
        ],
        "run_instance_event_hash": bindings[0]["event_hash"],
        "run_instance_hash": digest(bindings[0]["run_instance_nonce"]),
    }
    receipt_path = _authorization_receipt_path(receipt_dir)
    with run.execution_lock():
        if receipt_path.exists():
            details = receipt_path.lstat()
            if (not stat.S_ISREG(details.st_mode)
                    or stat.S_ISLNK(details.st_mode)
                    or details.st_uid != os.getuid()
                    or details.st_mode & 0o077):
                raise Gate1CalibrationError(
                    "authorization receipt must be an owner-only regular file"
                )
            receipt = _read_json(
                receipt_path, "Gate 1 authorization receipt"
            )
            body = dict(receipt)
            receipt_hash = body.pop("receipt_hash", None)
            if (set(body) != set(expected) | {"claimed_at"}
                    or receipt_hash != digest(body)
                    or any(body.get(key) != value
                           for key, value in expected.items())):
                raise Gate1CalibrationError(
                    "authorization was already claimed by a different or invalid run"
                )
            _parse_timestamp(
                body.get("claimed_at"), "authorization receipt claimed_at"
            )
        else:
            _validate_first_claim_window(authorization, proposal, now=now)
            body = {**expected, "claimed_at": utc_now()}
            receipt = {**body, "receipt_hash": digest(body)}
            try:
                _write_exclusive_json(receipt_path, receipt)
            except FileExistsError:
                receipt = _read_json(
                    receipt_path, "Gate 1 authorization receipt"
                )
                body = dict(receipt)
                receipt_hash = body.pop("receipt_hash", None)
                if (set(body) != set(expected) | {"claimed_at"}
                        or receipt_hash != digest(body)
                        or any(body.get(key) != value
                               for key, value in expected.items())):
                    raise Gate1CalibrationError(
                        "authorization was already claimed by a different or invalid run"
                    )
                _parse_timestamp(
                    body.get("claimed_at"),
                    "authorization receipt claimed_at",
                )
        claim_fields = _gate1_claim_event_fields(
            run, proposal, authorization, receipt
        )
        claims = [
            event for event in run.events
            if event.get("kind") == AUTHORIZATION_CLAIM_EVENT
        ]
        if len(claims) > 1:
            raise Gate1CalibrationError(
                "Gate 1 authorization claim is not singular"
            )
        if claims:
            if any(
                    claims[0].get(key) != value
                    for key, value in claim_fields.items()):
                raise Gate1CalibrationError(
                    "durable Gate 1 authorization claim differs from receipt"
                )
        else:
            if any(
                    event.get("kind") in {
                        EXECUTION_MODE_BINDING_EVENT, "reservation", "result"
                    }
                    for event in run.events):
                raise Gate1CalibrationError(
                    "Gate 1 dispatch exists before authorization was claimed"
                )
            run.append(AUTHORIZATION_CLAIM_EVENT, **claim_fields)
        claim = _require_gate1_claim_event(
            run, proposal, authorization, receipt
        )
        if (run.manifest.get("gate1_authorization_consumed") is not True
                or run.manifest.get("gate1_authorization_receipt_hash")
                != receipt["receipt_hash"]
                or run.manifest.get("gate1_authorization_claim_event_hash")
                != claim["event_hash"]):
            run.set_status(
                run.manifest["status"],
                gate1_authorization_consumed=True,
                gate1_authorization_receipt_hash=receipt["receipt_hash"],
                gate1_authorization_claim_event_hash=claim["event_hash"],
            )
        return receipt


def _gate1_completion_report_value(
        run: DurableRun, proposal: Mapping[str, Any],
        budget_snapshot: Mapping[str, Any], assessment: Mapping[str, Any],
        decision: Mapping[str, Any], mode_binding: Mapping[str, Any]
        ) -> dict[str, Any]:
    """Derive the immutable Gate 1 report from exact durable evidence."""
    passed = assessment.get("passed") is True
    status = (
        "completed_gate1_passed_hard_stop"
        if passed else "completed_gate1_failed_hard_stop"
    )
    execution_mode = mode_binding.get("mode")
    if execution_mode not in {EMPIRICAL_EXECUTION_MODE, FAKE_EXECUTION_MODE}:
        raise Gate1CalibrationError("Gate 1 execution mode is invalid")
    body = {
        "artifact_type": "gate_1_v1_1_calibration_hard_stop_report_v1",
        "controller": CONTROLLER_ID,
        "status": status,
        "decision": "passed" if passed else "failed",
        "source": (
            "fake-transport-test"
            if execution_mode == FAKE_EXECUTION_MODE else "empirical-live"
        ),
        "warning": (
            "FAKE TRANSPORT TEST OUTPUT. NOT EMPIRICAL MODEL EVIDENCE."
            if execution_mode == FAKE_EXECUTION_MODE else None
        ),
        "execution_mode": execution_mode,
        "execution_mode_event_hash": mode_binding["event_hash"],
        "experiment_started": False,
        "completed_stage": "calibration",
        "logical_requests": 20,
        "dispatch_attempts": len(run.result_events()),
        "preflight_dispatches": 0,
        "smoke_dispatches": 0,
        "later_stage_dispatches": 0,
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": run.manifest[
            "gate1_human_authorization_hash"
        ],
        "authorization_receipt_hash": run.manifest[
            "gate1_authorization_receipt_hash"
        ],
        "authorization_claim_event_hash": run.manifest[
            "gate1_authorization_claim_event_hash"
        ],
        "stage2_capability_lineage": proposal["stage2_capability_lineage"],
        "protocol_bridge": proposal["protocol_bridge"],
        "protocol_version": run.plan.protocol_version,
        "protocol_hash": run.plan.protocol_hash,
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "implementation_hash": run.plan.implementation_hash,
        "transport_hash": proposal["transport_hash"],
        "provider_identity": proposal["required_provider_identity"],
        "pricing_contract": proposal["pricing_contract"],
        "assessment": json.loads(canonical(dict(assessment))),
        "assessment_hash": digest(dict(assessment)),
        "gate_decision_event_hash": decision["event_hash"],
        "gate_decision_evidence_hash": decision["evidence_hash"],
        "gate1_passed": passed,
        "budget": json.loads(canonical(dict(budget_snapshot))),
        "journal_tail": run.manifest.get("last_event_hash"),
        "journal_event_count": run.manifest.get("event_count"),
        "next_stage_authorized": False,
    }
    return {**body, "report_hash": digest(body)}


class Gate1CalibrationExecutor:
    """Durable executor restricted to the 20 Gate 1 calibration verdicts."""

    def __init__(
            self, run: DurableRun, provider: OpenAIResponsesProvider,
            proposal: Mapping[str, Any], inventory: Mapping[str, Any], *,
            authorization_receipt: Mapping[str, Any],
            fake_transport_test: bool = False,
            sleep=None):
        if run.manifest.get("execution_controller") != CONTROLLER_ID:
            raise Gate1CalibrationError("run is not marked for Gate 1 calibration")
        receipt = dict(authorization_receipt)
        receipt_hash = receipt.pop("receipt_hash", None)
        bindings = [
            event for event in run.events
            if event.get("kind") == GATE1_CALIBRATION_BINDING_EVENT
        ]
        if (len(bindings) != 1
                or not isinstance(receipt_hash, str)
                or receipt_hash != digest(receipt)
                or receipt.get("artifact_type")
                != "gate_1_authorization_consumption_receipt_v1_1"
                or receipt.get("proposal_hash") != proposal["proposal_hash"]
                or receipt.get("authorization_hash")
                != run.manifest.get("gate1_human_authorization_hash")
                or receipt.get("run_directory") != str(run.path.resolve())
                or receipt.get("run_instance_event_hash")
                != bindings[0].get("event_hash")
                or receipt.get("run_instance_hash")
                != digest(bindings[0].get("run_instance_nonce"))
                or run.manifest.get("gate1_authorization_consumed") is not True
                or run.manifest.get("gate1_authorization_receipt_hash")
                != receipt_hash):
            raise Gate1CalibrationError(
                "a durable one-use Gate 1 authorization receipt is required"
            )
        claim = _require_gate1_claim_event(
            run, proposal, {
                "authorization_uuid": receipt["authorization_uuid"],
                "authorization_hash": receipt["authorization_hash"],
                "gate1_run_id": receipt["gate1_run_id"],
            }, authorization_receipt,
        )
        if (run.manifest.get("gate1_authorization_claim_event_hash")
                != claim["event_hash"]):
            raise Gate1CalibrationError(
                "Gate 1 authorization claim manifest is not journal-derived"
            )
        if not isinstance(provider, OpenAIResponsesProvider):
            raise Gate1CalibrationError(
                "Gate 1 requires the OpenAI Responses adapter"
            )
        if not fake_transport_test:
            transport = getattr(provider, "transport", None)
            if not isinstance(transport, OpenAISDKTransport):
                raise Gate1CalibrationError(
                    "empirical Gate 1 requires the locked official SDK transport"
                )
            if (transport.audit_metadata != proposal["transport"]
                    or digest(transport.audit_metadata)
                    != proposal["transport_hash"]):
                raise Gate1CalibrationError(
                    "provider transport differs from the Gate 1 proposal"
                )
        expected_inventory = _audit_calibration_inventory(run.config, run.plan)
        if (canonical(dict(inventory)) != canonical(expected_inventory)
                or expected_inventory["inventory_hash"]
                != proposal["calibration_projection"]["inventory_hash"]):
            raise Gate1CalibrationError(
                "runtime calibration inventory differs from the authorization"
            )
        self.run = run
        self.provider = provider
        self.proposal = proposal
        self.inventory = expected_inventory
        self.fake_transport_test = fake_transport_test
        self.sleep = sleep or (
            (lambda _seconds: None) if fake_transport_test else time.sleep
        )
        self.factory = ScientificRequestFactory(run.config, run.plan)
        self.rows = {row["identity"]: row for row in inventory["requests"]}
        self.release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
        self.ledger = self._replay_budget()
        self._validate_exact_scope_history()

    def _replay_budget(self) -> BudgetLedger:
        return BudgetLedger.replay(
            self.run.config.limits,
            self.run.config.pricing,
            Decimal(self.proposal["exact_caps"]["max_cost_usd"]),
            self.run.budget_records(),
            self.release,
        )

    def _selected(self) -> list[ScheduledRequest]:
        selected = list(self.run.plan.requests)
        if (len(selected) != 20
                or any(
                    spec.stage != "calibration"
                    or spec.phase != "calibration"
                    or spec.action != "verdict"
                    or spec.dependencies
                    or spec.parent_checkpoint is not None
                    for spec in selected
                )
                or digest([spec.identity for spec in selected])
                != self.proposal["calibration_scope"]["logical_identity_hash"]):
            raise Gate1CalibrationError(
                "runtime Gate 1 selection differs from the proposal"
            )
        return selected

    def _validate_exact_scope_history(self) -> None:
        allowed = {spec.identity for spec in self._selected()}
        for event in self.run.events:
            if (event.get("kind") in {"reservation", "result"}
                    and event.get("identity") not in allowed):
                raise ResumeError(
                    "Gate 1 journal contains preflight, smoke, or later dispatch"
                )
            if event.get("kind") == "checkpoint":
                raise ResumeError("Gate 1 journal cannot contain checkpoints")
            if (event.get("kind") == "gate"
                    and event.get("gate") != "calibration"):
                raise ResumeError("Gate 1 journal contains a non-calibration gate")

    def _bind_execution_mode(self) -> dict[str, Any]:
        """Immutably bind test versus empirical execution before dispatch."""
        expected_mode = (
            FAKE_EXECUTION_MODE
            if self.fake_transport_test else EMPIRICAL_EXECUTION_MODE
        )
        bindings = [
            event for event in self.run.events
            if event.get("kind") == EXECUTION_MODE_BINDING_EVENT
        ]
        if len(bindings) > 1:
            raise ResumeError("Gate 1 execution mode binding is not singular")
        if bindings:
            binding = bindings[0]
            if (binding.get("controller") != CONTROLLER_ID
                    or binding.get("proposal_hash")
                    != self.proposal["proposal_hash"]
                    or binding.get("run_id") != GATE1_RUN_ID
                    or binding.get("mode") != expected_mode):
                raise ResumeError("Gate 1 execution mode changed on resume")
            return binding
        if any(
                event.get("kind") in {"reservation", "result"}
                for event in self.run.events):
            raise ResumeError(
                "Gate 1 dispatch exists without a prior execution mode binding"
            )
        return self.run.append(
            EXECUTION_MODE_BINDING_EVENT,
            controller=CONTROLLER_ID,
            proposal_hash=self.proposal["proposal_hash"],
            run_id=GATE1_RUN_ID,
            mode=expected_mode,
        )

    def _status_terminal(
            self, _spec: ScheduledRequest,
            results: list[dict[str, Any]]) -> bool:
        if not results:
            return False
        status = results[-1]["status"]
        if status in OBSERVATION_STATUSES or status in STOP_STATUSES:
            return True
        if status == "overlength":
            return True
        if status == "retryable_error":
            return sum(
                row.get("status") == "retryable_error" for row in results
            ) > self.run.config.retry_policy.transport_retries
        raise Gate1CalibrationError(f"unknown provider status: {status}")

    def _resolved(self, identity: str) -> bool:
        spec = next(
            item for item in self.run.plan.requests
            if item.identity == identity
        )
        rows = self.run.result_events(identity)
        return bool(
            rows
            and self._status_terminal(spec, rows)
            and rows[-1].get("status") in OBSERVATION_STATUSES
        )

    def _hard_stop(self, spec: ScheduledRequest) -> str | None:
        results = self.run.result_events(spec.identity)
        if not results:
            return None
        status = results[-1]["status"]
        if status in PERSISTENT_GLOBAL_STOPS or status == "ambiguous_timeout":
            return f"paused_{status}"
        if status == "overlength":
            return "paused_overlength_verdict"
        if (status == "retryable_error"
                and self._status_terminal(spec, results)):
            return "paused_retry_exhausted"
        return None

    def _recover_unmatched_as_ambiguous(self) -> None:
        reservations = {
            event["reservation"]["reservation_id"]: event
            for event in self.run.events
            if event.get("kind") == "reservation"
        }
        for reservation_id in list(self.ledger.active):
            event = reservations[reservation_id]
            accounting = self.ledger.preview_reconciliation(
                reservation_id, None, ambiguous=True
            )
            self.run.append(
                "result",
                identity=event["identity"],
                attempt=event["attempt"],
                reservation_id=reservation_id,
                request_hash=event["request_hash"],
                wire_payload_hash=event["wire_payload_hash"],
                status="ambiguous_timeout",
                ambiguous=True,
                known_not_completed=False,
                usage=None,
                budget_accounting=accounting,
                provider_identity=None,
                provider_result={
                    "status": "ambiguous_timeout",
                    "ambiguous": True,
                    "error": (
                        "durable reservation had no result; completion is unknown"
                    ),
                },
            )
            self.ledger.reconcile(
                reservation_id,
                None,
                ambiguous=True,
                expected_accounting=accounting,
            )

    def _bind_live_transport(self) -> None:
        if self.fake_transport_test:
            return
        metadata = json.loads(canonical(self.provider.transport.audit_metadata))
        metadata_hash = digest(metadata)
        if (metadata != self.proposal["transport"]
                or metadata_hash
                != self.run.manifest.get("authorized_transport_hash")):
            raise Gate1CalibrationError(
                "live transport differs from Gate 1 authorization"
            )
        try:
            installed = importlib_metadata.version("openai")
        except importlib_metadata.PackageNotFoundError as exc:
            raise Gate1CalibrationError("locked OpenAI SDK is unavailable") from exc
        if installed != STAGE2_SDK_VERSION:
            raise Gate1CalibrationError("installed OpenAI SDK version differs")
        manifest = self.run.manifest
        existing = manifest.get("transport")
        existing_hash = manifest.get("transport_hash")
        if existing is not None and existing != metadata:
            raise ResumeError("Gate 1 transport changed on resume")
        if existing_hash is not None and existing_hash != metadata_hash:
            raise ResumeError("Gate 1 transport hash changed on resume")
        runtime = dict(manifest["runtime"])
        dependencies = [{"name": "openai", "version": installed}]
        if runtime.get("third_party_dependencies") not in ([], dependencies):
            raise ResumeError("Gate 1 dependency inventory changed")
        if (existing == metadata
                and existing_hash == metadata_hash
                and runtime.get("third_party_dependencies") == dependencies):
            return
        runtime["third_party_dependencies"] = dependencies
        self.run.set_status(
            manifest["status"],
            transport=metadata,
            transport_hash=metadata_hash,
            runtime=runtime,
        )

    def _verify_provider_identity_history(self) -> None:
        expected = self.proposal["required_provider_identity"]
        observed = False
        for event in self.run.result_events():
            if event.get("status") not in PROVIDER_IDENTITY_STATUSES:
                continue
            observed = True
            result = event.get("provider_result")
            candidate = (
                _provider_identity_candidate(
                    self.run.config, event["status"], result
                ) if isinstance(result, Mapping) else None
            )
            if event.get("provider_identity") != candidate or candidate != expected:
                self.run.set_status(
                    "paused_model_drift",
                    stop_identity=event.get("identity"),
                    required_provider_identity=expected,
                    rejected_provider_identity=candidate,
                    budget=self.ledger.snapshot(),
                )
                raise Gate1CalibrationError(
                    "Gate 1 provider identity differs from reviewed capability evidence"
                )
        if observed:
            durable = self.run.manifest.get("observed_provider_identity")
            if durable not in (None, expected):
                raise ResumeError("durable Gate 1 provider identity changed")
            if durable is None:
                self.run.set_status(
                    self.run.manifest["status"],
                    observed_provider_identity=expected,
                )

    def _validate_request_shape(
            self, spec: ScheduledRequest, request: Any,
            prompt_hash: str, attempt: int,
            prior: list[dict[str, Any]]) -> Mapping[str, Any]:
        if (request.request_identity != spec.identity
                or request.attempt != attempt
                or request.model != self.run.config.model
                or request.model_settings
                != dict(self.run.config.model_settings)):
            raise Gate1CalibrationError(
                "rendered calibration request differs from its frozen identity"
            )
        if (sum(row.get("status") == "retryable_error" for row in prior)
                > self.run.config.retry_policy.transport_retries
                or any(row.get("status") != "retryable_error" for row in prior)):
            raise Gate1CalibrationError("request retry shape was not authorized")
        ceiling = self.rows.get(spec.identity)
        if ceiling is None:
            raise Gate1CalibrationError(
                "calibration request is absent from the reviewed inventory"
            )
        if (request.max_output_tokens != ceiling["max_output_tokens"]
                or request.conservative_input_token_bound
                > ceiling["conservative_input_token_bound"]
                or request.conservative_input_token_bound
                > self.release.max_input_tokens_per_request
                or request.max_output_tokens
                > self.release.max_output_tokens_per_request
                or request.conservative_input_token_bound
                + request.max_output_tokens
                > self.run.config.model_context_tokens
                or ceiling["fixture_index"] != spec.metadata["fixture_index"]
                or ceiling["fixture_namespace"]
                != spec.metadata["fixture_namespace"]
                or not isinstance(prompt_hash, str)):
            raise Gate1CalibrationError(
                "rendered calibration request exceeds its authorized shape"
            )
        return OpenAIResponsesProvider.build_payload(request)

    def _gate_decision(self) -> tuple[dict[str, Any], dict[str, Any]]:
        assessment = calibration_assessment(self.run)
        gates = self.run.gate_events()
        existing = gates.get("calibration")
        if existing is None:
            existing = self.run._record_gate_decision_unlocked(
                "calibration",
                passed=assessment["passed"],
                evidence=assessment,
                decided_by=CONTROLLER_ID,
                note=(
                    "Frozen automatic Gate 1 v1.1 capability decision; "
                    "hard-stop before smoke."
                ),
            )
        elif (existing.get("passed") is not assessment["passed"]
                or canonical(existing.get("evidence")) != canonical(assessment)
                or existing.get("evidence_hash") != digest(assessment)
                or existing.get("decided_by") != CONTROLLER_ID):
            raise ResumeError("immutable Gate 1 decision differs from assessment")
        return assessment, existing

    def _write_completion_report(
            self, assessment: Mapping[str, Any],
            decision: Mapping[str, Any]) -> dict[str, Any]:
        report_path = self.run.path / "gate1_calibration_report.json"
        mode_binding = self._bind_execution_mode()
        report = _gate1_completion_report_value(
            self.run,
            self.proposal,
            self.ledger.snapshot(),
            assessment,
            decision,
            mode_binding,
        )
        if report_path.exists():
            value = _read_json(report_path, "Gate 1 calibration report")
            if canonical(value) != canonical(report):
                raise ResumeError(
                    "Gate 1 report differs from current durable evidence"
                )
            return value
        _write_immutable_json(report_path, report, "Gate 1 calibration report")
        return report

    def execute(self) -> dict[str, Any]:
        """Execute exactly Gate 1 calibration and hard-stop before smoke."""
        with self.run.execution_lock():
            if self.run.manifest.get("execution_controller") != CONTROLLER_ID:
                raise Gate1CalibrationError("Gate 1 controller marker changed")
            self.ledger = self._replay_budget()
            self._validate_exact_scope_history()
            self._bind_execution_mode()
            self._bind_live_transport()
            self._recover_unmatched_as_ambiguous()
            self._verify_provider_identity_history()
            selected = self._selected()
            completed_statuses = {
                "completed_gate1_passed_hard_stop",
                "completed_gate1_failed_hard_stop",
            }
            if self.run.manifest.get("status") in completed_statuses:
                if (self.ledger.active
                        or any(not self._resolved(spec.identity)
                               for spec in selected)):
                    raise ResumeError(
                        "completed Gate 1 contains unresolved calibration work"
                    )
                assessment, decision = self._gate_decision()
                expected_status = (
                    "completed_gate1_passed_hard_stop"
                    if assessment["passed"] else
                    "completed_gate1_failed_hard_stop"
                )
                if self.run.manifest.get("status") != expected_status:
                    raise ResumeError(
                        "completed Gate 1 status differs from its immutable decision"
                    )
                return self._write_completion_report(assessment, decision)
            if self.run.gate_events():
                raise ResumeError(
                    "Gate 1 decision exists before a completed hard-stop manifest"
                )
            for spec in selected:
                stop = self._hard_stop(spec)
                if stop is not None:
                    self.run.set_status(
                        stop,
                        stop_identity=spec.identity,
                        budget=self.ledger.snapshot(),
                    )
                    raise Gate1CalibrationError(
                        f"Gate 1 remains stopped at {spec.identity}: {stop}"
                    )
            self.run.set_status(
                "running_gate1_calibration",
                target_stage="calibration",
                stop_before_stage="smoke",
                budget=self.ledger.snapshot(),
            )
            for spec in selected:
                results = self.run.result_events(spec.identity)
                if self._status_terminal(spec, results):
                    continue
                while not self._status_terminal(spec, results):
                    attempt = len(results)
                    if results:
                        policy = self.run.config.retry_policy
                        delay = min(
                            policy.maximum_backoff_seconds,
                            policy.initial_backoff_seconds
                            * (Decimal(2) ** max(0, attempt - 1)),
                        )
                        if delay:
                            self.sleep(float(delay))
                    context = ExecutionContext(self.run, attempt, ())
                    request, rendered = self.factory.build(spec, context)
                    wire = self._validate_request_shape(
                        spec,
                        request,
                        rendered.prompt_hash,
                        attempt,
                        results,
                    )
                    request_hash, _ = self.run.save_request(request)
                    wire_hash = self.run.save_wire_payload(wire)
                    try:
                        reservation = self.ledger.reserve(
                            spec.identity,
                            attempt,
                            input_tokens=request.conservative_input_token_bound,
                            output_tokens=request.max_output_tokens,
                        )
                    except BudgetExhausted as exc:
                        self.run.set_status(
                            "paused_budget_exhausted",
                            stop_identity=spec.identity,
                            budget=self.ledger.snapshot(),
                        )
                        raise Gate1CalibrationError(
                            "an exact Gate 1 ceiling stopped dispatch"
                        ) from exc
                    self.run.append(
                        "reservation",
                        identity=spec.identity,
                        attempt=attempt,
                        request_hash=request_hash,
                        wire_payload_hash=wire_hash,
                        reservation=reservation.to_dict(),
                        gate1_projected_shape="initial_verdict",
                    )
                    try:
                        provider_result = self.provider.complete(request)
                        result = _object_dict(provider_result)
                        status_value = _status_text(provider_result.status)
                        ambiguous = bool(result.get("ambiguous"))
                        usage = result.get("usage")
                    except BaseException:
                        status_value = "ambiguous_timeout"
                        ambiguous = True
                        usage = None
                        result = {
                            "status": status_value,
                            "ambiguous": True,
                            "error": (
                                "provider raised after dispatch; completion is unknown"
                            ),
                        }
                    if status_value not in (
                            OBSERVATION_STATUSES | STOP_STATUSES
                            | {"overlength", "retryable_error"}):
                        status_value = "fatal_error"
                        ambiguous = False
                        usage = None
                        result = {
                            "status": status_value,
                            "ambiguous": False,
                            "error": "provider returned an unsupported status",
                        }
                    known_not_completed = result.get("known_not_completed") is True
                    # Proven-not-completed retries require an absent usage
                    # object.  Even an all-zero or malformed mapping is not
                    # proof and is conservatively treated as ambiguous.
                    if usage is not None:
                        known_not_completed = False
                    if (status_value in KNOWN_NOT_COMPLETED
                            and not known_not_completed):
                        result["original_status"] = status_value
                        status_value = "ambiguous_timeout"
                        ambiguous = True
                        result["status"] = status_value
                        result["ambiguous"] = True
                        result["known_not_completed"] = False
                    provider_identity = _provider_identity_candidate(
                        self.run.config, status_value, result
                    )
                    accounting = self.ledger.preview_reconciliation(
                        reservation.reservation_id,
                        usage,
                        ambiguous=ambiguous,
                        known_not_completed=known_not_completed,
                    )
                    self.run.append(
                        "result",
                        identity=spec.identity,
                        attempt=attempt,
                        reservation_id=reservation.reservation_id,
                        request_hash=request_hash,
                        wire_payload_hash=wire_hash,
                        status=status_value,
                        ambiguous=ambiguous,
                        known_not_completed=known_not_completed,
                        usage=usage,
                        budget_accounting=accounting,
                        provider_identity=provider_identity,
                        provider_result=result,
                    )
                    self.ledger.reconcile(
                        reservation.reservation_id,
                        usage,
                        ambiguous=ambiguous,
                        known_not_completed=known_not_completed,
                        expected_accounting=accounting,
                    )
                    results = self.run.result_events(spec.identity)
                    if (status_value in PROVIDER_IDENTITY_STATUSES
                            and provider_identity
                            != self.proposal["required_provider_identity"]):
                        self.run.set_status(
                            "paused_model_drift",
                            stop_identity=spec.identity,
                            required_provider_identity=self.proposal[
                                "required_provider_identity"
                            ],
                            rejected_provider_identity=provider_identity,
                            budget=self.ledger.snapshot(),
                        )
                        raise Gate1CalibrationError(
                            "provider identity differs from reviewed evidence"
                        )
                    if status_value in PROVIDER_IDENTITY_STATUSES:
                        expected_identity = self.proposal[
                            "required_provider_identity"
                        ]
                        durable = self.run.manifest.get(
                            "observed_provider_identity"
                        )
                        if durable not in (None, expected_identity):
                            raise ResumeError(
                                "durable Gate 1 provider identity changed"
                            )
                        if durable is None:
                            self.run.set_status(
                                self.run.manifest["status"],
                                observed_provider_identity=expected_identity,
                            )
                    if status_value in STOP_STATUSES:
                        self.run.set_status(
                            f"paused_{status_value}",
                            stop_identity=spec.identity,
                            budget=self.ledger.snapshot(),
                        )
                        raise Gate1CalibrationError(
                            f"provider hard-stop status: {status_value}"
                        )
                    if (status_value == "retryable_error"
                            and self._status_terminal(spec, results)):
                        self.run.set_status(
                            "paused_retry_exhausted",
                            stop_identity=spec.identity,
                            budget=self.ledger.snapshot(),
                        )
                        raise Gate1CalibrationError(
                            "transport retry is outside the Gate 1 release"
                        )
                    if status_value == "overlength":
                        self.run.set_status(
                            "paused_overlength_verdict",
                            stop_identity=spec.identity,
                            budget=self.ledger.snapshot(),
                        )
                        raise Gate1CalibrationError(
                            "a verdict overlength cannot satisfy Gate 1"
                        )
            if any(not self._resolved(spec.identity) for spec in selected):
                raise Gate1CalibrationError(
                    "Gate 1 did not produce an observation for every identity"
                )
            assessment, decision = self._gate_decision()
            status = (
                "completed_gate1_passed_hard_stop"
                if assessment["passed"] else
                "completed_gate1_failed_hard_stop"
            )
            self.run.set_status(
                status,
                completed_through_stage="calibration",
                calibration_decision=(
                    "passed" if assessment["passed"] else "failed"
                ),
                calibration_assessment_hash=digest(assessment),
                calibration_gate_event_hash=decision["event_hash"],
                next_stage_authorized=False,
                experiment_started=False,
                budget=self.ledger.snapshot(),
                stop_identity=None,
            )
            return self._write_completion_report(assessment, decision)


def inspect_gate1_calibration(
        proposal_path: Path = PROPOSAL_PATH, *,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate the no-dispatch package without authorization or credentials."""
    proposal = load_gate1_calibration_proposal(
        proposal_path, require_raw_lineage=False
    )
    _validate_pricing_window(proposal, now=now)
    if not STAGE2_RUN_DIR.is_dir():
        return {
            "status": "blocked_missing_raw_stage2_lineage",
            "execution_ready": False,
            "proposal_hash": proposal["proposal_hash"],
            "portability_snapshot_hash": proposal[
                "stage2_capability_lineage"
            ]["portability_snapshot_hash"],
            "credentials_accessed": False,
            "network_calls": 0,
            "paid_calls": 0,
            "experiment_started": False,
            "remaining_step": (
                "Restore the ignored complete Stage 2 evidence and cross-check "
                "it against the Git-attested portability snapshot before any "
                "authorization can be validated or claimed."
            ),
        }
    proposal = validate_gate1_calibration_proposal(proposal)
    return {
        "status": "ready_awaiting_external_human_authorization",
        "execution_ready": True,
        "proposal_hash": proposal["proposal_hash"],
        "stage2_report_hash": proposal["stage2_capability_lineage"]["report_hash"],
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "implementation_hash": proposal["current_implementation_hash"],
        "gate1_protocol": proposal["gate1_protocol"],
        "protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "authorization_validity": proposal["external_authorization"],
        "exact_caps": proposal["exact_caps"],
        "scope": proposal["calibration_scope"],
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
        "remaining_step": (
            "A human must create the external one-use authorization artifact "
            "matching the checked schema."
        ),
    }


def validate_gate1_authorization_file(
        authorization_path: Path,
        proposal_path: Path = PROPOSAL_PATH, *,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate external authorization and derived contract; never read a key."""
    proposal = load_gate1_calibration_proposal(proposal_path)
    authorization = load_gate1_human_authorization(
        authorization_path, proposal, _proposal_verified=True
    )
    _validate_first_claim_window(authorization, proposal, now=now)
    config, plan, dispatch, inventory = _gate1_config_and_plan(
        proposal,
        authorization,
        _proposal_verified=True,
    )
    return {
        "status": "authorization_valid_no_dispatch",
        "authorization_hash": authorization["authorization_hash"],
        "gate1_run_id": authorization["gate1_run_id"],
        "gate1_run_directory": authorization["gate1_run_directory"],
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "calibration_inventory_hash": inventory["inventory_hash"],
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
    }


def _verify_completed_gate1_calibration(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], receipt: Mapping[str, Any]
        ) -> dict[str, Any]:
    """Verify an exact completed empirical Gate 1 without repair or I/O."""
    completed_statuses = {
        "completed_gate1_passed_hard_stop",
        "completed_gate1_failed_hard_stop",
    }
    if (run.config.run_id != GATE1_RUN_ID
            or run.config.protocol_version != GATE1_PROTOCOL_VERSION
            or run.config.source != "empirical-live"
            or run.config.calibration_namespace != GATE1_CALIBRATION_NAMESPACE
            or run.manifest.get("status") not in completed_statuses
            or run.manifest.get("execution_controller") != CONTROLLER_ID
            or run.manifest.get("gate1_proposal_hash")
            != proposal["proposal_hash"]
            or digest(run.config.to_dict()) != run.manifest.get("config_hash")
            or run.plan.plan_hash != run.manifest.get("plan_hash")):
        raise Gate1CalibrationError(
            "completed Gate 1 differs from its reviewed release contract"
        )
    inventory = _audit_calibration_inventory(run.config, run.plan)
    selected = list(run.plan.requests)
    allowed = {spec.identity for spec in selected}
    if (len(selected) != 20 or run.plan.checkpoints
            or inventory["inventory_hash"]
            != proposal["calibration_projection"]["inventory_hash"]
            or any(
                spec.stage != "calibration"
                or spec.phase != "calibration"
                or spec.action != "verdict"
                or spec.dependencies
                or spec.parent_checkpoint is not None
                for spec in selected
            )):
        raise Gate1CalibrationError("completed Gate 1 scope is not exact")
    try:
        dispatch = DispatchAuthorizationArtifact.from_mapping(
            run.manifest["dispatch_authorization"]
        )
    except (AuthorizationRequiredError, KeyError, TypeError, ValueError) as exc:
        raise Gate1CalibrationError(
            "completed Gate 1 dispatch authorization is invalid"
        ) from exc
    expected_binding = _binding_event_value(
        proposal, authorization, run.config, run.plan, dispatch
    )
    bindings = [
        event for event in run.events
        if event.get("kind") == GATE1_CALIBRATION_BINDING_EVENT
    ]
    if len(bindings) != 1:
        raise Gate1CalibrationError("Gate 1 binding is not singular")
    binding = bindings[0]
    if (any(binding.get(key) != value
            for key, value in expected_binding.items())
            or not isinstance(binding.get("run_instance_nonce"), str)
            or re.fullmatch(r"[a-f0-9]{64}", binding["run_instance_nonce"])
            is None):
        raise Gate1CalibrationError("completed Gate 1 binding differs")
    claim = _require_gate1_claim_event(
        run, proposal, authorization, receipt
    )
    modes = [
        event for event in run.events
        if event.get("kind") == EXECUTION_MODE_BINDING_EVENT
    ]
    if len(modes) != 1:
        raise Gate1CalibrationError(
            "completed Gate 1 execution mode is not singular"
        )
    mode = modes[0]
    if (mode.get("controller") != CONTROLLER_ID
            or mode.get("proposal_hash") != proposal["proposal_hash"]
            or mode.get("run_id") != GATE1_RUN_ID
            or mode.get("mode") != EMPIRICAL_EXECUTION_MODE
            or mode.get("sequence", -1) <= claim.get("sequence", -1)):
        raise Gate1CalibrationError(
            "completed Gate 1 lacks its empirical execution-mode binding"
        )
    runtime_dependencies = run.manifest.get("runtime", {}).get(
        "third_party_dependencies"
    )
    if (run.manifest.get("transport") != proposal["transport"]
            or run.manifest.get("transport_hash") != proposal["transport_hash"]
            or runtime_dependencies != [{
                "name": "openai", "version": STAGE2_SDK_VERSION
            }]):
        raise Gate1CalibrationError(
            "completed empirical Gate 1 lacks the exact locked live transport"
        )
    dispatch_events = [
        event for event in run.events
        if event.get("kind") in {"reservation", "result"}
    ]
    results = run.result_events()
    if (not results
            or any(event.get("identity") not in allowed
                   for event in dispatch_events)
            or any(event.get("sequence", -1) <= mode["sequence"]
                   for event in dispatch_events)
            or any(not run.result_events(spec.identity)
                   or run.result_events(spec.identity)[-1].get("status")
                   not in OBSERVATION_STATUSES
                   for spec in selected)
            or any(event.get("kind") == "checkpoint" for event in run.events)):
        raise Gate1CalibrationError(
            "completed Gate 1 contains missing, pre-mode, or out-of-scope work"
        )
    for spec in selected:
        rows = run.result_events(spec.identity)
        if (any(row.get("status") != "retryable_error"
                for row in rows[:-1])
                or sum(row.get("status") == "retryable_error" for row in rows)
                > run.config.retry_policy.transport_retries):
            raise Gate1CalibrationError(
                "completed Gate 1 contains an unauthorized nonfinal "
                "observation or transport retry"
            )
    expected_identity = proposal["required_provider_identity"]
    for event in results:
        if event.get("status") not in PROVIDER_IDENTITY_STATUSES:
            continue
        provider_result = event.get("provider_result")
        candidate = (
            _provider_identity_candidate(
                run.config, event["status"], provider_result
            ) if isinstance(provider_result, Mapping) else None
        )
        if (event.get("provider_identity") != candidate
                or candidate != expected_identity):
            raise Gate1CalibrationError(
                "completed Gate 1 provider identity differs"
            )
    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    ledger = BudgetLedger.replay(
        run.config.limits,
        run.config.pricing,
        Decimal(proposal["exact_caps"]["max_cost_usd"]),
        run.budget_records(),
        release,
    )
    if ledger.active:
        raise Gate1CalibrationError(
            "completed Gate 1 has an unreconciled reservation"
        )
    assessment = calibration_assessment(run)
    gates = run.gate_events()
    decision = gates.get("calibration")
    if (set(gates) != {"calibration"}
            or decision is None
            or decision.get("passed") is not (assessment.get("passed") is True)
            or canonical(decision.get("evidence")) != canonical(assessment)
            or decision.get("evidence_hash") != digest(assessment)
            or decision.get("decided_by") != CONTROLLER_ID
            or decision.get("sequence", -1)
            <= max(event.get("sequence", -1) for event in results)
            or run.manifest.get("last_event_hash") != decision.get("event_hash")
            or run.manifest.get("event_count") != decision.get("sequence", -1) + 1):
        raise Gate1CalibrationError(
            "completed Gate 1 decision differs from its frozen assessment"
        )
    expected_status = (
        "completed_gate1_passed_hard_stop"
        if assessment.get("passed") is True
        else "completed_gate1_failed_hard_stop"
    )
    if (run.manifest.get("status") != expected_status
            or run.manifest.get("calibration_decision")
            != ("passed" if assessment.get("passed") is True else "failed")
            or run.manifest.get("calibration_assessment_hash")
            != digest(assessment)
            or run.manifest.get("calibration_gate_event_hash")
            != decision["event_hash"]
            or run.manifest.get("next_stage_authorized") is not False
            or run.manifest.get("experiment_started") is not False):
        raise Gate1CalibrationError(
            "completed Gate 1 manifest differs from its immutable decision"
        )
    expected_report = _gate1_completion_report_value(
        run, proposal, ledger.snapshot(), assessment, decision, mode
    )
    report = _read_json(
        run.path / "gate1_calibration_report.json", "Gate 1 calibration report"
    )
    if canonical(report) != canonical(expected_report):
        raise Gate1CalibrationError(
            "Gate 1 report differs from current durable evidence"
        )
    return report


def run_gate1_calibration(
        authorization_path: Path, *,
        proposal_path: Path = PROPOSAL_PATH,
        env_file: Path = STAGE2_ENV_FILE,
        _now: datetime | None = None) -> dict[str, Any]:
    """Execute the exact authorized calibration release and hard-stop."""
    proposal = load_gate1_calibration_proposal(proposal_path)
    authorization = load_gate1_human_authorization(
        authorization_path,
        proposal,
        require_external=True,
        _proposal_verified=True,
    )
    receipt_dir = _authorization_receipt_directory(Path(authorization_path))
    receipt_path = _authorization_receipt_path(receipt_dir)
    run_path = ROOT / authorization["gate1_run_directory"]
    if receipt_path.exists():
        if not run_path.is_dir():
            raise Gate1CalibrationError(
                "an authorization receipt exists without its durable Gate 1 run"
            )
        _validate_pricing_window(proposal, now=_now)
        validated_path, _ = _validated_gate1_run_path(
            run_path, authorization
        )
        candidate = DurableRun.inspect(
            validated_path, probe_installed_dependencies=False
        )
        if candidate.manifest.get("status") in {
                "completed_gate1_passed_hard_stop",
                "completed_gate1_failed_hard_stop",
        }:
            # The saved dependency identity remains part of the exact report;
            # the read-only resume deliberately avoids probing the locally
            # installed SDK, reading a secret, or constructing a client.
            receipt = claim_gate1_authorization_once(
                candidate,
                proposal,
                authorization,
                receipt_dir=receipt_dir,
                now=_now,
            )
            with candidate.execution_lock():
                return _verify_completed_gate1_calibration(
                    candidate, proposal, authorization, receipt
                )
    else:
        try:
            _validate_first_claim_window(authorization, proposal, now=_now)
        except Gate1CalibrationError as exc:
            if run_path.exists():
                raise Gate1CalibrationError(
                    "the prepared Gate 1 release missed its first-claim window; "
                    "this fixed run is permanently fail-closed and requires a "
                    "new versioned release/run ID, not a replacement authorization"
                ) from exc
            raise
    config, plan, dispatch, inventory = _gate1_config_and_plan(
        proposal,
        authorization,
        _proposal_verified=True,
    )
    run, prepared_config, prepared_plan, prepared_dispatch, prepared_inventory = (
        prepare_gate1_calibration_run(
            run_path,
            proposal,
            authorization,
            _proposal_verified=True,
        )
    )
    if (prepared_config != config
            or prepared_plan.to_dict() != plan.to_dict()
            or prepared_dispatch.to_dict() != dispatch.to_dict()
            or prepared_inventory["inventory_hash"]
            != inventory["inventory_hash"]):
        raise Gate1CalibrationError("prepared Gate 1 contract changed")
    receipt = claim_gate1_authorization_once(
        run,
        proposal,
        authorization,
        receipt_dir=receipt_dir,
        now=_now,
    )

    if run.manifest.get("status") in {
            "completed_gate1_passed_hard_stop",
            "completed_gate1_failed_hard_stop",
    }:
        # Exact completed resumes are read-only and return before checking the
        # SDK installation, reading a secret, or constructing a client.
        with run.execution_lock():
            return _verify_completed_gate1_calibration(
                run, proposal, authorization, receipt
            )

    # Claim is durable before any credential access or SDK-client creation.
    try:
        installed = importlib_metadata.version("openai")
    except importlib_metadata.PackageNotFoundError as exc:
        raise Gate1CalibrationError("locked OpenAI SDK is not installed") from exc
    if installed != STAGE2_SDK_VERSION:
        raise Gate1CalibrationError("installed OpenAI SDK differs from proposal")
    transport_metadata = _stage2_transport_metadata(sdk_version=installed)
    if (transport_metadata != proposal["transport"]
            or digest(transport_metadata) != proposal["transport_hash"]):
        raise Gate1CalibrationError("runtime transport differs from proposal")
    conflicting = sorted(
        name for name in os.environ if name.upper().startswith("OPENAI_")
    )
    if conflicting:
        raise Gate1CalibrationError(
            "all ambient OpenAI variables must be unset for this bound release"
        )
    secret = _load_local_openai_key(Path(env_file))
    from openai import DefaultHttpxClient, OpenAI
    http_client = DefaultHttpxClient(trust_env=False, follow_redirects=False)
    client = OpenAI(
        api_key=secret,
        base_url="https://api.openai.com/v1",
        max_retries=0,
        timeout=STAGE2_TIMEOUT_SECONDS,
        http_client=http_client,
    )
    transport = OpenAISDKTransport(
        client,
        timeout_seconds=STAGE2_TIMEOUT_SECONDS,
        sdk_version=installed,
        ambient_openai_environment="rejected",
        http_client_trust_env=False,
        http_client_follow_redirects=False,
    )
    return Gate1CalibrationExecutor(
        run,
        OpenAIResponsesProvider(transport),
        proposal,
        inventory,
        authorization_receipt=receipt,
        fake_transport_test=False,
    ).execute()


def verify_gate1_passed_lineage(
        run_dir: Path | None = None, *,
        proposal_path: Path = PROPOSAL_PATH) -> dict[str, Any]:
    """Verify exact immutable passed Gate 1 evidence for Gate 2 construction."""
    proposal = load_gate1_calibration_proposal(proposal_path)
    path = (
        ROOT / proposal["gate1_protocol"]["run_directory"]
        if run_dir is None else Path(run_dir)
    )
    run = DurableRun.inspect(path)
    if (run.config.run_id != GATE1_RUN_ID
            or run.config.protocol_version != GATE1_PROTOCOL_VERSION
            or run.config.calibration_namespace != GATE1_CALIBRATION_NAMESPACE
            or run.manifest.get("execution_controller") != CONTROLLER_ID
            or run.manifest.get("gate1_proposal_hash")
            != proposal["proposal_hash"]
            or run.manifest.get("gate1_authorization_consumed") is not True
            or re.fullmatch(
                r"[a-f0-9]{64}",
                str(run.manifest.get("gate1_authorization_receipt_hash")),
            ) is None):
        raise Gate1CalibrationError("Gate 1 manifest lineage is incomplete")
    bindings = [
        event for event in run.events
        if event.get("kind") == GATE1_CALIBRATION_BINDING_EVENT
    ]
    if len(bindings) != 1:
        raise Gate1CalibrationError("Gate 1 binding lineage is not singular")
    binding = bindings[0]
    expected_binding = {
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": run.manifest[
            "gate1_human_authorization_hash"
        ],
        "gate1_run_id": GATE1_RUN_ID,
        "stage2_report_hash": proposal["stage2_capability_lineage"]["report_hash"],
        "stage2_authorization_package_hash": proposal[
            "stage2_capability_lineage"
        ]["authorization_package_hash"],
        "stage2_journal_tail": proposal["stage2_capability_lineage"]["journal_tail"],
        "stage2_event_count": proposal["stage2_capability_lineage"][
            "journal_event_count"
        ],
        "protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "gate1_protocol_version": proposal["gate1_protocol"]["protocol_version"],
        "gate1_protocol_hash": proposal["gate1_protocol"]["protocol_hash"],
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "implementation_hash": proposal["current_implementation_hash"],
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "transport_hash": proposal["transport_hash"],
        "required_provider_identity": proposal["required_provider_identity"],
        "pricing_contract_hash": proposal["pricing_contract_hash"],
        "exact_caps": proposal["exact_caps"],
        "dispatch_authorization_hash": run.manifest[
            "dispatch_authorization_hash"
        ],
        "scope": proposal["calibration_scope"],
    }
    if any(binding.get(key) != value for key, value in expected_binding.items()):
        raise Gate1CalibrationError("Gate 1 durable binding differs from proposal")
    if (not isinstance(binding.get("run_instance_nonce"), str)
            or re.fullmatch(r"[a-f0-9]{64}", binding["run_instance_nonce"])
            is None
            or run.manifest.get("gate1_run_instance_hash")
            != digest(binding["run_instance_nonce"])):
        raise Gate1CalibrationError("Gate 1 run-instance binding is invalid")
    claim_events = [
        event for event in run.events
        if event.get("kind") == AUTHORIZATION_CLAIM_EVENT
    ]
    claim = claim_events[0] if len(claim_events) == 1 else None
    if (claim is None
            or claim.get("controller") != CONTROLLER_ID
            or claim.get("proposal_hash") != proposal["proposal_hash"]
            or claim.get("authorization_hash")
            != run.manifest["gate1_human_authorization_hash"]
            or re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}",
                str(claim.get("authorization_uuid")),
            ) is None
            or claim.get("receipt_hash")
            != run.manifest.get("gate1_authorization_receipt_hash")
            or claim.get("gate1_run_id") != GATE1_RUN_ID
            or claim.get("run_directory") != str(run.path.resolve())
            or claim.get("run_instance_event_hash") != binding["event_hash"]
            or claim.get("run_instance_hash")
            != digest(binding["run_instance_nonce"])
            or run.manifest.get("gate1_authorization_claim_event_hash")
            != claim.get("event_hash")
            or claim.get("sequence", -1) <= binding.get("sequence", -1)):
        raise Gate1CalibrationError(
            "Gate 1 authorization consumption is not journal-bound"
        )
    _parse_timestamp(
        claim.get("receipt_claimed_at"),
        "Gate 1 authorization claim receipt_claimed_at",
    )
    mode_bindings = [
        event for event in run.events
        if event.get("kind") == EXECUTION_MODE_BINDING_EVENT
    ]
    if (len(mode_bindings) != 1
            or mode_bindings[0].get("controller") != CONTROLLER_ID
            or mode_bindings[0].get("proposal_hash")
            != proposal["proposal_hash"]
            or mode_bindings[0].get("run_id") != GATE1_RUN_ID
            or mode_bindings[0].get("mode") != EMPIRICAL_EXECUTION_MODE
            or mode_bindings[0].get("sequence", -1)
            <= claim.get("sequence", -1)):
        raise Gate1CalibrationError(
            "Gate 1 passed lineage requires an immutable empirical execution mode"
        )
    mode_binding = mode_bindings[0]
    dispatch_events = [
        event for event in run.events
        if event.get("kind") in {"reservation", "result"}
    ]
    result_events = [
        event for event in dispatch_events
        if event.get("kind") == "result"
    ]
    if any(
            event.get("sequence", -1)
            <= mode_binding.get("sequence", -1)
            for event in dispatch_events):
        raise Gate1CalibrationError(
            "Gate 1 dispatch precedes its immutable empirical execution mode"
        )
    selected = list(run.plan.requests)
    allowed = {spec.identity for spec in selected}
    if (len(selected) != 20 or run.plan.checkpoints
            or any(
                event.get("identity") not in allowed
                for event in run.events
                if event.get("kind") in {"reservation", "result"}
            )
            or any(event.get("kind") == "checkpoint" for event in run.events)):
        raise Gate1CalibrationError("Gate 1 durable dispatch scope is invalid")
    assessment = calibration_assessment(run)
    gates = run.gate_events()
    decision = gates.get("calibration")
    if (set(gates) != {"calibration"}
            or decision is None
            or not result_events
            or decision.get("sequence", -1)
            <= max(event.get("sequence", -1) for event in result_events)
            or decision.get("passed") is not True
            or assessment.get("passed") is not True
            or canonical(decision.get("evidence")) != canonical(assessment)
            or decision.get("evidence_hash") != digest(assessment)
            or decision.get("decided_by") != CONTROLLER_ID
            or run.manifest.get("status")
            != "completed_gate1_passed_hard_stop"
            or run.manifest.get("calibration_decision") != "passed"
            or run.manifest.get("calibration_assessment_hash")
            != digest(assessment)
            or run.manifest.get("calibration_gate_event_hash")
            != decision.get("event_hash")
            or run.manifest.get("next_stage_authorized") is not False
            or run.manifest.get("experiment_started") is not False):
        raise Gate1CalibrationError(
            "Gate 1 has no exact immutable passed calibration decision"
        )
    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    ledger = BudgetLedger.replay(
        run.config.limits,
        run.config.pricing,
        Decimal(proposal["exact_caps"]["max_cost_usd"]),
        run.budget_records(),
        release,
    )
    if ledger.active:
        raise Gate1CalibrationError("Gate 1 has an unreconciled reservation")
    report = _read_json(path / "gate1_calibration_report.json", "Gate 1 report")
    report_body = dict(report)
    report_hash = report_body.pop("report_hash", None)
    expected_report = {
        "artifact_type": "gate_1_v1_1_calibration_hard_stop_report_v1",
        "controller": CONTROLLER_ID,
        "status": "completed_gate1_passed_hard_stop",
        "decision": "passed",
        "source": "empirical-live",
        "warning": None,
        "execution_mode": EMPIRICAL_EXECUTION_MODE,
        "execution_mode_event_hash": mode_binding["event_hash"],
        "experiment_started": False,
        "completed_stage": "calibration",
        "logical_requests": 20,
        "dispatch_attempts": len(run.result_events()),
        "preflight_dispatches": 0,
        "smoke_dispatches": 0,
        "later_stage_dispatches": 0,
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": run.manifest[
            "gate1_human_authorization_hash"
        ],
        "authorization_receipt_hash": run.manifest[
            "gate1_authorization_receipt_hash"
        ],
        "authorization_claim_event_hash": claim["event_hash"],
        "stage2_capability_lineage": proposal["stage2_capability_lineage"],
        "protocol_bridge": proposal["protocol_bridge"],
        "protocol_version": run.plan.protocol_version,
        "protocol_hash": run.plan.protocol_hash,
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "implementation_hash": run.plan.implementation_hash,
        "transport_hash": proposal["transport_hash"],
        "provider_identity": proposal["required_provider_identity"],
        "pricing_contract": proposal["pricing_contract"],
        "assessment": assessment,
        "assessment_hash": digest(assessment),
        "gate_decision_event_hash": decision["event_hash"],
        "gate_decision_evidence_hash": decision["evidence_hash"],
        "gate1_passed": True,
        "budget": ledger.snapshot(),
        "journal_tail": run.manifest.get("last_event_hash"),
        "journal_event_count": run.manifest.get("event_count"),
        "next_stage_authorized": False,
    }
    if report_hash != digest(report_body) or canonical(report_body) != canonical(expected_report):
        raise Gate1CalibrationError(
            "Gate 1 report is not an exact derivative of durable evidence"
        )
    return {
        "run_id": run.config.run_id,
        "protocol_version": run.plan.protocol_version,
        "protocol_hash": run.plan.protocol_hash,
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "implementation_hash": run.plan.implementation_hash,
        "fixture_inventory_hash": digest(dict(run.plan.fixture_inventory)),
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "proposal_hash": proposal["proposal_hash"],
        "authorization_hash": run.manifest["gate1_human_authorization_hash"],
        "authorization_receipt_hash": run.manifest[
            "gate1_authorization_receipt_hash"
        ],
        "authorization_claim_event_hash": claim["event_hash"],
        "binding_event_hash": binding["event_hash"],
        "assessment_hash": digest(assessment),
        "gate_decision_event_hash": decision["event_hash"],
        "report_hash": report["report_hash"],
        "journal_tail": run.manifest["last_event_hash"],
        "journal_event_count": run.manifest["event_count"],
        "transport_hash": proposal["transport_hash"],
        "provider_identity": proposal["required_provider_identity"],
        "passed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=PROPOSAL_PATH)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect")
    validate = sub.add_parser("validate-authorization")
    validate.add_argument("authorization", type=Path)
    execute = sub.add_parser("execute")
    execute.add_argument("authorization", type=Path)
    execute.add_argument("--env-file", type=Path, default=STAGE2_ENV_FILE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        result = inspect_gate1_calibration(args.proposal)
    elif args.command == "validate-authorization":
        result = validate_gate1_authorization_file(
            args.authorization, args.proposal
        )
    else:
        result = run_gate1_calibration(
            args.authorization,
            proposal_path=args.proposal,
            env_file=args.env_file,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
