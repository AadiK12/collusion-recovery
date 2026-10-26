"""Offline release contract for one-authorisation protocol-v1.2 pilot.

This module intentionally has no live execution entry point.  It cannot load a
credential, construct a provider client, create an empirical run directory, or
dispatch a request.  It defines the proposal, human-authorisation, and
single-use receipt primitives that a future dedicated controller must consume.

One approval covers the complete 1,580-request pilot, but later slices remain
conditional.  Calibration, smoke integrity (including the frozen human review),
and the five-block formation decision still stop later dispatch when they fail.
The cumulative operational cost cap is never reset at a stage boundary.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
from typing import Any, Mapping
import uuid

from .common import canonical, digest
from .events import file_hash
from .orchestration import (
    V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    V1_2_UNIFIED_PILOT_NAMESPACE,
    V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
    V1_2_UNIFIED_PILOT_RUN_ID,
    V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
)
from .visibility import ROOT
from .v1_2_unified_contract import (
    DISPATCH_ATTEMPT_CEILINGS,
    GATE1,
    GATE2,
    GATE3_PHASE1,
    GATE3_PHASE2,
    LOGICAL_REQUEST_CEILINGS,
    RELEASE_ORDER,
    TOTAL_DISPATCH_ATTEMPT_CEILING,
    TOTAL_LOGICAL_REQUEST_CEILING,
    UnifiedV12ContractError,
    validate_unified_stage_caps,
)


MASTER_CONTROLLER_ID = V1_2_UNIFIED_PILOT_CONTROLLER_ID
MASTER_RUN_ID = V1_2_UNIFIED_PILOT_RUN_ID
MASTER_NAMESPACE = V1_2_UNIFIED_PILOT_NAMESPACE
MASTER_RUN_DIRECTORY = f"runs/empirical/v1.2-unified-pilot-{MASTER_RUN_ID}"
MASTER_CONTROLLER_IMPLEMENTED = False

# This value is deliberately source-bound.  It is not accepted from a CLI,
# environment variable, authorisation file, or runtime call.  Before a final
# proposal is rendered it can be changed only by editing reviewed source and
# then regenerating every proposal/schema hash.
SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD = "250.000000"

AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS = 15 * 60
AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS = 60
PRICING_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_JSON_BYTES = 8 * 1024 * 1024

MASTER_PROPOSAL_DRAFT_PATH = ROOT / "configs/v1_2_unified_master_proposal.draft.json"
MASTER_AUTHORIZATION_SCHEMA_PATH = (
    ROOT / "release/V1_2_UNIFIED_HUMAN_AUTHORIZATION_SCHEMA.json"
)
PRIOR_GATE1_PROPOSAL_PATH = ROOT / "configs/gate1_v1_2_calibration_proposal.json"
PRIOR_GATE1_SCHEMA_PATH = ROOT / "release/GATE1_V1_2_HUMAN_AUTHORIZATION_SCHEMA.json"
AUTHORIZATION_RECEIPT_DIRECTORY = (
    Path(pwd.getpwuid(os.getuid()).pw_dir)
    / ".collusion-recovery-authorization-receipts"
    / "v1.2-unified"
)

SOURCE_PRICING_CONTRACT: Mapping[str, Any] = {
    "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
    "reverified_at": "2026-10-25T12:21:28-07:00",
    "standard_rates_usd_per_million": {
        "input": "2.00",
        "cached_input": "0.10",
        "cache_write_input": "2.50",
        "output": "10.00",
    },
    "long_context_threshold_input_tokens": 272000,
    "long_context_input_and_cache_multiplier": "2",
    "long_context_output_multiplier": "1.5",
    "classification_basis": (
        "per-attempt conservative input bound for reservations; reported input "
        "usage for completed-attempt reconciliation"
    ),
}

SOURCE_TRANSPORT_CONTRACT: Mapping[str, Any] = {
    "ambient_openai_environment": "rejected",
    "base_url": "https://api.openai.com/v1",
    "http_client": "openai.DefaultHttpxClient",
    "http_client_follow_redirects": False,
    "http_client_trust_env": False,
    "sdk_max_retries": 0,
    "sdk_package": "openai",
    "sdk_version": "3.37.0",
    "timeout_seconds": 60.0,
    "transport": "official-openai-python-sdk-injected",
}

SOURCE_PROVIDER_IDENTITY: Mapping[str, Any] = {
    "metadata_model": "gpt-6.1-sol",
    "requested_model": "gpt-6.1-sol",
    "requested_service_tier": "default",
    "returned_model": "gpt-6.1-sol",
    "returned_service_tier": "default",
    "system_fingerprint": None,
}

SOURCE_RETRY_POLICY: Mapping[str, Any] = {
    "policy_version": "ambiguous-http-5xx-v1",
    "protocol_version": "v1.2",
    "eligible_http_statuses": [500, 502, 503, 504],
    "maximum_retries_per_logical_identity": 1,
    "release_affected_identity_cap": "max(1,ceil(logical_requests/100))",
    "minimum_delay_seconds": 5,
    "maximum_delay_seconds": 60,
    "sdk_max_retries": 0,
    "accounting": "each ambiguous attempt consumes its full reservation",
    "observation_rule": (
        "the first non-ambiguous returned outcome after at most one "
        "prospectively eligible retry is retained"
    ),
    "model_visibility": (
        "transport-only incidents remain durable for audit and accounting but "
        "never enter model-visible attempt lists or retained scientific history"
    ),
}

HUMAN_ACKNOWLEDGEMENT = (
    "I authorize exactly the complete protocol-v1.2 pilot under this proposal's "
    "$250.000000 cumulative operational hard cap. I understand that later "
    "slices release automatically under the same authorization only after their "
    "frozen scientific and exhaustive outcome-blind mechanical integrity gates "
    "pass, that a failed gate hard-stops all remaining dispatch, that human "
    "transcript and claim audit remains mandatory before publication or "
    "confirmation, and that no confirmation or secondary study is authorized."
)

_STAGE_BINDING_FIELDS = {
    "artifact_type",
    "stage",
    "status",
    "logical_requests",
    "maximum_dispatch_attempts",
    "controller_id",
    "plan_hash",
    "inventory_hash",
    "implementation_hash",
    "exact_caps",
    "evidence",
    "binding_hash",
}

_AUTH_FIELDS = {
    "artifact_type",
    "authorization_uuid",
    "authorization_id",
    "authorization_nonce",
    "master_run_id",
    "master_run_directory",
    "confirmed_by",
    "confirmed_at",
    "proposal_hash",
    "approved_scope",
    "approved_operational_cost_hard_cap_usd",
    "approved_projected_stage_caps_hash",
    "approved_absolute_theoretical_envelope_hash",
    "approved_stage_bindings_hash",
    "approved_source_contract_hash",
    "approved_protocol_hash",
    "approved_pricing_contract_hash",
    "approved_transport_hash",
    "approved_retry_policy_hash",
    "approved_provider_identity",
    "allow_paid_calls",
    "human_acknowledgement",
    "authorization_hash",
}


class V12MasterReleaseError(RuntimeError):
    """The unified v1.2 proposal, authorization, or receipt is unsafe."""


def _exact_positive_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise V12MasterReleaseError(f"{label} must be an exact decimal string")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise V12MasterReleaseError(f"{label} is malformed") from exc
    if not amount.is_finite() or amount <= 0:
        raise V12MasterReleaseError(f"{label} must be finite and positive")
    return amount


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise V12MasterReleaseError(f"{label} must be a nonempty timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V12MasterReleaseError(f"{label} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V12MasterReleaseError(f"{label} requires an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


def _utc_now(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise V12MasterReleaseError("claim clock requires an explicit UTC offset")
    return value.astimezone(timezone.utc)


def _implementation_inventory() -> dict[str, str]:
    paths = sorted((ROOT / "src/collusion_recovery").rglob("*.py"))
    paths.extend(sorted((ROOT / "prompts").glob("*.txt")))
    return {
        str(path.relative_to(ROOT)): file_hash(path)
        for path in paths
        if path.exists()
    }


def current_implementation_hash() -> str:
    return digest(_implementation_inventory())


def _source_contract() -> dict[str, Any]:
    return {
        "master_release_module": {
            "path": "src/collusion_recovery/v1_2_master_release.py",
            "sha256": file_hash(Path(__file__)),
        },
        "conditional_gate_module": {
            "path": "src/collusion_recovery/v1_2_unified_contract.py",
            "sha256": file_hash(ROOT / "src/collusion_recovery/v1_2_unified_contract.py"),
        },
        "exhaustive_smoke_audit_module": {
            "path": "src/collusion_recovery/v1_2_smoke_audit.py",
            "sha256": file_hash(ROOT / "src/collusion_recovery/v1_2_smoke_audit.py"),
        },
        "implementation_hash": current_implementation_hash(),
        "master_controller_implemented": MASTER_CONTROLLER_IMPLEMENTED,
        "unified_request_order_hash": V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
        "unified_stage_partition_hash": V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        "protocol": {
            "base_path": "SPRINT_PROJECT_PLAN.md",
            "base_sha256": file_hash(ROOT / "SPRINT_PROJECT_PLAN.md"),
            "addendum_path": "PROTOCOL_V1_2_ADDENDUM.md",
            "addendum_sha256": file_hash(ROOT / "PROTOCOL_V1_2_ADDENDUM.md"),
        },
    }


def _protocol_hash(source_contract: Mapping[str, Any]) -> str:
    protocol = source_contract["protocol"]
    return digest({
        "base_protocol_sha256": protocol["base_sha256"],
        "addendum_sha256": protocol["addendum_sha256"],
    })


def absolute_theoretical_cost_envelope() -> dict[str, Any]:
    """Return a non-authorizing, deliberately extreme pricing envelope.

    It assumes every one of the 5,517 maximum dispatch attempts consumes the
    largest possible request under the model context limit, all input is billed
    at the most expensive input category, and the long-context multipliers
    apply.  This is not the operational budget and is not a prediction.
    """

    pricing = SOURCE_PRICING_CONTRACT
    rates = pricing["standard_rates_usd_per_million"]
    context_tokens = 1_050_000
    output_tokens = 4_096
    input_tokens = context_tokens - output_tokens
    input_rate = max(_exact_positive_decimal(rates[name], name) for name in (
        "input", "cached_input", "cache_write_input"
    ))
    output_rate = _exact_positive_decimal(rates["output"], "output")
    input_multiplier = _exact_positive_decimal(
        pricing["long_context_input_and_cache_multiplier"], "input multiplier"
    )
    output_multiplier = _exact_positive_decimal(
        pricing["long_context_output_multiplier"], "output multiplier"
    )
    per_attempt = (
        Decimal(input_tokens) * input_rate * input_multiplier
        + Decimal(output_tokens) * output_rate * output_multiplier
    ) / Decimal(1_000_000)
    total = per_attempt * Decimal(TOTAL_DISPATCH_ATTEMPT_CEILING)
    quantum = Decimal("0.000001")
    body = {
        "kind": "absolute_theoretical_non_authorizing_envelope",
        "assumption": (
            "every permitted physical attempt simultaneously saturates the model "
            "context window and the most expensive applicable token categories"
        ),
        "model_context_tokens": context_tokens,
        "max_input_tokens_per_attempt": input_tokens,
        "max_output_tokens_per_attempt": output_tokens,
        "maximum_dispatch_attempts": TOTAL_DISPATCH_ATTEMPT_CEILING,
        "per_attempt_cost_usd": str(per_attempt.quantize(quantum, rounding=ROUND_CEILING)),
        "maximum_cost_usd": str(total.quantize(quantum, rounding=ROUND_CEILING)),
        "authorizes_spend": False,
        "pricing_contract_hash": digest(dict(pricing)),
    }
    return {**body, "envelope_hash": digest(body)}


def build_stage_binding(
    stage: str,
    *,
    status: str,
    controller_id: str | None = None,
    plan_hash: str | None = None,
    inventory_hash: str | None = None,
    implementation_hash: str | None = None,
    exact_caps: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one self-hashed subplan binding without granting execution."""

    if stage not in RELEASE_ORDER:
        raise V12MasterReleaseError("stage binding names an unknown release slice")
    projected_status = "projected_awaiting_independent_audit"
    if status not in {"ready", projected_status, "missing", "superseded"}:
        raise V12MasterReleaseError("stage binding status is invalid")
    hash_pattern = re.compile(r"[a-f0-9]{64}")
    if status in {"ready", projected_status}:
        if not isinstance(controller_id, str) or not controller_id:
            raise V12MasterReleaseError("ready stage binding needs a controller id")
        for label, value in (
            ("plan_hash", plan_hash),
            ("inventory_hash", inventory_hash),
            ("implementation_hash", implementation_hash),
        ):
            if not isinstance(value, str) or hash_pattern.fullmatch(value) is None:
                raise V12MasterReleaseError(f"ready stage binding has invalid {label}")
        if exact_caps is None:
            raise V12MasterReleaseError("ready stage binding needs exact caps")
    elif any(value is not None for value in (
        controller_id, plan_hash, inventory_hash, implementation_hash, exact_caps
    )):
        raise V12MasterReleaseError(
            "non-ready stage binding cannot carry executable fields"
        )
    evidence_value = json.loads(canonical(dict(evidence or {})))
    if status != "ready" and not evidence_value:
        raise V12MasterReleaseError("non-ready stage binding needs a reason")
    if status == "ready" and evidence_value.get("independent_audit_passed") is not True:
        raise V12MasterReleaseError(
            "ready stage binding requires an independent audit pass"
        )
    if (status == projected_status
            and evidence_value.get("independent_audit_passed") is not False):
        raise V12MasterReleaseError(
            "projected stage binding must explicitly await independent audit"
        )
    body = {
        "artifact_type": "v1_2_unified_stage_binding_v1",
        "stage": stage,
        "status": status,
        "logical_requests": LOGICAL_REQUEST_CEILINGS[stage],
        "maximum_dispatch_attempts": DISPATCH_ATTEMPT_CEILINGS[stage],
        "controller_id": controller_id,
        "plan_hash": plan_hash,
        "inventory_hash": inventory_hash,
        "implementation_hash": implementation_hash,
        "exact_caps": (
            None if exact_caps is None
            else json.loads(canonical(dict(exact_caps)))
        ),
        "evidence": evidence_value,
    }
    return {**body, "binding_hash": digest(body)}


