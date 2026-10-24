"""One-use, smoke-only v1.1 successor controller for Gate 2.

The completed v1.0 Stage 2 run is immutable and intentionally cannot be
widened or relabeled.  This module creates a fresh v1.1 run and an explicit
cross-version bridge: Stage 2 is capability evidence for the transport, model
identity, and calibration scope, not semantic identity for the new experiment.
The proposal binds the exact current block-0 smoke schedule, v1.1 protocol and
namespace, observed provider identity, locked transport, reviewed pricing rule,
and conservative attempt/token/dollar ceilings.

Inspection and authorization validation never read a credential or initialize
a provider.  Empirical execution additionally requires an external, owner-only
human authorization artifact with a fresh UUID and random nonce.  That artifact
is claimed exactly once in a durable receipt registry before the first
hypothesis-bearing call.  Resume may use the same claim only for the same run.

The controller has no path to preflight, calibration, formation blocks 1+, the
remaining arms, or probes.  It hard-stops after the 152 logical smoke requests
in block 0.  The generic staged executor explicitly rejects successor runs so
it cannot reinterpret the full canonical plan and duplicate Stage 2 calls.
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
import threading
import time
from typing import Any, Mapping
import uuid

from .budget import (
    BudgetExhausted,
    BudgetLedger,
    DispatchReleaseLimits,
    pricing_multipliers,
)
from .common import canonical, digest, utc_now
from .gate2_readiness import (
    LONG_CONTEXT_INPUT_MULTIPLIER,
    LONG_CONTEXT_OUTPUT_MULTIPLIER,
    LONG_CONTEXT_THRESHOLD_INPUT_TOKENS,
    PRICING_REVERIFIED_AT,
    PRICING_SOURCE,
    STAGE2_RUN_DIR,
    verify_stage2_lineage,
)
from .gate1_calibration import verify_gate1_passed_lineage
from .gates import (
    SMOKE_REVIEW_BOOLEAN_FIELDS,
    smoke_integrity_assessment,
    validate_smoke_operator_review,
)
from .live_config import LiveConfig
from .orchestration import (
    KNOWN_NOT_COMPLETED,
    OBSERVATION_STATUSES,
    PERSISTENT_GLOBAL_STOPS,
    PROVIDER_IDENTITY_STATUSES,
    STOP_STATUSES,
    AuthorizationRequiredError,
    DispatchAuthorizationArtifact,
    DurableRun,
    ExecutionContext,
    OrchestrationError,
    ResumeError,
    ScheduledRequest,
    _object_dict,
    _provider_identity_candidate,
    _status_text,
    authorization_binding,
    build_request_plan,
    validate_canonical_plan,
    validate_dispatch_authorization,
)
from .providers.openai import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
)
from .stage2 import (
    STAGE2_ENV_FILE,
    STAGE2_PREFLIGHT_INVENTORY_HASH,
    STAGE2_SDK_VERSION,
    STAGE2_TIMEOUT_SECONDS,
    _load_local_openai_key,
    _stage2_transport_metadata,
)
from .staged_requests import ScientificRequestFactory
from .staged_requests import _SyntheticAuditContext, _synthetic_audit_results
from .visibility import ROOT


CONTROLLER_ID = "gate2_smoke_successor_v1"
AUTHORIZATION_CLAIM_EVENT = "gate2_authorization_claimed"
EXECUTION_MODE_BINDING_EVENT = "gate2_execution_mode_binding"
EXPERIMENT_STARTED_EVENT = "gate2_experiment_started"
SMOKE_COMPLETED_EVENT = "gate2_smoke_completed"
EMPIRICAL_EXECUTION_MODE = "empirical_live"
FAKE_EXECUTION_MODE = "fake_transport_test"
SUCCESSOR_PROTOCOL_VERSION = "v1.1"
SUCCESSOR_RUN_ID = "6abffb64-f71b-4868-89cb-28b0aff0c7e3"
SUCCESSOR_SPLIT = f"pilot/v1.1/gate2-{SUCCESSOR_RUN_ID}"
SUCCESSOR_SEED_NAMESPACE = f"pilot/v1.1/gate2-{SUCCESSOR_RUN_ID}"
AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS = 15 * 60
AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS = 60
PRICING_MAX_AGE_SECONDS = 24 * 60 * 60
PROPOSAL_PATH = ROOT / "configs/gate2_smoke_proposal.json"
AUTHORIZATION_SCHEMA_PATH = ROOT / "release/GATE2_HUMAN_AUTHORIZATION_SCHEMA.json"
SMOKE_REVIEW_DECISION_REPORT = "gate2_smoke_decision.json"
AUTHORIZATION_RECEIPT_DIRECTORY = (
    Path(pwd.getpwuid(os.getuid()).pw_dir)
    / ".collusion-recovery-authorization-receipts"
    / "gate2-v1.1"
)
HUMAN_ACKNOWLEDGEMENT = (
    "I authorize exactly the Gate 2 block-0 smoke scope and exact ceilings "
    "bound by this proposal. I understand the first dispatch starts the "
    "hypothesis-bearing experiment and that no later stage is authorized."
)
_AUTH_FIELDS = {
    "artifact_type",
    "authorization_uuid",
    "authorization_id",
    "authorization_nonce",
    "successor_run_id",
    "successor_run_directory",
    "confirmed_by",
    "confirmed_at",
    "proposal_hash",
    "approved_scope",
    "approved_caps",
    "approved_pricing_contract_hash",
    "approved_transport_hash",
    "approved_provider_identity",
    "approved_protocol_bridge_hash",
    "approved_successor_config_hash",
    "approved_successor_plan_hash",
    "approved_dispatch_authorization_hash",
    "approved_smoke_inventory_hash",
    "allow_paid_calls",
    "human_acknowledgement",
    "authorization_hash",
}
_SMOKE_REVIEW_FIELDS = {
    "artifact_type",
    "review_uuid",
    "reviewer",
    "reviewed_at",
    "notes",
    *SMOKE_REVIEW_BOOLEAN_FIELDS,
    "operator_decision",
    "proposal_hash",
    "gate2_run_id",
    "smoke_report_hash",
    "smoke_completed_event_hash",
    "smoke_completed_at",
    "reviewed_journal_tail",
    "reviewed_journal_event_count",
    "mechanical_assessment_hash",
    "gate1_lineage_hash",
    "review_hash",
}
_SMOKE_INVENTORY_CACHE: dict[str, str] = {}
_SMOKE_INVENTORY_CACHE_LOCK = threading.Lock()
_SMOKE_INVENTORY_CACHE_MAX_ENTRIES = 8
_SMOKE_ASSESSMENT_CACHE: dict[str, str] = {}
_SMOKE_ASSESSMENT_CACHE_LOCK = threading.Lock()
_SMOKE_ASSESSMENT_CACHE_MAX_ENTRIES = 8


class Gate2SuccessorError(RuntimeError):
    """The smoke successor contract, authorization, or durable run is unsafe."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > 16 * 1024 * 1024:
            raise Gate2SuccessorError(f"{label} is unexpectedly large")
        value = json.loads(raw.decode("utf-8"))
    except Gate2SuccessorError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise Gate2SuccessorError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise Gate2SuccessorError(f"{label} must be a JSON object")
    return value


def _write_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
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
    if path.exists():
        existing = _read_json(path, label)
        if canonical(existing) != canonical(dict(value)):
            raise Gate2SuccessorError(f"existing {label} differs from the durable result")
        return
    _write_exclusive_json(path, value)


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise Gate2SuccessorError(f"{label} must be a nonempty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Gate2SuccessorError(f"{label} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise Gate2SuccessorError(f"{label} requires an explicit UTC offset")
    return parsed


def _utc_now_datetime(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise Gate2SuccessorError("authorization clock requires an explicit UTC offset")
    return value.astimezone(timezone.utc)


def _validate_first_claim_window(
        authorization: Mapping[str, Any], proposal: Mapping[str, Any], *,
        now: datetime | None = None) -> None:
    """Reject stale/future unclaimed authorization and stale reviewed pricing."""
    current = _utc_now_datetime(now)
    confirmed = _parse_timestamp(
        authorization.get("confirmed_at"), "confirmed_at"
    ).astimezone(timezone.utc)
    pricing = _validate_pricing_window(proposal, now=current)
    future = timedelta(seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS)
    if confirmed > current + future:
        raise Gate2SuccessorError(
            "human authorization timestamp is implausibly far in the future"
        )
    if confirmed < pricing:
        raise Gate2SuccessorError(
            "human authorization predates the pricing and proposal reverification"
        )
    if current - confirmed > timedelta(
            seconds=AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS):
        raise Gate2SuccessorError(
            "unused human authorization expired before its first claim"
        )


def _validate_pricing_window(
        proposal: Mapping[str, Any], *, now: datetime | None = None) -> datetime:
    current = _utc_now_datetime(now)
    pricing = _parse_timestamp(
        proposal["pricing_contract"]["reverified_at"],
        "pricing reverification",
    ).astimezone(timezone.utc)
    if pricing > current + timedelta(
            seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS):
        raise Gate2SuccessorError(
            "pricing reverification timestamp is implausibly far in the future"
        )
    if current - pricing > timedelta(seconds=PRICING_MAX_AGE_SECONDS):
        raise Gate2SuccessorError(
            "reviewed pricing is stale; regenerate and reauthorize the proposal"
        )
    return pricing


def _scope_from_inventory(smoke: Mapping[str, Any]) -> dict[str, Any]:
    rows = smoke["requests"]
    logical = []
    seen: set[str] = set()
    for row in rows:
        identity = row["identity"]
        if identity not in seen:
            logical.append(identity)
            seen.add(identity)
    return {
        "stage": "smoke",
        "block": 0,
        "stop_after_stage": "smoke",
        "logical_request_count": smoke["logical_request_count"],
        "maximum_dispatch_attempts": smoke["maximum_new_smoke_dispatch_attempts"],
        "logical_identity_hash": digest(logical),
        "permitted_projected_shape_inventory_hash": smoke["inventory_hash"],
        "later_stages_authorized": [],
        "duplicate_stage2_identities_authorized": [],
    }


def _caps_from_inventory(smoke: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "max_dispatch_attempts": smoke["maximum_new_smoke_dispatch_attempts"],
        "max_total_input_tokens": smoke["conservative_input_token_bound_sum"],
        "max_total_output_tokens": smoke["output_token_allowance_sum"],
        "max_cost_usd": smoke["pricing_projection"][
            "strict_all_shapes_reservation_ceiling_usd"
        ],
        "max_in_flight": 1,
        "max_input_tokens_per_request": smoke["longest"][
            "conservative_input_token_bound"
        ],
        "max_output_tokens_per_request": max(
            row["max_output_tokens"] for row in smoke["requests"]
        ),
    }


def _smoke_schedule_projection(plan: Any) -> dict[str, Any]:
    """Normalize a plan's smoke schedule without asserting cross-version equality."""
    value = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
    requests = value["requests"]
    ordinal_by_identity = {
        row["identity"]: row["ordinal"] for row in requests
    }
    checkpoints = {
        row["checkpoint_id"]: {
            "block": row["block"],
            "history": row["history"],
            "contributor_ordinals": [
                ordinal_by_identity[identity] for identity in row["contributors"]
            ],
        }
        for row in value["checkpoints"]
    }
    rows = []
    for row in requests:
        if row["stage"] != "smoke" or row["metadata"].get("block") != 0:
            continue
        parent = row.get("parent_checkpoint")
        rows.append({
            "ordinal": row["ordinal"],
            "stage": row["stage"],
            "phase": row["phase"],
            "action": row["action"],
            "metadata": row["metadata"],
            "dependency_ordinals": [
                ordinal_by_identity[identity] for identity in row["dependencies"]
            ],
            "parent_checkpoint": None if parent is None else checkpoints[parent],
        })
    return {
        "request_count": len(rows),
        "schedule_hash": digest(rows),
        "rows": rows,
    }


def _validate_dedicated_successor_plan(plan: Any) -> None:
    """Require the fixed 152-request block-0-only Gate 2 schedule."""
    requests = list(plan.requests)
    checkpoints = list(plan.checkpoints)
    phase_counts = {
        phase: sum(spec.phase == phase for spec in requests)
        for phase in {spec.phase for spec in requests}
    }
    evaluation_arms = {
        spec.metadata.get("arm") for spec in requests
        if spec.phase == "evaluation"
    }
    if (len(requests) != 152
            or phase_counts != {"formation": 80, "evaluation": 72}
            or any(
                spec.stage != "smoke"
                or spec.metadata.get("block") != 0
                or spec.phase not in {"formation", "evaluation"}
                for spec in requests
            )
            or evaluation_arms != {"A", "B", "F"}
            or len(checkpoints) != 2
            or {checkpoint.history for checkpoint in checkpoints} != {"U", "C"}
            or any(checkpoint.block != 0 for checkpoint in checkpoints)
            or len(plan.fixture_inventory) != 32):
        raise Gate2SuccessorError(
            "successor plan is not the dedicated 152-request block-0 smoke schedule"
        )


def _smoke_inventory_cache_key(config: LiveConfig, plan: Any) -> str:
    """Key rendering inputs while excluding dispatch-only authorization values.

    A human authorization changes the paid-release id and timestamp, but those
    fields cannot affect a scientific request, wire payload, token ceiling, or
    pricing projection.  Keeping the authorization *kind* and paid-call bit in
    the key still prevents a cross-mode reuse.  Canonical-plan validation runs
    before every lookup, so the cache cannot mask source or schedule drift.
    """
    config_value = config.to_dict()
    authorization = config_value.get("authorization", {})
    config_value["authorization"] = {
        "kind": authorization.get("kind"),
        "allow_paid_calls": authorization.get("allow_paid_calls"),
        "dispatch_only_values_excluded": True,
    }
    plan_value = plan.to_dict()
    # RunPlan.config_hash includes the dispatch authorization.  Request and
    # checkpoint identities do not; replace only that derived field with the
    # normalized rendering-config hash used above.
    plan_value["config_hash"] = digest(config_value)
    return digest({
        "config": config_value,
        "plan": plan_value,
        "controller": CONTROLLER_ID,
        "inventory_algorithm": "gate2-smoke-inventory-v1.1-cache-v2",
    })


def _audit_successor_smoke_inventory(config: LiveConfig, plan: Any) -> dict[str, Any]:
    """Render the current v1.1 smoke inventory without comparing it to v1.0."""
    # Always rederive the canonical plan from the current source before a
    # cache lookup.  The cache therefore skips only repeated deterministic
    # rendering, never current-code/config/plan validation.
    try:
        validate_canonical_plan(config, plan)
    except ValueError as exc:
        raise Gate2SuccessorError(
            "successor inventory plan is not canonical for current source"
        ) from exc
    if (config.protocol_version != SUCCESSOR_PROTOCOL_VERSION
            or plan.protocol_version != SUCCESSOR_PROTOCOL_VERSION
            or plan.run_id != SUCCESSOR_RUN_ID):
        raise Gate2SuccessorError("successor inventory requires the explicit v1.1 plan")
    _validate_dedicated_successor_plan(plan)
    cache_key = _smoke_inventory_cache_key(config, plan)
    with _SMOKE_INVENTORY_CACHE_LOCK:
        cached = _SMOKE_INVENTORY_CACHE.get(cache_key)
    if cached is not None:
        value = json.loads(cached)
        value["plan_hash"] = plan.plan_hash
        return value
    factory = ScientificRequestFactory(config, plan)
    synthetic_results = _synthetic_audit_results(config, plan)
    base_context = _SyntheticAuditContext(synthetic_results)
    logical_specs = [
        spec for spec in plan.requests
        if spec.stage == "smoke" and spec.metadata.get("block") == 0
    ]
    rows: list[dict[str, Any]] = []
    for spec in logical_specs:
        contexts = [("initial_attempt", base_context)]
        if spec.action == "message":
            contexts.append((
                "overlength_retry",
                _SyntheticAuditContext(
                    synthetic_results,
                    attempt=1,
                    prior_results=(synthetic_results[spec.identity][0],),
                ),
            ))
        for shape, context in contexts:
            request, rendered = factory.build(spec, context)
            wire = OpenAIResponsesProvider.build_payload(request)
            rows.append({
                "identity": spec.identity,
                "ordinal": spec.ordinal,
                "phase": spec.phase,
                "action": spec.action,
                "metadata": dict(spec.metadata),
                "projected_shape": shape,
                "request_hash": digest(request.to_dict()),
                "prompt_hash": rendered.prompt_hash,
                "wire_payload_hash": digest(wire),
                "wire_payload_utf8_bytes": len(canonical(wire).encode("utf-8")),
                "conservative_input_token_bound": (
                    request.conservative_input_token_bound
                ),
                "max_output_tokens": request.max_output_tokens,
            })
    message_count = sum(spec.action == "message" for spec in logical_specs)
    verdict_count = sum(spec.action == "verdict" for spec in logical_specs)
    if (len(logical_specs), message_count, verdict_count, len(rows)) != (
            152, 76, 76, 228):
        raise Gate2SuccessorError(
            "v1.1 smoke schedule does not match the reviewed 152-request shape"
        )
    longest = max(rows, key=lambda row: row["conservative_input_token_bound"])
    input_sum = sum(row["conservative_input_token_bound"] for row in rows)
    output_sum = sum(row["max_output_tokens"] for row in rows)
    # Transport retries are counted globally per logical identity by
    # ``Gate2SmokeExecutor._status_terminal``.  The counter does not reset
    # when a message crosses from its initial shape to the one allowed
    # overlength-revision shape.  Thus each of the 152 identities contributes
    # at most two known-not-completed transport retries, rather than two per
    # projected shape.
    transport_retry_attempts = (
        len(logical_specs) * config.retry_policy.transport_retries
    )
    strict_cost = Decimal("0")
    nominal_cost = Decimal("0")
    most_expensive_input_rate = max(
        config.pricing.input_usd_per_million,
        config.pricing.cached_input_usd_per_million,
        config.pricing.cache_write_input_usd_per_million,
    )
    for row in rows:
        input_multiplier, output_multiplier = pricing_multipliers(
            config.pricing, row["conservative_input_token_bound"]
        )
        strict_cost += (
            Decimal(row["conservative_input_token_bound"])
            * most_expensive_input_rate * input_multiplier
            + Decimal(row["max_output_tokens"])
            * config.pricing.output_usd_per_million * output_multiplier
        ) / Decimal(1_000_000)
        nominal_cost += (
            Decimal(row["conservative_input_token_bound"])
            * config.pricing.input_usd_per_million * input_multiplier
            + Decimal(row["max_output_tokens"])
            * config.pricing.output_usd_per_million * output_multiplier
        ) / Decimal(1_000_000)
    inventory = {
        "inventory_kind": "v1.1_maximal_synthetic_no_dispatch_projection_v1",
        "projection_only": True,
        "dispatch_performed": False,
        "protocol_version": plan.protocol_version,
        "protocol_hash": plan.protocol_hash,
        "implementation_hash": plan.implementation_hash,
        "plan_hash": plan.plan_hash,
        "schedule_hash": _smoke_schedule_projection(plan)["schedule_hash"],
        "fixture_inventory_hash": digest(dict(plan.fixture_inventory)),
        "logical_request_count": len(logical_specs),
        "message_request_count": message_count,
        "verdict_request_count": verdict_count,
        "maximum_billable_attempt_shapes": len(rows),
        "overlength_retry_shapes": len(rows) - len(logical_specs),
        "maximum_known_not_completed_transport_retry_attempts": (
            transport_retry_attempts
        ),
        "maximum_new_smoke_dispatch_attempts": (
            len(rows) + transport_retry_attempts
        ),
        "inventory_hash": digest(rows),
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
                strict_cost.quantize(
                    Decimal("0.000001"), rounding=ROUND_CEILING
                )
            ),
            "nominal_uncached_all_shapes_ceiling_usd": str(
                nominal_cost.quantize(Decimal("0.000001"))
            ),
        },
        "requests": rows,
    }
    serialized = canonical(inventory)
    with _SMOKE_INVENTORY_CACHE_LOCK:
        if cache_key not in _SMOKE_INVENTORY_CACHE:
            if len(_SMOKE_INVENTORY_CACHE) >= _SMOKE_INVENTORY_CACHE_MAX_ENTRIES:
                _SMOKE_INVENTORY_CACHE.pop(next(iter(_SMOKE_INVENTORY_CACHE)))
            _SMOKE_INVENTORY_CACHE[cache_key] = serialized
    # Return a defensive copy so callers cannot mutate the cached value.
    return json.loads(serialized)


