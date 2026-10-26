"""Dedicated one-authorisation controller for the protocol-v1.2 pilot.

The public live entry point accepts only paths.  It consumes the immutable
master receipt before reading a credential, constructs the locked official
OpenAI SDK client itself, and then advances the single canonical run through
calibration, the exhaustive smoke audit, the five-block formation decision,
and conditional phase 2.  It always hard-stops before Gate 4.

The fake factory is conspicuously test-only.  It exercises the same durable
state machine with an SDK-shaped local transport but cannot confer empirical
provenance on a caller-supplied provider or capability.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .budget import (
    BudgetExhausted,
    BudgetLedger,
    BudgetStateError,
    DispatchReleaseLimits,
    Reservation,
)
from .common import canonical, digest
from .gates import calibration_assessment, formation_assessment
from .live_config import LiveConfig
from .orchestration import (
    AuthorizationRequiredError,
    DispatchAuthorizationArtifact,
    DurableRun,
    ExecutionContext,
    ExecutionReport,
    OBSERVATION_STATUSES,
    PROVIDER_IDENTITY_STATUSES,
    ResumeError,
    ScheduledRequest,
    STAGE_ORDER,
    StagedExecutor,
    V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    V1_2_UNIFIED_PILOT_NAMESPACE,
    V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
    V1_2_UNIFIED_PILOT_RUN_ID,
    V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
    _provider_identity_candidate,
    authorization_binding,
    build_request_plan,
)
from .preflight import PreflightRequestFactory, preflight_inventory
from .providers.openai import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
    provider_result_matches_raw_response,
)
from .providers.base import (
    ProviderResult,
    ProviderStatus,
    ProviderTiming,
    ProviderUsage,
    TransportFailure,
    TransportResponse,
)
from .stage2 import (
    STAGE2_ENV_FILE,
    STAGE2_SDK_VERSION,
    STAGE2_TIMEOUT_SECONDS,
    _load_local_openai_key,
    _stage2_transport_metadata,
)
from .staged_requests import ScientificRequestFactory
from .v1_2_master_projection import (
    build_unified_projection,
    validate_unified_projection,
)
from .v1_2_master_release import (
    AUTHORIZATION_RECEIPT_DIRECTORY,
    MASTER_AUTHORIZATION_SCHEMA_PATH,
    MASTER_CONTROLLER_ID,
    MASTER_RUN_DIRECTORY,
    MASTER_RUN_ID,
    SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
    SOURCE_PROVIDER_IDENTITY,
    V12MasterReleaseError,
    build_authorization_receipt,
    claim_human_authorization_once_with_state,
    load_human_authorization,
    validate_v1_2_master_proposal,
)
from .v1_2_smoke_audit import (
    V1_2_SMOKE_INTEGRITY_DECIDED_BY,
    V1_2_SMOKE_INTEGRITY_GATE,
    V1_2_SMOKE_INTEGRITY_NOTE,
    V12SmokeIntegrityError,
    _require_v1_2_smoke_integrity_offline_fake,
    _audit_v1_2_smoke_integrity,
    audit_v1_2_smoke_integrity,
    require_v1_2_smoke_integrity,
)
from .v1_2_unified_contract import (
    GATE1,
    GATE2,
    GATE3_PHASE1,
    GATE3_PHASE2,
    RELEASE_ORDER,
    validate_unified_stage_caps,
)
from .visibility import ROOT


PROPOSAL_PATH = ROOT / "configs/v1_2_unified_master_proposal.json"
RUN_DIRECTORY = MASTER_RUN_DIRECTORY
MASTER_BINDING_EVENT = "v1_2_master_release_binding"
MASTER_CLAIM_EVENT = "v1_2_master_authorization_claimed"
MASTER_MODE_EVENT = "v1_2_master_execution_mode_binding"
EMPIRICAL_EXECUTION_MODE = "empirical_live"
FAKE_EXECUTION_MODE = "fake_transport_test"
EMPIRICAL_EXECUTOR_PROVENANCE = (
    "official_openai_client_from_run_v1_2_master"
)
FAKE_EXECUTOR_PROVENANCE = "explicit_offline_fake_master_factory"
FINAL_STATUS = "completed_v1_2_pilot_hard_stop_before_gate4"
FAKE_FINAL_STATUS = "completed_fake_v1_2_pilot_offline_only"
CALIBRATION_FAILED_STATUS = "stopped_calibration_failed"
SMOKE_FAILED_STATUS = "stopped_smoke_integrity_failed"
FORMATION_FAILED_STATUS = "stopped_formation_gate_failed"
BUDGET_LIMITED_STATUS = "budget_limited"

_TERMINAL_STATUSES = {
    FINAL_STATUS,
    FAKE_FINAL_STATUS,
    CALIBRATION_FAILED_STATUS,
    SMOKE_FAILED_STATUS,
    FORMATION_FAILED_STATUS,
    BUDGET_LIMITED_STATUS,
}
_STAGE_AMBIGUOUS_5XX_IDENTITY_CAPS = {
    GATE1: 1,
    GATE2: 2,
    GATE3_PHASE1: 5,
    GATE3_PHASE2: 9,
}
_PLAN_STAGE_TO_RELEASE = {
    "calibration": GATE1,
    "smoke": GATE2,
    "formation": GATE3_PHASE1,
    "remaining_arms": GATE3_PHASE2,
    "probes": GATE3_PHASE2,
}


class V12MasterControllerError(RuntimeError):
    """The unified controller or its durable state is unsafe."""


class _OfflineScriptedTransport:
    """Finite data-only transport used exclusively by the offline test path."""

    def __init__(
        self,
        outcomes: Sequence[TransportResponse | TransportFailure],
        audit_metadata: Mapping[str, Any],
    ):
        if (type(outcomes) not in {list, tuple}
                or any(type(item) not in {TransportResponse, TransportFailure}
                       for item in outcomes)):
            raise V12MasterControllerError(
                "offline outcomes must be a finite response/failure sequence"
            )
        frozen: list[TransportResponse | TransportFailure] = []
        for item in outcomes:
            if type(item) is TransportResponse:
                frozen.append(TransportResponse(item.body_json, item.request_id))
            else:
                frozen.append(TransportFailure(
                    item.status,
                    item.safe_message,
                    request_id=item.request_id,
                    raw_response=(
                        None if item.raw_response_json is None
                        else json.loads(item.raw_response_json)
                    ),
                    known_not_completed=item.known_not_completed,
                    transport_evidence=json.loads(item.transport_evidence_json),
                ))
        self._outcomes = tuple(frozen)
        self._index = 0
        self.audit_metadata = json.loads(canonical(dict(audit_metadata)))

    @property
    def remaining(self) -> int:
        return len(self._outcomes) - self._index

    def send(self, payload: Mapping[str, Any]) -> TransportResponse:
        if not isinstance(payload, Mapping):
            raise TypeError("offline payload must be a mapping")
        if self._index >= len(self._outcomes):
            raise V12MasterControllerError("offline response script is exhausted")
        outcome = self._outcomes[self._index]
        self._index += 1
        if type(outcome) is TransportFailure:
            raise outcome
        return outcome


def _final_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    proposal = validate_v1_2_master_proposal(value)
    if (proposal.get("execution_ready") is not True
            or proposal.get("authorization_issuance_enabled") is not True
            or proposal.get("controller", {}).get("implemented") is not True
            or proposal.get("controller", {}).get(
                "cumulative_budget_ledger_implemented") is not True):
        raise V12MasterControllerError(
            "the master proposal is not a final executable release"
        )
    return proposal


def load_v1_2_master_proposal(path: Path = PROPOSAL_PATH) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise V12MasterControllerError(
            "the final unified master proposal is unavailable or invalid"
        ) from exc
    return _final_proposal(value)


def _release_limits_from_caps(
    caps: Mapping[str, Any], *, cumulative: bool,
) -> DispatchReleaseLimits:
    return DispatchReleaseLimits.from_mapping({
        "max_dispatch_attempts": caps["dispatch_attempts"],
        "max_total_input_tokens": caps["max_total_input_tokens"],
        "max_total_output_tokens": caps["max_total_output_tokens"],
        "max_cost_usd": (
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
            if cumulative else caps["max_cost_usd"]
        ),
        "max_in_flight": caps["max_in_flight"],
        "max_input_tokens_per_request": caps[
            "max_input_tokens_per_request"
        ],
        "max_output_tokens_per_request": caps[
            "max_output_tokens_per_request"
        ],
    })


def _master_config_plan_dispatch(
    proposal: Mapping[str, Any], authorization: Mapping[str, Any],
    projection: Mapping[str, Any] | None = None,
) -> tuple[
    LiveConfig, Any, DispatchAuthorizationArtifact, dict[str, Any]
]:
    proposal = _final_proposal(proposal)
    authorization = load_or_validate_authorization(authorization, proposal)
    projection = validate_unified_projection(
        build_unified_projection() if projection is None else projection
    )
    if (projection["implementation_hash"]
            != proposal["run"]["implementation_hash"]
            or projection["request_order_hash"]
            != V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH
            or projection["stage_partition_hash"]
            != V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH
            or projection["projected_stage_caps_hash"]
            != proposal["projected_stage_caps_hash"]):
        raise V12MasterControllerError(
            "the exact projection differs from the authorized proposal"
        )
    for stage in RELEASE_ORDER:
        binding = proposal["stage_bindings"][stage]
        inventory = projection["stage_inventories"][stage]
        if (binding["status"] != "ready"
                or binding["controller_id"] != MASTER_CONTROLLER_ID
                or binding["implementation_hash"]
                != projection["implementation_hash"]
                or binding["plan_hash"] != projection["plan_hash"]
                or binding["inventory_hash"] != inventory["inventory_hash"]
                or canonical(binding["exact_caps"])
                != canonical(inventory["exact_caps"])):
            raise V12MasterControllerError(
                f"{stage} binding differs from its exact projection"
            )

    values = json.loads(canonical(projection["exact_config_template"]))
    values["authorization"] = {
        "kind": "paid",
        "authorization_id": authorization["authorization_id"],
        "approved_at": authorization["confirmed_at"],
        "approved_budget_usd": SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
        "allow_paid_calls": True,
    }
    config = LiveConfig.from_mapping(values)
    plan = build_request_plan(config)
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
    if (config.run_id != MASTER_RUN_ID
            or config.protocol_version != "v1.2"
            or config.source != "empirical-live"
            or config.run_type != "pilot"
            or config.stage != "probes"
            or config.blocks != 5
            or config.split != V1_2_UNIFIED_PILOT_NAMESPACE
            or config.seed_namespace != V1_2_UNIFIED_PILOT_NAMESPACE
            or plan.planned_counts()["logical_requests"] != 1580
            or request_order_hash != V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH
            or stage_partition_hash != V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH
            or plan.protocol_hash != proposal["protocol_hash"]
            or plan.implementation_hash != proposal["run"]["implementation_hash"]
            or digest(dict(plan.fixture_inventory))
            != projection["fixture_inventory_hash"]):
        raise V12MasterControllerError(
            "the runtime canonical plan differs from the authorized release"
        )

    aggregate_caps = proposal["projected_stage_caps"]
    release = _release_limits_from_caps(aggregate_caps, cumulative=True)
    preflight_hash = preflight_inventory(PreflightRequestFactory(
        config.model,
        config.limits.max_output_tokens_per_request,
        dict(config.model_settings),
    ))["inventory_hash"]
    binding_hash = authorization_binding(
        config,
        plan,
        confirmed_at=authorization["confirmed_at"],
        transport_hash=proposal["transport_hash"],
        approved_budget_usd=SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
        max_stage="probes",
        max_block=4,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=preflight_hash,
    )
    dispatch = DispatchAuthorizationArtifact(
        authorization["authorization_id"],
        authorization["confirmed_at"],
        binding_hash,
        SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
        proposal["transport_hash"],
        "probes",
        4,
        "initial",
        release,
        preflight_hash,
    )
    return config, plan, dispatch, projection


def load_or_validate_authorization(
    value: Mapping[str, Any], proposal: Mapping[str, Any]
) -> dict[str, Any]:
    from .v1_2_master_release import validate_human_authorization
    return validate_human_authorization(value, proposal)


def _validated_run_path(
    run_dir: Path, authorization: Mapping[str, Any], *, workspace_root: Path,
) -> tuple[Path, bool]:
    root = Path(workspace_root).resolve()
    expected = root / authorization["master_run_directory"]
    path = Path(run_dir)
    if path.is_symlink() or path.resolve(strict=False) != expected.resolve(strict=False):
        raise V12MasterControllerError(
            "the master run path differs from its authorization"
        )
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise V12MasterControllerError(
            "the master run path resolves outside the workspace"
        ) from exc
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise V12MasterControllerError(
                "the master run path cannot contain symlink components"
            )
    exists = path.exists()
    if exists and not path.is_dir():
        raise V12MasterControllerError("the master run path is not a directory")
    return path, exists


def _receipt_matches(
    receipt: Mapping[str, Any], proposal: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        claimed_at = datetime.fromisoformat(
            str(receipt["claimed_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V12MasterControllerError("master receipt timestamp is invalid") from exc
    expected = build_authorization_receipt(
        authorization, proposal, claimed_at=claimed_at
    )
    if canonical(dict(receipt)) != canonical(expected):
        raise V12MasterControllerError(
            "master receipt differs from the authorization and proposal"
        )
    return expected


def _binding_value(
    proposal: Mapping[str, Any], authorization: Mapping[str, Any],
    receipt: Mapping[str, Any], config: LiveConfig, plan: Any,
    dispatch: DispatchAuthorizationArtifact, projection: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "controller": MASTER_CONTROLLER_ID,
        "run_id": MASTER_RUN_ID,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_hash": authorization["authorization_hash"],
        "receipt_hash": receipt["receipt_hash"],
        "source_contract_hash": proposal["source_contract_hash"],
        "stage_bindings_hash": proposal["stage_bindings_hash"],
        "projected_stage_caps_hash": proposal["projected_stage_caps_hash"],
        "projection_hash": projection["projection_hash"],
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "implementation_hash": plan.implementation_hash,
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "request_order_hash": V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
        "stage_partition_hash": V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        "operational_cost_hard_cap_usd": (
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
        ),
    }


def prepare_v1_2_master_run(
    run_dir: Path,
    proposal: Mapping[str, Any],
    authorization: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    workspace_root: Path,
    receipt_created: bool,
    projection: Mapping[str, Any] | None = None,
) -> tuple[DurableRun, LiveConfig, Any, DispatchAuthorizationArtifact, dict[str, Any]]:
    """Create or exactly resume the unified run without credential or I/O."""
    proposal = _final_proposal(proposal)
    authorization = load_or_validate_authorization(authorization, proposal)
    receipt = _receipt_matches(receipt, proposal, authorization)
    config, plan, dispatch, projection = _master_config_plan_dispatch(
        proposal, authorization, projection
    )
    if type(receipt_created) is not bool:
        raise V12MasterControllerError(
            "master receipt creation state must be an exact boolean"
        )
    path, _observed_exists = _validated_run_path(
        run_dir, authorization, workspace_root=workspace_root
    )
    run = DurableRun.open(
        path,
        config,
        plan,
        resume=not receipt_created,
        dispatch_authorization=dispatch,
        execution_controller=MASTER_CONTROLLER_ID,
    )
    binding = _binding_value(
        proposal, authorization, receipt, config, plan, dispatch, projection
    )
    with run.execution_lock():
        bindings = [
            event for event in run.events
            if event.get("kind") == MASTER_BINDING_EVENT
        ]
        claims = [
            event for event in run.events if event.get("kind") == MASTER_CLAIM_EVENT
        ]
        if len(bindings) > 1 or len(claims) > 1:
            raise ResumeError("master binding or receipt claim is not singular")
        if bindings:
            if any(bindings[0].get(key) != value for key, value in binding.items()):
                raise ResumeError("durable master binding differs")
        else:
            if run.events:
                raise ResumeError("master binding is not the first durable event")
            bindings = [run.append(MASTER_BINDING_EVENT, **binding)]
        claim_value = {
            "controller": MASTER_CONTROLLER_ID,
            "run_id": MASTER_RUN_ID,
            "proposal_hash": proposal["proposal_hash"],
            "authorization_hash": authorization["authorization_hash"],
            "receipt_hash": receipt["receipt_hash"],
            "receipt_claimed_at": receipt["claimed_at"],
            "binding_event_hash": bindings[0]["event_hash"],
        }
        if claims:
            if any(claims[0].get(key) != value for key, value in claim_value.items()):
                raise ResumeError("durable master receipt claim differs")
        else:
            if any(event.get("kind") in {
                    MASTER_MODE_EVENT, "reservation", "result"
                    } for event in run.events):
                raise ResumeError("dispatch state precedes the master receipt claim")
            claims = [run.append(MASTER_CLAIM_EVENT, **claim_value)]
        fields = {
            "master_proposal_hash": proposal["proposal_hash"],
            "master_authorization_hash": authorization["authorization_hash"],
            "master_authorization_receipt_hash": receipt["receipt_hash"],
            "master_authorization_claim_event_hash": claims[0]["event_hash"],
            "required_provider_identity": proposal["required_provider_identity"],
            "operational_cost_hard_cap_usd": (
                SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
            ),
            "one_authorization_no_renewal": True,
            "authorized_stage_caps": proposal["projected_stage_caps"][
                "stage_caps"
            ],
            "authorized_stage_caps_hash": proposal[
                "projected_stage_caps_hash"
            ],
            "master_projection_hash": projection["projection_hash"],
            "gate4_authorized": False,
            "confirmation_authorized": False,
        }
        for key, expected in fields.items():
            if key in run.manifest and run.manifest[key] != expected:
                raise ResumeError("durable master manifest binding differs")
        run.set_status(
            run.manifest.get("status", "prepared_v1_2_master")
            if not receipt_created else "prepared_v1_2_master",
            **fields,
        )
    return run, config, plan, dispatch, projection


class _MasterBudgetLedger:
    """One cumulative ledger plus four non-fungible stage ledgers."""

    def __init__(
        self,
        run: DurableRun,
        proposal: Mapping[str, Any],
        approved: Decimal,
        release: DispatchReleaseLimits,
    ):
        self._stage_for_identity = {
            spec.identity: _PLAN_STAGE_TO_RELEASE[spec.stage]
            for spec in run.plan.requests
        }
        records = run.budget_records()
        unknown = {
            row.get("identity") for row in records
            if row.get("identity") not in self._stage_for_identity
        }
        if unknown:
            raise BudgetStateError("master budget contains an unknown identity")
        self._aggregate = BudgetLedger.replay(
            run.config.limits, run.config.pricing, approved, records, release
        )
        self._stages: dict[str, BudgetLedger] = {}
        for stage in RELEASE_ORDER:
            caps = proposal["projected_stage_caps"]["stage_caps"][stage]
            stage_release = _release_limits_from_caps(caps, cumulative=False)
            stage_records = [
                row for row in records
                if self._stage_for_identity.get(row.get("identity")) == stage
            ]
            self._stages[stage] = BudgetLedger.replay(
                run.config.limits,
                run.config.pricing,
                Decimal(str(caps["max_cost_usd"])),
                stage_records,
                stage_release,
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._aggregate, name)

    @property
    def active(self):
        return self._aggregate.active

    def _stage(self, identity: str) -> BudgetLedger:
        try:
            return self._stages[self._stage_for_identity[identity]]
        except KeyError as exc:
            raise BudgetStateError("request identity has no master stage") from exc

    def reserve(self, identity: str, attempt: int, **tokens: Any):
        stage = self._stage(identity)
        stage_reservation = stage.reserve(identity, attempt, **tokens)
        try:
            aggregate_reservation = self._aggregate.reserve(
                identity, attempt, **tokens
            )
        except BaseException:
            stage.active.pop(stage_reservation.reservation_id, None)
            raise
        if aggregate_reservation != stage_reservation:
            self._aggregate.active.pop(aggregate_reservation.reservation_id, None)
            stage.active.pop(stage_reservation.reservation_id, None)
            raise BudgetStateError("stage and cumulative reservations differ")
        return aggregate_reservation

    def preview_reconciliation(self, reservation_id: str, usage: Any, **state: Any):
        aggregate = self._aggregate.preview_reconciliation(
            reservation_id, usage, **state
        )
        reservation = self._aggregate.active[reservation_id]
        stage = self._stage(reservation.identity).preview_reconciliation(
            reservation_id, usage, **state
        )
        if canonical(aggregate) != canonical(stage):
            raise BudgetStateError(
                "stage and cumulative accounting disagree before reconciliation"
            )
        return aggregate

    def reconcile(self, reservation_id: str, usage: Any, **state: Any):
        reservation = self._aggregate.active[reservation_id]
        stage_ledger = self._stage(reservation.identity)
        stage_ledger.preview_reconciliation(reservation_id, usage, **{
            key: value for key, value in state.items()
            if key != "expected_accounting"
        })
        aggregate = self._aggregate.reconcile(reservation_id, usage, **state)
        stage = stage_ledger.reconcile(reservation_id, usage, **state)
        if canonical(aggregate) != canonical(stage):
            raise BudgetStateError("stage and cumulative reconciliation differ")
        return aggregate

    def snapshot(self) -> dict[str, Any]:
        return self._aggregate.snapshot()

    def stage_snapshots(self) -> dict[str, Any]:
        return {stage: ledger.snapshot() for stage, ledger in self._stages.items()}


def _mode_binding(
    run: DurableRun, proposal: Mapping[str, Any], *, mode: str, provenance: str,
) -> dict[str, Any]:
    bindings = [
        event for event in run.events if event.get("kind") == MASTER_MODE_EVENT
    ]
    expected = {
        "controller": MASTER_CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "run_id": MASTER_RUN_ID,
        "mode": mode,
        "executor_provenance": provenance,
    }
    if bindings:
        if (len(bindings) != 1
                or any(bindings[0].get(key) != value
                       for key, value in expected.items())):
            raise ResumeError("master execution mode changed on resume")
        event = bindings[0]
        run.set_status(
            run.manifest["status"],
            execution_mode=mode,
            executor_provenance=provenance,
            empirical_evidence=mode == EMPIRICAL_EXECUTION_MODE,
        )
        return event
    claims = [
        event for event in run.events if event.get("kind") == MASTER_CLAIM_EVENT
    ]
    if len(claims) != 1:
        raise V12MasterControllerError(
            "master execution mode requires a prior receipt claim"
        )
    if any(event.get("kind") in {"reservation", "result"} for event in run.events):
        raise ResumeError("master dispatch exists without an execution mode binding")
    event = run.append(MASTER_MODE_EVENT, **expected)
    run.set_status(
        run.manifest["status"],
        execution_mode=mode,
        executor_provenance=provenance,
        empirical_evidence=mode == EMPIRICAL_EXECUTION_MODE,
    )
    return event


def _strict_provider_result_matches_event(
    run: DurableRun, event: Mapping[str, Any]
) -> bool:
    provider = event.get("provider_result")
    request_hash = event.get("request_hash")
    if not isinstance(provider, Mapping) or not isinstance(request_hash, str):
        return False
    try:
        request = run.request_payload(request_hash)
    except (OSError, TypeError, ValueError):
        return False
    return provider_result_matches_raw_response(
        provider,
        response_mode=request.get("response_mode"),
        message_max_codepoints=request.get("message_max_codepoints", 200),
        message_retry_history_max_utf8_bytes=request.get(
            "message_retry_history_max_utf8_bytes", 2048
        ),
    )


class V12MasterExecutor(StagedExecutor):
    """Exact one-run state machine for all conditionally released pilot work."""

    def __init__(self, *_args: Any, **_kwargs: Any):
        raise V12MasterControllerError(
            "master executors are constructed only by the sealed live or "
            "data-only offline entry points"
        )

    def _validate_release(self) -> None:
        bindings = [
            event for event in self.run.events
            if event.get("kind") == MASTER_BINDING_EVENT
        ]
        if (self.run.config.run_id != V1_2_UNIFIED_PILOT_RUN_ID
                or self.run.manifest.get("execution_controller")
                != V1_2_UNIFIED_PILOT_CONTROLLER_ID
                or self.run.manifest.get("master_proposal_hash")
                != self.proposal["proposal_hash"]
                or self.run.manifest.get("master_authorization_receipt_hash")
                != self.authorization_receipt.get("receipt_hash")
                or len(bindings) != 1
                or self.projection["projection_hash"]
                != bindings[0].get("projection_hash")):
            raise V12MasterControllerError(
                "master executor differs from the durable release"
            )
        transport = getattr(self.provider, "transport", None)
        expected_transport_type = (
            _OfflineScriptedTransport
            if self.fake_transport_test else OpenAISDKTransport
        )
        if (type(self.provider) is not OpenAIResponsesProvider
                or type(transport) is not expected_transport_type
                or canonical(transport.audit_metadata)
                != canonical(self.proposal["transport"])
                or digest(transport.audit_metadata)
                != self.proposal["transport_hash"]):
            raise V12MasterControllerError(
                "master provider transport differs from the proposal"
            )
        self._validate_scope_history()

    def _release_for_spec(self, spec: ScheduledRequest) -> str:
        try:
            return _PLAN_STAGE_TO_RELEASE[spec.stage]
        except KeyError as exc:
            raise V12MasterControllerError(
                "request has no unified release-stage binding"
            ) from exc

    def _v1_2_5xx_identity_cap(
        self, spec: ScheduledRequest | None = None,
    ) -> int:
        if spec is None:
            raise V12MasterControllerError(
                "master ambiguous-5xx accounting requires a request identity"
            )
        release_stage = self._release_for_spec(spec)
        projected = self.projection["stage_inventories"][release_stage][
            "ambiguous_5xx_retry_slots"
        ]
        expected = _STAGE_AMBIGUOUS_5XX_IDENTITY_CAPS[release_stage]
        if type(projected) is not int or projected != expected:
            raise V12MasterControllerError(
                "master ambiguous-5xx stage cap differs from its projection"
            )
        return expected

    def _v1_2_5xx_affected_identities(
        self, spec: ScheduledRequest | None = None,
    ) -> list[str]:
        if spec is None:
            raise V12MasterControllerError(
                "master ambiguous-5xx accounting requires a request identity"
            )
        release_stage = self._release_for_spec(spec)
        identity_release = {
            request.identity: _PLAN_STAGE_TO_RELEASE[request.stage]
            for request in self.run.plan.requests
        }
        identities: list[str] = []
        for row in self.run.result_events():
            identity = row.get("identity")
            if (self._eligible_ambiguous_5xx(row)
                    and isinstance(identity, str)
                    and identity_release.get(identity) == release_stage
                    and identity not in identities):
                identities.append(identity)
        return identities

    def _master_ledger(self) -> _MasterBudgetLedger:
        release = DispatchReleaseLimits.from_mapping(
            self.run.manifest["authorized_release_limits"]
        )
        return _MasterBudgetLedger(
            self.run,
            self.proposal,
            Decimal(self.run.manifest["authorized_budget_usd"]),
            release,
        )

    def _required_gate(self, spec: ScheduledRequest) -> str | None:
        if spec.stage == "calibration":
            return None
        if spec.stage == "smoke":
            return "calibration"
        if spec.stage == "formation":
            return V1_2_SMOKE_INTEGRITY_GATE
        if spec.stage in {"remaining_arms", "probes"}:
            return "formation"
        raise V12MasterControllerError(
            f"master plan contains unsupported stage {spec.stage!r}"
        )

    def _bind_or_check_provider_identity(
        self, provider_status: str, result: Mapping[str, Any]
    ) -> str | None:
        candidate = _provider_identity_candidate(
            self.run.config, provider_status, result
        )
        if candidate is None:
            return None
        required = self.proposal["required_provider_identity"]
        existing = self.run.manifest.get("observed_provider_identity")
        if candidate != required or existing not in (None, required):
            self.run.set_status(
                "paused_model_drift",
                observed_provider_identity=existing,
                required_provider_identity=required,
                rejected_provider_identity=candidate,
                budget=self.ledger.snapshot(),
            )
            return "paused_model_drift"
        if existing is None:
            self.run.set_status(
                "running",
                observed_provider_identity=required,
                required_provider_identity=required,
                budget=self.ledger.snapshot(),
            )
        return None

    def _audit_historical_provider_results(self) -> None:
        required = self.proposal["required_provider_identity"]
        for event in self.run.result_events():
            if event.get("status") not in PROVIDER_IDENTITY_STATUSES:
                continue
            provider = event.get("provider_result")
            candidate = (
                _provider_identity_candidate(
                    self.run.config, str(event.get("status")), provider
                ) if isinstance(provider, Mapping) else None
            )
            if (candidate != required
                    or event.get("provider_identity") != required
                    or not isinstance(provider, Mapping)
                    or not _strict_provider_result_matches_event(
                        self.run, event
                    )):
                self.run.set_status(
                    "paused_provider_result_audit",
                    stop_identity=event.get("identity"),
                    rejected_result_event_hash=event.get("event_hash"),
                    budget=self.ledger.snapshot(),
                )
                raise V12MasterControllerError(
                    "durable provider evidence failed before dispatch"
                )

    def _validate_scope_history(self) -> None:
        allowed = {spec.identity for spec in self.run.plan.requests}
        allowed_gates = {"calibration", V1_2_SMOKE_INTEGRITY_GATE, "formation"}
        for event in self.run.events:
            if (event.get("kind") in {"reservation", "result"}
                    and event.get("identity") not in allowed):
                raise ResumeError("master journal contains out-of-plan dispatch")
            if (event.get("kind") == "gate"
                    and event.get("gate") not in allowed_gates):
                raise ResumeError("master journal contains an unauthorized gate")
        if any(event.get("kind") == "authorization_renewal"
               for event in self.run.events):
            raise ResumeError("the one-authorization master run cannot renew")
        binding = [
            event for event in self.run.events
            if event.get("kind") == MASTER_BINDING_EVENT
        ]
        claim = [
            event for event in self.run.events
            if event.get("kind") == MASTER_CLAIM_EVENT
        ]
        mode = [
            event for event in self.run.events
            if event.get("kind") == MASTER_MODE_EVENT
        ]
        if (len(binding) != 1 or len(claim) != 1 or len(mode) != 1
                or [binding[0].get("sequence"), claim[0].get("sequence"),
                    mode[0].get("sequence")] != [0, 1, 2]
                or claim[0].get("binding_event_hash")
                != binding[0].get("event_hash")
                or self.run.manifest.get(
                    "master_authorization_claim_event_hash"
                ) != claim[0].get("event_hash")):
            raise ResumeError(
                "master binding, receipt claim, and execution mode are not "
                "the exact ordered journal prefix"
            )

    def _gate_decision(
        self, gate: str, assessment: Mapping[str, Any]
    ) -> dict[str, Any]:
        gates = self.run.gate_events()
        existing = gates.get(gate)
        if existing is None:
            return self.run._record_gate_decision_unlocked(
                gate,
                passed=assessment["passed"],
                evidence=assessment,
                decided_by=MASTER_CONTROLLER_ID,
                note=(
                    "Frozen automatic protocol-v1.2 calibration decision."
                    if gate == "calibration" else
                    "Frozen automatic protocol-v1.2 five-block 3-of-5 decision."
                ),
            )
        if (existing.get("passed") is not assessment["passed"]
                or canonical(existing.get("evidence")) != canonical(assessment)
                or existing.get("evidence_hash") != digest(dict(assessment))
                or existing.get("decided_by") != MASTER_CONTROLLER_ID):
            raise ResumeError(f"immutable {gate} decision differs from assessment")
        return existing

    def _smoke_gate(self) -> dict[str, Any]:
        if canonical(self.run.manifest.get("stage_budgets")) != canonical(
                self.ledger.stage_snapshots()):
            raise V12MasterControllerError(
                "non-fungible stage ledgers differ before smoke release"
            )
        existing = self.run.gate_events().get(V1_2_SMOKE_INTEGRITY_GATE)
        require_integrity = (
            _require_v1_2_smoke_integrity_offline_fake
            if self.fake_transport_test else require_v1_2_smoke_integrity
        )
        if existing is None:
            try:
                report = require_integrity(self.run)
            except V12SmokeIntegrityError:
                failed = (
                    _audit_v1_2_smoke_integrity(
                        self.run, fake_transport_test=True
                    )
                    if self.fake_transport_test
                    else audit_v1_2_smoke_integrity(self.run)
                )
                self.run.set_status(
                    SMOKE_FAILED_STATUS,
                    completed_through_stage="smoke",
                    completed_through_block=0,
                    smoke_integrity_report=failed,
                    next_stage_authorized=False,
                    completed_pilot=False,
                    gate4_authorized=False,
                    confirmation_authorized=False,
                    experiment_started=not self.fake_transport_test,
                    stop_identity=None,
                    budget=self.ledger.snapshot(),
                    stage_budgets=self.ledger.stage_snapshots(),
                )
                raise
            existing = self.run.append(
                "gate",
                gate=V1_2_SMOKE_INTEGRITY_GATE,
                passed=True,
                decided_by=V1_2_SMOKE_INTEGRITY_DECIDED_BY,
                note=V1_2_SMOKE_INTEGRITY_NOTE,
                evidence={"audit_report": report},
                evidence_hash=digest({"audit_report": report}),
                prerequisite_identities=[
                    spec.identity for spec in self.run.plan.requests
                    if STAGE_ORDER.index(spec.stage)
                    <= STAGE_ORDER.index("smoke")
                ],
            )
        elif existing is self.run.events[-1]:
            # Crash-safe idempotence at the exact boundary: the audit module
            # recomputes over the journal prefix before this sole final event.
            report = require_integrity(self.run)
            if canonical(existing.get("evidence")) != canonical(
                    {"audit_report": report}):
                raise ResumeError("persisted smoke gate differs from re-audit")
        evidence = existing.get("evidence")
        report = evidence.get("audit_report") if isinstance(evidence, Mapping) else None
        prerequisite_identities = [
            spec.identity for spec in self.run.plan.requests
            if STAGE_ORDER.index(spec.stage) <= STAGE_ORDER.index("smoke")
        ]
        if (existing.get("passed") is not True
                or existing.get("decided_by")
                != V1_2_SMOKE_INTEGRITY_DECIDED_BY
                or existing.get("note") != V1_2_SMOKE_INTEGRITY_NOTE
                or not isinstance(report, Mapping)
                or report.get("automatic_checks_passed") is not True
                or report.get("later_stage_release_permitted") is not True
                or existing.get("prerequisite_identities")
                != prerequisite_identities
                or existing.get("evidence_hash")
                != digest({"audit_report": report})):
            raise ResumeError("persisted smoke integrity gate is invalid")
        return existing

    def _execute_stage(self, stage: str, block: int) -> ExecutionReport:
        report = self._execute_unlocked(stage, block)
        self.run.set_status(
            self.run.manifest["status"],
            budget=self.ledger.snapshot(),
            stage_budgets=self.ledger.stage_snapshots(),
        )
        if report.status == "paused_budget_exhausted":
            self.run.set_status(
                BUDGET_LIMITED_STATUS,
                budget_limited_before_identity=report.stop_identity,
                completed_pilot=False,
                next_stage_authorized=False,
                gate4_authorized=False,
                budget=self.ledger.snapshot(),
                stage_budgets=self.ledger.stage_snapshots(),
            )
        return report

    def _summary(self) -> dict[str, Any]:
        manifest = self.run.manifest
        rows: dict[str, Mapping[str, Any]] = {}
        attempts = self.run.result_events()
        for event in attempts:
            rows[str(event.get("identity"))] = event
        completed = sum(
            rows.get(spec.identity, {}).get("status") in OBSERVATION_STATUSES
            for spec in self.run.plan.requests
        )
        empirical = not self.fake_transport_test
        body = {
            "artifact_type": (
                "v1_2_unified_master_execution_report_v1" if empirical else
                "v1_2_unified_master_offline_test_report_v1"
            ),
            "controller": MASTER_CONTROLLER_ID,
            "status": manifest.get("status"),
            "execution_mode": (
                EMPIRICAL_EXECUTION_MODE if empirical else FAKE_EXECUTION_MODE
            ),
            "executor_provenance": self.executor_provenance,
            "empirical_evidence": empirical,
            "network_calls": len(attempts) if empirical else 0,
            "paid_calls": len(attempts) if empirical else 0,
            "completed_logical_requests": completed,
            "planned_logical_requests": len(self.run.plan.requests),
            "dispatch_attempts": len(attempts),
            "experiment_started": (
                bool(manifest.get("experiment_started")) if empirical else False
            ),
            "completed_pilot": (
                empirical and manifest.get("status") == FINAL_STATUS
            ),
            "hard_stop_before_gate4": True,
            "gate4_authorized": False,
            "confirmation_authorized": False,
            "proposal_hash": self.proposal["proposal_hash"],
            "receipt_hash": self.authorization_receipt["receipt_hash"],
            "gates": {
                name: {
                    "passed": event.get("passed"),
                    "event_hash": event.get("event_hash"),
                    "evidence_hash": event.get("evidence_hash"),
                }
                for name, event in self.run.gate_events().items()
            },
            "budget": manifest.get("budget"),
            "stage_budgets": manifest.get("stage_budgets"),
            "journal_tail": manifest.get("last_event_hash"),
            "journal_event_count": manifest.get("event_count"),
        }
        return {**body, "report_hash": digest(body)}

    def execute(self) -> dict[str, Any]:
        with self.run.execution_lock():
            self._validate_scope_history()
            expected_mode = (
                FAKE_EXECUTION_MODE
                if self.fake_transport_test else EMPIRICAL_EXECUTION_MODE
            )
            self.mode_binding = _mode_binding(
                self.run,
                self.proposal,
                mode=expected_mode,
                provenance=self.executor_provenance,
            )
            self.ledger = self._master_ledger()
            self._audit_historical_provider_results()
            if self.run.manifest.get("status") in _TERMINAL_STATUSES:
                if self.fake_transport_test and isinstance(self.run, DurableRun):
                    _verify_v1_2_master_offline_fake_integrity(
                        self.run,
                        self.proposal,
                        self.authorization_receipt,
                        self.projection,
                    )
                return self._summary()

            calibration_gate = self.run.gate_events().get("calibration")
            if calibration_gate is None:
                report = self._execute_stage("calibration", 0)
                if report.status != "completed_stage":
                    return self._summary()
                assessment = calibration_assessment(self.run)
                calibration_gate = self._gate_decision(
                    "calibration", assessment
                )
            else:
                assessment = calibration_assessment(self.run)
                calibration_gate = self._gate_decision("calibration", assessment)
            if calibration_gate["passed"] is not True:
                self.run.set_status(
                    CALIBRATION_FAILED_STATUS,
                    completed_through_stage="calibration",
                    completed_through_block=0,
                    calibration_assessment_hash=digest(assessment),
                    next_stage_authorized=False,
                    completed_pilot=False,
                    gate4_authorized=False,
                    confirmation_authorized=False,
                    experiment_started=False,
                    stop_identity=None,
                    budget=self.ledger.snapshot(),
                    stage_budgets=self.ledger.stage_snapshots(),
                )
                return self._summary()

            smoke_gate = self.run.gate_events().get(V1_2_SMOKE_INTEGRITY_GATE)
            if smoke_gate is None:
                self.run.set_status(
                    "releasing_v1_2_smoke",
                    experiment_started=not self.fake_transport_test,
                )
                report = self._execute_stage("smoke", 0)
                if report.status != "completed_stage":
                    return self._summary()
                try:
                    smoke_gate = self._smoke_gate()
                except V12SmokeIntegrityError:
                    return self._summary()
            else:
                smoke_gate = self._smoke_gate()

            formation_gate = self.run.gate_events().get("formation")
            if formation_gate is None:
                report = self._execute_stage("formation", 4)
                if report.status != "completed_stage":
                    return self._summary()
                formation = formation_assessment(self.run)
                formation_gate = self._gate_decision("formation", formation)
            else:
                formation = formation_assessment(self.run)
                formation_gate = self._gate_decision("formation", formation)
            if formation_gate["passed"] is not True:
                self.run.set_status(
                    FORMATION_FAILED_STATUS,
                    completed_through_stage="formation",
                    completed_through_block=4,
                    formation_assessment_hash=digest(formation),
                    next_stage_authorized=False,
                    completed_pilot=False,
                    gate4_authorized=False,
                    confirmation_authorized=False,
                    experiment_started=not self.fake_transport_test,
                    stop_identity=None,
                    budget=self.ledger.snapshot(),
                    stage_budgets=self.ledger.stage_snapshots(),
                )
                return self._summary()

            report = self._execute_stage("remaining_arms", 4)
            if report.status != "completed_stage":
                return self._summary()
            report = self._execute_stage("probes", 4)
            if report.status != "completed_stage":
                return self._summary()
            final_rows = {
                str(event.get("identity")): event
                for event in self.run.result_events()
            }
            if (report.completed_logical_requests != 1580
                    or any(
                        final_rows.get(spec.identity, {}).get("status")
                        not in OBSERVATION_STATUSES
                        for spec in self.run.plan.requests
                    )):
                raise V12MasterControllerError(
                    "phase 2 returned without all 1,580 observations"
                )
            final_status = (
                FAKE_FINAL_STATUS if self.fake_transport_test else FINAL_STATUS
            )
            self.run.set_status(
                final_status,
                completed_through_stage="probes",
                completed_through_block=4,
                completed_pilot=not self.fake_transport_test,
                experiment_started=not self.fake_transport_test,
                next_stage_authorized=False,
                gate4_authorized=False,
                confirmation_authorized=False,
                post_run_human_audit_required=True,
                budget=self.ledger.snapshot(),
                stage_budgets=self.ledger.stage_snapshots(),
                stop_identity=None,
            )
            if self.fake_transport_test and isinstance(self.run, DurableRun):
                _verify_v1_2_master_offline_fake_integrity(
                    self.run,
                    self.proposal,
                    self.authorization_receipt,
                    self.projection,
                )
            return self._summary()


def build_fake_v1_2_master_executor_for_test(
    run: DurableRun,
    scripted_outcomes: Sequence[TransportResponse | TransportFailure],
    proposal: Mapping[str, Any],
    projection: Mapping[str, Any],
    *,
    authorization_receipt: Mapping[str, Any],
    sleep=None,
    now=None,
) -> V12MasterExecutor:
    run_path = getattr(run, "path", None)
    if (run_path is not None
            and Path(run_path).resolve(strict=False) == (
                ROOT / MASTER_RUN_DIRECTORY
            ).resolve(strict=False)):
        raise V12MasterControllerError(
            "offline fake execution cannot mutate the canonical production run"
        )
    controller = object.__new__(V12MasterExecutor)
    controller.proposal = _final_proposal(proposal)
    controller.projection = validate_unified_projection(projection)
    controller.authorization_receipt = json.loads(
        canonical(dict(authorization_receipt))
    )
    controller.fake_transport_test = True
    controller.executor_provenance = FAKE_EXECUTOR_PROVENANCE
    transport = _OfflineScriptedTransport(
        scripted_outcomes, controller.proposal["transport"]
    )
    provider = OpenAIResponsesProvider(transport)
    with run.execution_lock():
        controller.mode_binding = _mode_binding(
            run,
            controller.proposal,
            mode=FAKE_EXECUTION_MODE,
            provenance=FAKE_EXECUTOR_PROVENANCE,
        )
    StagedExecutor.__init__(
        controller,
        run,
        provider,
        ScientificRequestFactory(run.config, run.plan),
        sleep=(lambda _seconds: None) if sleep is None else sleep,
        now=now,
        dedicated_controller=MASTER_CONTROLLER_ID,
    )
    controller._validate_release()
    return controller


class _ReadOnlySmokePrefix(DurableRun):
    """In-memory read-only view of the journal at the persisted smoke gate."""

    @property
    def manifest(self) -> dict[str, Any]:
        return json.loads(canonical(self._prefix_manifest))


def _replay_prefix_stage_budgets(
    run: DurableRun,
    manifest: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    aggregate = validate_unified_stage_caps(manifest["authorized_stage_caps"])
    stage_for_identity = {
        spec.identity: _PLAN_STAGE_TO_RELEASE[spec.stage]
        for spec in run.plan.requests
    }
    snapshots: dict[str, Any] = {}
    for stage in RELEASE_ORDER:
        cap = aggregate["stage_caps"][stage]
        release = _release_limits_from_caps(cap, cumulative=False)
        ledger = BudgetLedger.replay(
            run.config.limits,
            run.config.pricing,
            Decimal(str(cap["max_cost_usd"])),
            [
                row for row in records
                if stage_for_identity.get(row.get("identity")) == stage
            ],
            release,
        )
        if ledger.active:
            raise V12MasterControllerError(
                "smoke prefix stage ledger contains an unresolved reservation"
            )
        snapshots[stage] = ledger.snapshot()
    return snapshots


def _smoke_prefix_run(run: DurableRun) -> DurableRun:
    smoke = [
        event for event in run.events
        if event.get("kind") == "gate"
        and event.get("gate") == V1_2_SMOKE_INTEGRITY_GATE
    ]
    if len(smoke) != 1:
        raise V12MasterControllerError(
            "terminal master run lacks exactly one smoke integrity gate"
        )
    gate = smoke[0]
    sequence = gate.get("sequence")
    if type(sequence) is not int or sequence < 0:
        raise V12MasterControllerError("smoke integrity gate sequence is invalid")
    prefix = object.__new__(_ReadOnlySmokePrefix)
    prefix.path = run.path
    prefix.config = run.config
    prefix.plan = run.plan
    prefix.journal_path = run.journal_path
    prefix.payload_path = run.payload_path
    prefix.wire_payload_path = run.wire_payload_path
    prefix.events = list(run.events[:sequence + 1])
    if not prefix.events or prefix.events[-1].get("event_hash") != gate.get(
            "event_hash"):
        raise V12MasterControllerError("smoke gate is not at its claimed prefix")
    manifest = run.manifest
    release = DispatchReleaseLimits.from_mapping(
        manifest["authorized_release_limits"]
    )
    budget = BudgetLedger.replay(
        run.config.limits,
        run.config.pricing,
        Decimal(manifest["authorized_budget_usd"]),
        prefix.budget_records(),
        release,
    )
    if budget.active:
        raise V12MasterControllerError(
            "smoke prefix contains an unresolved reservation"
        )
    manifest.update(
        status="completed_stage",
        completed_through_stage="smoke",
        completed_through_block=0,
        stop_identity=None,
        event_count=len(prefix.events),
        last_event_hash=gate["event_hash"],
        budget=budget.snapshot(),
        stage_budgets=_replay_prefix_stage_budgets(
            run, manifest, prefix.budget_records()
        ),
    )
    prefix._prefix_manifest = manifest
    return prefix


def _failed_smoke_audit_run(run: DurableRun) -> DurableRun:
    """Recreate the exact pre-gate smoke boundary for a failed audit."""
    if any(
        event.get("kind") == "gate"
        and event.get("gate") == V1_2_SMOKE_INTEGRITY_GATE
        for event in run.events
    ):
        raise V12MasterControllerError(
            "failed smoke run unexpectedly contains a smoke gate"
        )
    prefix = object.__new__(_ReadOnlySmokePrefix)
    prefix.path = run.path
    prefix.config = run.config
    prefix.plan = run.plan
    prefix.journal_path = run.journal_path
    prefix.payload_path = run.payload_path
    prefix.wire_payload_path = run.wire_payload_path
    prefix.events = list(run.events)
    manifest = run.manifest
    release = DispatchReleaseLimits.from_mapping(
        manifest["authorized_release_limits"]
    )
    budget = BudgetLedger.replay(
        run.config.limits,
        run.config.pricing,
        Decimal(manifest["authorized_budget_usd"]),
        prefix.budget_records(),
        release,
    )
    if budget.active:
        raise V12MasterControllerError(
            "failed smoke boundary contains an unresolved reservation"
        )
    manifest.update(
        status="completed_stage",
        completed_through_stage="smoke",
        completed_through_block=0,
        stop_identity=None,
        event_count=len(prefix.events),
        last_event_hash=(
            None if not prefix.events else prefix.events[-1]["event_hash"]
        ),
        budget=budget.snapshot(),
        stage_budgets=_replay_prefix_stage_budgets(
            run, manifest, prefix.budget_records()
        ),
    )
    prefix._prefix_manifest = manifest
    return prefix


def _verify_master_lineage(
    run: DurableRun,
    proposal: Mapping[str, Any],
    receipt: Mapping[str, Any],
    projection: Mapping[str, Any],
    *,
    execution_mode: str = EMPIRICAL_EXECUTION_MODE,
    executor_provenance: str = EMPIRICAL_EXECUTOR_PROVENANCE,
    empirical_evidence: bool = True,
) -> None:
    manifest = run.manifest
    dispatch = DispatchAuthorizationArtifact.from_mapping(
        manifest["initial_dispatch_authorization"]
    )
    if (canonical(manifest.get("dispatch_authorization"))
            != canonical(dispatch.to_dict())
            or manifest.get("dispatch_authorization_hash")
            != digest(dispatch.to_dict())):
        raise V12MasterControllerError(
            "master dispatch authorization lineage changed"
        )
    expected_binding = _binding_value(
        proposal,
        {"authorization_hash": receipt["authorization_hash"]},
        receipt,
        run.config,
        run.plan,
        dispatch,
        projection,
    )
    bindings = [
        event for event in run.events if event.get("kind") == MASTER_BINDING_EVENT
    ]
    claims = [
        event for event in run.events if event.get("kind") == MASTER_CLAIM_EVENT
    ]
    modes = [
        event for event in run.events if event.get("kind") == MASTER_MODE_EVENT
    ]
    if len(bindings) != 1 or len(claims) != 1 or len(modes) != 1:
        raise V12MasterControllerError(
            "master release prefix is not singular"
        )
    binding, claim, mode = bindings[0], claims[0], modes[0]
    event_fields = {
        "sequence", "previous_event_hash", "kind", "recorded_at", "event_hash"
    }
    if (binding.get("sequence") != 0
            or binding.get("previous_event_hash") is not None
            or set(binding) != event_fields | set(expected_binding)
            or any(binding.get(key) != value
                   for key, value in expected_binding.items())):
        raise V12MasterControllerError("master release binding is invalid")
    expected_claim = {
        "controller": MASTER_CONTROLLER_ID,
        "run_id": MASTER_RUN_ID,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_hash": receipt["authorization_hash"],
        "receipt_hash": receipt["receipt_hash"],
        "receipt_claimed_at": receipt["claimed_at"],
        "binding_event_hash": binding["event_hash"],
    }
    if (claim.get("sequence") != 1
            or claim.get("previous_event_hash") != binding.get("event_hash")
            or set(claim) != event_fields | set(expected_claim)
            or any(claim.get(key) != value for key, value in expected_claim.items())):
        raise V12MasterControllerError("master receipt claim is invalid")
    expected_mode = {
        "controller": MASTER_CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "run_id": MASTER_RUN_ID,
        "mode": execution_mode,
        "executor_provenance": executor_provenance,
    }
    if (mode.get("sequence") != 2
            or mode.get("previous_event_hash") != claim.get("event_hash")
            or set(mode) != event_fields | set(expected_mode)
            or any(mode.get(key) != value for key, value in expected_mode.items())):
        raise V12MasterControllerError("master empirical execution mode is invalid")
    if (manifest.get("master_proposal_hash") != proposal["proposal_hash"]
            or manifest.get("master_authorization_hash")
            != receipt["authorization_hash"]
            or manifest.get("master_authorization_receipt_hash")
            != receipt["receipt_hash"]
            or manifest.get("master_authorization_claim_event_hash")
            != claim["event_hash"]
            or canonical(manifest.get("authorized_stage_caps"))
            != canonical(proposal["projected_stage_caps"]["stage_caps"])
            or manifest.get("authorized_stage_caps_hash")
            != proposal["projected_stage_caps_hash"]
            or manifest.get("master_projection_hash")
            != projection["projection_hash"]
            or manifest.get("execution_mode") != execution_mode
            or manifest.get("executor_provenance") != executor_provenance
            or manifest.get("empirical_evidence") is not empirical_evidence
            or canonical(manifest.get("transport"))
            != canonical(proposal["transport"])
            or manifest.get("transport_hash") != proposal["transport_hash"]
            or any(event.get("kind") == "authorization_renewal"
                   for event in run.events)):
        raise V12MasterControllerError("master manifest lineage is invalid")


def _validate_receipt_evidence(
    receipt: Mapping[str, Any], proposal: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise V12MasterControllerError("master receipt evidence is invalid")
    value = json.loads(canonical(dict(receipt)))
    body = dict(value)
    receipt_hash = body.pop("receipt_hash", None)
    expected_fields = {
        "artifact_type", "controller_id", "master_run_id",
        "master_run_directory", "proposal_hash", "authorization_id",
        "authorization_uuid", "authorization_hash", "confirmed_at",
        "claimed_at", "source_contract_hash", "stage_bindings_hash",
        "projected_stage_caps_hash",
        "exact_shape_sum_reservation_envelope_hash",
        "operational_cost_hard_cap_usd", "scope_hash",
        "consumes_entire_master_authorization",
        "stage_boundaries_do_not_reset_authority_or_budget",
        "credential_access_permitted_only_after_receipt",
    }
    try:
        claimed_at = datetime.fromisoformat(
            str(value["claimed_at"]).replace("Z", "+00:00")
        )
        confirmed_at = datetime.fromisoformat(
            str(value["confirmed_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V12MasterControllerError("master receipt timestamp is invalid") from exc
    if (set(body) != expected_fields
            or receipt_hash != digest(body)
            or claimed_at.tzinfo is None or claimed_at.utcoffset() is None
            or confirmed_at.tzinfo is None or confirmed_at.utcoffset() is None
            or claimed_at < confirmed_at
            or value.get("artifact_type")
            != "v1_2_unified_authorization_receipt_v1"
            or value.get("controller_id") != MASTER_CONTROLLER_ID
            or value.get("master_run_id") != MASTER_RUN_ID
            or value.get("master_run_directory") != MASTER_RUN_DIRECTORY
            or value.get("proposal_hash") != proposal["proposal_hash"]
            or value.get("source_contract_hash")
            != proposal["source_contract_hash"]
            or value.get("stage_bindings_hash")
            != proposal["stage_bindings_hash"]
            or value.get("projected_stage_caps_hash")
            != proposal["projected_stage_caps_hash"]
            or value.get("exact_shape_sum_reservation_envelope_hash")
            != proposal["exact_shape_sum_reservation_envelope"]["envelope_hash"]
            or value.get("operational_cost_hard_cap_usd")
            != SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
            or value.get("scope_hash") != digest(proposal["scope"])
            or value.get("consumes_entire_master_authorization") is not True
            or value.get("stage_boundaries_do_not_reset_authority_or_budget")
            is not True
            or value.get("credential_access_permitted_only_after_receipt")
            is not True):
        raise V12MasterControllerError(
            "master receipt differs from the final proposal"
        )
    return value


_RESERVATION_EVENT_FIELDS = frozenset({
    "sequence", "previous_event_hash", "kind", "recorded_at", "identity",
    "attempt", "request_hash", "wire_payload_hash", "reservation",
    "event_hash",
})
_RESULT_EVENT_FIELDS = frozenset({
    "sequence", "previous_event_hash", "kind", "recorded_at", "identity",
    "attempt", "reservation_id", "request_hash", "wire_payload_hash",
    "status", "ambiguous", "known_not_completed", "usage",
    "budget_accounting", "provider_identity", "provider_result", "event_hash",
})
_CHECKPOINT_EVENT_FIELDS = frozenset({
    "sequence", "previous_event_hash", "kind", "recorded_at", "event_hash",
    "checkpoint_id", "checkpoint_hash", "block", "history",
    "contributors", "result_hashes",
})
_SCIENTIFIC_ARTIFACT_EVENT_FIELDS = frozenset({
    "sequence", "previous_event_hash", "kind", "recorded_at", "event_hash",
    "artifact_id", "artifact", "artifact_hash",
})
_MASTER_EVENT_KINDS = frozenset({
    MASTER_BINDING_EVENT,
    MASTER_CLAIM_EVENT,
    MASTER_MODE_EVENT,
    "reservation",
    "result",
    "ambiguous_5xx_retry_scheduled",
    "checkpoint",
    "scientific_artifact",
    "gate",
})
_BASE_PROVIDER_RESULT_FIELDS = frozenset({
    "status", "raw_response", "output_text", "parsed_output", "refusal",
    "error", "provider_request_id", "returned_model", "model_metadata",
    "usage", "timing", "ambiguous", "known_not_completed",
    "transport_evidence",
})
_RECOVERED_PROVIDER_RESULT_FIELDS = frozenset({
    "transport_recovered", "ambiguous_5xx_retry_count",
    "prior_ambiguous_result_event_hashes", "retry_policy_hash",
    "dependency_closure_hash",
})
_PROVIDER_USAGE_FIELDS = frozenset({
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_tokens", "total_tokens", "raw",
})


def _reconstruct_provider_result(
    provider: Mapping[str, Any],
) -> ProviderResult:
    """Validate the complete frozen ProviderResult schema, including timing."""
    base = {key: provider.get(key) for key in _BASE_PROVIDER_RESULT_FIELDS}
    timing = base.get("timing")
    if not isinstance(timing, Mapping) or set(timing) != {
            "started_at", "ended_at", "elapsed_seconds"}:
        raise V12MasterControllerError("master provider timing is invalid")
    elapsed = timing.get("elapsed_seconds")
    if (isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or elapsed < 0):
        raise V12MasterControllerError("master provider timing is invalid")
    try:
        started = datetime.fromisoformat(
            str(timing["started_at"]).replace("Z", "+00:00")
        )
        ended = datetime.fromisoformat(
            str(timing["ended_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V12MasterControllerError("master provider timing is invalid") from exc
    if (started.tzinfo is None or started.utcoffset() is None
            or ended.tzinfo is None or ended.utcoffset() is None
            or ended < started):
        raise V12MasterControllerError("master provider timing is invalid")

    usage_value = base.get("usage")
    usage = None
    if usage_value is not None:
        if (not isinstance(usage_value, Mapping)
                or set(usage_value) != _PROVIDER_USAGE_FIELDS
                or not isinstance(usage_value.get("raw"), Mapping)):
            raise V12MasterControllerError("master provider usage is invalid")
        try:
            usage = ProviderUsage(
                input_tokens=usage_value["input_tokens"],
                cached_input_tokens=usage_value["cached_input_tokens"],
                cache_write_input_tokens=usage_value[
                    "cache_write_input_tokens"
                ],
                output_tokens=usage_value["output_tokens"],
                reasoning_tokens=usage_value["reasoning_tokens"],
                total_tokens=usage_value["total_tokens"],
                raw_json=canonical(usage_value["raw"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V12MasterControllerError(
                "master provider usage is invalid"
            ) from exc
    try:
        reconstructed = ProviderResult(
            status=ProviderStatus(str(base["status"])),
            raw_response_json=(
                None if base["raw_response"] is None
                else canonical(base["raw_response"])
            ),
            output_text=base["output_text"],
            parsed_output_json=(
                None if base["parsed_output"] is None
                else canonical(base["parsed_output"])
            ),
            refusal=base["refusal"],
            error=base["error"],
            provider_request_id=base["provider_request_id"],
            returned_model=base["returned_model"],
            model_metadata_json=canonical(base["model_metadata"]),
            usage=usage,
            timing=ProviderTiming(
                started_at=timing["started_at"],
                ended_at=timing["ended_at"],
                elapsed_seconds=elapsed,
            ),
            ambiguous=base["ambiguous"],
            known_not_completed=base["known_not_completed"],
            transport_evidence_json=canonical(base["transport_evidence"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V12MasterControllerError(
            "master provider result cannot be reconstructed"
        ) from exc
    if canonical(reconstructed.to_dict()) != canonical(base):
        raise V12MasterControllerError(
            "master provider result differs from its typed reconstruction"
        )
    return reconstructed


def _verify_master_attempts(
    run: DurableRun, proposal: Mapping[str, Any]
) -> dict[str, Any]:
    specs = {spec.identity: spec for spec in run.plan.requests}
    positions = {
        spec.identity: index for index, spec in enumerate(run.plan.requests)
    }
    reservations = [
        event for event in run.events if event.get("kind") == "reservation"
    ]
    results = [event for event in run.events if event.get("kind") == "result"]
    reservation_by_id: dict[str, Mapping[str, Any]] = {}
    result_by_reservation: dict[str, Mapping[str, Any]] = {}
    for event in reservations:
        if set(event) != _RESERVATION_EVENT_FIELDS:
            raise V12MasterControllerError(
                "master reservation event schema is invalid"
            )
        value = event.get("reservation")
        reservation_id = (
            value.get("reservation_id") if isinstance(value, Mapping) else None
        )
        if (not isinstance(reservation_id, str)
                or reservation_id in reservation_by_id):
            raise V12MasterControllerError(
                "master reservation identity is invalid or duplicated"
            )
        reservation_by_id[reservation_id] = event
    for event in results:
        if set(event) != _RESULT_EVENT_FIELDS:
            raise V12MasterControllerError(
                "master result event schema is invalid"
            )
        reservation_id = event.get("reservation_id")
        if (not isinstance(reservation_id, str)
                or reservation_id in result_by_reservation):
            raise V12MasterControllerError(
                "master result reservation identity is invalid or duplicated"
            )
        result_by_reservation[reservation_id] = event
    if set(reservation_by_id) != set(result_by_reservation):
        raise V12MasterControllerError(
            "master reservations and results are not one-to-one"
        )

    factory = ScientificRequestFactory(run.config, run.plan)
    prior_by_identity: dict[str, list[dict[str, Any]]] = {}
    last_position = -1
    required_provider = proposal["required_provider_identity"]
    for reservation in reservations:
        identity = reservation.get("identity")
        attempt = reservation.get("attempt")
        if identity not in specs or type(attempt) is not int or attempt < 0:
            raise V12MasterControllerError("master reservation scope is invalid")
        position = positions[identity]
        if position < last_position:
            raise V12MasterControllerError(
                "master requests were not dispatched in canonical order"
            )
        last_position = position
        prior = prior_by_identity.setdefault(identity, [])
        if attempt != len(prior):
            raise V12MasterControllerError(
                "master attempt ordinals are not contiguous"
            )
        reservation_id = reservation["reservation"]["reservation_id"]
        result = result_by_reservation[reservation_id]
        if (result.get("sequence") != reservation.get("sequence", -1) + 1
                or result.get("identity") != identity
                or result.get("attempt") != attempt
                or result.get("request_hash") != reservation.get("request_hash")
                or result.get("wire_payload_hash")
                != reservation.get("wire_payload_hash")):
            raise V12MasterControllerError(
                "master reservation/result lineage is invalid"
            )
        try:
            expected_request = factory(
                specs[identity], ExecutionContext(run, attempt, tuple(prior))
            )
            actual_request = run.request_payload(result["request_hash"])
            actual_wire = run.wire_payload(result["wire_payload_hash"])
            expected_wire = OpenAIResponsesProvider.build_payload(
                expected_request
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise V12MasterControllerError(
                "master request or wire payload cannot be reconstructed"
            ) from exc
        if (canonical(actual_request) != canonical(expected_request.to_dict())
                or digest(actual_request) != result.get("request_hash")
                or canonical(actual_wire) != canonical(expected_wire)
                or digest(actual_wire) != result.get("wire_payload_hash")):
            raise V12MasterControllerError(
                "master request or wire payload differs from the frozen factory"
            )
        try:
            reservation_value = reservation["reservation"]
            reserved = Reservation.from_mapping(reservation_value)
        except (KeyError, TypeError, ValueError, BudgetStateError) as exc:
            raise V12MasterControllerError(
                "master reservation cannot be reconstructed"
            ) from exc
        if (not isinstance(reservation_value, Mapping)
                or set(reservation_value) != set(reserved.to_dict())
                or canonical(reservation_value) != canonical(reserved.to_dict())
                or reserved.identity != identity
                or reserved.attempt != attempt
                or reserved.input_tokens
                != expected_request.conservative_input_token_bound
                or reserved.output_tokens != expected_request.max_output_tokens):
            raise V12MasterControllerError(
                "master reservation differs from its exact request shape"
            )
        provider = result.get("provider_result")
        if not isinstance(provider, Mapping):
            raise V12MasterControllerError("master result lacks provider evidence")
        provider_fields = set(provider)
        if provider_fields not in (
            set(_BASE_PROVIDER_RESULT_FIELDS),
            set(_BASE_PROVIDER_RESULT_FIELDS | _RECOVERED_PROVIDER_RESULT_FIELDS),
        ):
            raise V12MasterControllerError(
                "master provider result schema is invalid"
            )
        _reconstruct_provider_result(provider)
        if (result.get("status") != provider.get("status")
                or result.get("ambiguous") is not provider.get("ambiguous")
                or result.get("known_not_completed")
                is not provider.get("known_not_completed")
                or canonical(result.get("usage"))
                != canonical(provider.get("usage"))):
            raise V12MasterControllerError(
                "master result derivatives differ from provider evidence"
            )
        status = result.get("status")
        if status in PROVIDER_IDENTITY_STATUSES:
            if (not _strict_provider_result_matches_event(run, result)
                    or _provider_identity_candidate(
                        run.config, str(status), provider
                    ) != required_provider
                    or result.get("provider_identity") != required_provider
                    or not isinstance(provider.get("provider_request_id"), str)
                    or not provider["provider_request_id"].strip()):
                raise V12MasterControllerError(
                    "master raw provider evidence or identity is invalid"
                )
        else:
            try:
                provider_status = ProviderStatus(str(status))
                TransportFailure(
                    provider_status,
                    provider["error"],
                    request_id=provider.get("provider_request_id"),
                    raw_response=provider.get("raw_response"),
                    known_not_completed=provider.get("known_not_completed"),
                    transport_evidence=provider.get("transport_evidence"),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise V12MasterControllerError(
                    "master typed transport-failure evidence is invalid"
                ) from exc
            if (result.get("provider_identity") is not None
                    or (status == "retryable_error"
                        and provider.get("known_not_completed") is not True)
                    or provider.get("output_text") is not None
                    or provider.get("parsed_output") is not None
                    or provider.get("refusal") is not None
                    or provider.get("returned_model") is not None
                    or provider.get("model_metadata") != {}
                    or provider.get("usage") is not None):
                raise V12MasterControllerError(
                    "transport-only failure carries forbidden response fields"
                )
        prior.append(result)
    return {
        "reservations": reservations,
        "results": results,
        "final_rows": {
            identity: rows[-1]
            for identity, rows in prior_by_identity.items() if rows
        },
    }


def _verify_master_checkpoints_and_artifacts(
    run: DurableRun, final_rows: Mapping[str, Mapping[str, Any]]
) -> None:
    checkpoint_events = [
        event for event in run.events if event.get("kind") == "checkpoint"
    ]
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for event in checkpoint_events:
        if set(event) != _CHECKPOINT_EVENT_FIELDS:
            raise V12MasterControllerError(
                "master checkpoint event schema is invalid"
            )
        by_id.setdefault(str(event.get("checkpoint_id")), []).append(event)
    expected_ids: set[str] = set()
    for checkpoint in run.plan.checkpoints:
        rows = [final_rows.get(identity) for identity in checkpoint.contributors]
        complete = all(
            isinstance(row, Mapping)
            and row.get("status") in OBSERVATION_STATUSES
            for row in rows
        )
        matches = by_id.get(checkpoint.checkpoint_id, [])
        if not complete:
            if matches:
                raise V12MasterControllerError(
                    "checkpoint exists before all contributors resolve"
                )
            continue
        expected_ids.add(checkpoint.checkpoint_id)
        if len(matches) != 1:
            raise V12MasterControllerError(
                "completed checkpoint is missing or duplicated"
            )
        result_hashes = [digest(row) for row in rows]
        expected_hash = digest({
            "spec": checkpoint.to_dict(), "result_hashes": result_hashes,
        })
        event = matches[0]
        if (event.get("checkpoint_hash") != expected_hash
                or event.get("block") != checkpoint.block
                or event.get("history") != checkpoint.history
                or event.get("contributors") != list(checkpoint.contributors)
                or event.get("result_hashes") != result_hashes
                or event.get("sequence", -1)
                <= max(row.get("sequence", -1) for row in rows)):
            raise V12MasterControllerError(
                "checkpoint lineage differs from contributor results"
            )
    if set(by_id) != expected_ids:
        raise V12MasterControllerError("checkpoint inventory is not exact")

    artifact_events = [
        event for event in run.events
        if event.get("kind") == "scientific_artifact"
    ]
    actual: dict[str, Any] = {}
    for event in artifact_events:
        if set(event) != _SCIENTIFIC_ARTIFACT_EVENT_FIELDS:
            raise V12MasterControllerError(
                "master scientific-artifact event schema is invalid"
            )
        artifact = event.get("artifact")
        artifact_id = event.get("artifact_id")
        if (not isinstance(artifact_id, str)
                or not isinstance(artifact, Mapping)
                or artifact.get("artifact_id") != artifact_id
                or event.get("artifact_hash") != digest(artifact)
                or artifact_id in actual):
            raise V12MasterControllerError(
                "scientific artifact lineage is invalid"
            )
        actual[artifact_id] = artifact
    expected_items = ScientificRequestFactory(
        run.config, run.plan
    ).durable_artifacts(run)
    expected = {item["artifact_id"]: item for item in expected_items}
    if len(expected) != len(expected_items) or canonical(actual) != canonical(expected):
        raise V12MasterControllerError(
            "scientific artifact inventory differs from reconstruction"
        )


def _verify_master_retry_policy(
    run: DurableRun,
    attempts: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> None:
    specs = {spec.identity: spec for spec in run.plan.requests}
    results_by_identity: dict[str, list[Mapping[str, Any]]] = {}
    for row in attempts["results"]:
        results_by_identity.setdefault(str(row.get("identity")), []).append(row)
    schedules = [
        event for event in run.events
        if event.get("kind") == "ambiguous_5xx_retry_scheduled"
    ]
    schedule_by_failure: dict[str, list[Mapping[str, Any]]] = {}
    for event in schedules:
        schedule_by_failure.setdefault(
            str(event.get("failed_result_event_hash")), []
        ).append(event)
    reservations_by_attempt = {
        (str(event.get("identity")), event.get("attempt")): event
        for event in attempts["reservations"]
    }

    release_for_identity = {
        spec.identity: _PLAN_STAGE_TO_RELEASE[spec.stage]
        for spec in run.plan.requests
    }
    affected_by_release: dict[str, list[Mapping[str, Any]]] = {
        stage: [] for stage in RELEASE_ORDER
    }
    for row in attempts["results"]:
        if StagedExecutor._eligible_ambiguous_5xx(row):
            affected_by_release[
                release_for_identity[str(row.get("identity"))]
            ].append(row)
    for stage in RELEASE_ORDER:
        distinct: list[str] = []
        for row in affected_by_release[stage]:
            identity = str(row.get("identity"))
            if identity not in distinct:
                distinct.append(identity)
        cap = projection["stage_inventories"][stage][
            "ambiguous_5xx_retry_slots"
        ]
        if cap != _STAGE_AMBIGUOUS_5XX_IDENTITY_CAPS[stage]:
            raise V12MasterControllerError(
                "ambiguous-5xx projection cap changed during replay"
            )
        if len(distinct) > cap:
            raise V12MasterControllerError(
                "ambiguous-5xx retry exceeded its non-fungible stage cap"
            )
        if any(
            sum(str(item.get("identity")) == identity
                for item in affected_by_release[stage]) > 1
            for identity in distinct
        ):
            raise V12MasterControllerError(
                "per-identity ambiguous-5xx retry cap permits only one "
                "eligible failure"
            )
        for row in affected_by_release[stage]:
            identity = str(row.get("identity"))
            index = distinct.index(identity) + 1
            matches = schedule_by_failure.get(str(row.get("event_hash")), [])
            delay = StagedExecutor._v1_2_5xx_delay(row)
            if delay > 60 or len(matches) != 1:
                raise V12MasterControllerError(
                    "eligible ambiguous-5xx result lacks one retry schedule"
                )
            event = matches[0]
            evidence = row["provider_result"]["transport_evidence"]
            policy_hash = projection["retry_policy_hash"]
            exact = {
                "identity": identity,
                "failed_attempt": row.get("attempt"),
                "next_attempt": row.get("attempt") + 1,
                "failed_result_event_hash": row.get("event_hash"),
                "provider_request_id": evidence.get("provider_request_id"),
                "http_status": evidence.get("http_status"),
                "request_hash": row.get("request_hash"),
                "wire_payload_hash": row.get("wire_payload_hash"),
                "retry_index": 1,
                "per_identity_retry_cap": 1,
                "release_affected_identity_cap": cap,
                "release_affected_identity_index": index,
                "delay_seconds": delay,
                "retry_policy_hash": policy_hash,
            }
            expected_keys = {
                "sequence", "previous_event_hash", "kind", "recorded_at",
                "event_hash", "not_before", *exact.keys(),
            }
            if (set(event) != expected_keys
                    or event.get("kind") != "ambiguous_5xx_retry_scheduled"
                    or any(event.get(key) != value for key, value in exact.items())
                    or event.get("sequence") != row.get("sequence", -1) + 1
                    or event.get("previous_event_hash") != row.get("event_hash")):
                raise V12MasterControllerError(
                    "ambiguous-5xx retry schedule fields are invalid"
                )
            try:
                not_before = StagedExecutor._v1_2_5xx_schedule_not_before(event)
                scheduled_at = datetime.fromisoformat(
                    str(event["recorded_at"]).replace("Z", "+00:00")
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise V12MasterControllerError(
                    "ambiguous-5xx retry schedule timing is invalid"
                ) from exc
            if (scheduled_at.tzinfo is None
                    or scheduled_at.utcoffset() is None
                    or (not_before - scheduled_at).total_seconds() != delay):
                raise V12MasterControllerError(
                    "ambiguous-5xx retry schedule timing is invalid"
                )
            retry_reservation = reservations_by_attempt.get(
                (identity, row.get("attempt") + 1)
            )
            if retry_reservation is None:
                if not (
                    run.manifest.get("status") == BUDGET_LIMITED_STATUS
                    and run.manifest.get("budget_limited_before_identity")
                    == identity
                ):
                    raise V12MasterControllerError(
                        "ambiguous-5xx schedule lacks its retry reservation"
                    )
            else:
                try:
                    reserved_at = datetime.fromisoformat(
                        str(retry_reservation["recorded_at"]).replace(
                            "Z", "+00:00"
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise V12MasterControllerError(
                        "ambiguous-5xx retry reservation timing is invalid"
                    ) from exc
                if (reserved_at.tzinfo is None
                        or reserved_at.utcoffset() is None
                        or retry_reservation.get("sequence")
                        != event.get("sequence", -1) + 1
                        or retry_reservation.get("previous_event_hash")
                        != event.get("event_hash")
                        or reserved_at < not_before
                        or retry_reservation.get("wire_payload_hash")
                        != row.get("wire_payload_hash")):
                    raise V12MasterControllerError(
                        "ambiguous-5xx retry reservation chronology is invalid"
                    )
    if any(
        len(rows) != 1
        for failure_hash, rows in schedule_by_failure.items()
    ) or any(
        failure_hash not in {
            str(row.get("event_hash"))
            for stage_rows in affected_by_release.values()
            for row in stage_rows
        }
        for failure_hash in schedule_by_failure
    ):
        raise V12MasterControllerError(
            "ambiguous-5xx retry schedule inventory is invalid"
        )

    for identity, rows in results_by_identity.items():
        spec = specs[identity]
        eligible_rows = [
            row for row in rows if StagedExecutor._eligible_ambiguous_5xx(row)
        ]
        expected_recovery = None if not eligible_rows else eligible_rows[0]
        seen_recovery = False
        known_not_completed_retries = 0
        for index, row in enumerate(rows[:-1]):
            status = row.get("status")
            next_row = rows[index + 1]
            if next_row.get("attempt") != row.get("attempt") + 1:
                raise V12MasterControllerError(
                    "retry attempt ordinal is not contiguous"
                )
            if status in OBSERVATION_STATUSES:
                raise V12MasterControllerError(
                    "a scientific observation was retried"
                )
            if status == "overlength":
                allowed = (
                    spec.action == "message"
                    and sum(item.get("status") == "overlength"
                            for item in rows[:index + 1])
                    <= run.config.retry_policy.message_overlength_retries
                )
            elif status == "retryable_error":
                known_not_completed_retries += 1
                allowed = (
                    row.get("known_not_completed") is True
                    and known_not_completed_retries
                    <= run.config.retry_policy.transport_retries
                )
            elif status == "ambiguous_timeout":
                schedule = schedule_by_failure.get(
                    str(row.get("event_hash")), []
                )
                allowed = (
                    len(schedule) == 1
                    and next_row.get("wire_payload_hash")
                    == schedule[0].get("wire_payload_hash")
                )
            else:
                allowed = False
            if not allowed:
                raise V12MasterControllerError(
                    "master result history contains an unauthorized retry"
                )
            prior_schedule = schedule_by_failure.get(
                str(row.get("event_hash")), []
            )
            expected_reservation_sequence = row.get("sequence", -1) + (
                2 if prior_schedule else 1
            )
            reservation = reservations_by_attempt.get(
                (identity, next_row.get("attempt"))
            )
            if (not isinstance(reservation, Mapping)
                    or reservation.get("sequence")
                    != expected_reservation_sequence):
                raise V12MasterControllerError(
                    "retry reservation is not adjacent to its failed result"
                )

        if sum(row.get("status") == "retryable_error" for row in rows) > (
                run.config.retry_policy.transport_retries):
            raise V12MasterControllerError(
                "known-not-completed transport retry cap was exceeded"
            )
        if sum(row.get("status") == "overlength" for row in rows) > (
                1 if spec.action == "message" else 0):
            raise V12MasterControllerError(
                "overlength retry cap was exceeded"
            )
        for row in rows:
            provider = row.get("provider_result")
            has_recovery = (
                isinstance(provider, Mapping)
                and set(provider) == set(
                    _BASE_PROVIDER_RESULT_FIELDS
                    | _RECOVERED_PROVIDER_RESULT_FIELDS
                )
            )
            should_recover = (
                expected_recovery is not None
                and row.get("sequence", -1)
                > expected_recovery.get("sequence", -1)
                and row.get("status") in (
                    OBSERVATION_STATUSES | {"overlength"}
                )
            )
            if has_recovery != should_recover:
                raise V12MasterControllerError(
                    "ambiguous-5xx recovery markers are not exact"
                )
            if not should_recover:
                continue
            seen_recovery = True
            expected_closure = {identity}
            checkpoint_ids = {
                checkpoint.checkpoint_id
                for checkpoint in run.plan.checkpoints
                if identity in checkpoint.contributors
            }
            changed = True
            while changed:
                changed = False
                for candidate in run.plan.requests:
                    if (candidate.identity not in expected_closure
                            and (expected_closure.intersection(
                                candidate.dependencies
                            ) or candidate.parent_checkpoint in checkpoint_ids)):
                        expected_closure.add(candidate.identity)
                        changed = True
            exact_recovery = {
                "transport_recovered": True,
                "ambiguous_5xx_retry_count": 1,
                "prior_ambiguous_result_event_hashes": [
                    expected_recovery["event_hash"]
                ],
                "retry_policy_hash": projection["retry_policy_hash"],
                "dependency_closure_hash": digest(sorted(expected_closure)),
            }
            if any(provider.get(key) != value
                   for key, value in exact_recovery.items()):
                raise V12MasterControllerError(
                    "ambiguous-5xx recovery markers are invalid"
                )
        if expected_recovery is not None and not seen_recovery and not (
                run.manifest.get("status") == BUDGET_LIMITED_STATUS
                and run.manifest.get("budget_limited_before_identity")
                == identity):
            raise V12MasterControllerError(
                "eligible ambiguous-5xx failure lacks a recovered result"
            )


def _verify_terminal_manifest(run: DurableRun, status: str) -> None:
    manifest = run.manifest
    if (manifest.get("status") != status
            or manifest.get("next_stage_authorized") is not False
            or manifest.get("gate4_authorized") is not False
            or manifest.get("confirmation_authorized") is not False
            or manifest.get("operational_cost_hard_cap_usd")
            != SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD):
        raise V12MasterControllerError(
            "terminal master manifest does not preserve the hard stop"
        )
    if status == FINAL_STATUS:
        exact = {
            "completed_through_stage": "probes",
            "completed_through_block": 4,
            "completed_pilot": True,
            "experiment_started": True,
            "post_run_human_audit_required": True,
            "stop_identity": None,
        }
    elif status == FAKE_FINAL_STATUS:
        exact = {
            "completed_through_stage": "probes",
            "completed_through_block": 4,
            "completed_pilot": False,
            "experiment_started": False,
            "post_run_human_audit_required": True,
            "stop_identity": None,
        }
    elif status == CALIBRATION_FAILED_STATUS:
        exact = {
            "completed_through_stage": "calibration",
            "completed_through_block": 0,
            "completed_pilot": False,
            "experiment_started": False,
            "stop_identity": None,
        }
    elif status == SMOKE_FAILED_STATUS:
        exact = {
            "completed_through_stage": "smoke",
            "completed_through_block": 0,
            "completed_pilot": False,
            "experiment_started": True,
            "stop_identity": None,
        }
    elif status == FORMATION_FAILED_STATUS:
        exact = {
            "completed_through_stage": "formation",
            "completed_through_block": 4,
            "completed_pilot": False,
            "experiment_started": True,
            "stop_identity": None,
        }
    elif status == BUDGET_LIMITED_STATUS:
        exact = {"completed_pilot": False}
    else:
        raise V12MasterControllerError("unsupported terminal master status")
    if any(manifest.get(key) != value for key, value in exact.items()):
        raise V12MasterControllerError(
            "terminal master manifest status fields are invalid"
        )


def _verify_complete_through_stage(
    run: DurableRun,
    final_rows: Mapping[str, Mapping[str, Any]],
    stage: str,
) -> None:
    expected = [
        spec.identity for spec in run.plan.requests
        if STAGE_ORDER.index(spec.stage) <= STAGE_ORDER.index(stage)
    ]
    if (set(final_rows) != set(expected)
            or any(final_rows[identity].get("status") not in OBSERVATION_STATUSES
                   for identity in expected)):
        raise V12MasterControllerError(
            f"terminal master run is incomplete through {stage}"
        )


def _verify_budget_limited_stop(
    run: DurableRun,
    proposal: Mapping[str, Any],
    attempts: Mapping[str, Any],
) -> None:
    """Prove the terminal label is the exact next cumulative reservation stop."""
    manifest = run.manifest
    stop = manifest.get("budget_limited_before_identity")
    if (not isinstance(stop, str)
            or manifest.get("stop_identity") != stop):
        raise V12MasterControllerError(
            "budget-limited stop identity is missing or inconsistent"
        )
    plan_ids = [spec.identity for spec in run.plan.requests]
    try:
        stop_position = plan_ids.index(stop)
    except ValueError as exc:
        raise V12MasterControllerError(
            "budget-limited stop identity is outside the plan"
        ) from exc
    result_rows: dict[str, list[Mapping[str, Any]]] = {}
    for row in attempts["results"]:
        result_rows.setdefault(str(row.get("identity")), []).append(row)
    seen: list[str] = []
    for event in attempts["reservations"]:
        identity = str(event.get("identity"))
        if identity not in seen:
            seen.append(identity)
    stopped_after_attempt = stop in result_rows
    expected_seen = plan_ids[:stop_position + (1 if stopped_after_attempt else 0)]
    if seen != expected_seen:
        raise V12MasterControllerError(
            "budget-limited run is not a canonical identity prefix"
        )
    for identity in plan_ids[:stop_position]:
        rows = result_rows.get(identity, [])
        if not rows or rows[-1].get("status") not in OBSERVATION_STATUSES:
            raise V12MasterControllerError(
                "budget-limited run skipped an earlier scientific observation"
            )
    if stopped_after_attempt and result_rows[stop][-1].get(
            "status") in OBSERVATION_STATUSES:
        raise V12MasterControllerError(
            "budget-limited stop cannot follow a completed stop identity"
        )

    spec = next(spec for spec in run.plan.requests if spec.identity == stop)
    prior = tuple(result_rows.get(stop, []))
    if prior:
        last = prior[-1]
        last_status = last.get("status")
        if last_status == "retryable_error":
            retryable = (
                last.get("known_not_completed") is True
                and sum(row.get("status") == "retryable_error" for row in prior)
                <= run.config.retry_policy.transport_retries
            )
        elif last_status == "overlength":
            retryable = (
                spec.action == "message"
                and sum(row.get("status") == "overlength" for row in prior)
                <= run.config.retry_policy.message_overlength_retries
            )
        elif last_status == "ambiguous_timeout":
            schedule = [
                event for event in run.events
                if event.get("kind") == "ambiguous_5xx_retry_scheduled"
                and event.get("failed_result_event_hash")
                == last.get("event_hash")
            ]
            retryable = (
                StagedExecutor._eligible_ambiguous_5xx(last)
                and len([
                    row for row in prior
                    if StagedExecutor._eligible_ambiguous_5xx(row)
                ]) == 1
                and len(schedule) == 1
                and StagedExecutor._v1_2_5xx_delay(last) <= 60
            )
        else:
            retryable = False
        if not retryable:
            raise V12MasterControllerError(
                "budget-limited stop identity is not eligible for another attempt"
            )
    attempt = len(prior)
    try:
        request = ScientificRequestFactory(run.config, run.plan)(
            spec, ExecutionContext(run, attempt, prior)
        )
        release = DispatchReleaseLimits.from_mapping(
            manifest["authorized_release_limits"]
        )
        ledger = _MasterBudgetLedger(
            run,
            proposal,
            Decimal(manifest["authorized_budget_usd"]),
            release,
        )
    except (KeyError, RuntimeError, TypeError, ValueError, BudgetStateError) as exc:
        raise V12MasterControllerError(
            "budget-limited next reservation cannot be reconstructed"
        ) from exc
    if ledger.active:
        raise V12MasterControllerError(
            "budget-limited run contains an unresolved reservation"
        )
    next_cost = ledger._aggregate.reservation_price(
        request.conservative_input_token_bound,
        request.max_output_tokens,
    )
    hard_cap = Decimal(SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD)
    if (Decimal(manifest["authorized_budget_usd"]) != hard_cap
            or ledger._aggregate.cost_usd + next_cost <= hard_cap):
        raise V12MasterControllerError(
            "budget-limited label is not proven by the next cumulative reservation"
        )
    try:
        ledger.reserve(
            stop,
            attempt,
            input_tokens=request.conservative_input_token_bound,
            output_tokens=request.max_output_tokens,
        )
    except BudgetExhausted:
        pass
    else:
        raise V12MasterControllerError(
            "budget-limited next request still fits the authorized ledgers"
        )


def _verify_gate_chronology(run: DurableRun) -> None:
    if not hasattr(run, "events") or not hasattr(run, "plan"):
        return
    gates = run.gate_events()
    transitions = (
        ("calibration", "calibration", {"smoke"}),
        (V1_2_SMOKE_INTEGRITY_GATE, "smoke", {"formation"}),
        ("formation", "formation", {"remaining_arms", "probes"}),
    )
    specs = {spec.identity: spec for spec in run.plan.requests}
    for gate_name, completed_stage, later_stages in transitions:
        gate = gates.get(gate_name)
        if gate is None:
            continue
        prior_results = [
            event for event in run.events
            if event.get("kind") == "result"
            and specs.get(str(event.get("identity"))) is not None
            and specs[str(event.get("identity"))].stage == completed_stage
        ]
        later_reservations = [
            event for event in run.events
            if event.get("kind") == "reservation"
            and specs.get(str(event.get("identity"))) is not None
            and specs[str(event.get("identity"))].stage in later_stages
        ]
        if (not prior_results
                or gate.get("sequence", -1)
                <= max(event.get("sequence", -1) for event in prior_results)
                or (later_reservations
                    and gate.get("sequence", -1)
                    >= min(event.get("sequence", -1)
                           for event in later_reservations))):
            raise V12MasterControllerError(
                f"{gate_name} gate is outside its exact stage boundary"
            )


def _verify_master_gates(
    run: DurableRun, status: str, *, fake_transport_test: bool = False,
) -> None:
    raw_gates = [
        event for event in run.events if event.get("kind") == "gate"
    ]
    gates = run.gate_events()
    raw_names = [event.get("gate") for event in raw_gates]
    if (any(not isinstance(name, str) for name in raw_names)
            or len(raw_names) != len(set(raw_names))
            or len(raw_gates) != len(gates)):
        raise V12MasterControllerError(
            "master run contains a duplicated or malformed gate"
        )
    allowed = {"calibration", V1_2_SMOKE_INTEGRITY_GATE, "formation"}
    if not set(gates).issubset(allowed):
        raise V12MasterControllerError("master run contains an unknown gate")
    _verify_gate_chronology(run)
    for gate, assessment_function in (
        ("calibration", calibration_assessment),
        ("formation", formation_assessment),
    ):
        event = gates.get(gate)
        if event is None:
            continue
        assessment = assessment_function(run)
        prerequisite = [
            spec.identity for spec in run._gate_prerequisites(gate)
        ]
        note = (
            "Frozen automatic protocol-v1.2 calibration decision."
            if gate == "calibration" else
            "Frozen automatic protocol-v1.2 five-block 3-of-5 decision."
        )
        expected_fields = {
            "sequence", "previous_event_hash", "kind", "recorded_at",
            "event_hash", "gate", "passed", "evidence", "evidence_hash",
            "decided_by", "note", "prerequisite_identities",
        }
        if (set(event) != expected_fields
                or event.get("kind") != "gate"
                or event.get("gate") != gate
                or event.get("passed") is not assessment["passed"]
                or canonical(event.get("evidence")) != canonical(assessment)
                or event.get("evidence_hash") != digest(assessment)
                or event.get("decided_by") != MASTER_CONTROLLER_ID
                or event.get("note") != note
                or event.get("prerequisite_identities") != prerequisite):
            raise V12MasterControllerError(
                f"{gate} gate differs from its recomputed assessment"
            )
        manifest_hash_field = f"{gate}_assessment_hash"
        if (status == CALIBRATION_FAILED_STATUS and gate == "calibration"
                and run.manifest.get(manifest_hash_field) != digest(assessment)):
            raise V12MasterControllerError(
                "calibration terminal assessment hash is invalid"
            )
        if (status == FORMATION_FAILED_STATUS and gate == "formation"
                and run.manifest.get(manifest_hash_field) != digest(assessment)):
            raise V12MasterControllerError(
                "formation terminal assessment hash is invalid"
            )
    smoke = gates.get(V1_2_SMOKE_INTEGRITY_GATE)
    if smoke is not None:
        smoke_prefix = _smoke_prefix_run(run)
        report = (
            _audit_v1_2_smoke_integrity(
                smoke_prefix, fake_transport_test=True
            )
            if fake_transport_test
            else audit_v1_2_smoke_integrity(smoke_prefix)
        )
        if (report.get("automatic_checks_passed") is not True
                or report.get("later_stage_release_permitted") is not True):
            raise V12MasterControllerError(
                "persisted smoke prefix fails exhaustive re-audit"
            )
    if status == SMOKE_FAILED_STATUS:
        stored = run.manifest.get("smoke_integrity_report")
        failed_boundary = _failed_smoke_audit_run(run)
        report = (
            _audit_v1_2_smoke_integrity(
                failed_boundary, fake_transport_test=True
            )
            if fake_transport_test
            else audit_v1_2_smoke_integrity(failed_boundary)
        )
        if (not isinstance(stored, Mapping)
                or canonical(stored) != canonical(report)
                or report.get("report_hash") != digest({
                    key: value for key, value in report.items()
                    if key != "report_hash"
                })
                or report.get("automatic_checks_passed") is not False
                or report.get("later_stage_release_permitted") is not False):
            raise V12MasterControllerError(
                "failed smoke report differs from exhaustive re-audit"
            )

    exact_gates: set[str] | None = None
    if status in {FINAL_STATUS, FAKE_FINAL_STATUS}:
        exact_gates = {"calibration", V1_2_SMOKE_INTEGRITY_GATE, "formation"}
        if any(gates[name].get("passed") is not True for name in exact_gates):
            raise V12MasterControllerError("completed pilot has a failed gate")
    elif status == CALIBRATION_FAILED_STATUS:
        exact_gates = {"calibration"}
        if gates.get("calibration", {}).get("passed") is not False:
            raise V12MasterControllerError("calibration stop lacks its failed gate")
    elif status == SMOKE_FAILED_STATUS:
        exact_gates = {"calibration"}
        if gates.get("calibration", {}).get("passed") is not True:
            raise V12MasterControllerError("smoke stop lacks passed calibration")
    elif status == FORMATION_FAILED_STATUS:
        exact_gates = {"calibration", V1_2_SMOKE_INTEGRITY_GATE, "formation"}
        if (gates.get("calibration", {}).get("passed") is not True
                or gates.get(V1_2_SMOKE_INTEGRITY_GATE, {}).get("passed") is not True
                or gates.get("formation", {}).get("passed") is not False):
            raise V12MasterControllerError("formation stop has invalid gates")
    elif status == BUDGET_LIMITED_STATUS:
        stop = run.manifest.get("budget_limited_before_identity")
        specs = {spec.identity: spec for spec in run.plan.requests}
        if stop not in specs:
            raise V12MasterControllerError(
                "budget-limited stop has no planned identity"
            )
        stage = specs[stop].stage
        exact_gates = (
            set() if stage == "calibration" else
            {"calibration"} if stage == "smoke" else
            {"calibration", V1_2_SMOKE_INTEGRITY_GATE}
            if stage == "formation" else
            {"calibration", V1_2_SMOKE_INTEGRITY_GATE, "formation"}
        )
        if any(gates[name].get("passed") is not True for name in exact_gates):
            raise V12MasterControllerError(
                "budget-limited stop has an invalid prior gate"
            )
    if exact_gates is not None and set(gates) != exact_gates:
        raise V12MasterControllerError(
            "terminal master gate inventory is not exact"
        )


def _verify_v1_2_master_terminal_integrity(
    run: DurableRun,
    proposal: Mapping[str, Any],
    receipt: Mapping[str, Any],
    projection: Mapping[str, Any],
    *,
    fake_transport_test: bool,
) -> dict[str, Any]:
    """Deeply verify one terminal master run without provider access."""

    proposal = _final_proposal(proposal)
    projection = validate_unified_projection(projection)
    receipt = _validate_receipt_evidence(receipt, proposal)
    status = run.manifest.get("status")
    permitted_statuses = (
        {FAKE_FINAL_STATUS}
        if fake_transport_test else _TERMINAL_STATUSES - {FAKE_FINAL_STATUS}
    )
    if status not in permitted_statuses:
        raise V12MasterControllerError(
            "master run has the wrong terminal execution provenance"
        )
    unknown_event_kinds = {
        event.get("kind") for event in run.events
        if event.get("kind") not in _MASTER_EVENT_KINDS
    }
    if unknown_event_kinds:
        raise V12MasterControllerError(
            "terminal master journal contains a forbidden event kind"
        )
    _verify_master_lineage(
        run,
        proposal,
        receipt,
        projection,
        execution_mode=(
            FAKE_EXECUTION_MODE
            if fake_transport_test else EMPIRICAL_EXECUTION_MODE
        ),
        executor_provenance=(
            FAKE_EXECUTOR_PROVENANCE
            if fake_transport_test else EMPIRICAL_EXECUTOR_PROVENANCE
        ),
        empirical_evidence=not fake_transport_test,
    )
    attempts = _verify_master_attempts(run, proposal)
    ceiling = {
        CALIBRATION_FAILED_STATUS: "calibration",
        SMOKE_FAILED_STATUS: "smoke",
        FORMATION_FAILED_STATUS: "formation",
        FINAL_STATUS: "probes",
        FAKE_FINAL_STATUS: "probes",
    }.get(status)
    if ceiling is not None:
        allowed = {
            spec.identity for spec in run.plan.requests
            if STAGE_ORDER.index(spec.stage) <= STAGE_ORDER.index(ceiling)
        }
        if any(
            event.get("identity") not in allowed
            for event in (*attempts["reservations"], *attempts["results"])
        ):
            raise V12MasterControllerError(
                "terminal stop contains dispatch after its stage boundary"
            )
    _verify_terminal_manifest(run, str(status))
    exact_completion_stage = {
        CALIBRATION_FAILED_STATUS: "calibration",
        SMOKE_FAILED_STATUS: "smoke",
        FORMATION_FAILED_STATUS: "formation",
        FINAL_STATUS: "probes",
        FAKE_FINAL_STATUS: "probes",
    }.get(status)
    if exact_completion_stage is not None:
        _verify_complete_through_stage(
            run, attempts["final_rows"], exact_completion_stage
        )
    elif status == BUDGET_LIMITED_STATUS:
        _verify_budget_limited_stop(run, proposal, attempts)
    _verify_master_retry_policy(run, attempts, projection)
    _verify_master_checkpoints_and_artifacts(run, attempts["final_rows"])
    _verify_master_gates(
        run, status, fake_transport_test=fake_transport_test
    )
    return attempts


def verify_v1_2_master_empirical_integrity(
    run: DurableRun,
    proposal: Mapping[str, Any],
    receipt: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Deeply verify one terminal empirical master run without provider access."""

    return _verify_v1_2_master_terminal_integrity(
        run,
        proposal,
        receipt,
        projection,
        fake_transport_test=False,
    )


