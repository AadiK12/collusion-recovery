"""Exact no-dispatch prompt-shape projection for the unified v1.2 pilot.

The projection renders every initial request and every permitted overlength
message-retry shape against the same maximal local synthetic transcript used by
the reviewed staged releases.  It then adds the most expensive distinct
logical identities allowed by the v1.2 ambiguous-5xx policy.  Known-not-
completed transport retries count toward the physical-attempt ceiling but not
the consumed token/cost ceiling because their reservations are released.

This module never loads a credential, constructs an SDK client, creates a live
run, opens a socket, or dispatches a provider request.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
import json
import math
from typing import Any, Mapping

from .budget import (
    LONG_CONTEXT_THRESHOLD_INPUT_TOKENS,
    pricing_multipliers,
)
from .common import canonical, digest
from .live_config import LiveConfig
from .orchestration import (
    V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
    V1_2_CALIBRATION_NAMESPACE,
    V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    V1_2_UNIFIED_PILOT_NAMESPACE,
    V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
    V1_2_UNIFIED_PILOT_RUN_ID,
    V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
    build_request_plan,
)
from .providers.openai import OpenAIResponsesProvider
from .staged_requests import (
    ScientificRequestFactory,
    _SyntheticAuditContext,
    _synthetic_audit_results,
    protocol_prompt_inventory,
)
from .v1_2_master_release import (
    MASTER_CONTROLLER_ID,
    MASTER_RUN_ID,
    SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
    SOURCE_PRICING_CONTRACT,
    SOURCE_PROVIDER_IDENTITY,
    SOURCE_TRANSPORT_CONTRACT,
    V12MasterReleaseError,
    build_stage_binding,
)
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
    validate_unified_stage_caps,
)


PROJECTION_ALGORITHM_VERSION = "v1-2-unified-maximal-shape-projection-v1"
MODEL_CONTEXT_TOKENS = 1_050_000
MAX_OUTPUT_TOKENS = 4_096
MAX_INPUT_TOKENS = MODEL_CONTEXT_TOKENS - MAX_OUTPUT_TOKENS
_QUANTUM = Decimal("0.000001")

_PLAN_STAGE_TO_RELEASE = {
    "calibration": GATE1,
    "smoke": GATE2,
    "formation": GATE3_PHASE1,
    "remaining_arms": GATE3_PHASE2,
    "probes": GATE3_PHASE2,
}


class V12MasterProjectionError(V12MasterReleaseError):
    """The exact offline projection drifted or could not be rendered."""


def _config_values(limits: Mapping[str, Any]) -> dict[str, Any]:
    rates = SOURCE_PRICING_CONTRACT["standard_rates_usd_per_million"]
    return {
        "run_id": V1_2_UNIFIED_PILOT_RUN_ID,
        "protocol_version": "v1.2",
        "source": "empirical-live",
        "provider": "openai",
        "model": SOURCE_PROVIDER_IDENTITY["requested_model"],
        "model_context_tokens": MODEL_CONTEXT_TOKENS,
        "model_settings": {
            "reasoning": {"effort": "low", "mode": "standard"},
            "service_tier": SOURCE_PROVIDER_IDENTITY["requested_service_tier"],
        },
        "pricing": {
            "source": SOURCE_PRICING_CONTRACT["source"],
            "verified_at": SOURCE_PRICING_CONTRACT["reverified_at"],
            "input_usd_per_million": rates["input"],
            "cached_input_usd_per_million": rates["cached_input"],
            "cache_write_input_usd_per_million": rates["cache_write_input"],
            "output_usd_per_million": rates["output"],
        },
        "authorization": {
            "kind": "paid",
            "authorization_id": "proposal-only-not-a-dispatch-release",
            "approved_at": SOURCE_PRICING_CONTRACT["reverified_at"],
            "approved_budget_usd": SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
            "allow_paid_calls": True,
        },
        "limits": json.loads(canonical(dict(limits))),
        "retry_policy": {
            "transport_retries": 2,
            "message_overlength_retries": 1,
            "initial_backoff_seconds": "1",
            "maximum_backoff_seconds": "8",
        },
        "run_type": "pilot",
        "split": V1_2_UNIFIED_PILOT_NAMESPACE,
        "stage": "probes",
        "seed_namespace": V1_2_UNIFIED_PILOT_NAMESPACE,
        "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
        "blocks": 5,
    }


def _strict_shape_cost(config: LiveConfig, row: Mapping[str, Any]) -> Decimal:
    input_tokens = row["conservative_input_token_bound"]
    output_tokens = row["max_output_tokens"]
    input_multiplier, output_multiplier = pricing_multipliers(
        config.pricing, input_tokens
    )
    input_rate = max(
        config.pricing.input_usd_per_million,
        config.pricing.cached_input_usd_per_million,
        config.pricing.cache_write_input_usd_per_million,
    )
    return (
        Decimal(input_tokens) * input_rate * input_multiplier
        + Decimal(output_tokens)
        * config.pricing.output_usd_per_million
        * output_multiplier
    ) / Decimal(1_000_000)


def _nominal_shape_cost(config: LiveConfig, row: Mapping[str, Any]) -> Decimal:
    input_tokens = row["conservative_input_token_bound"]
    output_tokens = row["max_output_tokens"]
    input_multiplier, output_multiplier = pricing_multipliers(
        config.pricing, input_tokens
    )
    return (
        Decimal(input_tokens)
        * config.pricing.input_usd_per_million
        * input_multiplier
        + Decimal(output_tokens)
        * config.pricing.output_usd_per_million
        * output_multiplier
    ) / Decimal(1_000_000)


def _render_rows(config: LiveConfig, plan: Any) -> list[dict[str, Any]]:
    factory = ScientificRequestFactory(config, plan)
    synthetic = _synthetic_audit_results(config, plan)
    base_context = _SyntheticAuditContext(synthetic)
    rows: list[dict[str, Any]] = []
    for spec in plan.requests:
        try:
            release_stage = _PLAN_STAGE_TO_RELEASE[spec.stage]
        except KeyError as exc:
            raise V12MasterProjectionError(
                f"unified plan contains unsupported stage {spec.stage!r}"
            ) from exc
        contexts = [("initial_attempt", base_context)]
        if spec.action == "message":
            contexts.append((
                "overlength_retry",
                _SyntheticAuditContext(
                    synthetic,
                    attempt=1,
                    prior_results=(synthetic[spec.identity][0],),
                ),
            ))
        for shape, context in contexts:
            request, rendered = factory.build(spec, context)
            wire = OpenAIResponsesProvider.build_payload(request)
            row = {
                "release_stage": release_stage,
                "plan_stage": spec.stage,
                "identity": spec.identity,
                "ordinal": spec.ordinal,
                "phase": spec.phase,
                "action": spec.action,
                "metadata": dict(spec.metadata),
                "projected_shape": shape,
                "request_hash": digest(request.to_dict()),
                "prompt_hash": rendered.prompt_hash,
                "wire_payload_hash": digest(wire),
                "wire_payload_utf8_bytes": len(
                    canonical(wire).encode("utf-8")
                ),
                "conservative_input_token_bound": (
                    request.conservative_input_token_bound
                ),
                "max_output_tokens": request.max_output_tokens,
            }
            row["strict_reservation_cost_usd"] = str(
                _strict_shape_cost(config, row)
            )
            row["nominal_uncached_cost_usd"] = str(
                _nominal_shape_cost(config, row)
            )
            row["shape_hash"] = digest(row)
            rows.append(row)
    return rows


def _ambiguous_allowance_rows(
    rows: list[dict[str, Any]], affected_identity_cap: int
) -> list[dict[str, Any]]:
    most_expensive_by_identity: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = most_expensive_by_identity.get(row["identity"])
        key = (
            Decimal(row["strict_reservation_cost_usd"]),
            row["conservative_input_token_bound"],
            row["max_output_tokens"],
            row["shape_hash"],
        )
        if current is None:
            most_expensive_by_identity[row["identity"]] = row
            continue
        current_key = (
            Decimal(current["strict_reservation_cost_usd"]),
            current["conservative_input_token_bound"],
            current["max_output_tokens"],
            current["shape_hash"],
        )
        if key > current_key:
            most_expensive_by_identity[row["identity"]] = row
    selected = sorted(
        most_expensive_by_identity.values(),
        key=lambda row: (
            Decimal(row["strict_reservation_cost_usd"]),
            row["conservative_input_token_bound"],
            row["max_output_tokens"],
            row["shape_hash"],
        ),
        reverse=True,
    )[:affected_identity_cap]
    return [{
        "identity": row["identity"],
        "projected_shape": row["projected_shape"],
        "shape_hash": row["shape_hash"],
        "conservative_input_token_bound": row[
            "conservative_input_token_bound"
        ],
        "max_output_tokens": row["max_output_tokens"],
        "strict_reservation_cost_usd": row["strict_reservation_cost_usd"],
        "nominal_uncached_cost_usd": row["nominal_uncached_cost_usd"],
    } for row in selected]


def _stage_inventory(
    config: LiveConfig,
    plan: Any,
    release_stage: str,
    all_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    specs = [
        spec for spec in plan.requests
        if _PLAN_STAGE_TO_RELEASE[spec.stage] == release_stage
    ]
    rows = [row for row in all_rows if row["release_stage"] == release_stage]
    logical = LOGICAL_REQUEST_CEILINGS[release_stage]
    message_count = sum(spec.action == "message" for spec in specs)
    verdict_count = sum(spec.action == "verdict" for spec in specs)
    affected_cap = max(1, math.ceil(logical / 100))
    ambiguous = _ambiguous_allowance_rows(rows, affected_cap)
    known_not_completed = logical * config.retry_policy.transport_retries
    expected_attempts = len(rows) + known_not_completed + affected_cap
    if (len(specs) != logical
            or len(rows) != logical + message_count
            or message_count + verdict_count != logical
            or expected_attempts != DISPATCH_ATTEMPT_CEILINGS[release_stage]
            or len({row["identity"] for row in ambiguous}) != affected_cap):
        raise V12MasterProjectionError(
            f"{release_stage} projected attempt accounting drifted"
        )
    billable_rows: list[Mapping[str, Any]] = [*rows, *ambiguous]
    input_sum = sum(
        row["conservative_input_token_bound"] for row in billable_rows
    )
    output_sum = sum(row["max_output_tokens"] for row in billable_rows)
    strict_cost = sum(
        (Decimal(row["strict_reservation_cost_usd"]) for row in billable_rows),
        Decimal("0"),
    )
    nominal_cost = sum(
        (Decimal(row["nominal_uncached_cost_usd"]) for row in billable_rows),
        Decimal("0"),
    )
    longest = max(
        rows,
        key=lambda row: (
            row["conservative_input_token_bound"], row["max_output_tokens"]
        ),
    )
    exact_caps = {
        "logical_requests": logical,
        "dispatch_attempts": expected_attempts,
        "max_total_input_tokens": input_sum,
        "max_total_output_tokens": output_sum,
        "max_input_tokens_per_request": longest[
            "conservative_input_token_bound"
        ],
        "max_output_tokens_per_request": max(
            row["max_output_tokens"] for row in rows
        ),
        "max_in_flight": 1,
        "max_cost_usd": str(
            strict_cost.quantize(_QUANTUM, rounding=ROUND_CEILING)
        ),
    }
    body = {
        "artifact_type": "v1_2_unified_stage_maximal_shape_projection_v1",
        "projection_only": True,
        "dispatch_performed": False,
        "release_stage": release_stage,
        "logical_request_count": logical,
        "message_request_count": message_count,
        "verdict_request_count": verdict_count,
        "initial_attempt_shapes": logical,
        "overlength_retry_shapes": message_count,
        "ambiguous_5xx_retry_slots": affected_cap,
        "maximum_billable_attempt_shapes": len(billable_rows),
        "maximum_known_not_completed_transport_retry_attempts": (
            known_not_completed
        ),
        "maximum_dispatch_attempts": expected_attempts,
        "logical_identity_hash": digest([spec.identity for spec in specs]),
        "shape_inventory_hash": digest(rows),
        "ambiguous_allowance_hash": digest(ambiguous),
        "conservative_input_token_bound_sum": input_sum,
        "output_token_allowance_sum": output_sum,
        "long_context_projected_shape_count": sum(
            row["conservative_input_token_bound"]
            > LONG_CONTEXT_THRESHOLD_INPUT_TOKENS
            for row in billable_rows
        ),
        "longest": {
            key: longest[key] for key in (
                "identity",
                "ordinal",
                "plan_stage",
                "phase",
                "action",
                "projected_shape",
                "shape_hash",
                "conservative_input_token_bound",
                "max_output_tokens",
                "strict_reservation_cost_usd",
            )
        },
        "within_model_context": (
            longest["conservative_input_token_bound"]
            + longest["max_output_tokens"]
            <= config.model_context_tokens
        ),
        "pricing_projection": {
            "strict_all_billable_shapes_reservation_ceiling_usd": str(
                strict_cost.quantize(_QUANTUM, rounding=ROUND_CEILING)
            ),
            "nominal_uncached_all_billable_shapes_ceiling_usd": str(
                nominal_cost.quantize(_QUANTUM, rounding=ROUND_CEILING)
            ),
        },
        "exact_caps": exact_caps,
        "ambiguous_allowance": ambiguous,
        "requests": rows,
    }
    return {**body, "inventory_hash": digest(body)}


def _render_projection(config: LiveConfig) -> dict[str, Any]:
    plan = build_request_plan(config)
    if (plan.run_id != V1_2_UNIFIED_PILOT_RUN_ID
            or plan.planned_counts()["logical_requests"]
            != TOTAL_LOGICAL_REQUEST_CEILING
            or digest([spec.identity for spec in plan.requests])
            != V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH):
        raise V12MasterProjectionError("canonical unified plan identity drifted")
    all_rows = _render_rows(config, plan)
    if len(all_rows) != 2340:
        raise V12MasterProjectionError("unified billable shape count drifted")
    inventories = {
        stage: _stage_inventory(config, plan, stage, all_rows)
        for stage in RELEASE_ORDER
    }
    stage_caps = {
        stage: inventories[stage]["exact_caps"] for stage in RELEASE_ORDER
    }
    aggregate = validate_unified_stage_caps(stage_caps)
    if aggregate["dispatch_attempts"] != TOTAL_DISPATCH_ATTEMPT_CEILING:
        raise V12MasterProjectionError("aggregate dispatch ceiling drifted")
    return {
        "config": config,
        "plan": plan,
        "rows": all_rows,
        "inventories": inventories,
        "aggregate": aggregate,
    }


def build_unified_projection() -> dict[str, Any]:
    """Render, converge, and return the complete keyless projection artifact."""

    provisional_limits = {
        "max_requests": TOTAL_DISPATCH_ATTEMPT_CEILING,
        "max_total_input_tokens": TOTAL_DISPATCH_ATTEMPT_CEILING * MAX_INPUT_TOKENS,
        "max_total_output_tokens": TOTAL_DISPATCH_ATTEMPT_CEILING * MAX_OUTPUT_TOKENS,
        "max_input_tokens_per_request": MAX_INPUT_TOKENS,
        "max_output_tokens_per_request": MAX_OUTPUT_TOKENS,
        "max_in_flight": 1,
    }
    first = _render_projection(LiveConfig.from_mapping(
        _config_values(provisional_limits)
    ))
    aggregate = first["aggregate"]
    exact_limits = {
        "max_requests": aggregate["dispatch_attempts"],
        "max_total_input_tokens": aggregate["max_total_input_tokens"],
        "max_total_output_tokens": aggregate["max_total_output_tokens"],
        "max_input_tokens_per_request": aggregate[
            "max_input_tokens_per_request"
        ],
        "max_output_tokens_per_request": aggregate[
            "max_output_tokens_per_request"
        ],
        "max_in_flight": 1,
    }
    final = _render_projection(LiveConfig.from_mapping(_config_values(exact_limits)))
    first_semantic = {
        stage: {
            key: value for key, value in inventory.items()
            if key not in {"plan_hash"}
        }
        for stage, inventory in first["inventories"].items()
    }
    final_semantic = {
        stage: {
            key: value for key, value in inventory.items()
            if key not in {"plan_hash"}
        }
        for stage, inventory in final["inventories"].items()
    }
    if (canonical(first["aggregate"]) != canonical(final["aggregate"])
            or canonical(first_semantic) != canonical(final_semantic)):
        raise V12MasterProjectionError(
            "unified projection did not converge under exact limits"
        )
    config, plan = final["config"], final["plan"]
    prompt_inventory = protocol_prompt_inventory(config)
    strict_cost = Decimal(final["aggregate"]["max_cost_usd"])
    operational = Decimal(SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD)
    body = {
        "artifact_type": "v1_2_unified_master_no_dispatch_projection_v1",
        "projection_algorithm_version": PROJECTION_ALGORITHM_VERSION,
        "projection_only": True,
        "dispatch_performed": False,
        "credential_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "controller_id": V1_2_UNIFIED_PILOT_CONTROLLER_ID,
        "run_id": V1_2_UNIFIED_PILOT_RUN_ID,
        "namespace": V1_2_UNIFIED_PILOT_NAMESPACE,
        "protocol_version": plan.protocol_version,
        "protocol_hash": plan.protocol_hash,
        "implementation_hash": plan.implementation_hash,
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "fixture_inventory_hash": digest(dict(plan.fixture_inventory)),
        "prompt_inventory_hash": digest(prompt_inventory),
        "request_order_hash": V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
        "stage_partition_hash": V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        "retry_policy_hash": V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
        "transport_hash": digest(dict(SOURCE_TRANSPORT_CONTRACT)),
        "pricing_contract_hash": digest(dict(SOURCE_PRICING_CONTRACT)),
        "logical_request_count": TOTAL_LOGICAL_REQUEST_CEILING,
        "maximum_dispatch_attempts": TOTAL_DISPATCH_ATTEMPT_CEILING,
        "initial_attempt_shapes": TOTAL_LOGICAL_REQUEST_CEILING,
        "overlength_retry_shapes": sum(
            inventory["overlength_retry_shapes"]
            for inventory in final["inventories"].values()
        ),
        "ambiguous_5xx_retry_slots": sum(
            inventory["ambiguous_5xx_retry_slots"]
            for inventory in final["inventories"].values()
        ),
        "maximum_billable_attempt_shapes": sum(
            inventory["maximum_billable_attempt_shapes"]
            for inventory in final["inventories"].values()
        ),
        "maximum_known_not_completed_transport_retry_attempts": sum(
            inventory["maximum_known_not_completed_transport_retry_attempts"]
            for inventory in final["inventories"].values()
        ),
        "stage_inventories": final["inventories"],
        "stage_inventory_hashes": {
            stage: final["inventories"][stage]["inventory_hash"]
            for stage in RELEASE_ORDER
        },
        "projected_stage_caps": final["aggregate"],
        "projected_stage_caps_hash": digest(final["aggregate"]),
        "exact_config_template": config.to_dict(),
        "operational_cost_hard_cap_usd": (
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
        ),
        "projected_strict_reservation_ceiling_usd": str(
            strict_cost.quantize(_QUANTUM, rounding=ROUND_CEILING)
        ),
        "operational_cap_covers_full_projected_envelope": (
            strict_cost <= operational
        ),
        "authorization_issuance_enabled": False,
        "independent_audit_required": True,
        "warning": (
            "This exact shape-sum projection is not authorization, does not "
            "predict actual spend, and cannot dispatch."
        ),
    }
    return {**body, "projection_hash": digest(body)}


def validate_unified_projection(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the projection's complete nested accounting without dispatch."""

    if (not isinstance(value, Mapping)
            or value.get("artifact_type")
            != "v1_2_unified_master_no_dispatch_projection_v1"):
        raise V12MasterProjectionError("unified projection artifact is invalid")
    projection = json.loads(canonical(dict(value)))
    body = dict(projection)
    projection_hash = body.pop("projection_hash", None)
    if projection_hash != digest(body):
        raise V12MasterProjectionError("unified projection self-hash is invalid")
    exact_identity = {
        "projection_algorithm_version": PROJECTION_ALGORITHM_VERSION,
        "projection_only": True,
        "dispatch_performed": False,
        "credential_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "controller_id": V1_2_UNIFIED_PILOT_CONTROLLER_ID,
        "run_id": V1_2_UNIFIED_PILOT_RUN_ID,
        "namespace": V1_2_UNIFIED_PILOT_NAMESPACE,
        "protocol_version": "v1.2",
        "request_order_hash": V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
        "stage_partition_hash": V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        "retry_policy_hash": V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
        "transport_hash": digest(dict(SOURCE_TRANSPORT_CONTRACT)),
        "pricing_contract_hash": digest(dict(SOURCE_PRICING_CONTRACT)),
        "logical_request_count": TOTAL_LOGICAL_REQUEST_CEILING,
        "maximum_dispatch_attempts": TOTAL_DISPATCH_ATTEMPT_CEILING,
        "operational_cost_hard_cap_usd": (
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
        ),
        "authorization_issuance_enabled": False,
        "independent_audit_required": True,
    }
    if any(projection.get(key) != expected
           for key, expected in exact_identity.items()):
        raise V12MasterProjectionError("unified projection identity drifted")
    if projection.get("config_hash") != digest(
        projection.get("exact_config_template")
    ):
        raise V12MasterProjectionError("unified projection config hash drifted")

    inventories = projection.get("stage_inventories")
    if not isinstance(inventories, Mapping) or set(inventories) != set(RELEASE_ORDER):
        raise V12MasterProjectionError("unified stage inventory set is invalid")
    normalized_caps: dict[str, Mapping[str, Any]] = {}
    inventory_hashes: dict[str, str] = {}
    for stage in RELEASE_ORDER:
        inventory = inventories[stage]
        if not isinstance(inventory, Mapping):
            raise V12MasterProjectionError(f"{stage} inventory is invalid")
        inventory_body = dict(inventory)
        inventory_hash = inventory_body.pop("inventory_hash", None)
        if inventory_hash != digest(inventory_body):
            raise V12MasterProjectionError(f"{stage} inventory hash is invalid")
        if (inventory.get("release_stage") != stage
                or inventory.get("projection_only") is not True
                or inventory.get("dispatch_performed") is not False):
            raise V12MasterProjectionError(f"{stage} inventory identity drifted")
        requests = inventory.get("requests")
        ambiguous = inventory.get("ambiguous_allowance")
        if not isinstance(requests, list) or not isinstance(ambiguous, list):
            raise V12MasterProjectionError(f"{stage} shape rows are invalid")
        if any(
            not isinstance(row, Mapping)
            or row.get("shape_hash")
            != digest({key: item for key, item in row.items() if key != "shape_hash"})
            for row in requests
        ):
            raise V12MasterProjectionError(f"{stage} request shape hash is invalid")
        if inventory.get("shape_inventory_hash") != digest(requests):
            raise V12MasterProjectionError(f"{stage} shape inventory hash drifted")
        if inventory.get("ambiguous_allowance_hash") != digest(ambiguous):
            raise V12MasterProjectionError(f"{stage} ambiguous allowance hash drifted")
        if len({row.get("identity") for row in ambiguous}) != len(ambiguous):
            raise V12MasterProjectionError(
                f"{stage} ambiguous allowance repeats a logical identity"
            )
        expected_ambiguous = _ambiguous_allowance_rows(requests, len(ambiguous))
        if canonical(ambiguous) != canonical(expected_ambiguous):
            raise V12MasterProjectionError(
                f"{stage} ambiguous allowance is not the exact maximal selection"
            )
        initial_count = sum(
            row.get("projected_shape") == "initial_attempt" for row in requests
        )
        overlength_count = sum(
            row.get("projected_shape") == "overlength_retry" for row in requests
        )
        if (inventory.get("logical_request_count")
                != LOGICAL_REQUEST_CEILINGS[stage]
                or inventory.get("maximum_dispatch_attempts")
                != DISPATCH_ATTEMPT_CEILINGS[stage]
                or inventory.get("initial_attempt_shapes")
                != LOGICAL_REQUEST_CEILINGS[stage]
                or initial_count != inventory.get("initial_attempt_shapes")
                or overlength_count != inventory.get("overlength_retry_shapes")
                or len(requests) != initial_count + overlength_count
                or len(ambiguous) != inventory.get("ambiguous_5xx_retry_slots")
                or inventory.get("maximum_billable_attempt_shapes")
                != len(requests) + len(ambiguous)):
            raise V12MasterProjectionError(f"{stage} shape accounting drifted")
        billable = [*requests, *ambiguous]
        input_sum = sum(
            row["conservative_input_token_bound"] for row in billable
        )
        output_sum = sum(row["max_output_tokens"] for row in billable)
        strict_cost = sum(
            (Decimal(row["strict_reservation_cost_usd"]) for row in billable),
            Decimal("0"),
        ).quantize(_QUANTUM, rounding=ROUND_CEILING)
        caps = inventory.get("exact_caps")
        if (not isinstance(caps, Mapping)
                or caps.get("max_total_input_tokens") != input_sum
                or caps.get("max_total_output_tokens") != output_sum
                or caps.get("max_cost_usd") != str(strict_cost)
                or inventory.get("conservative_input_token_bound_sum") != input_sum
                or inventory.get("output_token_allowance_sum") != output_sum
                or inventory.get("long_context_projected_shape_count") != sum(
                    row["conservative_input_token_bound"]
                    > LONG_CONTEXT_THRESHOLD_INPUT_TOKENS
                    for row in billable
                )):
            raise V12MasterProjectionError(f"{stage} shape sum drifted")
        normalized_caps[stage] = caps
        inventory_hashes[stage] = inventory_hash

    aggregate = validate_unified_stage_caps(normalized_caps)
    if canonical(aggregate) != canonical(projection.get("projected_stage_caps")):
        raise V12MasterProjectionError("unified projected caps drifted")
    if projection.get("projected_stage_caps_hash") != digest(aggregate):
        raise V12MasterProjectionError("unified projected cap hash is invalid")
    if canonical(projection.get("stage_inventory_hashes")) != canonical(
        inventory_hashes
    ):
        raise V12MasterProjectionError("unified inventory hash index drifted")
    summed_fields = {
        "initial_attempt_shapes": "initial_attempt_shapes",
        "overlength_retry_shapes": "overlength_retry_shapes",
        "ambiguous_5xx_retry_slots": "ambiguous_5xx_retry_slots",
        "maximum_billable_attempt_shapes": "maximum_billable_attempt_shapes",
        "maximum_known_not_completed_transport_retry_attempts": (
            "maximum_known_not_completed_transport_retry_attempts"
        ),
        "maximum_dispatch_attempts": "maximum_dispatch_attempts",
    }
    for projection_field, inventory_field in summed_fields.items():
        if projection.get(projection_field) != sum(
            inventory[inventory_field] for inventory in inventories.values()
        ):
            raise V12MasterProjectionError(
                f"unified {projection_field} accounting drifted"
            )
    strict_cost = Decimal(aggregate["max_cost_usd"])
    if projection.get("projected_strict_reservation_ceiling_usd") != str(
        strict_cost.quantize(_QUANTUM, rounding=ROUND_CEILING)
    ):
        raise V12MasterProjectionError("unified strict cost ceiling drifted")
    operational = Decimal(SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD)
    if projection.get("operational_cap_covers_full_projected_envelope") != (
        strict_cost <= operational
    ):
        raise V12MasterProjectionError("unified operational-cap decision drifted")
    return projection


def projected_stage_bindings(
    projection: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Convert an exact projection into non-ready master proposal bindings."""

    projection = validate_unified_projection(projection)
    projection_hash = projection["projection_hash"]
    inventories = projection.get("stage_inventories")
    if not isinstance(inventories, Mapping) or set(inventories) != set(RELEASE_ORDER):
        raise V12MasterProjectionError("unified stage inventory set is invalid")
    return {
        stage: build_stage_binding(
            stage,
            status="projected_awaiting_independent_audit",
            controller_id=MASTER_CONTROLLER_ID,
            plan_hash=projection["plan_hash"],
            inventory_hash=inventories[stage]["inventory_hash"],
            implementation_hash=projection["implementation_hash"],
            exact_caps=inventories[stage]["exact_caps"],
            evidence={
                "projection_hash": projection_hash,
                "projection_algorithm_version": PROJECTION_ALGORITHM_VERSION,
                "independent_audit_passed": False,
                "dispatch_permitted": False,
            },
        )
        for stage in RELEASE_ORDER
    }


__all__ = [
    "PROJECTION_ALGORITHM_VERSION",
    "V12MasterProjectionError",
    "build_unified_projection",
    "projected_stage_bindings",
    "validate_unified_projection",
]