def _projection_config_values(lineage: Mapping[str, Any],
                              limits: Mapping[str, Any]) -> dict[str, Any]:
    values = lineage["config"].to_dict()
    values.update({
        "run_id": SUCCESSOR_RUN_ID,
        "protocol_version": SUCCESSOR_PROTOCOL_VERSION,
        "split": SUCCESSOR_SPLIT,
        "seed_namespace": SUCCESSOR_SEED_NAMESPACE,
        "calibration_namespace": "calibration/v1.1",
        "stage": "smoke",
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


def build_gate2_successor_proposal(
        run_dir: Path = STAGE2_RUN_DIR) -> dict[str, Any]:
    """Build Gate 2 only after exact immutable passed v1.1 Gate 1 lineage."""
    lineage = verify_stage2_lineage(Path(run_dir))
    gate1 = verify_gate1_passed_lineage()
    if (gate1.get("passed") is not True
            or gate1.get("protocol_version") != SUCCESSOR_PROTOCOL_VERSION):
        raise Gate2SuccessorError(
            "Gate 2 requires exact immutable passed selected-version Gate 1 lineage"
        )
    transport = _stage2_transport_metadata(sdk_version=STAGE2_SDK_VERSION)
    if digest(transport) != lineage["transport_hash"]:
        raise Gate2SuccessorError("locked transport differs from completed Stage 2")

    output_cap = lineage["config"].limits.max_output_tokens_per_request
    provisional_limits = {
        "max_requests": 10_000,
        "max_total_input_tokens": (
            lineage["config"].model_context_tokens * 10_000
        ),
        "max_total_output_tokens": output_cap * 10_000,
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
    _validate_dedicated_successor_plan(projection_plan)
    smoke = _audit_successor_smoke_inventory(
        projection_config, projection_plan
    )
    if (smoke["logical_request_count"] != 152
            or smoke["maximum_billable_attempt_shapes"] != 228
            or smoke["maximum_new_smoke_dispatch_attempts"] != 532
            or smoke["within_model_context"] is not True):
        raise Gate2SuccessorError(
            "v1.1 smoke projection differs from the reviewed block-0 scope"
        )

    caps = _caps_from_inventory(smoke)
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
    _validate_dedicated_successor_plan(projection_plan)
    smoke = _audit_successor_smoke_inventory(
        projection_config, projection_plan
    )
    if _caps_from_inventory(smoke) != caps:
        raise Gate2SuccessorError("v1.1 exact caps did not converge")
    scope = _scope_from_inventory(smoke)
    pricing_contract = {
        "source": PRICING_SOURCE,
        "reverified_at": PRICING_REVERIFIED_AT,
        # Bind the executable successor pricing object, including the same
        # fresh reverification timestamp used by its LiveConfig.  The Stage 2
        # timestamp is historical lineage evidence, not current approval.
        "standard_rates_usd_per_million": projection_config.pricing.to_dict(),
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
    template = projection_config.to_dict()
    # Preserve the pre-registered retry policy. Known-not-completed transport
    # failures reconcile to zero usage/cost but still consume one of the exact
    # 532 dispatch-attempt slots.
    template["authorization"] = {
        "kind": "paid",
        "authorization_id": "EXTERNAL_UUID_REQUIRED",
        "approved_at": "EXTERNAL_TIMESTAMP_REQUIRED",
        "approved_budget_usd": caps["max_cost_usd"],
        "allow_paid_calls": True,
    }

    stage2_predecessor_specs = [
        row for row in lineage["plan"]["requests"]
        if row["stage"] in {"preflight", "calibration"}
    ]
    stage2_smoke = _smoke_schedule_projection(lineage["plan"])
    successor_smoke = _smoke_schedule_projection(projection_plan)
    successor_smoke_ids = {
        spec.identity for spec in projection_plan.requests
        if spec.stage == "smoke" and spec.metadata.get("block") == 0
    }
    stage2_ids = {row["identity"] for row in lineage["plan"]["requests"]}
    if successor_smoke_ids & stage2_ids:
        raise Gate2SuccessorError(
            "fresh v1.1 smoke identities overlap completed Stage 2"
        )
    if projection_config.calibration_namespace != "calibration/v1.1":
        raise Gate2SuccessorError(
            "Gate 2 does not bind the selected-version calibration namespace"
        )
    protocol_bridge_body = {
        "artifact_type": "stage2_v1_0_to_gate2_v1_1_bridge_v1",
        "predecessor_role": (
            "completed v1.0 transport/model/calibration capability evidence only; "
            "not semantic identity for the v1.1 experiment"
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
            "smoke_schedule_hash": stage2_smoke["schedule_hash"],
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
            "smoke_schedule_hash": successor_smoke["schedule_hash"],
            "smoke_inventory_hash": smoke["inventory_hash"],
        },
        "changed_dimensions": {
            "protocol_version": [
                lineage["config"].protocol_version,
                projection_plan.protocol_version,
            ],
            "protocol_hash": [
                lineage["plan"]["protocol_hash"],
                projection_plan.protocol_hash,
            ],
            "run_id": [lineage["run_id"], projection_plan.run_id],
            "split": [lineage["config"].split, projection_config.split],
            "seed_namespace": [
                lineage["config"].seed_namespace,
                projection_config.seed_namespace,
            ],
            "config_hash": [
                lineage["config_hash"], digest(projection_config.to_dict()),
            ],
            "plan_hash": [lineage["plan_hash"], projection_plan.plan_hash],
            "implementation_hash": [
                lineage["implementation_hash"],
                projection_plan.implementation_hash,
            ],
            "smoke_schedule_hash": [
                stage2_smoke["schedule_hash"],
                successor_smoke["schedule_hash"],
            ],
        },
        "preserved_dimensions": {
            "source": projection_config.source,
            "provider": projection_config.provider,
            "model": projection_config.model,
            "model_settings": dict(projection_config.model_settings),
            "model_context_tokens": projection_config.model_context_tokens,
            "run_type": projection_config.run_type,
            "blocks": projection_config.blocks,
            "calibration_namespace": projection_config.calibration_namespace,
            "retry_policy": projection_config.retry_policy.to_dict(),
            "transport_hash": lineage["transport_hash"],
            "required_provider_identity": lineage["provider_identity"],
        },
        "schedule_comparison": {
            "v1_0_request_count": stage2_smoke["request_count"],
            "v1_1_request_count": successor_smoke["request_count"],
            "normalized_schedule_equal": (
                stage2_smoke["schedule_hash"]
                == successor_smoke["schedule_hash"]
            ),
        },
    }
    protocol_bridge = {
        **protocol_bridge_body,
        "bridge_hash": digest(protocol_bridge_body),
    }
    body = {
        "artifact_type": "gate_2_smoke_successor_proposal_v1_1",
        "controller": CONTROLLER_ID,
        "prepared_at": PRICING_REVERIFIED_AT,
        "status": "awaiting_external_human_authorization",
        "dispatch_enabled": False,
        "credentials_required_for_inspection": False,
        "experiment_started": False,
        "stage2_lineage": {
            "run_id": lineage["run_id"],
            "config_hash": lineage["config_hash"],
            "plan_hash": lineage["plan_hash"],
            "semantic_plan_hash": lineage["semantic_plan_hash"],
            "implementation_hash_at_stage2": lineage["implementation_hash"],
            "authorization_package_hash": lineage["authorization_package_hash"],
            "report_hash": lineage["report_hash"],
            "journal_tail": lineage["journal_tail"],
            "journal_event_count": lineage["journal_event_count"],
            "dispatch_authorization_hash": lineage["dispatch_authorization_hash"],
            "transport_hash": lineage["transport_hash"],
            "provider_identity": lineage["provider_identity"],
            "protocol_version": lineage["config"].protocol_version,
            "protocol_hash": lineage["plan"]["protocol_hash"],
            "preflight_inventory_hash": STAGE2_PREFLIGHT_INVENTORY_HASH,
            "verified_gates_hash": digest(lineage["gates"]),
        },
        "gate1_passed_lineage": {
            **gate1,
            "lineage_hash": digest(gate1),
        },
        "protocol_bridge": protocol_bridge,
        "current_implementation_hash": projection_plan.implementation_hash,
        "successor_protocol": {
            "protocol_version": projection_plan.protocol_version,
            "protocol_hash": projection_plan.protocol_hash,
            "run_id": projection_plan.run_id,
            "split": projection_config.split,
            "seed_namespace": projection_config.seed_namespace,
            "calibration_namespace": projection_config.calibration_namespace,
            "config_template_hash": digest(template),
            "projection_config_hash": digest(projection_config.to_dict()),
            "projection_plan_hash": projection_plan.plan_hash,
            "fixture_inventory_hash": digest(dict(projection_plan.fixture_inventory)),
            "smoke_schedule_hash": successor_smoke["schedule_hash"],
        },
        "smoke_scope": scope,
        "smoke_projection": {
            key: smoke[key] for key in (
                "inventory_kind",
                "inventory_hash",
                "logical_request_count",
                "message_request_count",
                "verdict_request_count",
                "maximum_billable_attempt_shapes",
                "overlength_retry_shapes",
                "maximum_known_not_completed_transport_retry_attempts",
                "maximum_new_smoke_dispatch_attempts",
                "conservative_input_token_bound_sum",
                "output_token_allowance_sum",
                "long_context_projected_shape_count",
                "longest",
                "within_model_context",
                "pricing_projection",
                "protocol_version",
                "protocol_hash",
                "schedule_hash",
                "fixture_inventory_hash",
            )
        },
        "attempt_accounting": {
            "transport_retry_counter_scope": (
                "logical identity across initial and overlength-revision shapes"
            ),
            "transport_retries_per_logical_identity": (
                projection_config.retry_policy.transport_retries
            ),
            "message_overlength_revisions_per_logical_identity": (
                projection_config.retry_policy.message_overlength_retries
            ),
            "maximum_attempts_per_message_identity": (
                1
                + projection_config.retry_policy.transport_retries
                + projection_config.retry_policy.message_overlength_retries
            ),
            "maximum_attempts_per_verdict_identity": (
                1 + projection_config.retry_policy.transport_retries
            ),
            "message_identity_count": smoke["message_request_count"],
            "verdict_identity_count": smoke["verdict_request_count"],
            "maximum_dispatch_attempts": smoke[
                "maximum_new_smoke_dispatch_attempts"
            ],
            "transport_retry_allowance_resets_after_overlength": False,
            "known_not_completed_transport_retries_reconcile_to_zero": True,
            "ambiguous_or_usage_bearing_transport_failure_is_retried": False,
        },
        "dedicated_plan_scope": {
            "stage2_identity_count": len(stage2_predecessor_specs),
            "stage2_identity_hash": digest([
                row["identity"] for row in stage2_predecessor_specs
            ]),
            "successor_request_count": len(projection_plan.requests),
            "successor_checkpoint_count": len(projection_plan.checkpoints),
            "successor_fixture_count": len(projection_plan.fixture_inventory),
            "successor_stages": ["smoke"],
            "successor_phases": ["formation", "evaluation"],
            "preflight_request_count": 0,
            "calibration_request_count": 0,
            "later_stage_request_count": 0,
        },
        "transport": transport,
        "transport_hash": digest(transport),
        "required_provider_identity": lineage["provider_identity"],
        "pricing_contract": pricing_contract,
        "pricing_contract_hash": digest(pricing_contract),
        "exact_caps": caps,
        "successor_config_template": template,
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
                "run_id": SUCCESSOR_RUN_ID,
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
            "after_stage": "smoke",
            "after_block": 0,
            "status_after_execution": (
                "completed_smoke_awaiting_operator_review_hard_stop"
            ),
            "separate_owner_only_operator_review_required": True,
            "later_stage_dispatch_possible": False,
            "generic_staged_executor_allowed": False,
        },
    }
    return {**body, "proposal_hash": digest(body)}


def validate_gate2_successor_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a proposal against current code and immutable Stage 2 evidence."""
    if not isinstance(value, Mapping):
        raise Gate2SuccessorError("Gate 2 successor proposal must be an object")
    expected = build_gate2_successor_proposal()
    if canonical(dict(value)) != canonical(expected):
        raise Gate2SuccessorError(
            "Gate 2 proposal differs from current code, inventory, or Stage 2 lineage"
        )
    return expected


def load_gate2_successor_proposal(
        path: Path = PROPOSAL_PATH) -> dict[str, Any]:
    return validate_gate2_successor_proposal(_read_json(Path(path), "Gate 2 proposal"))


def gate2_authorization_schema(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON Schema whose constants bind the exact reviewed proposal."""
    proposal = validate_gate2_successor_proposal(proposal)
    run_id_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:collusion-recovery:gate2-human-authorization:v1.1",
        "title": "Gate 2 v1.1 smoke human authorization",
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_AUTH_FIELDS),
        "properties": {
            "artifact_type": {
                "const": "gate_2_smoke_human_authorization_v1_1"
            },
            "authorization_uuid": {
                "type": "string",
                "pattern": run_id_pattern,
            },
            "authorization_id": {
                "type": "string",
                "pattern": r"^gate2-human-[0-9a-f-]{36}$",
            },
            "authorization_nonce": {
                "type": "string",
                "pattern": r"^[0-9a-f]{64}$",
            },
            "successor_run_id": {
                "const": proposal["successor_protocol"]["run_id"]
            },
            "successor_run_directory": {
                "const": (
                    "runs/empirical/gate2-smoke-v1.1-"
                    + proposal["successor_protocol"]["run_id"]
                ),
            },
            "confirmed_by": {"type": "string", "minLength": 1, "maxLength": 200},
            "confirmed_at": {"type": "string", "format": "date-time"},
            "proposal_hash": {"const": proposal["proposal_hash"]},
            "approved_scope": {"const": proposal["smoke_scope"]},
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
            "approved_smoke_inventory_hash": {
                "const": proposal["smoke_projection"]["inventory_hash"]
            },
            "approved_successor_config_hash": {
                "type": "string", "pattern": r"^[0-9a-f]{64}$"
            },
            "approved_successor_plan_hash": {
                "type": "string", "pattern": r"^[0-9a-f]{64}$"
            },
            "approved_dispatch_authorization_hash": {
                "type": "string", "pattern": r"^[0-9a-f]{64}$"
            },
            "allow_paid_calls": {"const": True},
            "human_acknowledgement": {"const": HUMAN_ACKNOWLEDGEMENT},
            "authorization_hash": {
                "type": "string",
                "pattern": r"^[0-9a-f]{64}$",
            },
        },
        "$comment": (
            "authorization_hash is sha256 of canonical JSON after removing "
            "authorization_hash. The UUID, nonce, confirmer, and timestamp must "
            "be supplied externally by the human authorizer. The three derived "
            "successor hashes must match the exact configuration, canonical plan, "
            "and dispatch binding produced from those external values. An unused "
            "authorization expires after 15 minutes and permits at most 60 seconds "
            "of future clock skew."
        ),
    }


def _validate_external_owner_only_file(path: Path, label: str) -> None:
    path = Path(path)
    try:
        resolved = path.resolve(strict=True)
        details = path.lstat()
    except OSError as exc:
        raise Gate2SuccessorError(
            f"{label} is unavailable or invalid"
        ) from exc
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise Gate2SuccessorError(
            f"{label} must be an external file outside the repository"
        )
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise Gate2SuccessorError(
            f"{label} must be a regular non-symlink file"
        )
    if details.st_uid != os.getuid() or details.st_mode & 0o077:
        raise Gate2SuccessorError(
            f"{label} must be owned by the current user and owner-only"
        )


def _validate_external_file(path: Path) -> None:
    _validate_external_owner_only_file(path, "human authorization")


def _authorization_receipt_directory(authorization_path: Path) -> Path:
    """Return the stable registry, independent of authorization-file copies."""
    del authorization_path
    return AUTHORIZATION_RECEIPT_DIRECTORY


def _authorization_receipt_path(receipt_dir: Path) -> Path:
    return Path(receipt_dir) / f"{CONTROLLER_ID}--{SUCCESSOR_RUN_ID}.json"


def _validate_receipt_registry_path(receipt_dir: Path) -> Path:
    value = Path(os.path.abspath(receipt_dir))
    expected = Path(os.path.abspath(AUTHORIZATION_RECEIPT_DIRECTORY))
    if value != expected:
        raise Gate2SuccessorError(
            "authorization receipt registry is not the fixed per-user release registry"
        )
    # The fixed live layout is HOME/.collusion-recovery.../gate2-v1.1.
    # Inspect every existing descendant from that trusted lexical anchor;
    # never resolve away a replaceable symlink before examining it.
    trusted_home = expected.parents[1]
    current = trusted_home
    for part in (None, *value.relative_to(trusted_home).parts):
        if part is not None:
            current = current / part
        if current.is_symlink():
            raise Gate2SuccessorError(
                "authorization receipt registry cannot contain symlink components"
            )
        if current.exists():
            details = current.lstat()
            if (not stat.S_ISDIR(details.st_mode)
                    or details.st_uid != os.getuid()
                    or details.st_mode & 0o022):
                raise Gate2SuccessorError(
                    "authorization receipt registry components must be "
                    "current-user-owned non-writable directories"
                )
    return value


def validate_gate2_human_authorization(
        value: Mapping[str, Any], proposal: Mapping[str, Any], *,
        _proposal_verified: bool = False,
        _verify_derived_contract: bool = True) -> dict[str, Any]:
    if not _proposal_verified:
        proposal = validate_gate2_successor_proposal(proposal)
    if not isinstance(value, Mapping) or set(value) != _AUTH_FIELDS:
        raise Gate2SuccessorError(
            f"human authorization must contain exactly {sorted(_AUTH_FIELDS)}"
        )
    auth = json.loads(canonical(dict(value)))
    body = dict(auth)
    authorization_hash = body.pop("authorization_hash", None)
    if (not isinstance(authorization_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", authorization_hash) is None
            or authorization_hash != digest(body)):
        raise Gate2SuccessorError("human authorization self-hash is invalid")

    try:
        authorization_uuid = uuid.UUID(auth["authorization_uuid"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise Gate2SuccessorError(
            "authorization_uuid must be a lowercase UUIDv4"
        ) from exc
    authorization_uuid_text = str(authorization_uuid)
    if (authorization_uuid.version != 4
            or authorization_uuid_text != auth["authorization_uuid"]):
        raise Gate2SuccessorError(
            "authorization_uuid must be a lowercase UUIDv4"
        )
    if auth["authorization_id"] != f"gate2-human-{authorization_uuid_text}":
        raise Gate2SuccessorError("authorization_id must bind the fresh UUID")
    run_id = proposal["successor_protocol"]["run_id"]
    expected_directory = f"runs/empirical/gate2-smoke-v1.1-{run_id}"
    if auth["successor_run_directory"] != expected_directory:
        raise Gate2SuccessorError("successor run directory must bind the successor UUID")
    nonce = auth["authorization_nonce"]
    if (not isinstance(nonce, str) or re.fullmatch(r"[a-f0-9]{64}", nonce) is None
            or len(set(nonce)) < 8):
        raise Gate2SuccessorError("authorization nonce must be a fresh 64-hex value")
    confirmer = auth["confirmed_by"]
    if (not isinstance(confirmer, str) or not confirmer.strip()
            or len(confirmer) > 200):
        raise Gate2SuccessorError("confirmed_by must name the human authorizer")
    _parse_timestamp(auth["confirmed_at"], "confirmed_at")

    exact = {
        "artifact_type": "gate_2_smoke_human_authorization_v1_1",
        "successor_run_id": run_id,
        "successor_run_directory": expected_directory,
        "proposal_hash": proposal["proposal_hash"],
        "approved_scope": proposal["smoke_scope"],
        "approved_caps": proposal["exact_caps"],
        "approved_pricing_contract_hash": proposal["pricing_contract_hash"],
        "approved_transport_hash": proposal["transport_hash"],
        "approved_provider_identity": proposal["required_provider_identity"],
        "approved_protocol_bridge_hash": proposal["protocol_bridge"][
            "bridge_hash"
        ],
        "approved_smoke_inventory_hash": proposal["smoke_projection"][
            "inventory_hash"
        ],
        "allow_paid_calls": True,
        "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
    }
    if any(auth.get(name) != expected for name, expected in exact.items()):
        raise Gate2SuccessorError(
            "human authorization does not bind the exact smoke scope, identity, pricing, and caps"
        )
    for name in (
            "approved_successor_config_hash",
            "approved_successor_plan_hash",
            "approved_dispatch_authorization_hash"):
        if (not isinstance(auth.get(name), str)
                or re.fullmatch(r"[a-f0-9]{64}", auth[name]) is None):
            raise Gate2SuccessorError(
                "human authorization contains a malformed derived contract hash"
            )
    if _verify_derived_contract:
        config, plan, dispatch, inventory = _successor_config_and_plan(
            proposal,
            auth,
            _proposal_verified=True,
            _authorization_verified=True,
        )
        derived = {
            "approved_successor_config_hash": digest(config.to_dict()),
            "approved_successor_plan_hash": plan.plan_hash,
            "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
            "approved_smoke_inventory_hash": inventory["inventory_hash"],
        }
        if any(auth.get(name) != expected for name, expected in derived.items()):
            raise Gate2SuccessorError(
                "human authorization differs from the exact derived v1.1 contract"
            )
    return auth


def complete_gate2_human_authorization(
        externally_supplied_fields: Mapping[str, Any],
        proposal: Mapping[str, Any], *,
        _proposal_verified: bool = False) -> dict[str, Any]:
    """Bind human-supplied approval fields to the exact derived v1.1 hashes.

    This is deterministic packaging, not approval: the caller must supply the
    fresh authorization UUID, random nonce, confirmer, timestamp, exact scope,
    caps, bridge, pricing, transport, provider identity, and acknowledgement.
    The resulting artifact is still inert unless saved owner-only outside the
    repository and atomically claimed within its short validity window.
    """
    proposal = (dict(proposal) if _proposal_verified
                else validate_gate2_successor_proposal(proposal))
    derived_names = {
        "approved_successor_config_hash",
        "approved_successor_plan_hash",
        "approved_dispatch_authorization_hash",
        "authorization_hash",
    }
    required = _AUTH_FIELDS - derived_names
    if (not isinstance(externally_supplied_fields, Mapping)
            or set(externally_supplied_fields) != required):
        raise Gate2SuccessorError(
            "authorization packaging requires exactly the external human fields"
        )
    draft = json.loads(canonical(dict(externally_supplied_fields)))
    draft.update({name: "0" * 64 for name in derived_names - {"authorization_hash"}})
    draft["authorization_hash"] = digest({
        key: value for key, value in draft.items()
        if key != "authorization_hash"
    })
    envelope = validate_gate2_human_authorization(
        draft,
        proposal,
        _proposal_verified=True,
        _verify_derived_contract=False,
    )
    config, plan, dispatch, _inventory = _successor_config_and_plan(
        proposal,
        envelope,
        _proposal_verified=True,
        _authorization_verified=True,
    )
    completed = dict(envelope)
    completed.update({
        "approved_successor_config_hash": digest(config.to_dict()),
        "approved_successor_plan_hash": plan.plan_hash,
        "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
    })
    body = dict(completed)
    body.pop("authorization_hash", None)
    completed["authorization_hash"] = digest(body)
    return validate_gate2_human_authorization(
        completed,
        proposal,
        _proposal_verified=True,
        _verify_derived_contract=False,
    )


def load_gate2_human_authorization(
        path: Path, proposal: Mapping[str, Any], *,
        require_external: bool = True,
        _proposal_verified: bool = False) -> dict[str, Any]:
    path = Path(path)
    if require_external:
        _validate_external_file(path)
    return validate_gate2_human_authorization(
        _read_json(path, "Gate 2 human authorization"), proposal,
        _proposal_verified=_proposal_verified,
    )


def _successor_config_and_plan(
        proposal: Mapping[str, Any], authorization: Mapping[str, Any],
        *, _proposal_verified: bool = False,
        _authorization_verified: bool = False,
        ) -> tuple[LiveConfig, Any, DispatchAuthorizationArtifact, dict[str, Any]]:
    if not _proposal_verified:
        proposal = validate_gate2_successor_proposal(proposal)
    auth = (json.loads(canonical(dict(authorization)))
            if _authorization_verified else
            validate_gate2_human_authorization(
                authorization,
                proposal,
                _proposal_verified=True,
                _verify_derived_contract=False,
            ))
    values = json.loads(canonical(proposal["successor_config_template"]))
    if digest(values) != proposal["successor_protocol"]["config_template_hash"]:
        raise Gate2SuccessorError("successor configuration template hash differs")
    values["authorization"] = {
        "kind": "paid",
        "authorization_id": auth["authorization_id"],
        "approved_at": auth["confirmed_at"],
        "approved_budget_usd": proposal["exact_caps"]["max_cost_usd"],
        "allow_paid_calls": True,
    }
    config = LiveConfig.from_mapping(values)
    plan = build_request_plan(config)
    _validate_dedicated_successor_plan(plan)
    successor_protocol = proposal["successor_protocol"]
    if (config.protocol_version != SUCCESSOR_PROTOCOL_VERSION
            or config.run_id != successor_protocol["run_id"]
            or config.split != successor_protocol["split"]
            or config.seed_namespace != successor_protocol["seed_namespace"]
            or plan.protocol_version != successor_protocol["protocol_version"]
            or plan.protocol_hash != successor_protocol["protocol_hash"]
            or plan.implementation_hash != proposal["current_implementation_hash"]
            or digest(dict(plan.fixture_inventory))
            != successor_protocol["fixture_inventory_hash"]
            or _smoke_schedule_projection(plan)["schedule_hash"]
            != successor_protocol["smoke_schedule_hash"]):
        raise Gate2SuccessorError("current implementation differs from the proposal")
    successor_inventory = _audit_successor_smoke_inventory(config, plan)
    expected_projection = proposal["smoke_projection"]
    if (successor_inventory["inventory_hash"] != expected_projection["inventory_hash"]
            or successor_inventory["logical_request_count"] != 152
            or successor_inventory["maximum_billable_attempt_shapes"] != 228
            or successor_inventory["within_model_context"] is not True):
        raise Gate2SuccessorError(
            "successor configuration does not render the exact authorized smoke inventory"
        )
    scope = proposal["dedicated_plan_scope"]
    if (scope.get("successor_request_count") != len(plan.requests)
            or scope.get("successor_checkpoint_count") != len(plan.checkpoints)
            or scope.get("successor_fixture_count") != len(plan.fixture_inventory)
            or scope.get("preflight_request_count") != 0
            or scope.get("calibration_request_count") != 0
            or scope.get("later_stage_request_count") != 0):
        raise Gate2SuccessorError("dedicated successor plan scope differs")
    smoke_ids = []
    seen: set[str] = set()
    for row in successor_inventory["requests"]:
        if row["identity"] not in seen:
            smoke_ids.append(row["identity"])
            seen.add(row["identity"])
    if digest(smoke_ids) != proposal["smoke_scope"]["logical_identity_hash"]:
        raise Gate2SuccessorError("smoke identity inventory differs")

    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    binding = authorization_binding(
        config,
        plan,
        confirmed_at=auth["confirmed_at"],
        transport_hash=proposal["transport_hash"],
        approved_budget_usd=proposal["exact_caps"]["max_cost_usd"],
        max_stage="smoke",
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
        max_stage="smoke",
        max_block=0,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
    )
    validate_dispatch_authorization(config, plan, dispatch, require_initial=True)
    if not _authorization_verified:
        derived = {
            "approved_successor_config_hash": digest(config.to_dict()),
            "approved_successor_plan_hash": plan.plan_hash,
            "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
            "approved_smoke_inventory_hash": successor_inventory[
                "inventory_hash"
            ],
        }
        if any(auth.get(name) != expected for name, expected in derived.items()):
            raise Gate2SuccessorError(
                "human authorization differs from the exact derived v1.1 contract"
            )
    return config, plan, dispatch, successor_inventory


def _binding_event_value(
        proposal: Mapping[str, Any], authorization: Mapping[str, Any],
        config: LiveConfig, plan: Any,
        dispatch: DispatchAuthorizationArtifact) -> dict[str, Any]:
    return {
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": authorization["authorization_hash"],
        "successor_run_id": authorization["successor_run_id"],
        "stage2_report_hash": proposal["stage2_lineage"]["report_hash"],
        "stage2_authorization_package_hash": proposal["stage2_lineage"][
            "authorization_package_hash"
        ],
        "stage2_journal_tail": proposal["stage2_lineage"]["journal_tail"],
        "stage2_event_count": proposal["stage2_lineage"]["journal_event_count"],
        "gate1_lineage_hash": proposal["gate1_passed_lineage"]["lineage_hash"],
        "gate1_passed": proposal["gate1_passed_lineage"]["passed"],
        "gate1_assessment_hash": proposal["gate1_passed_lineage"][
            "assessment_hash"
        ],
        "gate1_report_hash": proposal["gate1_passed_lineage"]["report_hash"],
        "gate1_decision_event_hash": proposal["gate1_passed_lineage"][
            "gate_decision_event_hash"
        ],
        "gate1_authorization_claim_event_hash": proposal[
            "gate1_passed_lineage"
        ]["authorization_claim_event_hash"],
        "protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "successor_protocol_version": proposal["successor_protocol"][
            "protocol_version"
        ],
        "successor_protocol_hash": proposal["successor_protocol"][
            "protocol_hash"
        ],
        "smoke_inventory_hash": proposal["smoke_projection"]["inventory_hash"],
        "implementation_hash": proposal["current_implementation_hash"],
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "transport_hash": proposal["transport_hash"],
        "required_provider_identity": proposal["required_provider_identity"],
        "pricing_contract_hash": proposal["pricing_contract_hash"],
        "exact_caps": proposal["exact_caps"],
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "scope": proposal["smoke_scope"],
    }


def _validated_successor_run_path(
        run_dir: Path, authorization: Mapping[str, Any], *,
        workspace_root: Path | None = None) -> tuple[Path, bool]:
    """Return the exact non-symlinked authorized path and existence flag."""
    run_path = Path(run_dir)
    root = ROOT if workspace_root is None else Path(workspace_root)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise Gate2SuccessorError(
            "successor workspace root must already exist"
        ) from exc
    if not root_resolved.is_dir():
        raise Gate2SuccessorError("successor workspace root is not a directory")
    relative = Path(authorization["successor_run_directory"])
    if (relative.is_absolute() or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise Gate2SuccessorError(
            "successor run directory must be a safe workspace-relative path"
        )
    root_lexical = Path(os.path.abspath(root))
    expected_path = root_lexical.joinpath(*relative.parts)
    if Path(os.path.abspath(run_path)) != expected_path:
        raise Gate2SuccessorError("successor run path differs from the human authorization")
    try:
        expected_path.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise Gate2SuccessorError(
            "successor run path resolves outside the authorized workspace"
        ) from exc
    current = root_lexical
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise Gate2SuccessorError(
                "successor run path cannot contain symlink components"
            )
    exists = run_path.exists()
    if exists and not run_path.is_dir():
        raise Gate2SuccessorError("successor run path exists but is not a directory")
    return run_path, exists


def prepare_gate2_successor_run(
        run_dir: Path, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], *,
        workspace_root: Path | None = None,
        _proposal_verified: bool = False) -> tuple[
            DurableRun, LiveConfig, Any, DispatchAuthorizationArtifact,
            dict[str, Any]
        ]:
    """Create or exactly resume a successor run without provider or credential I/O."""
    config, plan, dispatch, inventory = _successor_config_and_plan(
        proposal, authorization, _proposal_verified=_proposal_verified
    )
    run_path, exists = _validated_successor_run_path(
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
            if event.get("kind") == "gate2_successor_binding"
        ]
        if len(existing) > 1:
            raise ResumeError("successor binding was recorded more than once")
        created_binding = not existing
        if existing:
            durable = {key: existing[0].get(key) for key in binding}
            if canonical(durable) != canonical(binding):
                raise ResumeError("durable successor binding differs")
            nonce = existing[0].get("run_instance_nonce")
            if (not isinstance(nonce, str)
                    or re.fullmatch(r"[a-f0-9]{64}", nonce) is None):
                raise ResumeError("durable successor run-instance nonce is invalid")
        else:
            nonce = secrets.token_hex(32)
            run.append(
                "gate2_successor_binding",
                **binding,
                run_instance_nonce=nonce,
            )
        prepared_fields = {
            "execution_controller": CONTROLLER_ID,
            "gate2_proposal_hash": proposal["proposal_hash"],
            "gate2_human_authorization_hash": authorization["authorization_hash"],
            "gate2_successor_run_id": authorization["successor_run_id"],
            "gate2_run_instance_hash": digest(nonce),
            "required_provider_identity": proposal["required_provider_identity"],
            "predecessor_dispatch_forbidden": True,
            "later_stage_dispatch_forbidden": True,
        }
        missing_prepared_fields = {
            name for name in prepared_fields if name not in run.manifest
        }
        mismatched_prepared_fields = {
            name for name, expected in prepared_fields.items()
            if name in run.manifest and run.manifest[name] != expected
        }
        if mismatched_prepared_fields:
            raise ResumeError("durable successor manifest binding differs")
        if created_binding or missing_prepared_fields:
            # The only recoverable partial state is a crash after the binding
            # event was fsynced but before the initial prepared manifest write.
            if (not created_binding
                    and run.manifest.get("status") != "created"):
                raise ResumeError("durable successor manifest binding is incomplete")
            run.set_status(
                "prepared_gate2_successor",
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


def _gate2_claim_event_fields(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any],
        receipt: Mapping[str, Any]) -> dict[str, Any]:
    bindings = [
        event for event in run.events
        if event.get("kind") == "gate2_successor_binding"
    ]
    if len(bindings) != 1:
        raise Gate2SuccessorError(
            "durable successor run-instance binding is absent"
        )
    body = dict(receipt)
    receipt_hash = body.pop("receipt_hash", None)
    if (not isinstance(receipt_hash, str)
            or receipt_hash != digest(body)):
        raise Gate2SuccessorError(
            "authorization receipt self-hash is invalid"
        )
    return {
        "controller": CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "receipt_hash": receipt_hash,
        "receipt_claimed_at": body.get("claimed_at"),
        "successor_run_id": authorization["successor_run_id"],
        "run_directory": str(run.path.resolve()),
        "run_instance_event_hash": bindings[0]["event_hash"],
        "run_instance_hash": digest(bindings[0]["run_instance_nonce"]),
    }


def _require_gate2_claim_event(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any],
        receipt: Mapping[str, Any]) -> dict[str, Any]:
    expected = _gate2_claim_event_fields(
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
                    EXECUTION_MODE_BINDING_EVENT,
                    EXPERIMENT_STARTED_EVENT,
                    "reservation",
                    "result",
                }
                and event.get("sequence", -1) < claims[0].get("sequence", -1)
                for event in run.events
            )):
        raise Gate2SuccessorError(
            "a singular journal-bound Gate 2 authorization claim is required"
        )
    _parse_timestamp(
        claims[0].get("receipt_claimed_at"),
        "authorization claim receipt_claimed_at",
    )
    return claims[0]