def _verify_v1_2_master_offline_fake_integrity(
    run: DurableRun,
    proposal: Mapping[str, Any],
    receipt: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a full fake-factory run while preserving non-empirical status."""

    run_path = getattr(run, "path", None)
    if (run_path is None
            or Path(run_path).resolve(strict=False) == (
                ROOT / MASTER_RUN_DIRECTORY
            ).resolve(strict=False)):
        raise V12MasterControllerError(
            "offline fake integrity requires an isolated noncanonical run"
        )
    return _verify_v1_2_master_terminal_integrity(
        run,
        proposal,
        receipt,
        projection,
        fake_transport_test=True,
    )


def _verified_terminal_summary(
    run: DurableRun,
    proposal: Mapping[str, Any],
    receipt: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a terminal receipt-backed run without constructing a provider."""
    status = run.manifest.get("status")
    modes = [
        event for event in run.events if event.get("kind") == MASTER_MODE_EVENT
    ]
    if (status not in _TERMINAL_STATUSES - {FAKE_FINAL_STATUS}
            or len(modes) != 1
            or modes[0].get("mode") != EMPIRICAL_EXECUTION_MODE
            or modes[0].get("executor_provenance")
            != EMPIRICAL_EXECUTOR_PROVENANCE
            or run.manifest.get("execution_mode") != EMPIRICAL_EXECUTION_MODE
            or run.manifest.get("empirical_evidence") is not True):
        raise V12MasterControllerError(
            "master run is not a terminal empirical release"
        )
    integrity = verify_v1_2_master_empirical_integrity(
        run, proposal, receipt, projection
    )
    release = DispatchReleaseLimits.from_mapping(
        run.manifest["authorized_release_limits"]
    )
    ledger = _MasterBudgetLedger(
        run,
        proposal,
        Decimal(run.manifest["authorized_budget_usd"]),
        release,
    )
    if (ledger.active
            or canonical(run.manifest.get("budget"))
            != canonical(ledger.snapshot())
            or canonical(run.manifest.get("stage_budgets"))
            != canonical(ledger.stage_snapshots())):
        raise V12MasterControllerError(
            "terminal master budget does not replay exactly"
        )
    attempts = integrity["results"]
    rows: dict[str, Mapping[str, Any]] = {}
    for event in attempts:
        rows[str(event.get("identity"))] = event
    completed = sum(
        rows.get(spec.identity, {}).get("status") in OBSERVATION_STATUSES
        for spec in run.plan.requests
    )
    gates = run.gate_events()
    if status == FINAL_STATUS:
        if (completed != 1580
                or set(gates) != {
                    "calibration", V1_2_SMOKE_INTEGRITY_GATE, "formation"
                }
                or any(event.get("passed") is not True
                       for event in gates.values())):
            raise V12MasterControllerError(
                "completed master run lacks all observations and passed gates"
            )
    elif status == CALIBRATION_FAILED_STATUS:
        if gates.get("calibration", {}).get("passed") is not False:
            raise V12MasterControllerError(
                "calibration-failed terminal lacks its failed gate"
            )
    elif status == FORMATION_FAILED_STATUS:
        if gates.get("formation", {}).get("passed") is not False:
            raise V12MasterControllerError(
                "formation-failed terminal lacks its failed gate"
            )
    elif status == SMOKE_FAILED_STATUS:
        report = run.manifest.get("smoke_integrity_report")
        if (not isinstance(report, Mapping)
                or report.get("automatic_checks_passed") is not False):
            raise V12MasterControllerError(
                "smoke-failed terminal lacks its failing audit"
            )
    body = {
        "artifact_type": "v1_2_unified_master_execution_report_v1",
        "controller": MASTER_CONTROLLER_ID,
        "status": status,
        "execution_mode": EMPIRICAL_EXECUTION_MODE,
        "executor_provenance": EMPIRICAL_EXECUTOR_PROVENANCE,
        "empirical_evidence": True,
        "network_calls": len(attempts),
        "paid_calls": len(attempts),
        "completed_logical_requests": completed,
        "planned_logical_requests": len(run.plan.requests),
        "dispatch_attempts": len(attempts),
        "experiment_started": bool(run.manifest.get("experiment_started")),
        "completed_pilot": status == FINAL_STATUS,
        "hard_stop_before_gate4": True,
        "gate4_authorized": False,
        "confirmation_authorized": False,
        "proposal_hash": proposal["proposal_hash"],
        "receipt_hash": receipt["receipt_hash"],
        "gates": {
            name: {
                "passed": event.get("passed"),
                "event_hash": event.get("event_hash"),
                "evidence_hash": event.get("evidence_hash"),
            }
            for name, event in gates.items()
        },
        "budget": run.manifest.get("budget"),
        "stage_budgets": run.manifest.get("stage_budgets"),
        "journal_tail": run.manifest.get("last_event_hash"),
        "journal_event_count": run.manifest.get("event_count"),
    }
    return {**body, "report_hash": digest(body)}


def _execute_live_controller_and_verify_terminal(
    executor: V12MasterExecutor,
) -> dict[str, Any]:
    """Return a fresh live result only after terminal evidence replays cleanly."""

    if (type(executor) is not V12MasterExecutor
            or executor.fake_transport_test is not False
            or executor.executor_provenance != EMPIRICAL_EXECUTOR_PROVENANCE):
        raise V12MasterControllerError(
            "live terminal verification requires the sealed empirical executor"
        )
    report = executor.execute()
    if executor.run.manifest.get("status") in (
            _TERMINAL_STATUSES - {FAKE_FINAL_STATUS}):
        return _verified_terminal_summary(
            executor.run,
            executor.proposal,
            executor.authorization_receipt,
            executor.projection,
        )
    return report


def run_v1_2_master(
    authorization_path: Path,
    *,
    proposal_path: Path = PROPOSAL_PATH,
    env_file: Path = STAGE2_ENV_FILE,
) -> dict[str, Any]:
    """Consume one exact authorization and run/resume only the canonical path."""
    proposal = load_v1_2_master_proposal(proposal_path)
    authorization = load_human_authorization(authorization_path, proposal)
    receipt, receipt_created = claim_human_authorization_once_with_state(
        authorization, proposal
    )
    run_path = ROOT / authorization["master_run_directory"]
    observed_exists = run_path.exists()
    if receipt_created and observed_exists:
        raise V12MasterControllerError(
            "a freshly claimed authorization cannot attach to a preexisting run"
        )
    if not receipt_created and not observed_exists:
        raise V12MasterControllerError(
            "a consumed authorization cannot recreate a missing master run"
        )
    projection = build_unified_projection()
    run, config, _plan, _dispatch, projection = prepare_v1_2_master_run(
        run_path,
        proposal,
        authorization,
        receipt,
        workspace_root=ROOT,
        receipt_created=receipt_created,
        projection=projection,
    )
    if run.manifest.get("status") in _TERMINAL_STATUSES:
        # Completed and terminally stopped releases never need an SDK or key.
        return _verified_terminal_summary(run, proposal, receipt, projection)
    try:
        installed = importlib_metadata.version("openai")
    except importlib_metadata.PackageNotFoundError as exc:
        raise V12MasterControllerError("locked OpenAI SDK is not installed") from exc
    if installed != STAGE2_SDK_VERSION:
        raise V12MasterControllerError(
            "installed OpenAI SDK differs from the proposal"
        )
    metadata = _stage2_transport_metadata(sdk_version=installed)
    if (canonical(metadata) != canonical(proposal["transport"])
            or digest(metadata) != proposal["transport_hash"]):
        raise V12MasterControllerError("runtime transport differs from proposal")
    conflicting = sorted(
        name for name in os.environ if name.upper().startswith("OPENAI_")
    )
    if conflicting:
        raise V12MasterControllerError(
            "all ambient OpenAI variables must be unset for this release"
        )

    # The immutable receipt and journal claim exist before this first secret
    # access.  No caller can inject a client, provider, transport, or capability.
    secret = _load_local_openai_key(Path(env_file))
    from openai import (
        DefaultHttpxClient as ExportedDefaultHttpxClient,
        OpenAI as ExportedOpenAI,
    )
    from openai._base_client import DefaultHttpxClient as SDKDefaultHttpxClient
    from openai._client import OpenAI as SDKOpenAI
    from openai.resources.responses.responses import Responses as SDKResponses

    if (ExportedOpenAI is not SDKOpenAI
            or ExportedDefaultHttpxClient is not SDKDefaultHttpxClient
            or SDKOpenAI.__module__ != "openai"
            or SDKOpenAI.__qualname__ != "OpenAI"
            or SDKDefaultHttpxClient.__module__ != "openai"
            or SDKDefaultHttpxClient.__qualname__ != "_DefaultHttpxClient"
            or SDKResponses.__module__ != "openai.resources.responses.responses"
            or SDKResponses.__qualname__ != "Responses"):
        raise V12MasterControllerError(
            "official OpenAI SDK class identities differ from the locked runtime"
        )
    http_client = SDKDefaultHttpxClient(
        trust_env=False, follow_redirects=False
    )
    client = None
    try:
        client = SDKOpenAI(
            api_key=secret,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=STAGE2_TIMEOUT_SECONDS,
            http_client=http_client,
        )
        responses = client.responses
        if (type(http_client) is not SDKDefaultHttpxClient
                or getattr(http_client, "trust_env", None) is not False
                or getattr(http_client, "follow_redirects", None) is not False
                or type(client) is not SDKOpenAI
                or getattr(client, "_client", None) is not http_client
                or type(responses) is not SDKResponses
                or str(getattr(client, "base_url", "")).rstrip("/")
                != "https://api.openai.com/v1"
                or getattr(client, "max_retries", None) != 0
                or float(client.timeout) != float(STAGE2_TIMEOUT_SECONDS)
                or getattr(client, "organization", None) is not None
                or getattr(client, "project", None) is not None):
            raise V12MasterControllerError(
                "constructed OpenAI client differs from the exact release"
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
        controller = object.__new__(V12MasterExecutor)
        controller.proposal = proposal
        controller.projection = projection
        controller.authorization_receipt = receipt
        controller.fake_transport_test = False
        controller.executor_provenance = EMPIRICAL_EXECUTOR_PROVENANCE
        with run.execution_lock():
            controller.mode_binding = _mode_binding(
                run,
                proposal,
                mode=EMPIRICAL_EXECUTION_MODE,
                provenance=EMPIRICAL_EXECUTOR_PROVENANCE,
            )
        StagedExecutor.__init__(
            controller,
            run,
            provider,
            ScientificRequestFactory(config, run.plan),
            dedicated_controller=MASTER_CONTROLLER_ID,
        )
        controller._validate_release()
        return _execute_live_controller_and_verify_terminal(controller)
    finally:
        if client is not None:
            client.close()
        else:
            http_client.close()


def inspect_v1_2_master(
    proposal_path: Path = PROPOSAL_PATH,
) -> dict[str, Any]:
    proposal = load_v1_2_master_proposal(proposal_path)
    return {
        "status": "ready_awaiting_external_human_authorization",
        "proposal_hash": proposal["proposal_hash"],
        "run_id": MASTER_RUN_ID,
        "run_directory": MASTER_RUN_DIRECTORY,
        "logical_requests": 1580,
        "maximum_dispatch_attempts": proposal["scope"][
            "maximum_dispatch_attempts"
        ],
        "operational_cost_hard_cap_usd": (
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
        ),
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or execute the unified protocol-v1.2 pilot"
    )
    parser.add_argument("command", choices=("inspect", "execute"))
    parser.add_argument("--proposal", type=Path, default=PROPOSAL_PATH)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--env-file", type=Path, default=STAGE2_ENV_FILE)
    args = parser.parse_args()
    if args.command == "inspect":
        result = inspect_v1_2_master(args.proposal)
    else:
        if args.authorization is None:
            parser.error("--authorization is required for execute")
        result = run_v1_2_master(
            args.authorization,
            proposal_path=args.proposal,
            env_file=args.env_file,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())


__all__ = [
    "BUDGET_LIMITED_STATUS",
    "FINAL_STATUS",
    "PROPOSAL_PATH",
    "V12MasterControllerError",
    "V12MasterExecutor",
    "build_fake_v1_2_master_executor_for_test",
    "inspect_v1_2_master",
    "load_v1_2_master_proposal",
    "prepare_v1_2_master_run",
    "run_v1_2_master",
]
