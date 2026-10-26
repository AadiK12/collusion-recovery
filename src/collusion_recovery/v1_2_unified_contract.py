"""Offline contract primitives for a possible one-authorization v1.2 pilot.

This module deliberately has no execution entry point.  It does not read an
environment file, import a provider, construct an SDK client, create a run
directory, or dispatch a request.  Its purpose is narrower: make the conditions
that a future unified release must preserve inspectable and testable before any
live controller is implemented.

A single prospective spending authorization may cover all four release slices,
but it cannot turn the scientific gates into no-ops.  Calibration, the
exhaustive outcome-blind block-0 smoke integrity audit, and the five-block
formation marker still determine whether later requests are in scope.  The
human transcript-and-claim audit occurs only after the pilot: it is required
before publication or confirmation, but it is not a dispatch gate.  This state
machine fails closed on a failed prerequisite or on evidence recorded out of
order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Literal, Mapping


EvidenceState = Literal["pending", "passed", "failed"]
PhaseTwoState = Literal["pending", "completed", "failed"]

GATE1 = "gate1_calibration"
GATE2 = "gate2_block0_smoke"
GATE3_PHASE1 = "gate3_phase1_formation"
GATE3_PHASE2 = "gate3_phase2_completion"
COMPLETE = "complete"
STOPPED = "stopped"

RELEASE_ORDER = (GATE1, GATE2, GATE3_PHASE1, GATE3_PHASE2)

# Frozen in PROTOCOL_V1_2_ADDENDUM.md.  Attempt ceilings include the existing
# overlength and known-not-completed retry surfaces plus the bounded v1.2
# ambiguous-5xx recovery policy.  They are ceilings, not expected usage.
LOGICAL_REQUEST_CEILINGS = MappingProxyType({
    GATE1: 20,
    GATE2: 192,
    GATE3_PHASE1: 480,
    GATE3_PHASE2: 888,
})
DISPATCH_ATTEMPT_CEILINGS = MappingProxyType({
    GATE1: 61,
    GATE2: 694,
    GATE3_PHASE1: 1765,
    GATE3_PHASE2: 2997,
})
TOTAL_LOGICAL_REQUEST_CEILING = 1580
TOTAL_DISPATCH_ATTEMPT_CEILING = 5517

# Mirrors the naming and semantics frozen into ``v1_2_master_release``.  Keep
# this as an inspectable contract rather than treating a post-run review as an
# implicit fifth release gate.
POST_RUN_HUMAN_AUDIT_CONTRACT = MappingProxyType({
    "mid_run_human_smoke_approval_required": False,
    "human_transcript_and_claim_audit_timing": "post_run_pre_publication",
    "post_run_human_audit_required_before_claims_or_confirmation": True,
    "post_run_human_audit_is_dispatch_gate": False,
    "failure_invalidates_publication_claims_and_hard_stops_confirmation": True,
})


class UnifiedV12ContractError(ValueError):
    """A proposed unified v1.2 contract or transition is not fail-closed."""


@dataclass(frozen=True)
class UnifiedPilotEvidence:
    """Minimal decision evidence for releasing successive pilot slices.

    ``smoke_exhaustive_integrity`` is the complete, outcome-blind automatic
    smoke audit.  Its pass releases formation without a mid-run human approval.
    ``post_run_human_audit`` cannot be decided until phase 2 has completed; it
    controls publication and confirmation only, never experimental dispatch.
    """

    calibration: EvidenceState = "pending"
    smoke_exhaustive_integrity: EvidenceState = "pending"
    formation: EvidenceState = "pending"
    phase2: PhaseTwoState = "pending"
    post_run_human_audit: EvidenceState = "pending"

    def __post_init__(self) -> None:
        ordinary = {
            "calibration": self.calibration,
            "smoke_exhaustive_integrity": self.smoke_exhaustive_integrity,
            "formation": self.formation,
            "post_run_human_audit": self.post_run_human_audit,
        }
        for label, value in ordinary.items():
            if value not in {"pending", "passed", "failed"}:
                raise UnifiedV12ContractError(
                    f"{label} must be pending, passed, or failed"
                )
        if self.phase2 not in {"pending", "completed", "failed"}:
            raise UnifiedV12ContractError(
                "phase2 must be pending, completed, or failed"
            )


@dataclass(frozen=True)
class UnifiedPilotDecision:
    """The only next action permitted by already-observed gate evidence."""

    status: Literal["release", "review", "complete", "stopped"]
    next_step: str
    reason: str


def _later_than_pending(evidence: UnifiedPilotEvidence, field: str) -> bool:
    later_fields = {
        "calibration": (
            evidence.smoke_exhaustive_integrity,
            evidence.formation,
            evidence.phase2,
            evidence.post_run_human_audit,
        ),
        "smoke": (
            evidence.formation,
            evidence.phase2,
            evidence.post_run_human_audit,
        ),
        "formation": (evidence.phase2, evidence.post_run_human_audit),
        "phase2": (evidence.post_run_human_audit,),
    }
    return any(value != "pending" for value in later_fields[field])


def next_unified_pilot_decision(
    evidence: UnifiedPilotEvidence,
) -> UnifiedPilotDecision:
    """Return the next permitted release/review, rejecting contradictory state.

    This function models conditional authority only.  A ``release`` result does
    not itself authorize spending and is not a dispatch capability.
    """

    if evidence.calibration == "pending":
        if _later_than_pending(evidence, "calibration"):
            raise UnifiedV12ContractError(
                "later evidence exists before calibration was decided"
            )
        return UnifiedPilotDecision(
            "release", GATE1, "calibration is the first permitted live slice"
        )
    if evidence.calibration == "failed":
        if _later_than_pending(evidence, "calibration"):
            raise UnifiedV12ContractError(
                "later evidence exists after calibration failed"
            )
        return UnifiedPilotDecision(
            "stopped",
            STOPPED,
            "calibration failed; no hypothesis-bearing call is permitted",
        )

    if evidence.smoke_exhaustive_integrity == "pending":
        if _later_than_pending(evidence, "smoke"):
            raise UnifiedV12ContractError(
                "later evidence exists before smoke execution completed"
            )
        return UnifiedPilotDecision(
            "release", GATE2, "calibration passed; block-0 smoke is next"
        )
    if evidence.smoke_exhaustive_integrity == "failed":
        if _later_than_pending(evidence, "smoke"):
            raise UnifiedV12ContractError(
                "later evidence exists after exhaustive smoke integrity failed"
            )
        return UnifiedPilotDecision(
            "stopped", STOPPED, "exhaustive outcome-blind smoke integrity failed"
        )

    if evidence.formation == "pending":
        if _later_than_pending(evidence, "formation"):
            raise UnifiedV12ContractError(
                "phase-2 evidence exists before the formation gate"
            )
        return UnifiedPilotDecision(
            "release",
            GATE3_PHASE1,
            "exhaustive outcome-blind smoke integrity passed; remaining formation is next",
        )
    if evidence.formation == "failed":
        if _later_than_pending(evidence, "formation"):
            raise UnifiedV12ContractError(
                "phase-2 evidence exists after the formation gate failed"
            )
        return UnifiedPilotDecision(
            "stopped",
            STOPPED,
            "fewer than three of five C histories qualified or integrity failed",
        )

    if evidence.phase2 == "pending":
        if _later_than_pending(evidence, "phase2"):
            raise UnifiedV12ContractError(
                "post-run audit evidence exists before phase 2 completed"
            )
        return UnifiedPilotDecision(
            "release", GATE3_PHASE2, "the five-block formation gate passed"
        )
    if evidence.phase2 == "failed":
        if _later_than_pending(evidence, "phase2"):
            raise UnifiedV12ContractError(
                "post-run audit evidence exists after phase 2 failed"
            )
        return UnifiedPilotDecision(
            "stopped", STOPPED, "phase 2 stopped without a complete pilot"
        )

    if evidence.post_run_human_audit == "pending":
        return UnifiedPilotDecision(
            "review",
            "post_run_human_transcript_and_claim_audit",
            "all requests completed; human audit is required before publication or confirmation",
        )
    if evidence.post_run_human_audit == "failed":
        return UnifiedPilotDecision(
            "stopped",
            STOPPED,
            "pilot execution completed, but the human audit invalidated publication claims and stopped confirmation",
        )
    return UnifiedPilotDecision(
        "complete",
        COMPLETE,
        "all 1,580 planned logical requests completed and the publication audit passed",
    )


_CAP_FIELDS = {
    "logical_requests",
    "dispatch_attempts",
    "max_total_input_tokens",
    "max_total_output_tokens",
    "max_input_tokens_per_request",
    "max_output_tokens_per_request",
    "max_in_flight",
    "max_cost_usd",
}


def validate_unified_stage_caps(
    values: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate exact per-slice ceilings before a unified proposal can exist.

    The function intentionally does not invent missing token or dollar bounds.
    All four rendered inventories must supply those values.  The returned
    aggregate is suitable for later proposal construction, but carries no
    authorization and exposes no execution capability.
    """

    if not isinstance(values, Mapping) or set(values) != set(RELEASE_ORDER):
        raise UnifiedV12ContractError(
            f"stage caps must contain exactly {list(RELEASE_ORDER)}"
        )
    normalized: dict[str, dict[str, Any]] = {}
    for stage in RELEASE_ORDER:
        row = values[stage]
        if not isinstance(row, Mapping) or set(row) != _CAP_FIELDS:
            raise UnifiedV12ContractError(
                f"{stage} caps must contain exactly {sorted(_CAP_FIELDS)}"
            )
        if row["logical_requests"] != LOGICAL_REQUEST_CEILINGS[stage]:
            raise UnifiedV12ContractError(f"{stage} logical-request ceiling drifted")
        if row["dispatch_attempts"] != DISPATCH_ATTEMPT_CEILINGS[stage]:
            raise UnifiedV12ContractError(f"{stage} dispatch-attempt ceiling drifted")
        integer_fields = (
            "max_total_input_tokens",
            "max_total_output_tokens",
            "max_input_tokens_per_request",
            "max_output_tokens_per_request",
            "max_in_flight",
        )
        if any(
            type(row[name]) is not int or row[name] <= 0
            for name in integer_fields
        ):
            raise UnifiedV12ContractError(
                f"{stage} token and concurrency caps must be positive integers"
            )
        if row["max_in_flight"] != 1:
            raise UnifiedV12ContractError(f"{stage} must keep max_in_flight at one")
        if row["max_input_tokens_per_request"] > row["max_total_input_tokens"]:
            raise UnifiedV12ContractError(f"{stage} input caps are inconsistent")
        if row["max_output_tokens_per_request"] > row["max_total_output_tokens"]:
            raise UnifiedV12ContractError(f"{stage} output caps are inconsistent")
        try:
            cost = Decimal(str(row["max_cost_usd"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise UnifiedV12ContractError(f"{stage} cost ceiling is malformed") from exc
        if (
            isinstance(row["max_cost_usd"], (bool, float))
            or not cost.is_finite()
            or cost <= 0
        ):
            raise UnifiedV12ContractError(
                f"{stage} cost ceiling must be a finite positive exact value"
            )
        normalized[stage] = {**dict(row), "max_cost_usd": str(cost)}

    aggregate = {
        "protocol_version": "v1.2",
        "logical_requests": sum(
            row["logical_requests"] for row in normalized.values()
        ),
        "dispatch_attempts": sum(
            row["dispatch_attempts"] for row in normalized.values()
        ),
        "max_total_input_tokens": sum(
            row["max_total_input_tokens"] for row in normalized.values()
        ),
        "max_total_output_tokens": sum(
            row["max_total_output_tokens"] for row in normalized.values()
        ),
        "max_input_tokens_per_request": max(
            row["max_input_tokens_per_request"] for row in normalized.values()
        ),
        "max_output_tokens_per_request": max(
            row["max_output_tokens_per_request"] for row in normalized.values()
        ),
        "max_in_flight": 1,
        "max_cost_usd": str(sum(
            (Decimal(row["max_cost_usd"]) for row in normalized.values()),
            Decimal("0"),
        )),
        "conditional_release_order": list(RELEASE_ORDER),
        "stage_caps": normalized,
    }
    if (
        aggregate["logical_requests"] != TOTAL_LOGICAL_REQUEST_CEILING
        or aggregate["dispatch_attempts"]
        != TOTAL_DISPATCH_ATTEMPT_CEILING
    ):
        raise UnifiedV12ContractError(
            "frozen per-stage ceilings do not sum to the full pilot envelope"
        )
    return aggregate