def claim_gate2_authorization_once(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], *,
        receipt_dir: Path,
        now: datetime | None = None) -> dict[str, Any]:
    """Claim an external authorization once, allowing only same-run resume."""
    if run.manifest.get("execution_controller") != CONTROLLER_ID:
        raise Gate2SuccessorError("successor controller marker is absent")
    if (run.manifest.get("gate2_proposal_hash") != proposal["proposal_hash"]
            or run.manifest.get("gate2_human_authorization_hash")
            != authorization["authorization_hash"]
            or run.manifest.get("gate2_successor_run_id")
            != authorization["successor_run_id"]):
        raise Gate2SuccessorError(
            "durable run differs from the authorization being claimed"
        )
    receipt_dir = _validate_receipt_registry_path(Path(receipt_dir))
    try:
        receipt_dir.resolve(strict=False).relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise Gate2SuccessorError(
            "authorization receipt registry must remain outside the repository"
        )
    if receipt_dir.exists():
        details = receipt_dir.lstat()
        if (not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode)
                or details.st_uid != os.getuid() or details.st_mode & 0o077):
            raise Gate2SuccessorError(
                "authorization receipt registry must be an owner-only directory"
            )
    else:
        receipt_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        _validate_receipt_registry_path(receipt_dir)
        details = receipt_dir.lstat()
        if details.st_mode & 0o077:
            raise Gate2SuccessorError(
                "authorization receipt registry was not created owner-only"
            )
    receipt_path = _authorization_receipt_path(receipt_dir)
    bindings = [
        event for event in run.events
        if event.get("kind") == "gate2_successor_binding"
    ]
    if len(bindings) != 1:
        raise Gate2SuccessorError("durable successor run-instance binding is absent")
    expected = {
        "artifact_type": "gate_2_authorization_consumption_receipt_v1_1",
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "proposal_hash": proposal["proposal_hash"],
        "protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "successor_run_id": authorization["successor_run_id"],
        "run_directory": str(run.path.resolve()),
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "dispatch_authorization_hash": run.manifest[
            "dispatch_authorization_hash"
        ],
        "run_instance_event_hash": bindings[0]["event_hash"],
        "run_instance_hash": digest(bindings[0]["run_instance_nonce"]),
    }
    with run.execution_lock():
        if receipt_path.exists():
            receipt_details = receipt_path.lstat()
            if (not stat.S_ISREG(receipt_details.st_mode)
                    or stat.S_ISLNK(receipt_details.st_mode)
                    or receipt_details.st_uid != os.getuid()
                    or receipt_details.st_mode & 0o077):
                raise Gate2SuccessorError(
                    "authorization receipt must be an owner-only regular file"
                )
            receipt = _read_json(receipt_path, "Gate 2 authorization receipt")
            body = dict(receipt)
            receipt_hash = body.pop("receipt_hash", None)
            if (set(body) != set(expected) | {"claimed_at"}
                    or receipt_hash != digest(body)
                    or any(body.get(key) != value
                           for key, value in expected.items())):
                raise Gate2SuccessorError(
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
                    receipt_path, "Gate 2 authorization receipt"
                )
                body = dict(receipt)
                receipt_hash = body.pop("receipt_hash", None)
                if (set(body) != set(expected) | {"claimed_at"}
                        or receipt_hash != digest(body)
                        or any(body.get(key) != value
                               for key, value in expected.items())):
                    raise Gate2SuccessorError(
                        "authorization was already claimed by a different or invalid run"
                    )
                _parse_timestamp(
                    body.get("claimed_at"),
                    "authorization receipt claimed_at",
                )
        claim_fields = _gate2_claim_event_fields(
            run, proposal, authorization, receipt
        )
        claims = [
            event for event in run.events
            if event.get("kind") == AUTHORIZATION_CLAIM_EVENT
        ]
        if len(claims) > 1:
            raise Gate2SuccessorError(
                "Gate 2 authorization claim is not singular"
            )
        if claims:
            if any(
                    claims[0].get(key) != value
                    for key, value in claim_fields.items()):
                raise Gate2SuccessorError(
                    "durable Gate 2 authorization claim differs from receipt"
                )
        else:
            if any(
                    event.get("kind") in {
                        EXECUTION_MODE_BINDING_EVENT,
                        EXPERIMENT_STARTED_EVENT,
                        "reservation",
                        "result",
                    }
                    for event in run.events):
                raise Gate2SuccessorError(
                    "Gate 2 dispatch exists before authorization was claimed"
                )
            run.append(AUTHORIZATION_CLAIM_EVENT, **claim_fields)
        claim = _require_gate2_claim_event(
            run, proposal, authorization, receipt
        )
        if (run.manifest.get("gate2_authorization_consumed") is not True
                or run.manifest.get("gate2_authorization_receipt_hash")
                != receipt["receipt_hash"]
                or run.manifest.get("gate2_authorization_claim_event_hash")
                != claim["event_hash"]):
            run.set_status(
                run.manifest["status"],
                gate2_authorization_consumed=True,
                gate2_authorization_receipt_hash=receipt["receipt_hash"],
                gate2_authorization_claim_event_hash=claim["event_hash"],
            )
        return receipt