def validate_stage_binding(value: Mapping[str, Any], stage: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _STAGE_BINDING_FIELDS:
        raise V12MasterReleaseError("stage binding has an unexpected shape")
    observed = json.loads(canonical(dict(value)))
    body = dict(observed)
    binding_hash = body.pop("binding_hash", None)
    if binding_hash != digest(body):
        raise V12MasterReleaseError("stage binding self-hash is invalid")
    rebuilt = build_stage_binding(
        stage,
        status=observed["status"],
        controller_id=observed["controller_id"],
        plan_hash=observed["plan_hash"],
        inventory_hash=observed["inventory_hash"],
        implementation_hash=observed["implementation_hash"],
        exact_caps=observed["exact_caps"],
        evidence=observed["evidence"],
    )
    if canonical(rebuilt) != canonical(observed):
        raise V12MasterReleaseError("stage binding differs from its exact stage contract")
    return rebuilt


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise V12MasterReleaseError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise V12MasterReleaseError(f"{label} must be an object")
    return value


def current_stage_bindings() -> dict[str, dict[str, Any]]:
    """Describe the current incomplete state without claiming readiness."""

    prior = _read_json(PRIOR_GATE1_PROPOSAL_PATH, "prior v1.2 Gate 1 proposal")
    current_hash = current_implementation_hash()
    prior_hash = prior.get("run", {}).get("implementation_hash")
    if prior_hash == current_hash:
        # A unified controller still cannot reuse a stage-scoped plan/run id.
        reason = "stage-scoped release cannot serve as a one-run master subplan"
    else:
        reason = "implementation inventory changed after the stage-scoped release was rendered"
    bindings = {
        GATE1: build_stage_binding(
            GATE1,
            status="superseded",
            evidence={
                "reason": reason,
                "prior_proposal_path": str(PRIOR_GATE1_PROPOSAL_PATH.relative_to(ROOT)),
                "prior_proposal_hash": prior.get("proposal_hash"),
                "prior_implementation_hash": prior_hash,
                "current_implementation_hash": current_hash,
                "dispatch_permitted": False,
            },
        ),
    }
    missing = {
        GATE2: (
            "unified block-0 smoke inventory/caps are not rendered and the live "
            "master-controller transition is not implemented"
        ),
        GATE3_PHASE1: (
            "unified blocks 1-4 formation inventory/caps are not rendered and "
            "the live master-controller transition is not implemented"
        ),
        GATE3_PHASE2: (
            "unified remaining-arm/probe inventory/caps are not rendered and "
            "the live master-controller transition is not implemented"
        ),
    }
    for stage, reason_value in missing.items():
        bindings[stage] = build_stage_binding(
            stage,
            status="missing",
            evidence={"reason": reason_value, "dispatch_permitted": False},
        )
    return bindings


def build_v1_2_master_proposal(
    stage_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the source-bound proposal or its non-issuable current draft.

    The operational cap is intentionally not a parameter.  Changing it requires
    a reviewed source edit and therefore changes the source and proposal hashes.
    """

    raw_bindings = current_stage_bindings() if stage_bindings is None else stage_bindings
    if not isinstance(raw_bindings, Mapping) or set(raw_bindings) != set(RELEASE_ORDER):
        raise V12MasterReleaseError(
            f"stage bindings must contain exactly {list(RELEASE_ORDER)}"
        )
    bindings = {
        stage: validate_stage_binding(raw_bindings[stage], stage)
        for stage in RELEASE_ORDER
    }
    all_ready = all(row["status"] == "ready" for row in bindings.values())
    all_projected = all(
        row["status"] in {"ready", "projected_awaiting_independent_audit"}
        for row in bindings.values()
    )
    projected_caps: dict[str, Any] | None = None
    cap_error: str | None = None
    if all_projected:
        try:
            projected_caps = validate_unified_stage_caps({
                stage: bindings[stage]["exact_caps"] for stage in RELEASE_ORDER
            })
        except UnifiedV12ContractError as exc:
            raise V12MasterReleaseError(str(exc)) from exc
        projected_cost = _exact_positive_decimal(
            projected_caps["max_cost_usd"], "projected stage cost"
        )
        operational = _exact_positive_decimal(
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
            "operational cost hard cap",
        )
        if projected_cost > operational:
            cap_error = (
                "rendered stage reservation ceilings exceed the source-bound "
                "operational hard cap"
            )
    source_contract = _source_contract()
    execution_ready = (
        all_ready and cap_error is None and MASTER_CONTROLLER_IMPLEMENTED
    )
    protocol_hash = _protocol_hash(source_contract)
    theoretical = absolute_theoretical_cost_envelope()
    operational_cap = {
        "currency": "USD",
        "max_cost_usd": SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
        "enforcement": "reserve before every physical dispatch",
        "scope": "cumulative across the single master run and every stage",
        "resets_at_stage_boundary": False,
        "runtime_override_allowed": False,
        "exhaustion_behavior": "hard stop before the next unaffordable dispatch",
    }
    scope = {
        "protocol_version": "v1.2",
        "run_type": "pilot",
        "logical_requests": TOTAL_LOGICAL_REQUEST_CEILING,
        "maximum_dispatch_attempts": TOTAL_DISPATCH_ATTEMPT_CEILING,
        "release_order": list(RELEASE_ORDER),
        "later_slices_conditionally_authorized": True,
        "failed_gate_hard_stops_remaining_dispatch": True,
        "smoke_automatic_continuation_requires_exhaustive_outcome_blind_"
        "mechanical_integrity_pass": True,
        "mid_run_human_smoke_approval_required": False,
        "human_transcript_and_claim_audit_timing": "post_run_pre_publication",
        "post_run_human_audit_failure_invalidates_publication_claims_and_"
        "hard_stops_confirmation": True,
        "confirmation_authorized": False,
        "secondary_studies_authorized": False,
    }
    body = {
        "artifact_type": "v1_2_unified_master_proposal_v1",
        "status": (
            "awaiting_external_human_authorization"
            if execution_ready else "offline_draft_not_authorizable"
        ),
        "dispatch_enabled": False,
        "execution_ready": execution_ready,
        "authorization_issuance_enabled": execution_ready,
        "credentials_required_for_inspection": False,
        "credential_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
        "controller": {
            "controller_id": MASTER_CONTROLLER_ID,
            "implemented": MASTER_CONTROLLER_IMPLEMENTED,
            "one_run": True,
            "automatic_conditional_release": True,
            "outcome_blind_exhaustive_smoke_audit": True,
            "post_run_human_audit_required_before_claims_or_confirmation": True,
            "caller_supplied_provider_or_executor_allowed": False,
        },
        "run": {
            "run_id": MASTER_RUN_ID,
            "run_directory": MASTER_RUN_DIRECTORY,
            "namespace": MASTER_NAMESPACE,
            "implementation_hash": source_contract["implementation_hash"],
            "request_order_hash": V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
            "stage_partition_hash": V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        },
        "scope": scope,
        "stage_bindings": bindings,
        "stage_bindings_hash": digest(bindings),
        "projected_stage_caps": projected_caps,
        "projected_stage_caps_hash": (
            None if projected_caps is None else digest(projected_caps)
        ),
        "operational_cost_hard_cap": operational_cap,
        "absolute_theoretical_cost_envelope": theoretical,
        "pricing_contract": json.loads(canonical(dict(SOURCE_PRICING_CONTRACT))),
        "pricing_contract_hash": digest(dict(SOURCE_PRICING_CONTRACT)),
        "transport": json.loads(canonical(dict(SOURCE_TRANSPORT_CONTRACT))),
        "transport_hash": digest(dict(SOURCE_TRANSPORT_CONTRACT)),
        "retry_policy": json.loads(canonical(dict(SOURCE_RETRY_POLICY))),
        "retry_policy_hash": digest(dict(SOURCE_RETRY_POLICY)),
        "required_provider_identity": json.loads(
            canonical(dict(SOURCE_PROVIDER_IDENTITY))
        ),
        "source_contract": source_contract,
        "source_contract_hash": digest(source_contract),
        "protocol_hash": protocol_hash,
        "hard_stops": {
            "before_first_dispatch_without_consumed_receipt": True,
            "on_calibration_failure": True,
            "after_smoke_until_exhaustive_outcome_blind_mechanical_integrity_pass": True,
            "on_smoke_failure": True,
            "after_phase1_until_formation_decision": True,
            "on_fewer_than_three_of_five_qualifying_c_histories": True,
            "after_complete_pilot_before_confirmation": True,
            "on_operational_cost_cap_exhaustion": True,
            "on_post_run_human_transcript_or_claim_audit_failure": True,
        },
        "non_ready_reasons": ([
            f"{stage}: {bindings[stage]['status']}"
            for stage in RELEASE_ORDER
            if bindings[stage]["status"] != "ready"
        ] + ([] if cap_error is None else [cap_error])),
        "external_authorization": {
            "required": True,
            "created": False,
            "single_approval_covers_all_conditional_slices": True,
            "single_use_receipt_required_before_credential_access": True,
            "must_be_outside_repository": True,
            "owner_only_file_required": True,
            "maximum_unclaimed_age_seconds": AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS,
            "maximum_future_skew_seconds": AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS,
            "maximum_pricing_age_seconds": PRICING_MAX_AGE_SECONDS,
            "schema_artifact": (
                str(MASTER_AUTHORIZATION_SCHEMA_PATH.relative_to(ROOT))
                if execution_ready else None
            ),
            "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
        },
        "supersedes": {
            "stage_scoped_gate1_proposal": str(
                PRIOR_GATE1_PROPOSAL_PATH.relative_to(ROOT)
            ),
            "stage_scoped_gate1_schema": str(
                PRIOR_GATE1_SCHEMA_PATH.relative_to(ROOT)
            ),
            "reason": (
                "the complete package implementation hash changed and the new "
                "one-run controller requires freshly rendered subplans"
            ),
            "old_artifacts_dispatch_authority": False,
        },
    }
    return {**body, "proposal_hash": digest(body)}


def validate_v1_2_master_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V12MasterReleaseError("master proposal must be an object")
    proposal = json.loads(canonical(dict(value)))
    body = dict(proposal)
    proposal_hash = body.pop("proposal_hash", None)
    if proposal_hash != digest(body):
        raise V12MasterReleaseError("master proposal self-hash is invalid")
    if proposal.get("artifact_type") != "v1_2_unified_master_proposal_v1":
        raise V12MasterReleaseError("master proposal type is invalid")
    if proposal.get("run", {}).get("run_id") != MASTER_RUN_ID:
        raise V12MasterReleaseError("master proposal run id is invalid")
    if proposal.get("operational_cost_hard_cap", {}).get("max_cost_usd") != (
        SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
    ):
        raise V12MasterReleaseError("master operational cap differs from source")
    expected_source = _source_contract()
    if canonical(proposal.get("source_contract")) != canonical(expected_source):
        raise V12MasterReleaseError("master proposal differs from current source")
    if proposal.get("source_contract_hash") != digest(expected_source):
        raise V12MasterReleaseError("master proposal source hash is invalid")
    if proposal.get("protocol_hash") != _protocol_hash(expected_source):
        raise V12MasterReleaseError("master protocol hash is invalid")
    bindings = proposal.get("stage_bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != set(RELEASE_ORDER):
        raise V12MasterReleaseError("master stage bindings are invalid")
    normalized = {
        stage: validate_stage_binding(bindings[stage], stage)
        for stage in RELEASE_ORDER
    }
    if proposal.get("stage_bindings_hash") != digest(normalized):
        raise V12MasterReleaseError("master stage binding hash is invalid")
    expected_theoretical = absolute_theoretical_cost_envelope()
    if canonical(proposal.get("absolute_theoretical_cost_envelope")) != canonical(
        expected_theoretical
    ):
        raise V12MasterReleaseError("master theoretical envelope differs from source")
    ready = all(row["status"] == "ready" for row in normalized.values())
    all_projected = all(
        row["status"] in {"ready", "projected_awaiting_independent_audit"}
        for row in normalized.values()
    )
    if all_projected:
        try:
            projected = validate_unified_stage_caps({
                stage: normalized[stage]["exact_caps"] for stage in RELEASE_ORDER
            })
        except UnifiedV12ContractError as exc:
            raise V12MasterReleaseError(str(exc)) from exc
        if canonical(projected) != canonical(proposal.get("projected_stage_caps")):
            raise V12MasterReleaseError("master projected stage caps are invalid")
        if proposal.get("projected_stage_caps_hash") != digest(projected):
            raise V12MasterReleaseError("master projected stage cap hash is invalid")
        projected_cost = _exact_positive_decimal(
            projected["max_cost_usd"], "projected stage cost"
        )
        operational = _exact_positive_decimal(
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD, "operational cap"
        )
        executable = (
            ready
            and projected_cost <= operational
            and MASTER_CONTROLLER_IMPLEMENTED
        )
    else:
        if proposal.get("projected_stage_caps") is not None:
            raise V12MasterReleaseError("incomplete proposal carries projected caps")
        if proposal.get("projected_stage_caps_hash") is not None:
            raise V12MasterReleaseError("incomplete proposal carries a cap hash")
        executable = False
    if proposal.get("execution_ready") is not executable:
        raise V12MasterReleaseError("master execution readiness is inconsistent")
    if proposal.get("authorization_issuance_enabled") is not executable:
        raise V12MasterReleaseError("master authorization readiness is inconsistent")
    expected = build_v1_2_master_proposal(normalized)
    if canonical(proposal) != canonical(expected):
        raise V12MasterReleaseError("master proposal differs from current source")
    return proposal


def _require_final_proposal(proposal: Mapping[str, Any]) -> dict[str, Any]:
    value = validate_v1_2_master_proposal(proposal)
    if (value["execution_ready"] is not True
            or value["authorization_issuance_enabled"] is not True
            or value["controller"]["implemented"] is not True):
        raise V12MasterReleaseError(
            "master draft cannot issue authorization or execute"
        )
    return value


def authorization_schema(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact one-approval schema for a final, executable proposal."""

    proposal = _require_final_proposal(proposal)
    uuid_pattern = (
        "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
        "[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:collusion-recovery:v1.2-unified-pilot-authorization:v1",
        "title": "Unified protocol-v1.2 pilot human authorization",
        "$comment": (
            "One single-use authorization covers the four conditionally released "
            "pilot slices. authorization_hash is sha256 of canonical JSON after "
            "removing authorization_hash."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_AUTH_FIELDS),
        "properties": {
            "artifact_type": {"const": "v1_2_unified_pilot_human_authorization_v1"},
            "authorization_uuid": {"type": "string", "pattern": uuid_pattern},
            "authorization_id": {
                "type": "string",
                "pattern": "^v1-2-unified-human-[0-9a-f-]{36}$",
            },
            "authorization_nonce": {
                "type": "string", "pattern": "^[0-9a-f]{64}$"
            },
            "master_run_id": {"const": MASTER_RUN_ID},
            "master_run_directory": {"const": MASTER_RUN_DIRECTORY},
            "confirmed_by": {"type": "string", "minLength": 1, "maxLength": 200},
            "confirmed_at": {"type": "string", "format": "date-time"},
            "proposal_hash": {"const": proposal["proposal_hash"]},
            "approved_scope": {"const": proposal["scope"]},
            "approved_operational_cost_hard_cap_usd": {
                "const": SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
            },
            "approved_projected_stage_caps_hash": {
                "const": proposal["projected_stage_caps_hash"]
            },
            "approved_absolute_theoretical_envelope_hash": {
                "const": proposal["absolute_theoretical_cost_envelope"]["envelope_hash"]
            },
            "approved_stage_bindings_hash": {
                "const": proposal["stage_bindings_hash"]
            },
            "approved_source_contract_hash": {
                "const": proposal["source_contract_hash"]
            },
            "approved_protocol_hash": {"const": proposal["protocol_hash"]},
            "approved_pricing_contract_hash": {
                "const": proposal["pricing_contract_hash"]
            },
            "approved_transport_hash": {"const": proposal["transport_hash"]},
            "approved_retry_policy_hash": {"const": proposal["retry_policy_hash"]},
            "approved_provider_identity": {
                "const": proposal["required_provider_identity"]
            },
            "allow_paid_calls": {"const": True},
            "human_acknowledgement": {"const": HUMAN_ACKNOWLEDGEMENT},
            "authorization_hash": {
                "type": "string", "pattern": "^[a-f0-9]{64}$"
            },
        },
    }


def _authorization_exact_fields(proposal: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "artifact_type": "v1_2_unified_pilot_human_authorization_v1",
        "master_run_id": MASTER_RUN_ID,
        "master_run_directory": MASTER_RUN_DIRECTORY,
        "proposal_hash": proposal["proposal_hash"],
        "approved_scope": proposal["scope"],
        "approved_operational_cost_hard_cap_usd": (
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
        ),
        "approved_projected_stage_caps_hash": proposal["projected_stage_caps_hash"],
        "approved_absolute_theoretical_envelope_hash": proposal[
            "absolute_theoretical_cost_envelope"
        ]["envelope_hash"],
        "approved_stage_bindings_hash": proposal["stage_bindings_hash"],
        "approved_source_contract_hash": proposal["source_contract_hash"],
        "approved_protocol_hash": proposal["protocol_hash"],
        "approved_pricing_contract_hash": proposal["pricing_contract_hash"],
        "approved_transport_hash": proposal["transport_hash"],
        "approved_retry_policy_hash": proposal["retry_policy_hash"],
        "approved_provider_identity": proposal["required_provider_identity"],
        "allow_paid_calls": True,
        "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
    }


def validate_human_authorization(
    value: Mapping[str, Any], proposal: Mapping[str, Any]
) -> dict[str, Any]:
    proposal = _require_final_proposal(proposal)
    if not isinstance(value, Mapping) or set(value) != _AUTH_FIELDS:
        raise V12MasterReleaseError(
            f"human authorization must contain exactly {sorted(_AUTH_FIELDS)}"
        )
    authorization = json.loads(canonical(dict(value)))
    body = dict(authorization)
    authorization_hash = body.pop("authorization_hash", None)
    if (not isinstance(authorization_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", authorization_hash) is None
            or authorization_hash != digest(body)):
        raise V12MasterReleaseError("human authorization self-hash is invalid")
    try:
        authorization_uuid = uuid.UUID(authorization["authorization_uuid"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise V12MasterReleaseError("authorization UUID must be UUIDv4") from exc
    uuid_text = str(authorization_uuid)
    if authorization_uuid.version != 4 or uuid_text != authorization["authorization_uuid"]:
        raise V12MasterReleaseError("authorization UUID must be lowercase UUIDv4")
    if authorization["authorization_id"] != f"v1-2-unified-human-{uuid_text}":
        raise V12MasterReleaseError("authorization id does not bind its UUID")
    nonce = authorization["authorization_nonce"]
    if (not isinstance(nonce, str)
            or re.fullmatch(r"[a-f0-9]{64}", nonce) is None
            or len(set(nonce)) < 8):
        raise V12MasterReleaseError("authorization nonce must be fresh 64-hex")
    confirmer = authorization["confirmed_by"]
    if (not isinstance(confirmer, str) or not confirmer.strip()
            or len(confirmer) > 200):
        raise V12MasterReleaseError("confirmed_by must name the human authorizer")
    _parse_timestamp(authorization["confirmed_at"], "confirmed_at")
    exact = _authorization_exact_fields(proposal)
    if any(authorization.get(name) != expected for name, expected in exact.items()):
        raise V12MasterReleaseError(
            "human authorization does not bind the exact unified release"
        )
    return authorization


def _validate_external_parent_chain(path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    parent = lexical.parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current = current / part
        try:
            details = current.lstat()
        except OSError as exc:
            raise V12MasterReleaseError(
                "human authorization parent path is unavailable"
            ) from exc
        if (stat.S_ISLNK(details.st_mode)
                or not stat.S_ISDIR(details.st_mode)
                or details.st_uid not in {0, os.getuid()}
                or details.st_mode & 0o022):
            raise V12MasterReleaseError(
                "authorization parent components must be safe non-writable directories"
            )
    if parent.lstat().st_uid != os.getuid():
        raise V12MasterReleaseError(
            "authorization immediate parent must be current-user-owned"
        )
    resolved = parent.resolve(strict=True) / lexical.name
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return lexical
    raise V12MasterReleaseError("human authorization must be outside the repository")


def load_human_authorization(
    path: Path, proposal: Mapping[str, Any]
) -> dict[str, Any]:
    """Read a bounded owner-only external artifact once, then validate it."""

    lexical = _validate_external_parent_chain(Path(path))
    value = _read_owner_only_json(lexical, "master human authorization")
    return validate_human_authorization(value, proposal)


def complete_human_authorization(
    externally_supplied_fields: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> dict[str, Any]:
    """Package human-supplied fields; it never infers or creates approval."""

    proposal = _require_final_proposal(proposal)
    human_fields = {
        "authorization_uuid",
        "authorization_id",
        "authorization_nonce",
        "confirmed_by",
        "confirmed_at",
    }
    if (not isinstance(externally_supplied_fields, Mapping)
            or set(externally_supplied_fields) != human_fields):
        raise V12MasterReleaseError(
            "authorization packaging requires exactly the external human fields"
        )
    authorization = json.loads(canonical(dict(externally_supplied_fields)))
    authorization.update(_authorization_exact_fields(proposal))
    authorization["authorization_hash"] = digest(authorization)
    return validate_human_authorization(authorization, proposal)


def _validate_first_claim_window(
    authorization: Mapping[str, Any],
    proposal: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    current = _utc_now(now)
    confirmed = _parse_timestamp(authorization["confirmed_at"], "confirmed_at")
    pricing = _parse_timestamp(
        proposal["pricing_contract"]["reverified_at"], "pricing reverification"
    )
    skew = timedelta(seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS)
    if confirmed > current + skew:
        raise V12MasterReleaseError("authorization timestamp is in the future")
    if pricing > current + skew:
        raise V12MasterReleaseError("pricing timestamp is in the future")
    if confirmed < pricing:
        raise V12MasterReleaseError("authorization predates pricing verification")
    if current - confirmed > timedelta(
        seconds=AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS
    ):
        raise V12MasterReleaseError("unused master authorization expired")
    if current - pricing > timedelta(seconds=PRICING_MAX_AGE_SECONDS):
        raise V12MasterReleaseError("master pricing is stale; rerender and reauthorize")


def build_authorization_receipt(
    authorization: Mapping[str, Any],
    proposal: Mapping[str, Any],
    *,
    claimed_at: datetime,
) -> dict[str, Any]:
    """Build the immutable receipt value; perform no filesystem mutation."""

    proposal = _require_final_proposal(proposal)
    authorization = validate_human_authorization(authorization, proposal)
    _validate_first_claim_window(authorization, proposal, now=claimed_at)
    claimed = _utc_now(claimed_at).isoformat()
    body = {
        "artifact_type": "v1_2_unified_authorization_receipt_v1",
        "controller_id": MASTER_CONTROLLER_ID,
        "master_run_id": MASTER_RUN_ID,
        "master_run_directory": MASTER_RUN_DIRECTORY,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_id": authorization["authorization_id"],
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "confirmed_at": authorization["confirmed_at"],
        "claimed_at": claimed,
        "source_contract_hash": proposal["source_contract_hash"],
        "stage_bindings_hash": proposal["stage_bindings_hash"],
        "projected_stage_caps_hash": proposal["projected_stage_caps_hash"],
        "operational_cost_hard_cap_usd": (
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
        ),
        "scope_hash": digest(proposal["scope"]),
        "consumes_entire_master_authorization": True,
        "stage_boundaries_do_not_reset_authority_or_budget": True,
        "credential_access_permitted_only_after_receipt": True,
    }
    return {**body, "receipt_hash": digest(body)}


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (canonical(dict(value)) + "\n").encode("utf-8")
    temporary = path.parent / (
        f".{path.name}.publish-{os.getpid()}-{secrets.token_hex(16)}"
    )
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    closed = False
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short immutable receipt write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        closed = True
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        if not closed:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _validate_receipt_registry(path: Path) -> Path:
    expected = Path(os.path.abspath(AUTHORIZATION_RECEIPT_DIRECTORY))
    observed = Path(os.path.abspath(path))
    if observed != expected:
        raise V12MasterReleaseError("receipt registry is not the fixed master registry")
    trusted_home = expected.parents[1]
    if not trusted_home.exists():
        raise V12MasterReleaseError("trusted receipt home does not exist")
    current = trusted_home
    for part in (None, *expected.relative_to(trusted_home).parts):
        if part is not None:
            current = current / part
        if current.is_symlink():
            raise V12MasterReleaseError("receipt registry cannot contain symlinks")
        if current.exists():
            details = current.lstat()
            if (not stat.S_ISDIR(details.st_mode)
                    or details.st_uid != os.getuid()
                    or details.st_mode & 0o022):
                raise V12MasterReleaseError(
                    "receipt registry components must be owner-controlled directories"
                )
    return expected


def _read_owner_only_json(path: Path, label: str) -> dict[str, Any]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise V12MasterReleaseError("platform cannot enforce no-follow receipt reads")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise V12MasterReleaseError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_mode & 0o077
                or before.st_nlink != 1
                or before.st_size < 2
                or before.st_size > MAX_JSON_BYTES):
            raise V12MasterReleaseError(f"{label} must be a bounded owner-only file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(16 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        if (remaining != 0
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise V12MasterReleaseError(f"{label} changed while being read")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise V12MasterReleaseError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise V12MasterReleaseError(f"{label} must be an object")
    return value


def _receipt_path(directory: Path) -> Path:
    return directory / f"{MASTER_CONTROLLER_ID}--{MASTER_RUN_ID}.json"


def claim_human_authorization_once(
    authorization: Mapping[str, Any],
    proposal: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically consume the full master authority before credential access."""

    current = _utc_now(now)
    proposal = _require_final_proposal(proposal)
    authorization = validate_human_authorization(authorization, proposal)
    _validate_first_claim_window(authorization, proposal, now=current)
    registry = _validate_receipt_registry(AUTHORIZATION_RECEIPT_DIRECTORY)
    registry.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(registry, 0o700)
    path = _receipt_path(registry)
    receipt = build_authorization_receipt(
        authorization, proposal, claimed_at=current
    )
    try:
        _publish_exclusive_json(path, receipt)
        return receipt
    except FileExistsError:
        existing = _read_owner_only_json(path, "master authorization receipt")
        body = dict(existing)
        receipt_hash = body.pop("receipt_hash", None)
        if receipt_hash != digest(body):
            raise V12MasterReleaseError("existing receipt self-hash is invalid")
        immutable = {
            key: value
            for key, value in receipt.items()
            if key not in {"claimed_at", "receipt_hash"}
        }
        if any(existing.get(key) != value for key, value in immutable.items()):
            raise V12MasterReleaseError(
                "master authorization was already claimed by a different release"
            )
        return existing


__all__ = [
    "AUTHORIZATION_RECEIPT_DIRECTORY",
    "HUMAN_ACKNOWLEDGEMENT",
    "MASTER_CONTROLLER_ID",
    "MASTER_RUN_DIRECTORY",
    "MASTER_RUN_ID",
    "SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD",
    "V12MasterReleaseError",
    "absolute_theoretical_cost_envelope",
    "authorization_schema",
    "build_authorization_receipt",
    "build_stage_binding",
    "build_v1_2_master_proposal",
    "claim_human_authorization_once",
    "complete_human_authorization",
    "current_stage_bindings",
    "load_human_authorization",
    "validate_human_authorization",
    "validate_stage_binding",
    "validate_v1_2_master_proposal",
]