def _gate2_smoke_report_value(
        run: DurableRun, proposal: Mapping[str, Any],
        budget_snapshot: Mapping[str, Any], mode: Mapping[str, Any],
        experiment_start: Mapping[str, Any] | None,
        completion: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the immutable execution report from durable pre-review evidence."""
    selected = [
        spec for spec in run.plan.requests
        if spec.stage == "smoke" and spec.metadata.get("block") == 0
    ]
    selected_ids = {spec.identity for spec in selected}
    attempts = [
        row for row in run.result_events()
        if row.get("identity") in selected_ids
    ]
    execution_mode = mode.get("mode")
    if execution_mode not in {EMPIRICAL_EXECUTION_MODE, FAKE_EXECUTION_MODE}:
        raise Gate2SuccessorError("Gate 2 execution mode is invalid")
    body = {
        "artifact_type": "gate_2_smoke_hard_stop_report_v1_1",
        "controller": CONTROLLER_ID,
        "status": "completed_smoke_awaiting_operator_review_hard_stop",
        "source": (
            "fake-transport-test"
            if execution_mode == FAKE_EXECUTION_MODE else "empirical-live"
        ),
        "warning": (
            "FAKE TRANSPORT TEST OUTPUT. NOT EMPIRICAL MODEL EVIDENCE."
            if execution_mode == FAKE_EXECUTION_MODE else None
        ),
        "execution_mode": execution_mode,
        "execution_mode_event_hash": mode["event_hash"],
        "experiment_started": experiment_start is not None,
        "experiment_start_event_hash": (
            None if experiment_start is None else experiment_start["event_hash"]
        ),
        "completed_stage": "smoke",
        "completed_block": 0,
        "completed_at": completion["recorded_at"],
        "smoke_completed_event_hash": completion["event_hash"],
        "logical_requests": len(selected),
        "dispatch_attempts": len(attempts),
        "preflight_dispatches": 0,
        "calibration_dispatches": 0,
        "later_stage_dispatches": 0,
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": run.manifest[
            "gate2_human_authorization_hash"
        ],
        "authorization_receipt_hash": run.manifest[
            "gate2_authorization_receipt_hash"
        ],
        "authorization_claim_event_hash": run.manifest[
            "gate2_authorization_claim_event_hash"
        ],
        "stage2_lineage": proposal["stage2_lineage"],
        "gate1_passed_lineage": proposal["gate1_passed_lineage"],
        "protocol_bridge": proposal["protocol_bridge"],
        "protocol_version": run.plan.protocol_version,
        "protocol_hash": run.plan.protocol_hash,
        "smoke_inventory_hash": proposal["smoke_projection"]["inventory_hash"],
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "implementation_hash": run.plan.implementation_hash,
        "transport_hash": proposal["transport_hash"],
        "provider_identity": proposal["required_provider_identity"],
        "pricing_contract": proposal["pricing_contract"],
        "budget": json.loads(canonical(dict(budget_snapshot))),
        # The completion event is the immutable pre-review boundary.  Later
        # operator review cannot change the execution report's journal anchor.
        "journal_tail": completion["event_hash"],
        "journal_event_count": completion["sequence"] + 1,
        "next_stage_authorized": False,
    }
    return {**body, "report_hash": digest(body)}


class Gate2SmokeExecutor:
    """Durable executor whose selection is exactly smoke/block 0."""

    def __init__(self, run: DurableRun, provider: OpenAIResponsesProvider,
                 proposal: Mapping[str, Any], inventory: Mapping[str, Any], *,
                 authorization_receipt: Mapping[str, Any],
                 fake_transport_test: bool = False,
                 sleep=None):
        if run.manifest.get("execution_controller") != CONTROLLER_ID:
            raise Gate2SuccessorError("run is not marked for the Gate 2 successor")
        receipt = dict(authorization_receipt)
        receipt_hash = receipt.pop("receipt_hash", None)
        if (not isinstance(receipt_hash, str)
                or receipt_hash != digest(receipt)
                or receipt.get("artifact_type")
                != "gate_2_authorization_consumption_receipt_v1_1"
                or receipt.get("proposal_hash") != proposal["proposal_hash"]
                or receipt.get("authorization_hash")
                != run.manifest.get("gate2_human_authorization_hash")
                or receipt.get("run_directory") != str(run.path.resolve())
                or run.manifest.get("gate2_authorization_consumed") is not True
                or run.manifest.get("gate2_authorization_receipt_hash")
                != receipt_hash):
            raise Gate2SuccessorError(
                "a durable one-use authorization receipt is required"
            )
        claim = _require_gate2_claim_event(
            run, proposal, {
                "authorization_uuid": receipt["authorization_uuid"],
                "authorization_hash": receipt["authorization_hash"],
                "successor_run_id": receipt["successor_run_id"],
            }, authorization_receipt,
        )
        if (run.manifest.get("gate2_authorization_claim_event_hash")
                != claim["event_hash"]):
            raise Gate2SuccessorError(
                "Gate 2 authorization claim manifest is not journal-derived"
            )
        if not isinstance(provider, OpenAIResponsesProvider):
            raise Gate2SuccessorError("successor requires the OpenAI Responses adapter")
        if not fake_transport_test:
            transport = getattr(provider, "transport", None)
            if not isinstance(transport, OpenAISDKTransport):
                raise Gate2SuccessorError(
                    "empirical successor requires the locked official SDK transport"
                )
            if (transport.audit_metadata != proposal["transport"]
                    or digest(transport.audit_metadata) != proposal["transport_hash"]):
                raise Gate2SuccessorError("provider transport differs from the proposal")
        self.run = run
        self.provider = provider
        self.proposal = proposal
        expected_inventory = _audit_successor_smoke_inventory(
            run.config, run.plan
        )
        if (canonical(dict(inventory)) != canonical(expected_inventory)
                or expected_inventory["inventory_hash"]
                != proposal["smoke_projection"]["inventory_hash"]):
            raise Gate2SuccessorError(
                "runtime smoke inventory differs from the authorized proposal"
            )
        self.inventory = expected_inventory
        # Bind the constructor choice once.  A mutable public testing flag must
        # never be able to relabel fake execution as empirical evidence after
        # the official-transport check above.
        self._execution_mode = (
            FAKE_EXECUTION_MODE if fake_transport_test
            else EMPIRICAL_EXECUTION_MODE
        )
        self.fake_transport_test = fake_transport_test
        self.sleep = sleep or ((lambda _seconds: None)
                               if fake_transport_test else time.sleep)
        self.factory = ScientificRequestFactory(run.config, run.plan)
        self.rows = {
            (row["identity"], row["projected_shape"]): row
            for row in expected_inventory["requests"]
        }
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
        selected = [
            spec for spec in self.run.plan.requests
            if spec.stage == "smoke" and spec.metadata.get("block") == 0
        ]
        if (len(selected) != 152
                or digest([spec.identity for spec in selected])
                != self.proposal["smoke_scope"]["logical_identity_hash"]):
            raise Gate2SuccessorError("runtime smoke selection differs from the proposal")
        return selected

    def _validate_exact_scope_history(self) -> None:
        allowed = {spec.identity for spec in self._selected()}
        for event in self.run.events:
            if event.get("kind") in {"reservation", "result"}:
                if event.get("identity") not in allowed:
                    raise ResumeError(
                        "successor journal contains predecessor or later-stage dispatch"
                    )

    def _bind_execution_mode(self) -> dict[str, Any]:
        expected_mode = self._execution_mode
        bindings = [
            event for event in self.run.events
            if event.get("kind") == EXECUTION_MODE_BINDING_EVENT
        ]
        if len(bindings) > 1:
            raise ResumeError("Gate 2 execution mode binding is not singular")
        if bindings:
            binding = bindings[0]
            if (binding.get("controller") != CONTROLLER_ID
                    or binding.get("proposal_hash")
                    != self.proposal["proposal_hash"]
                    or binding.get("run_id") != SUCCESSOR_RUN_ID
                    or binding.get("mode") != expected_mode):
                raise ResumeError("Gate 2 execution mode changed on resume")
            return binding
        if any(
                event.get("kind") in {
                    EXPERIMENT_STARTED_EVENT, "reservation", "result"
                }
                for event in self.run.events):
            raise ResumeError(
                "Gate 2 dispatch exists without a prior execution mode binding"
            )
        claims = [
            event for event in self.run.events
            if event.get("kind") == AUTHORIZATION_CLAIM_EVENT
        ]
        if len(claims) != 1:
            raise ResumeError(
                "Gate 2 execution mode cannot precede authorization claim"
            )
        return self.run.append(
            EXECUTION_MODE_BINDING_EVENT,
            controller=CONTROLLER_ID,
            proposal_hash=self.proposal["proposal_hash"],
            run_id=SUCCESSOR_RUN_ID,
            mode=expected_mode,
            authorization_claim_event_hash=claims[0]["event_hash"],
        )

    def _ensure_experiment_start(
            self, reservation_event: Mapping[str, Any] | None = None
            ) -> dict[str, Any] | None:
        """Bind the empirical experiment boundary before provider invocation."""
        mode = self._bind_execution_mode()
        starts = [
            event for event in self.run.events
            if event.get("kind") == EXPERIMENT_STARTED_EVENT
        ]
        if len(starts) > 1:
            raise ResumeError("Gate 2 experiment-start binding is not singular")
        if self._execution_mode == FAKE_EXECUTION_MODE:
            if (starts
                    or self.run.manifest.get("experiment_started") is True
                    or self.run.manifest.get("experiment_start_event_hash")
                    is not None):
                raise ResumeError(
                    "fake Gate 2 execution cannot be empirical experiment evidence"
                )
            return None
        reservations = [
            event for event in self.run.events
            if event.get("kind") == "reservation"
        ]
        first = reservations[0] if reservations else None
        if reservation_event is not None:
            if (reservation_event.get("kind") != "reservation"
                    or first is None
                    or reservation_event.get("event_hash")
                    != first.get("event_hash")):
                if starts:
                    first = reservations[0]
                else:
                    raise ResumeError(
                        "Gate 2 experiment start must bind the first reservation"
                    )
        if first is None:
            if (starts
                    or self.run.manifest.get("experiment_started") is True
                    or self.run.manifest.get("experiment_start_event_hash")
                    is not None):
                raise ResumeError(
                    "Gate 2 experiment-start marker has no reservation"
                )
            return None
        expected = {
            "controller": CONTROLLER_ID,
            "proposal_hash": self.proposal["proposal_hash"],
            "run_id": SUCCESSOR_RUN_ID,
            "execution_mode_event_hash": mode["event_hash"],
            "first_reservation_event_hash": first["event_hash"],
            "identity": first["identity"],
            "attempt": first["attempt"],
            "request_hash": first["request_hash"],
            "wire_payload_hash": first["wire_payload_hash"],
        }
        if starts:
            start = starts[0]
            if (any(start.get(key) != value
                    for key, value in expected.items())
                    or start.get("sequence", -1)
                    <= first.get("sequence", -1)
                    or any(
                        event.get("kind") == "result"
                        and event.get("sequence", -1)
                        < start.get("sequence", -1)
                        for event in self.run.events
                    )):
                raise ResumeError(
                    "Gate 2 experiment-start binding differs from first dispatch"
                )
        else:
            start = self.run.append(
                EXPERIMENT_STARTED_EVENT,
                **expected,
                recovered_before_ambiguous_reconciliation=(
                    reservation_event is None
                ),
            )
        if (self.run.manifest.get("experiment_started") is not True
                or self.run.manifest.get("experiment_start_event_hash")
                != start["event_hash"]):
            self.run.set_status(
                self.run.manifest["status"],
                experiment_started=True,
                experiment_start_event_hash=start["event_hash"],
            )
        return start

    def _status_terminal(self, spec: ScheduledRequest,
                         results: list[dict[str, Any]]) -> bool:
        if not results:
            return False
        status = results[-1]["status"]
        if status in OBSERVATION_STATUSES or status in STOP_STATUSES:
            return True
        if status == "overlength":
            count = sum(row["status"] == status for row in results)
            return spec.action != "message" or count > 1
        if status == "retryable_error":
            # This count deliberately spans both message content shapes.  A
            # prior overlength result may change the visible request, but it
            # never replenishes the identity's transport-retry allowance.
            count = sum(row["status"] == status for row in results)
            return count > self.run.config.retry_policy.transport_retries
        raise Gate2SuccessorError(f"unknown provider status: {status}")

    def _resolved(self, identity: str) -> bool:
        spec = next(
            request for request in self.run.plan.requests
            if request.identity == identity
        )
        results = self.run.result_events(identity)
        status = results[-1]["status"] if results else None
        # Only actual observation statuses satisfy dependencies.  A terminal
        # retry/overlength outcome merely means its retry allowance is
        # exhausted; it is never scientific evidence.
        return (self._status_terminal(spec, results)
                and status in OBSERVATION_STATUSES)

    def _hard_stop(self, spec: ScheduledRequest) -> str | None:
        results = self.run.result_events(spec.identity)
        if not results:
            return None
        status = results[-1]["status"]
        if status in PERSISTENT_GLOBAL_STOPS or status == "ambiguous_timeout":
            return f"paused_{status}"
        if (status == "retryable_error"
                and self._status_terminal(spec, results)):
            return "paused_retry_exhausted"
        if (status == "overlength"
                and self._status_terminal(spec, results)):
            return "paused_overlength_exhausted"
        return None

    def _recover_unmatched_as_ambiguous(self) -> None:
        reservations = {
            event["reservation"]["reservation_id"]: event
            for event in self.run.events if event.get("kind") == "reservation"
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

    def _materialize_checkpoints(self) -> None:
        existing = self.run.checkpoint_events()
        for checkpoint in self.run.plan.checkpoints:
            if checkpoint.block != 0:
                continue
            rows = []
            for identity in checkpoint.contributors:
                results = self.run.result_events(identity)
                if not results or not self._resolved(identity):
                    break
                rows.append(results[-1])
            else:
                result_hashes = [digest(row) for row in rows]
                checkpoint_hash = digest({
                    "spec": checkpoint.to_dict(),
                    "result_hashes": result_hashes,
                })
                prior = existing.get(checkpoint.checkpoint_id)
                if prior is not None:
                    if prior.get("checkpoint_hash") != checkpoint_hash:
                        raise ResumeError("successor checkpoint changed on resume")
                else:
                    self.run.append(
                        "checkpoint",
                        checkpoint_id=checkpoint.checkpoint_id,
                        checkpoint_hash=checkpoint_hash,
                        block=checkpoint.block,
                        history=checkpoint.history,
                        contributors=list(checkpoint.contributors),
                        result_hashes=result_hashes,
                    )

    def _materialize_scientific_artifacts(self) -> None:
        existing = {
            event["artifact_id"]: event["artifact"]
            for event in self.run.events
            if event.get("kind") == "scientific_artifact"
        }
        for artifact in self.factory.durable_artifacts(self.run):
            artifact = json.loads(canonical(artifact))
            artifact_id = artifact.get("artifact_id")
            if not isinstance(artifact_id, str) or not artifact_id:
                raise Gate2SuccessorError("scientific artifact has no identity")
            if artifact_id in existing:
                if canonical(existing[artifact_id]) != canonical(artifact):
                    raise ResumeError("scientific artifact changed on resume")
                continue
            self.run.append(
                "scientific_artifact",
                artifact_id=artifact_id,
                artifact=artifact,
                artifact_hash=digest(artifact),
            )
            existing[artifact_id] = artifact

    def _bind_live_transport(self) -> None:
        if self._execution_mode == FAKE_EXECUTION_MODE:
            return
        transport = self.provider.transport
        metadata = json.loads(canonical(transport.audit_metadata))
        metadata_hash = digest(metadata)
        if (metadata != self.proposal["transport"]
                or metadata_hash
                != self.run.manifest.get("authorized_transport_hash")):
            raise Gate2SuccessorError("live transport differs from authorization")
        try:
            installed = importlib_metadata.version("openai")
        except importlib_metadata.PackageNotFoundError as exc:
            raise Gate2SuccessorError("locked OpenAI SDK is unavailable") from exc
        if installed != STAGE2_SDK_VERSION:
            raise Gate2SuccessorError("installed OpenAI SDK version differs")
        manifest = self.run.manifest
        existing = manifest.get("transport")
        if existing is not None and existing != metadata:
            raise ResumeError("successor transport changed on resume")
        existing_hash = manifest.get("transport_hash")
        if existing_hash is not None and existing_hash != metadata_hash:
            raise ResumeError("successor transport hash changed on resume")
        runtime = dict(manifest["runtime"])
        dependencies = [{"name": "openai", "version": installed}]
        if runtime.get("third_party_dependencies") not in ([], dependencies):
            raise ResumeError("successor dependency inventory changed")
        # Exact live resumes are read-only at this boundary.  In particular,
        # do not rewrite a completed manifest before comparing its immutable
        # completion report with the current durable run.
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
            candidate = _provider_identity_candidate(
                self.run.config, event["status"], result
            ) if isinstance(result, Mapping) else None
            if event.get("provider_identity") != candidate or candidate != expected:
                self.run.set_status(
                    "paused_model_drift",
                    stop_identity=event.get("identity"),
                    required_provider_identity=expected,
                    rejected_provider_identity=candidate,
                    budget=self.ledger.snapshot(),
                )
                raise Gate2SuccessorError(
                    "successor provider identity differs from completed Stage 2"
                )
        if observed:
            durable = self.run.manifest.get("observed_provider_identity")
            if durable not in (None, expected):
                raise ResumeError("durable successor provider identity changed")
            if durable is None:
                self.run.set_status(
                    self.run.manifest["status"],
                    observed_provider_identity=expected,
                )

    def _validate_request_shape(self, spec: ScheduledRequest, request: Any,
                                prompt_hash: str, attempt: int,
                                prior: list[dict[str, Any]]) -> Mapping[str, Any]:
        if (request.request_identity != spec.identity
                or request.attempt != attempt
                or request.model != self.run.config.model
                or request.model_settings != dict(self.run.config.model_settings)):
            raise Gate2SuccessorError("rendered request differs from its frozen identity")
        retryable_count = sum(row.get("status") == "retryable_error" for row in prior)
        overlength_count = sum(row.get("status") == "overlength" for row in prior)
        if (retryable_count > self.run.config.retry_policy.transport_retries
                or overlength_count > self.run.config.retry_policy.message_overlength_retries
                or any(row.get("status") not in {"retryable_error", "overlength"}
                       for row in prior)):
            raise Gate2SuccessorError("request attempt shape was not authorized")
        shape = (
            "overlength_retry"
            if spec.action == "message" and overlength_count
            else "initial_attempt"
        )
        try:
            ceiling = self.rows[(spec.identity, shape)]
        except KeyError as exc:
            raise Gate2SuccessorError("request is absent from the smoke inventory") from exc
        if (request.max_output_tokens != ceiling["max_output_tokens"]
                or request.conservative_input_token_bound
                > ceiling["conservative_input_token_bound"]
                or request.conservative_input_token_bound
                > self.release.max_input_tokens_per_request
                or request.max_output_tokens
                > self.release.max_output_tokens_per_request
                or request.conservative_input_token_bound
                + request.max_output_tokens
                > self.run.config.model_context_tokens):
            raise Gate2SuccessorError("rendered request exceeds an authorized shape ceiling")
        wire = OpenAIResponsesProvider.build_payload(request)
        # The maximal synthetic projection has dynamic transcript content, so
        # actual payload hashes need not equal its hash.  Identity, shape,
        # metadata, renderer, implementation, and conservative ceiling are all
        # bound; persist the exact realized hashes before reservation.
        if (ceiling["identity"] != spec.identity
                or ceiling["phase"] != spec.phase
                or ceiling["action"] != spec.action
                or ceiling["metadata"] != dict(spec.metadata)
                or not isinstance(prompt_hash, str)):
            raise Gate2SuccessorError("rendered request metadata differs")
        return wire

    def _smoke_completion_event(self) -> dict[str, Any]:
        """Append or verify the immutable pre-review smoke completion boundary."""
        selected = self._selected()
        if self.ledger.active or any(
                not self._resolved(spec.identity) for spec in selected):
            raise Gate2SuccessorError(
                "smoke completion cannot be bound before all observations resolve"
            )
        mode = self._bind_execution_mode()
        experiment_start = self._ensure_experiment_start()
        result_events = self.run.result_events()
        checkpoint_events = self.run.checkpoint_events()
        artifact_events = [
            event for event in self.run.events
            if event.get("kind") == "scientific_artifact"
        ]
        expected = {
            "controller": CONTROLLER_ID,
            "proposal_hash": self.proposal["proposal_hash"],
            "run_id": SUCCESSOR_RUN_ID,
            "execution_mode": mode["mode"],
            "execution_mode_event_hash": mode["event_hash"],
            "experiment_start_event_hash": (
                None if experiment_start is None
                else experiment_start["event_hash"]
            ),
            "logical_request_count": len(selected),
            "dispatch_attempt_count": len(result_events),
            "result_set_hash": digest([
                event["event_hash"] for event in result_events
            ]),
            "checkpoint_set_hash": digest({
                key: value["event_hash"]
                for key, value in sorted(checkpoint_events.items())
            }),
            "scientific_artifact_set_hash": digest([
                event["event_hash"] for event in artifact_events
            ]),
            "smoke_inventory_hash": self.proposal["smoke_projection"][
                "inventory_hash"
            ],
            "gate1_lineage_hash": self.proposal["gate1_passed_lineage"][
                "lineage_hash"
            ],
        }
        completions = [
            event for event in self.run.events
            if event.get("kind") == SMOKE_COMPLETED_EVENT
        ]
        if len(completions) > 1:
            raise ResumeError("Gate 2 smoke completion binding is not singular")
        if completions:
            completion = completions[0]
            if (any(completion.get(key) != value
                    for key, value in expected.items())
                    or any(
                        event.get("kind") in {
                            "reservation", "result", "checkpoint",
                            "scientific_artifact",
                        }
                        and event.get("sequence", -1)
                        > completion.get("sequence", -1)
                        for event in self.run.events
                    )):
                raise ResumeError(
                    "Gate 2 smoke completion differs from durable evidence"
                )
            return completion
        if self.run.gate_events():
            raise ResumeError(
                "Gate 2 gate decision exists without a smoke completion boundary"
            )
        return self.run.append(SMOKE_COMPLETED_EVENT, **expected)

    def _write_completion_report(self) -> dict[str, Any]:
        report_path = self.run.path / "gate2_smoke_report.json"
        mode = self._bind_execution_mode()
        experiment_start = self._ensure_experiment_start()
        completion = self._smoke_completion_event()
        report = _gate2_smoke_report_value(
            self.run,
            self.proposal,
            self.ledger.snapshot(),
            mode,
            experiment_start,
            completion,
        )
        if report_path.exists():
            value = _read_json(report_path, "Gate 2 smoke report")
            if canonical(value) != canonical(report):
                raise ResumeError(
                    "Gate 2 smoke completion report differs from the current durable run"
                )
            return value
        _write_immutable_json(report_path, report, "Gate 2 smoke report")
        return report

    def execute(self) -> dict[str, Any]:
        """Execute only smoke/block 0 and return the durable hard-stop report."""
        with self.run.execution_lock():
            if self.run.manifest.get("execution_controller") != CONTROLLER_ID:
                raise Gate2SuccessorError("successor controller marker changed")
            self.ledger = self._replay_budget()
            self._validate_exact_scope_history()
            self._bind_execution_mode()
            self._bind_live_transport()
            # If a prior process fsynced a reservation but died before the
            # marker/provider call, conservatively bind experiment start before
            # reconciling that unmatched attempt as ambiguous.
            self._ensure_experiment_start()
            self._recover_unmatched_as_ambiguous()
            self._verify_provider_identity_history()
            self._materialize_checkpoints()
            selected = self._selected()
            completed_statuses = {
                "completed_smoke_awaiting_operator_review_hard_stop",
                "completed_smoke_passed_hard_stop",
                "completed_smoke_failed_hard_stop",
            }
            if self.run.manifest.get("status") in completed_statuses:
                if (self.ledger.active
                        or any(not self._resolved(spec.identity)
                               for spec in selected)):
                    raise ResumeError(
                        "completed successor contains unresolved or "
                        "non-observation smoke work"
                    )
                self._materialize_scientific_artifacts()
                completion = self._smoke_completion_event()
                manifest = self.run.manifest
                if (manifest.get("completed_at") != completion["recorded_at"]
                        or manifest.get("smoke_completed_event_hash")
                        != completion["event_hash"]):
                    raise ResumeError(
                        "completed successor manifest differs from its completion event"
                    )
                gate = self.run.gate_events().get("smoke_integrity")
                expected_reviewed_status = None
                if gate is not None:
                    expected_reviewed_status = (
                        "completed_smoke_passed_hard_stop"
                        if gate.get("passed") is True
                        else "completed_smoke_failed_hard_stop"
                    )
                if ((gate is None and manifest["status"]
                     != "completed_smoke_awaiting_operator_review_hard_stop")
                        or (gate is not None
                            and manifest["status"] != expected_reviewed_status)):
                    raise ResumeError(
                        "completed successor status differs from its immutable review"
                    )
                return self._write_completion_report()
            for spec in selected:
                stop = self._hard_stop(spec)
                if stop is not None:
                    self.run.set_status(
                        stop,
                        stop_identity=spec.identity,
                        budget=self.ledger.snapshot(),
                    )
                    raise Gate2SuccessorError(
                        f"successor remains stopped at {spec.identity}: {stop}"
                    )
            self.run.set_status(
                "running_gate2_smoke",
                target_stage="smoke",
                target_block=0,
                budget=self.ledger.snapshot(),
            )
            for spec in selected:
                results = self.run.result_events(spec.identity)
                if self._status_terminal(spec, results):
                    continue
                if any(not self._resolved(dep) for dep in spec.dependencies):
                    self.run.set_status(
                        "paused_unresolved_dependency",
                        stop_identity=spec.identity,
                        budget=self.ledger.snapshot(),
                    )
                    raise Gate2SuccessorError("smoke dependency is unresolved")
                self._materialize_checkpoints()
                if (spec.parent_checkpoint is not None
                        and spec.parent_checkpoint not in self.run.checkpoint_events()):
                    raise Gate2SuccessorError("required smoke checkpoint is unavailable")
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
                    # Known-not-completed transport failures do not change the
                    # model-visible request.  Once an overlength response has
                    # selected the one allowed content-revision shape, retries
                    # remain on that shape even if a later transport attempt is
                    # proven not completed.
                    visible_prior = tuple(
                        row for row in results
                        if row.get("status") == "overlength"
                    )
                    context = ExecutionContext(
                        self.run, attempt, visible_prior
                    )
                    request, rendered = self.factory.build(spec, context)
                    wire = self._validate_request_shape(
                        spec, request, rendered.prompt_hash, attempt, results
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
                        raise Gate2SuccessorError(
                            "an exact Gate 2 ceiling stopped dispatch"
                        ) from exc
                    reservation_event = self.run.append(
                        "reservation",
                        identity=spec.identity,
                        attempt=attempt,
                        request_hash=request_hash,
                        wire_payload_hash=wire_hash,
                        reservation=reservation.to_dict(),
                        gate2_projected_shape=(
                            "overlength_retry"
                            if spec.action == "message" and any(
                                row.get("status") == "overlength"
                                for row in results
                            )
                            else "initial_attempt"
                        ),
                    )
                    self._ensure_experiment_start(reservation_event)
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
                    # A transport attempt can be proven not completed only
                    # when the provider reports no usage object at all.  Zero,
                    # malformed, or partial usage remains ambiguous and burns
                    # the full reservation fail-closed.
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
                        raise Gate2SuccessorError(
                            "provider identity differs from completed Stage 2"
                        )
                    if status_value in PROVIDER_IDENTITY_STATUSES:
                        expected_identity = self.proposal[
                            "required_provider_identity"
                        ]
                        durable_identity = self.run.manifest.get(
                            "observed_provider_identity"
                        )
                        if durable_identity not in (None, expected_identity):
                            raise ResumeError(
                                "durable successor provider identity changed"
                            )
                        if durable_identity is None:
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
                        raise Gate2SuccessorError(
                            f"provider hard-stop status: {status_value}"
                        )
                    if (status_value == "retryable_error"
                            and self._status_terminal(spec, results)):
                        self.run.set_status(
                            "paused_retry_exhausted",
                            stop_identity=spec.identity,
                            budget=self.ledger.snapshot(),
                        )
                        raise Gate2SuccessorError(
                            "transport retry is outside the Gate 2 release"
                        )
                    if (status_value == "overlength"
                            and self._status_terminal(spec, results)):
                        self.run.set_status(
                            "paused_overlength_exhausted",
                            stop_identity=spec.identity,
                            budget=self.ledger.snapshot(),
                        )
                        raise Gate2SuccessorError(
                            "message overlength retry is exhausted"
                        )
                self._materialize_checkpoints()
            self._materialize_scientific_artifacts()
            if any(not self._resolved(spec.identity) for spec in selected):
                raise Gate2SuccessorError(
                    "smoke did not produce an observation for every identity"
                )
            experiment_start = self._ensure_experiment_start()
            completion = self._smoke_completion_event()
            self.run.set_status(
                "completed_smoke_awaiting_operator_review_hard_stop",
                completed_through_stage="smoke",
                completed_through_block=0,
                completed_at=completion["recorded_at"],
                smoke_completed_event_hash=completion["event_hash"],
                next_stage_authorized=False,
                experiment_started=experiment_start is not None,
                experiment_start_event_hash=(
                    None if experiment_start is None
                    else experiment_start["event_hash"]
                ),
                budget=self.ledger.snapshot(),
                stop_identity=None,
            )
            return self._write_completion_report()


def _singular_gate2_event(
        run: DurableRun, kind: str, label: str) -> dict[str, Any]:
    rows = [event for event in run.events if event.get("kind") == kind]
    if len(rows) != 1:
        raise Gate2SuccessorError(f"a singular {label} event is required")
    return rows[0]


def _verify_completed_gate2_smoke(
        run: DurableRun, proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the exact completed-smoke boundary without dispatch or repair."""
    _validate_dedicated_successor_plan(run.plan)
    if (run.config.run_id != SUCCESSOR_RUN_ID
            or run.config.protocol_version != SUCCESSOR_PROTOCOL_VERSION
            or run.config.source != "empirical-live"
            or run.manifest.get("execution_controller") != CONTROLLER_ID
            or run.manifest.get("gate2_proposal_hash")
            != proposal["proposal_hash"]
            or digest(run.config.to_dict())
            != run.manifest.get("config_hash")
            or run.plan.plan_hash != run.manifest.get("plan_hash")):
        raise Gate2SuccessorError(
            "completed Gate 2 run differs from the reviewed successor contract"
        )
    verified_gate1 = verify_gate1_passed_lineage()
    expected_gate1 = {**verified_gate1, "lineage_hash": digest(verified_gate1)}
    if (verified_gate1.get("passed") is not True
            or canonical(expected_gate1)
            != canonical(proposal["gate1_passed_lineage"])):
        raise Gate2SuccessorError(
            "completed smoke no longer has exact independently passed Gate 1 lineage"
        )

    try:
        dispatch = DispatchAuthorizationArtifact.from_mapping(
            run.manifest["dispatch_authorization"]
        )
    except (AuthorizationRequiredError, KeyError, TypeError, ValueError) as exc:
        raise Gate2SuccessorError(
            "completed Gate 2 dispatch authorization is invalid"
        ) from exc
    auth_stub = {
        "authorization_hash": run.manifest.get(
            "gate2_human_authorization_hash"
        ),
        "successor_run_id": run.manifest.get("gate2_successor_run_id"),
    }
    expected_binding = _binding_event_value(
        proposal, auth_stub, run.config, run.plan, dispatch
    )
    binding = _singular_gate2_event(
        run, "gate2_successor_binding", "Gate 2 successor binding"
    )
    if (any(binding.get(key) != value
            for key, value in expected_binding.items())
            or not isinstance(binding.get("run_instance_nonce"), str)
            or re.fullmatch(r"[a-f0-9]{64}", binding["run_instance_nonce"])
            is None):
        raise Gate2SuccessorError("durable Gate 2 successor binding differs")

    claim = _singular_gate2_event(
        run, AUTHORIZATION_CLAIM_EVENT, "Gate 2 authorization claim"
    )
    claim_exact = {
        "controller": CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_hash": auth_stub["authorization_hash"],
        "successor_run_id": SUCCESSOR_RUN_ID,
        "run_directory": str(run.path.resolve()),
        "run_instance_event_hash": binding["event_hash"],
        "run_instance_hash": digest(binding["run_instance_nonce"]),
    }
    receipt_hash = claim.get("receipt_hash")
    if (any(claim.get(key) != value for key, value in claim_exact.items())
            or not isinstance(receipt_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", receipt_hash) is None
            or run.manifest.get("gate2_authorization_consumed") is not True
            or run.manifest.get("gate2_authorization_receipt_hash")
            != receipt_hash
            or run.manifest.get("gate2_authorization_claim_event_hash")
            != claim["event_hash"]
            or claim["sequence"] <= binding["sequence"]):
        raise Gate2SuccessorError(
            "completed smoke lacks its exact journal-bound authorization claim"
        )
    _parse_timestamp(
        claim.get("receipt_claimed_at"),
        "authorization claim receipt_claimed_at",
    )

    mode = _singular_gate2_event(
        run, EXECUTION_MODE_BINDING_EVENT, "Gate 2 execution-mode binding"
    )
    if (mode.get("controller") != CONTROLLER_ID
            or mode.get("proposal_hash") != proposal["proposal_hash"]
            or mode.get("run_id") != SUCCESSOR_RUN_ID
            or mode.get("authorization_claim_event_hash")
            != claim["event_hash"]
            or mode.get("mode") not in {
                EMPIRICAL_EXECUTION_MODE, FAKE_EXECUTION_MODE
            }
            or mode["sequence"] <= claim["sequence"]):
        raise Gate2SuccessorError("durable Gate 2 execution mode differs")
    if mode.get("mode") == FAKE_EXECUTION_MODE:
        raise Gate2SuccessorError(
            "fake-transport smoke can never receive an operator gate decision"
        )
    runtime_dependencies = run.manifest.get("runtime", {}).get(
        "third_party_dependencies"
    )
    if (run.manifest.get("transport") != proposal["transport"]
            or run.manifest.get("transport_hash") != proposal["transport_hash"]
            or runtime_dependencies != [{
                "name": "openai", "version": STAGE2_SDK_VERSION
            }]):
        raise Gate2SuccessorError(
            "completed empirical smoke lacks the exact locked live transport"
        )

    selected = list(run.plan.requests)
    allowed = {spec.identity for spec in selected}
    result_events = run.result_events()
    reservation_events = [
        event for event in run.events if event.get("kind") == "reservation"
    ]
    if (not reservation_events
            or any(event.get("identity") not in allowed
                   for event in (*reservation_events, *result_events))
            or any(
                event.get("kind") in {"reservation", "result"}
                and event.get("sequence", -1) <= mode["sequence"]
                for event in run.events
            )):
        raise Gate2SuccessorError(
            "completed smoke contains missing, pre-mode, or out-of-scope dispatch"
        )
    first_reservation = reservation_events[0]
    start = _singular_gate2_event(
        run, EXPERIMENT_STARTED_EVENT, "Gate 2 experiment-start binding"
    )
    start_exact = {
        "controller": CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "run_id": SUCCESSOR_RUN_ID,
        "execution_mode_event_hash": mode["event_hash"],
        "first_reservation_event_hash": first_reservation["event_hash"],
        "identity": first_reservation["identity"],
        "attempt": first_reservation["attempt"],
        "request_hash": first_reservation["request_hash"],
        "wire_payload_hash": first_reservation["wire_payload_hash"],
    }
    first_result_sequence = min(
        event["sequence"] for event in result_events
    )
    if (mode.get("mode") == EMPIRICAL_EXECUTION_MODE
            and (any(start.get(key) != value
                     for key, value in start_exact.items())
                 or start["sequence"] <= first_reservation["sequence"]
                 or start["sequence"] >= first_result_sequence
                 or run.manifest.get("experiment_started") is not True
                 or run.manifest.get("experiment_start_event_hash")
                 != start["event_hash"])):
        raise Gate2SuccessorError(
            "completed smoke experiment-start boundary differs"
        )
    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    try:
        ledger = BudgetLedger.replay(
            run.config.limits,
            run.config.pricing,
            Decimal(proposal["exact_caps"]["max_cost_usd"]),
            run.budget_records(),
            release,
        )
    except (BudgetExhausted, RuntimeError, TypeError, ValueError) as exc:
        raise Gate2SuccessorError(
            "completed smoke budget evidence is invalid"
        ) from exc
    if ledger.active:
        raise Gate2SuccessorError("completed smoke has an active reservation")
    for spec in selected:
        rows = run.result_events(spec.identity)
        if (not rows or rows[-1].get("status") not in OBSERVATION_STATUSES
                or any(
                    row.get("status") not in {"retryable_error", "overlength"}
                    for row in rows[:-1]
                )
                or sum(row.get("status") == "retryable_error" for row in rows)
                > run.config.retry_policy.transport_retries
                or sum(row.get("status") == "overlength" for row in rows)
                > (run.config.retry_policy.message_overlength_retries
                   if spec.action == "message" else 0)):
            raise Gate2SuccessorError(
                "completed smoke lacks an authorized final observation or "
                "contains an unauthorized nonfinal observation"
            )

    checkpoints = run.checkpoint_events()
    expected_checkpoint_ids = {
        checkpoint.checkpoint_id for checkpoint in run.plan.checkpoints
    }
    if set(checkpoints) != expected_checkpoint_ids:
        raise Gate2SuccessorError(
            "completed smoke checkpoint inventory differs"
        )
    artifacts = [
        event for event in run.events
        if event.get("kind") == "scientific_artifact"
    ]
    completion = _singular_gate2_event(
        run, SMOKE_COMPLETED_EVENT, "Gate 2 smoke completion"
    )
    expected_completion = {
        "controller": CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "run_id": SUCCESSOR_RUN_ID,
        "execution_mode": mode["mode"],
        "execution_mode_event_hash": mode["event_hash"],
        "experiment_start_event_hash": start["event_hash"],
        "logical_request_count": len(selected),
        "dispatch_attempt_count": len(result_events),
        "result_set_hash": digest([
            event["event_hash"] for event in result_events
        ]),
        "checkpoint_set_hash": digest({
            key: value["event_hash"]
            for key, value in sorted(checkpoints.items())
        }),
        "scientific_artifact_set_hash": digest([
            event["event_hash"] for event in artifacts
        ]),
        "smoke_inventory_hash": proposal["smoke_projection"]["inventory_hash"],
        "gate1_lineage_hash": proposal["gate1_passed_lineage"]["lineage_hash"],
    }
    if (any(completion.get(key) != value
            for key, value in expected_completion.items())
            or any(
                event.get("kind") in {
                    "reservation", "result", "checkpoint", "scientific_artifact"
                }
                and event.get("sequence", -1) > completion["sequence"]
                for event in run.events
            )):
        raise Gate2SuccessorError(
            "completed smoke boundary differs from durable evidence"
        )
    completed_statuses = {
        "completed_smoke_awaiting_operator_review_hard_stop",
        "completed_smoke_passed_hard_stop",
        "completed_smoke_failed_hard_stop",
    }
    manifest = run.manifest
    if (manifest.get("status") not in completed_statuses
            or manifest.get("completed_at") != completion["recorded_at"]
            or manifest.get("smoke_completed_event_hash")
            != completion["event_hash"]
            or manifest.get("next_stage_authorized") is not False):
        raise Gate2SuccessorError(
            "completed smoke manifest is not at the exact hard stop"
        )

    report_path = run.path / "gate2_smoke_report.json"
    report = _read_json(report_path, "Gate 2 smoke report")
    expected_report = _gate2_smoke_report_value(
        run, proposal, ledger.snapshot(), mode, start, completion
    )
    if canonical(report) != canonical(expected_report):
        raise Gate2SuccessorError(
            "Gate 2 smoke report differs from the durable completed run"
        )
    return {
        "run": run,
        "ledger": ledger,
        "binding": binding,
        "claim": claim,
        "mode": mode,
        "start": start,
        "completion": completion,
        "report": report,
    }


def validate_gate2_smoke_review(
        value: Mapping[str, Any], proposal: Mapping[str, Any],
        report: Mapping[str, Any], assessment: Mapping[str, Any],
        completion: Mapping[str, Any], *,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate an exact external post-smoke review without recording it."""
    if not isinstance(value, Mapping) or set(value) != _SMOKE_REVIEW_FIELDS:
        raise Gate2SuccessorError(
            f"smoke review must contain exactly {sorted(_SMOKE_REVIEW_FIELDS)}"
        )
    review = json.loads(canonical(dict(value)))
    body = dict(review)
    review_hash = body.pop("review_hash", None)
    if (not isinstance(review_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", review_hash) is None
            or review_hash != digest(body)):
        raise Gate2SuccessorError("smoke review self-hash is invalid")
    try:
        review_uuid = uuid.UUID(review["review_uuid"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise Gate2SuccessorError(
            "smoke review_uuid must be a lowercase UUIDv4"
        ) from exc
    if (review_uuid.version != 4 or str(review_uuid) != review["review_uuid"]):
        raise Gate2SuccessorError(
            "smoke review_uuid must be a lowercase UUIDv4"
        )
    decision = review.get("operator_decision")
    if decision not in {"pass", "fail"}:
        raise Gate2SuccessorError("smoke operator_decision must be pass or fail")
    operator_review = {
        name: review[name]
        for name in ("reviewer", "reviewed_at", "notes",
                     *SMOKE_REVIEW_BOOLEAN_FIELDS)
    }
    try:
        operator_review = validate_smoke_operator_review(
            operator_review, require_pass=decision == "pass"
        )
    except ValueError as exc:
        raise Gate2SuccessorError("smoke operator review is invalid") from exc
    exact = {
        "artifact_type": "gate_2_v1_1_smoke_operator_review_v1",
        "proposal_hash": proposal["proposal_hash"],
        "gate2_run_id": SUCCESSOR_RUN_ID,
        "smoke_report_hash": report["report_hash"],
        "smoke_completed_event_hash": completion["event_hash"],
        "smoke_completed_at": completion["recorded_at"],
        "reviewed_journal_tail": completion["event_hash"],
        "reviewed_journal_event_count": completion["sequence"] + 1,
        "mechanical_assessment_hash": digest(dict(assessment)),
        "gate1_lineage_hash": proposal["gate1_passed_lineage"]["lineage_hash"],
    }
    if any(review.get(key) != expected for key, expected in exact.items()):
        raise Gate2SuccessorError(
            "smoke review does not bind the exact completed run and assessment"
        )
    reviewed_at = _parse_timestamp(
        operator_review["reviewed_at"], "smoke reviewed_at"
    ).astimezone(timezone.utc)
    durable_completed_at = _parse_timestamp(
        completion["recorded_at"], "durable smoke completed_at"
    ).astimezone(timezone.utc)
    provider_completed_at = _parse_timestamp(
        assessment.get("smoke_completed_at"), "mechanical smoke_completed_at"
    ).astimezone(timezone.utc)
    if reviewed_at < max(durable_completed_at, provider_completed_at):
        raise Gate2SuccessorError(
            "smoke review must occur after durable smoke completion"
        )
    if reviewed_at > _utc_now_datetime(now) + timedelta(
            seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS):
        raise Gate2SuccessorError(
            "smoke review timestamp is implausibly far in the future"
        )
    if decision == "pass" and assessment.get("automatic_checks_passed") is not True:
        raise Gate2SuccessorError(
            "a passing smoke review requires every mechanical check"
        )
    return review


def _gate2_smoke_decision_report_value(
        proposal: Mapping[str, Any], verified: Mapping[str, Any],
        assessment: Mapping[str, Any], review: Mapping[str, Any],
        gate: Mapping[str, Any], status: str) -> dict[str, Any]:
    decision_body = {
        "artifact_type": "gate_2_v1_1_smoke_decision_report_v1",
        "controller": CONTROLLER_ID,
        "status": status,
        "decision": review["operator_decision"],
        "proposal_hash": proposal["proposal_hash"],
        "run_id": SUCCESSOR_RUN_ID,
        "smoke_report_hash": verified["report"]["report_hash"],
        "smoke_completed_event_hash": verified["completion"]["event_hash"],
        "completed_at": verified["completion"]["recorded_at"],
        "operator_review": json.loads(canonical(dict(review))),
        "operator_review_hash": review["review_hash"],
        "mechanical_assessment": json.loads(canonical(dict(assessment))),
        "mechanical_assessment_hash": digest(dict(assessment)),
        "gate1_passed_lineage": proposal["gate1_passed_lineage"],
        "gate_decision_event_hash": gate["event_hash"],
        "gate_decision_evidence_hash": gate["evidence_hash"],
        "pre_review_journal_tail": verified["completion"]["event_hash"],
        "pre_review_journal_event_count": verified["completion"]["sequence"] + 1,
        "journal_tail": gate["event_hash"],
        "journal_event_count": gate["sequence"] + 1,
        "next_stage_authorized": False,
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
    }
    return {**decision_body, "report_hash": digest(decision_body)}


def _smoke_integrity_assessment_locked(run: DurableRun) -> dict[str, Any]:
    """Memoize the expensive exact smoke reconstruction under the run lock.

    Callers must already hold ``run.execution_lock()``.  That lock refreshes
    and verifies the journal and payload inventory.  We additionally verify
    every content-addressed request/wire file and independently reverify Gate
    1 before deriving an evidence-complete cache key.  Operator-review gate
    events are excluded because the mechanical assessment does not read them;
    every dispatch/result/checkpoint/artifact/completion event remains keyed.
    """
    run._verify_payloads()
    verified_gate1 = verify_gate1_passed_lineage()
    if verified_gate1.get("passed") is not True:
        raise Gate2SuccessorError(
            "smoke assessment requires exact independently passed Gate 1 lineage"
        )
    evidence_events = [
        event for event in run.events if event.get("kind") != "gate"
    ]
    manifest = run.manifest
    cache_key = digest({
        "path": str(run.path.resolve()),
        "run_id": run.config.run_id,
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "protocol_version": run.plan.protocol_version,
        "protocol_hash": run.plan.protocol_hash,
        "implementation_hash": run.plan.implementation_hash,
        "execution_controller": manifest.get("execution_controller"),
        "evidence_events_hash": digest(evidence_events),
        "evidence_event_count": len(evidence_events),
        "verified_gate1": verified_gate1,
    })
    with _SMOKE_ASSESSMENT_CACHE_LOCK:
        cached = _SMOKE_ASSESSMENT_CACHE.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    assessment = smoke_integrity_assessment(run)
    lineage = assessment.get("gate1_passed_lineage")
    if (not isinstance(lineage, Mapping)
            or lineage.get("independently_verified") is not True
            or lineage.get("durable_binding_verified") is not True
            or lineage.get("lineage_hash") != digest(verified_gate1)):
        raise Gate2SuccessorError(
            "smoke assessment Gate 1 lineage differs from independent verification"
        )
    serialized = canonical(assessment)
    with _SMOKE_ASSESSMENT_CACHE_LOCK:
        if cache_key not in _SMOKE_ASSESSMENT_CACHE:
            if len(_SMOKE_ASSESSMENT_CACHE) >= (
                    _SMOKE_ASSESSMENT_CACHE_MAX_ENTRIES):
                _SMOKE_ASSESSMENT_CACHE.pop(
                    next(iter(_SMOKE_ASSESSMENT_CACHE))
                )
            _SMOKE_ASSESSMENT_CACHE[cache_key] = serialized
    return json.loads(serialized)


def _verify_existing_gate2_smoke_decision(
        verified: Mapping[str, Any], proposal: Mapping[str, Any], *,
        now: datetime | None = None) -> dict[str, Any] | None:
    """Verify the optional post-smoke decision and its immutable report."""
    run = verified["run"]
    manifest = run.manifest
    gate = run.gate_events().get("smoke_integrity")
    decision_path = run.path / SMOKE_REVIEW_DECISION_REPORT
    awaiting = "completed_smoke_awaiting_operator_review_hard_stop"
    if manifest.get("status") == awaiting:
        if (gate is not None or decision_path.exists()
                or manifest.get("smoke_integrity_decision_event_hash") is not None
                or manifest.get("smoke_operator_review_hash") is not None):
            raise Gate2SuccessorError(
                "awaiting-review smoke contains partial review state"
            )
        return None
    if gate is None:
        raise Gate2SuccessorError(
            "reviewed smoke lacks its immutable smoke_integrity decision"
        )
    decision_report = _read_json(
        decision_path, "Gate 2 smoke decision report"
    )
    review = decision_report.get("operator_review")
    assessment = _smoke_integrity_assessment_locked(run)
    review = validate_gate2_smoke_review(
        review,
        proposal,
        verified["report"],
        assessment,
        verified["completion"],
        now=now,
    )
    passed = review["operator_decision"] == "pass"
    expected_status = (
        "completed_smoke_passed_hard_stop"
        if passed else "completed_smoke_failed_hard_stop"
    )
    operator_review = {
        name: review[name]
        for name in ("reviewer", "reviewed_at", "notes",
                     *SMOKE_REVIEW_BOOLEAN_FIELDS)
    }
    evidence = {
        "mechanical": assessment,
        "operator_review": operator_review,
        "operator_decision": review["operator_decision"],
    }
    if (manifest.get("status") != expected_status
            or gate.get("passed") is not passed
            or gate.get("decided_by") != review["reviewer"]
            or gate.get("note") != review["notes"]
            or canonical(gate.get("evidence")) != canonical(evidence)
            or gate.get("evidence_hash") != digest(evidence)
            or gate.get("sequence") != verified["completion"]["sequence"] + 1
            or manifest.get("last_event_hash") != gate["event_hash"]
            or manifest.get("event_count") != gate["sequence"] + 1
            or manifest.get("smoke_integrity_decision_event_hash")
            != gate["event_hash"]
            or manifest.get("smoke_operator_review_hash")
            != review["review_hash"]
            or manifest.get("next_stage_authorized") is not False):
        raise Gate2SuccessorError(
            "reviewed smoke differs from its external review and immutable gate"
        )
    expected_report = _gate2_smoke_decision_report_value(
        proposal, verified, assessment, review, gate, expected_status
    )
    if canonical(decision_report) != canonical(expected_report):
        raise Gate2SuccessorError(
            "Gate 2 smoke decision report differs from durable evidence"
        )
    return decision_report


def record_gate2_smoke_decision(
        review_path: Path, *, proposal_path: Path = PROPOSAL_PATH,
        run_dir: Path | None = None,
        workspace_root: Path | None = None,
        _now: datetime | None = None) -> dict[str, Any]:
    """Record the one immutable post-smoke decision; never dispatch or read a key."""
    proposal = load_gate2_successor_proposal(proposal_path)
    review_path = Path(review_path)
    _validate_external_owner_only_file(review_path, "smoke operator review")
    initial_review = _read_json(review_path, "smoke operator review")
    root = ROOT if workspace_root is None else Path(workspace_root)
    expected_run = root / (
        "runs/empirical/gate2-smoke-v1.1-"
        + proposal["successor_protocol"]["run_id"]
    )
    run_path = expected_run if run_dir is None else Path(run_dir)
    if Path(os.path.abspath(run_path)) != Path(os.path.abspath(expected_run)):
        raise Gate2SuccessorError(
            "smoke review run path differs from the fixed Gate 2 release"
        )
    # Post-smoke review is a no-dispatch provenance operation.  Validate the
    # saved SDK identity against the immutable proposal in
    # _verify_completed_gate2_smoke without requiring the SDK to be installed.
    run = DurableRun.inspect(
        run_path, probe_installed_dependencies=False
    )
    with run.execution_lock():
        _validate_external_owner_only_file(review_path, "smoke operator review")
        current_review = _read_json(review_path, "smoke operator review")
        if canonical(current_review) != canonical(initial_review):
            raise Gate2SuccessorError("smoke operator review changed during validation")
        verified = _verify_completed_gate2_smoke(run, proposal)
        assessment = _smoke_integrity_assessment_locked(run)
        review = validate_gate2_smoke_review(
            current_review,
            proposal,
            verified["report"],
            assessment,
            verified["completion"],
            now=_now,
        )
        passed = review["operator_decision"] == "pass"
        operator_review = {
            name: review[name]
            for name in ("reviewer", "reviewed_at", "notes",
                         *SMOKE_REVIEW_BOOLEAN_FIELDS)
        }
        evidence = {
            "mechanical": assessment,
            "operator_review": operator_review,
            "operator_decision": review["operator_decision"],
        }
        existing = run.gate_events().get("smoke_integrity")
        if existing is None:
            if run.manifest.get("status") != (
                    "completed_smoke_awaiting_operator_review_hard_stop"):
                raise Gate2SuccessorError(
                    "smoke decision can only follow the awaiting-review hard stop"
                )
            gate = run._record_gate_decision_unlocked(
                "smoke_integrity",
                passed=passed,
                evidence=evidence,
                decided_by=review["reviewer"],
                note=review["notes"],
            )
        else:
            gate = existing
            if (gate.get("passed") is not passed
                    or gate.get("decided_by") != review["reviewer"]
                    or gate.get("note") != review["notes"]
                    or canonical(gate.get("evidence")) != canonical(evidence)
                    or gate.get("evidence_hash") != digest(evidence)):
                raise Gate2SuccessorError(
                    "immutable smoke decision differs from the external review"
                )
        status = (
            "completed_smoke_passed_hard_stop"
            if passed else "completed_smoke_failed_hard_stop"
        )
        manifest = run.manifest
        if manifest.get("status") != status:
            recoverable_gate_only_crash = (
                existing is not None
                and manifest.get("status")
                == "completed_smoke_awaiting_operator_review_hard_stop"
                and manifest.get("last_event_hash") == gate["event_hash"]
                and manifest.get("event_count") == gate["sequence"] + 1
            )
            if existing is not None and not recoverable_gate_only_crash:
                raise Gate2SuccessorError(
                    "reviewed smoke manifest differs from its immutable decision"
                )
            run.set_status(
                status,
                completed_through_stage="smoke",
                completed_through_block=0,
                completed_at=verified["completion"]["recorded_at"],
                smoke_completed_event_hash=verified["completion"]["event_hash"],
                smoke_integrity_decision_event_hash=gate["event_hash"],
                smoke_operator_review_hash=review["review_hash"],
                next_stage_authorized=False,
                budget=verified["ledger"].snapshot(),
                stop_identity=None,
            )
        elif (manifest.get("smoke_integrity_decision_event_hash")
              != gate["event_hash"]
              or manifest.get("smoke_operator_review_hash")
              != review["review_hash"]
              or manifest.get("next_stage_authorized") is not False):
            raise Gate2SuccessorError(
                "reviewed smoke manifest is not derived from the exact decision"
            )
        decision_report = _gate2_smoke_decision_report_value(
            proposal, verified, assessment, review, gate, status
        )
        _write_immutable_json(
            run.path / SMOKE_REVIEW_DECISION_REPORT,
            decision_report,
            "Gate 2 smoke decision report",
        )
        return decision_report


def inspect_gate2_successor(
        proposal_path: Path = PROPOSAL_PATH, *,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate the full package with no authorization, credential, or dispatch."""
    proposal = load_gate2_successor_proposal(proposal_path)
    _validate_pricing_window(proposal, now=now)
    return {
        "status": "ready_awaiting_external_human_authorization",
        "proposal_hash": proposal["proposal_hash"],
        "stage2_report_hash": proposal["stage2_lineage"]["report_hash"],
        "smoke_inventory_hash": proposal["smoke_projection"]["inventory_hash"],
        "implementation_hash": proposal["current_implementation_hash"],
        "successor_protocol": proposal["successor_protocol"],
        "protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "authorization_validity": proposal["external_authorization"],
        "exact_caps": proposal["exact_caps"],
        "scope": proposal["smoke_scope"],
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
        "remaining_step": (
            "A human must create the external one-use authorization artifact "
            "matching the checked schema."
        ),
    }


def validate_gate2_authorization_file(
        authorization_path: Path,
        proposal_path: Path = PROPOSAL_PATH, *,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate the external authorization and derived run contract, no credentials."""
    proposal = load_gate2_successor_proposal(proposal_path)
    auth = load_gate2_human_authorization(
        authorization_path, proposal, _proposal_verified=True
    )
    _validate_first_claim_window(auth, proposal, now=now)
    config, plan, dispatch, inventory = _successor_config_and_plan(
        proposal, auth, _proposal_verified=True
    )
    return {
        "status": "authorization_valid_no_dispatch",
        "authorization_hash": auth["authorization_hash"],
        "successor_run_id": auth["successor_run_id"],
        "successor_run_directory": auth["successor_run_directory"],
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "smoke_inventory_hash": inventory["inventory_hash"],
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
    }


def run_gate2_successor(
        authorization_path: Path, *,
        proposal_path: Path = PROPOSAL_PATH,
        env_file: Path = STAGE2_ENV_FILE,
        _now: datetime | None = None) -> dict[str, Any]:
    """Execute the authorized empirical smoke release and hard-stop.

    This is the only public path that reads a credential or creates the live
    SDK client.  The external human authorization is fully validated first.
    """
    proposal = load_gate2_successor_proposal(proposal_path)
    authorization = load_gate2_human_authorization(
        authorization_path, proposal, require_external=True,
        _proposal_verified=True,
    )
    receipt_dir = _authorization_receipt_directory(Path(authorization_path))
    receipt_path = _authorization_receipt_path(receipt_dir)
    run_path = ROOT / authorization["successor_run_directory"]
    if receipt_path.exists():
        if not run_path.is_dir():
            raise Gate2SuccessorError(
                "an authorization receipt exists without its durable successor run"
            )
        # A valid claimed receipt permits same-run resume after the short
        # first-use authorization window, but every dispatch/resume still
        # requires current reviewed pricing.  The claim function below will
        # verify that the existing receipt belongs to this exact durable run.
        _validate_pricing_window(proposal, now=_now)
        validated_path, _ = _validated_successor_run_path(
            run_path, authorization
        )
        candidate = DurableRun.inspect(
            validated_path, probe_installed_dependencies=False
        )
        if candidate.manifest.get("status") in {
                "completed_smoke_awaiting_operator_review_hard_stop",
                "completed_smoke_passed_hard_stop",
                "completed_smoke_failed_hard_stop",
        }:
            receipt = claim_gate2_authorization_once(
                candidate,
                proposal,
                authorization,
                receipt_dir=receipt_dir,
                now=_now,
            )
            with candidate.execution_lock():
                verified = _verify_completed_gate2_smoke(candidate, proposal)
                _verify_existing_gate2_smoke_decision(
                    verified, proposal, now=_now
                )
                return verified["report"]
    else:
        # Reject stale/future first-use authorization before creating the
        # fixed successor directory, preventing an unusable auth-bound run
        # from squatting the one reviewed run identity.
        try:
            _validate_first_claim_window(authorization, proposal, now=_now)
        except Gate2SuccessorError as exc:
            if run_path.exists():
                raise Gate2SuccessorError(
                    "the prepared Gate 2 release missed its first-claim window; "
                    "this fixed run is permanently fail-closed and requires a "
                    "new versioned release/run ID, not a replacement authorization"
                ) from exc
            raise
    config, plan, dispatch, inventory = _successor_config_and_plan(
        proposal, authorization, _proposal_verified=True
    )
    run, prepared_config, prepared_plan, prepared_dispatch, prepared_inventory = (
        prepare_gate2_successor_run(
            run_path, proposal, authorization, _proposal_verified=True
        )
    )
    if (prepared_config != config or prepared_plan.to_dict() != plan.to_dict()
            or prepared_dispatch.to_dict() != dispatch.to_dict()
            or prepared_inventory["inventory_hash"] != inventory["inventory_hash"]):
        raise Gate2SuccessorError("prepared successor contract changed")
    receipt = claim_gate2_authorization_once(
        run,
        proposal,
        authorization,
        receipt_dir=receipt_dir,
        now=_now,
    )

    completed_statuses = {
        "completed_smoke_awaiting_operator_review_hard_stop",
        "completed_smoke_passed_hard_stop",
        "completed_smoke_failed_hard_stop",
    }
    if run.manifest.get("status") in completed_statuses:
        # A completed exact run is a read-only resume.  Reverify every durable
        # boundary (including an optional operator decision) and return its
        # immutable execution report before touching a secret or SDK client.
        with run.execution_lock():
            verified = _verify_completed_gate2_smoke(run, proposal)
            _verify_existing_gate2_smoke_decision(
                verified, proposal, now=_now
            )
            return verified["report"]

    # The durable run and external one-use claim must exist before any secret
    # is read or any SDK client is constructed.  A missing/mismatched/replayed
    # authorization therefore cannot reach credential-handling code.
    try:
        installed = importlib_metadata.version("openai")
    except importlib_metadata.PackageNotFoundError as exc:
        raise Gate2SuccessorError("locked OpenAI SDK is not installed") from exc
    if installed != STAGE2_SDK_VERSION:
        raise Gate2SuccessorError("installed OpenAI SDK differs from the proposal")
    transport_metadata = _stage2_transport_metadata(sdk_version=installed)
    if (transport_metadata != proposal["transport"]
            or digest(transport_metadata) != proposal["transport_hash"]):
        raise Gate2SuccessorError("runtime transport differs from the proposal")
    conflicting = sorted(
        name for name in os.environ if name.upper().startswith("OPENAI_")
    )
    if conflicting:
        raise Gate2SuccessorError(
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
    provider = OpenAIResponsesProvider(transport)
    return Gate2SmokeExecutor(
        run, provider, proposal, inventory,
        authorization_receipt=receipt,
        fake_transport_test=False,
    ).execute()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--proposal", type=Path, default=PROPOSAL_PATH,
        help="checked Gate 2 successor proposal",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inspect", help="validate package without authorization or credentials")
    validate = sub.add_parser(
        "validate-authorization", help="validate an external authorization without dispatch"
    )
    validate.add_argument("authorization", type=Path)
    execute = sub.add_parser(
        "execute", help="START THE EXPERIMENT: run only authorized block-0 smoke"
    )
    execute.add_argument("authorization", type=Path)
    execute.add_argument("--env-file", type=Path, default=STAGE2_ENV_FILE)
    review = sub.add_parser(
        "record-smoke-decision",
        help=(
            "record an external post-smoke review without credentials, "
            "network, or dispatch"
        ),
    )
    review.add_argument("review", type=Path)
    review.add_argument("--run-dir", type=Path)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "inspect":
        value = inspect_gate2_successor(args.proposal)
    elif args.command == "validate-authorization":
        value = validate_gate2_authorization_file(
            args.authorization, args.proposal
        )
    elif args.command == "execute":
        value = run_gate2_successor(
            args.authorization,
            proposal_path=args.proposal,
            env_file=args.env_file,
        )
    else:
        value = record_gate2_smoke_decision(
            args.review,
            proposal_path=args.proposal,
            run_dir=args.run_dir,
        )
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through functions
    raise SystemExit(main())
