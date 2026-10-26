"""Offline contract tests for the one-authorization v1.2 master controller.

The suite deliberately exercises only local state-machine, budget, receipt,
and provenance surfaces.  It must never read a repository credential, build a
live SDK client, or open a network socket.
"""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import uuid
from unittest.mock import patch

from collusion_recovery.budget import (
    BudgetExhausted,
    DispatchReleaseLimits,
)
from collusion_recovery.common import canonical, digest
from collusion_recovery.live_config import ExecutionLimits, Pricing
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    ExecutionReport,
    ResumeError,
    StagedExecutor,
    V1_2_UNIFIED_PILOT_RUN_ID,
)
import collusion_recovery.analysis as analysis
import collusion_recovery.v1_2_master_controller as controller
from collusion_recovery.v1_2_master_controller import (
    BUDGET_LIMITED_STATUS,
    CALIBRATION_FAILED_STATUS,
    FAKE_FINAL_STATUS,
    FORMATION_FAILED_STATUS,
    SMOKE_FAILED_STATUS,
    V12MasterControllerError,
    V12MasterExecutor,
    _MasterBudgetLedger,
)
import collusion_recovery.v1_2_master_release as master_release
from collusion_recovery.v1_2_master_release import (
    V12MasterReleaseError,
    build_stage_binding,
    build_v1_2_master_proposal,
    claim_human_authorization_once,
    complete_human_authorization,
)
from collusion_recovery.v1_2_smoke_audit import V12SmokeIntegrityError
from collusion_recovery.providers import (
    ProviderStatus,
    TransportFailure,
    TransportResponse,
)
from collusion_recovery.v1_2_unified_contract import (
    DISPATCH_ATTEMPT_CEILINGS,
    GATE1,
    GATE2,
    GATE3_PHASE1,
    GATE3_PHASE2,
    LOGICAL_REQUEST_CEILINGS,
    RELEASE_ORDER,
)


TEST_NOW = datetime.fromisoformat("2026-10-25T20:00:00-07:00")
_PLAN_STAGE_COUNTS = {
    "calibration": 20,
    "smoke": 192,
    "formation": 480,
    "remaining_arms": 648,
    "probes": 240,
}


def no_network_or_credentials():
    stack = ExitStack()
    for name in (
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
        "collusion_recovery.v1_2_master_controller._load_local_openai_key",
    ):
        stack.enter_context(
            patch(name, side_effect=AssertionError(
                "network and credential access are forbidden in this test"
            ))
        )
    return stack


class _FlowRun:
    """Small in-memory durable-run double for master control-flow tests."""

    def __init__(self):
        requests = []
        for stage, count in _PLAN_STAGE_COUNTS.items():
            requests.extend(
                SimpleNamespace(identity=f"{stage}/{index}", stage=stage)
                for index in range(count)
            )
        self.plan = SimpleNamespace(requests=tuple(requests))
        self.events = []
        self._results = {}
        self._gates = {}
        self._manifest = {
            "status": "prepared_v1_2_master",
            "event_count": 0,
            "last_event_hash": None,
            "experiment_started": False,
        }

    @property
    def manifest(self):
        return json.loads(canonical(self._manifest))

    @contextmanager
    def execution_lock(self):
        yield

    def set_status(self, status, **fields):
        self._manifest.update(status=status, **fields)

    def result_events(self, identity=None):
        if identity is None:
            return [
                row
                for spec in self.plan.requests
                for row in self._results.get(spec.identity, ())
            ]
        return list(self._results.get(identity, ()))

    def gate_events(self):
        return dict(self._gates)

    def _record_gate_decision_unlocked(
        self, gate, *, passed, evidence, decided_by, note
    ):
        event = {
            "kind": "gate",
            "gate": gate,
            "passed": passed,
            "evidence": json.loads(canonical(dict(evidence))),
            "evidence_hash": digest(dict(evidence)),
            "decided_by": decided_by,
            "note": note,
        }
        event["event_hash"] = digest(event)
        self._gates[gate] = event
        self.events.append(event)
        self._manifest.update(
            event_count=len(self.events), last_event_hash=event["event_hash"]
        )
        return event

    def append(self, kind, **fields):
        event = {"kind": kind, **json.loads(canonical(fields))}
        event["event_hash"] = digest(event)
        self.events.append(event)
        if kind == "gate":
            self._gates[event["gate"]] = event
        self._manifest.update(
            event_count=len(self.events), last_event_hash=event["event_hash"]
        )
        return event

    def complete_stage(self, stage):
        for spec in self.plan.requests:
            if spec.stage == stage and spec.identity not in self._results:
                self._results[spec.identity] = [{
                    "identity": spec.identity,
                    "status": "ok",
                }]


class _FlowLedger:
    active = {}

    @staticmethod
    def snapshot():
        return {"consumed": {"cost_usd": "0"}}

    @staticmethod
    def stage_snapshots():
        return {
            stage: {"consumed": {"cost_usd": "0"}}
            for stage in RELEASE_ORDER
        }


def _flow_executor(run):
    executor = object.__new__(V12MasterExecutor)
    executor.run = run
    executor.proposal = {"proposal_hash": "p" * 64}
    executor.projection = {"projection_hash": "x" * 64}
    executor.authorization_receipt = {"receipt_hash": "r" * 64}
    executor.fake_transport_test = True
    executor.executor_provenance = controller.FAKE_EXECUTOR_PROVENANCE
    return executor


def _flow_stage(calls, *, crash_stage=None):
    def execute_stage(self, stage, block):
        calls.append((stage, block))
        if stage == crash_stage:
            raise RuntimeError("synthetic crash")
        self.run.complete_stage(stage)
        completed = len(self.run.result_events())
        return ExecutionReport(
            status="completed_stage",
            target_stage=stage,
            target_max_block=block,
            dispatched_attempts=_PLAN_STAGE_COUNTS[stage],
            skipped_terminal_identities=0,
            completed_logical_requests=completed,
            planned_through_stage=completed,
            stop_identity=None,
            budget=self.ledger.snapshot(),
        )
    return execute_stage


def _passing_smoke(self):
    existing = self.run.gate_events().get("smoke_integrity")
    if existing is not None:
        return existing
    report = {
        "automatic_checks_passed": True,
        "later_stage_release_permitted": True,
    }
    return self.run.append(
        "gate",
        gate="smoke_integrity",
        passed=True,
        decided_by=controller.V1_2_SMOKE_INTEGRITY_DECIDED_BY,
        note=controller.V1_2_SMOKE_INTEGRITY_NOTE,
        evidence={"audit_report": report},
        evidence_hash=digest({"audit_report": report}),
        prerequisite_identities=[],
    )


@contextmanager
def _flow_patches(calls, *, calibration=True, formation=True,
                  crash_stage=None, smoke="pass"):
    def calibration_assessment(_run):
        return {"assessment_type": "offline", "passed": calibration}

    def formation_assessment(_run):
        return {
            "assessment_type": "offline",
            "passed": formation,
            "qualified_trajectories": 3 if formation else 2,
        }

    def failed_smoke(self):
        report = {
            "automatic_checks_passed": False,
            "later_stage_release_permitted": False,
        }
        self.run.set_status(
            SMOKE_FAILED_STATUS,
            smoke_integrity_report=report,
            experiment_started=True,
            next_stage_authorized=False,
            budget=self.ledger.snapshot(),
            stage_budgets=self.ledger.stage_snapshots(),
        )
        raise V12SmokeIntegrityError("synthetic exhaustive-audit failure")

    with patch.object(controller, "_mode_binding", return_value={}), patch.object(
        V12MasterExecutor, "_validate_scope_history", return_value=None
    ), patch.object(
        V12MasterExecutor, "_audit_historical_provider_results", return_value=None
    ), patch.object(
        V12MasterExecutor, "_master_ledger", return_value=_FlowLedger()
    ), patch.object(
        V12MasterExecutor,
        "_execute_stage",
        _flow_stage(calls, crash_stage=crash_stage),
    ), patch.object(
        V12MasterExecutor,
        "_smoke_gate",
        _passing_smoke if smoke == "pass" else failed_smoke,
    ), patch.object(
        controller, "calibration_assessment", side_effect=calibration_assessment
    ), patch.object(
        controller, "formation_assessment", side_effect=formation_assessment
    ), no_network_or_credentials():
        yield


def _ready_bindings(*, cost_per_stage="10.000000"):
    implementation = master_release.current_implementation_hash()
    plan_hash = digest(["master-controller-test", "plan"])
    bindings = {}
    for index, stage in enumerate(RELEASE_ORDER):
        logical = LOGICAL_REQUEST_CEILINGS[stage]
        bindings[stage] = build_stage_binding(
            stage,
            status="ready",
            controller_id=master_release.MASTER_CONTROLLER_ID,
            plan_hash=plan_hash,
            inventory_hash=digest([stage, "inventory", index]),
            implementation_hash=implementation,
            exact_caps={
                "logical_requests": logical,
                "dispatch_attempts": DISPATCH_ATTEMPT_CEILINGS[stage],
                "max_total_input_tokens": logical * 10_000,
                "max_total_output_tokens": logical * 4_096,
                "max_input_tokens_per_request": 10_000,
                "max_output_tokens_per_request": 4_096,
                "max_in_flight": 1,
                "max_cost_usd": cost_per_stage,
            },
            evidence={
                "offline_test_projection": True,
                "independent_audit_passed": True,
            },
        )
    return bindings


@contextmanager
def _implemented_release():
    with patch.object(
        master_release, "MASTER_CONTROLLER_IMPLEMENTED", True
    ), patch.object(
        master_release, "MASTER_CUMULATIVE_BUDGET_LEDGER_IMPLEMENTED", True
    ):
        yield


def _final_proposal_and_authorization(*, confirmed_at=TEST_NOW):
    proposal = build_v1_2_master_proposal(_ready_bindings())
    auth_uuid = str(uuid.uuid4())
    authorization = complete_human_authorization({
        "authorization_uuid": auth_uuid,
        "authorization_id": f"v1-2-unified-human-{auth_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "confirmed_by": "offline master-controller test",
        "confirmed_at": confirmed_at.isoformat(),
    }, proposal)
    return proposal, authorization


class V12MasterFlowTests(unittest.TestCase):
    def test_one_authorization_advances_every_slice_in_exact_order(self):
        run = _FlowRun()
        calls = []
        with _flow_patches(calls, calibration=True, formation=True):
            report = _flow_executor(run).execute()
        self.assertEqual(
            calls,
            [
                ("calibration", 0),
                ("smoke", 0),
                ("formation", 4),
                ("remaining_arms", 4),
                ("probes", 4),
            ],
        )
        self.assertEqual(report["completed_logical_requests"], 1580)
        self.assertEqual(report["planned_logical_requests"], 1580)
        self.assertEqual(set(report["gates"]), {
            "calibration", "smoke_integrity", "formation",
        })
        self.assertEqual(report["status"], FAKE_FINAL_STATUS)
        self.assertFalse(report["completed_pilot"])
        self.assertEqual(report["execution_mode"], "fake_transport_test")
        self.assertFalse(report["empirical_evidence"])
        self.assertEqual(report["network_calls"], 0)
        self.assertEqual(report["paid_calls"], 0)
        self.assertFalse(report["gate4_authorized"])
        self.assertFalse(report["confirmation_authorized"])
        self.assertFalse(any(
            event.get("kind") == "authorization_renewal"
            for event in run.events
        ))

    def test_calibration_failure_hard_stops_before_hypothesis_calls(self):
        run = _FlowRun()
        calls = []
        with _flow_patches(calls, calibration=False):
            report = _flow_executor(run).execute()
        self.assertEqual(calls, [("calibration", 0)])
        self.assertEqual(report["status"], CALIBRATION_FAILED_STATUS)
        self.assertEqual(report["completed_logical_requests"], 20)
        self.assertFalse(report["experiment_started"])
        self.assertEqual(report["gates"]["calibration"]["passed"], False)

    def test_exhaustive_smoke_failure_hard_stops_before_formation(self):
        run = _FlowRun()
        calls = []
        with _flow_patches(calls, smoke="fail"):
            report = _flow_executor(run).execute()
        self.assertEqual(calls, [("calibration", 0), ("smoke", 0)])
        self.assertEqual(report["status"], SMOKE_FAILED_STATUS)
        self.assertEqual(report["completed_logical_requests"], 212)
        self.assertFalse(report["experiment_started"])

    def test_genuine_smoke_failure_persists_terminal_resume_fields(self):
        run = _FlowRun()
        run.set_status(
            "completed_stage",
            stage_budgets=_FlowLedger.stage_snapshots(),
        )
        executor = _flow_executor(run)
        executor.fake_transport_test = False
        executor.ledger = _FlowLedger()
        failed_report = {
            "automatic_checks_passed": False,
            "later_stage_release_permitted": False,
            "report_hash": "failed-smoke-report",
        }
        with patch.object(
            controller,
            "require_v1_2_smoke_integrity",
            side_effect=V12SmokeIntegrityError("synthetic audit failure"),
        ), patch.object(
            controller,
            "audit_v1_2_smoke_integrity",
            return_value=failed_report,
        ), no_network_or_credentials(), self.assertRaises(V12SmokeIntegrityError):
            executor._smoke_gate()

        manifest = run.manifest
        self.assertEqual(manifest["status"], SMOKE_FAILED_STATUS)
        self.assertEqual(manifest["completed_through_stage"], "smoke")
        self.assertEqual(manifest["completed_through_block"], 0)
        self.assertEqual(manifest["smoke_integrity_report"], failed_report)
        self.assertTrue(manifest["experiment_started"])
        self.assertFalse(manifest["next_stage_authorized"])
        self.assertFalse(manifest["completed_pilot"])
        self.assertFalse(manifest["gate4_authorized"])
        self.assertFalse(manifest["confirmation_authorized"])
        self.assertIsNone(manifest["stop_identity"])
        self.assertEqual(manifest["budget"], _FlowLedger.snapshot())
        self.assertEqual(
            manifest["stage_budgets"], _FlowLedger.stage_snapshots()
        )

    def test_two_of_five_formation_stops_and_three_of_five_releases_phase2(self):
        failed_run = _FlowRun()
        failed_calls = []
        with _flow_patches(failed_calls, formation=False):
            failed = _flow_executor(failed_run).execute()
        self.assertEqual(
            failed_calls,
            [("calibration", 0), ("smoke", 0), ("formation", 4)],
        )
        self.assertEqual(failed["status"], FORMATION_FAILED_STATUS)
        self.assertEqual(failed["completed_logical_requests"], 692)
        self.assertEqual(failed["gates"]["formation"]["passed"], False)

        passed_run = _FlowRun()
        passed_calls = []
        with _flow_patches(passed_calls, formation=True):
            passed = _flow_executor(passed_run).execute()
        self.assertEqual(
            passed_calls[-2:], [("remaining_arms", 4), ("probes", 4)]
        )
        self.assertEqual(passed["completed_logical_requests"], 1580)
        self.assertEqual(passed["status"], FAKE_FINAL_STATUS)
        self.assertFalse(passed["completed_pilot"])
        self.assertFalse(passed["empirical_evidence"])

    def test_crash_after_calibration_gate_resumes_without_releasing_it_twice(self):
        run = _FlowRun()
        first_calls = []
        with _flow_patches(first_calls, crash_stage="smoke"):
            with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
                _flow_executor(run).execute()
        self.assertEqual(first_calls, [("calibration", 0), ("smoke", 0)])
        first_calibration_hash = run.gate_events()["calibration"]["event_hash"]

        resumed_calls = []
        with _flow_patches(resumed_calls):
            report = _flow_executor(run).execute()
        self.assertEqual(
            resumed_calls,
            [
                ("smoke", 0),
                ("formation", 4),
                ("remaining_arms", 4),
                ("probes", 4),
            ],
        )
        self.assertEqual(
            run.gate_events()["calibration"]["event_hash"],
            first_calibration_hash,
        )
        self.assertEqual(report["completed_logical_requests"], 1580)

    def test_terminal_execute_is_idempotent_and_dispatches_nothing(self):
        run = _FlowRun()
        calls = []
        with _flow_patches(calls):
            executor = _flow_executor(run)
            first = executor.execute()
            before = len(run.result_events())
            second = executor.execute()
        self.assertEqual(len(run.result_events()), before)
        self.assertEqual(len(calls), 5)
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first["report_hash"], second["report_hash"])

    def test_authorization_renewal_in_history_is_rejected(self):
        run = _FlowRun()
        run.events.append({"kind": "authorization_renewal"})
        with self.assertRaisesRegex(ResumeError, "cannot renew"):
            _flow_executor(run)._validate_scope_history()


class V12MasterBudgetTests(unittest.TestCase):
    @staticmethod
    def _run_and_proposal(stage_costs):
        specs = (
            SimpleNamespace(identity="calibration/0", stage="calibration"),
            SimpleNamespace(identity="calibration/1", stage="calibration"),
            SimpleNamespace(identity="smoke/0", stage="smoke"),
            SimpleNamespace(identity="formation/0", stage="formation"),
            SimpleNamespace(identity="remaining/0", stage="remaining_arms"),
        )
        run = SimpleNamespace(
            plan=SimpleNamespace(requests=specs),
            config=SimpleNamespace(
                limits=ExecutionLimits(
                    max_requests=100,
                    max_total_input_tokens=10_000,
                    max_total_output_tokens=10_000,
                    max_input_tokens_per_request=1_000,
                    max_output_tokens_per_request=1_000,
                    max_in_flight=1,
                ),
                pricing=Pricing(
                    source="offline-master-budget-test",
                    verified_at=TEST_NOW.isoformat(),
                    input_usd_per_million=Decimal("1000000"),
                    cached_input_usd_per_million=Decimal("1000000"),
                    cache_write_input_usd_per_million=Decimal("1000000"),
                    output_usd_per_million=Decimal("0"),
                ),
            ),
            budget_records=lambda: [],
        )
        caps = {}
        for stage in RELEASE_ORDER:
            caps[stage] = {
                "logical_requests": LOGICAL_REQUEST_CEILINGS[stage],
                "dispatch_attempts": 10,
                "max_total_input_tokens": 10_000,
                "max_total_output_tokens": 10_000,
                "max_input_tokens_per_request": 1_000,
                "max_output_tokens_per_request": 1_000,
                "max_in_flight": 1,
                "max_cost_usd": str(stage_costs.get(stage, "250")),
            }
        return run, {"projected_stage_caps": {"stage_caps": caps}}

    @staticmethod
    def _aggregate_release():
        return DispatchReleaseLimits(
            max_dispatch_attempts=100,
            max_total_input_tokens=10_000,
            max_total_output_tokens=10_000,
            max_cost_usd=Decimal("250"),
            max_in_flight=1,
            max_input_tokens_per_request=1_000,
            max_output_tokens_per_request=1_000,
        )

    @staticmethod
    def _usage(input_tokens):
        return {
            "input_tokens": input_tokens,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": input_tokens,
        }

    def test_stage_budget_is_non_fungible_even_when_global_budget_is_unused(self):
        run, proposal = self._run_and_proposal({GATE1: "1"})
        ledger = _MasterBudgetLedger(
            run, proposal, Decimal("250"), self._aggregate_release()
        )
        first = ledger.reserve(
            "calibration/0", 0, input_tokens=1, output_tokens=1
        )
        ledger.reconcile(first.reservation_id, self._usage(1))
        self.assertEqual(
            ledger.snapshot()["remaining_after_reservations"]["cost_usd"],
            "249",
        )
        with self.assertRaisesRegex(BudgetExhausted, "dollar ceiling"):
            ledger.reserve(
                "calibration/1", 0, input_tokens=1, output_tokens=1
            )

    def test_cumulative_250_cap_does_not_reset_at_stage_boundary(self):
        run, proposal = self._run_and_proposal({})
        ledger = _MasterBudgetLedger(
            run, proposal, Decimal("250"), self._aggregate_release()
        )
        first = ledger.reserve(
            "calibration/0", 0, input_tokens=249, output_tokens=1
        )
        ledger.reconcile(first.reservation_id, self._usage(249))
        with self.assertRaisesRegex(BudgetExhausted, "dollar ceiling"):
            ledger.reserve("smoke/0", 0, input_tokens=2, output_tokens=1)
        self.assertEqual(
            ledger.stage_snapshots()[GATE2]["consumed"]["cost_usd"], "0"
        )

    def test_budget_exhaustion_becomes_durable_terminal_noncompletion(self):
        run = _FlowRun()
        executor = _flow_executor(run)
        executor.ledger = _FlowLedger()
        stopped = ExecutionReport(
            status="paused_budget_exhausted",
            target_stage="calibration",
            target_max_block=0,
            dispatched_attempts=0,
            skipped_terminal_identities=0,
            completed_logical_requests=0,
            planned_through_stage=20,
            stop_identity="calibration/0",
            budget=executor.ledger.snapshot(),
        )
        with patch.object(executor, "_execute_unlocked", return_value=stopped):
            report = executor._execute_stage("calibration", 0)
        self.assertEqual(report.status, "paused_budget_exhausted")
        self.assertEqual(run.manifest["status"], BUDGET_LIMITED_STATUS)
        self.assertFalse(run.manifest["completed_pilot"])
        self.assertFalse(run.manifest["next_stage_authorized"])


class _FiveXXRun:
    def __init__(self, specs, rows):
        self.plan = SimpleNamespace(requests=tuple(specs), checkpoints=())
        self.config = SimpleNamespace(protocol_version="v1.2")
        self._rows = list(rows)
        self.events = []

    def result_events(self, identity=None):
        if identity is None:
            return list(self._rows)
        return [row for row in self._rows if row.get("identity") == identity]

    def _append_delayed_event(self, kind, *, recorded_at, **fields):
        delay = fields["delay_seconds"]
        event = {
            "sequence": len(self.events),
            "previous_event_hash": (
                None if not self.events else self.events[-1]["event_hash"]
            ),
            "kind": kind,
            "recorded_at": recorded_at.astimezone(timezone.utc).isoformat(),
            "not_before": (
                recorded_at.astimezone(timezone.utc)
                + timedelta(seconds=delay)
            ).isoformat(),
            **fields,
        }
        event["event_hash"] = digest(event)
        self.events.append(event)
        return event


def _eligible_5xx(identity, *, attempt=0):
    request_id = f"req-{digest(identity)[:12]}"
    return {
        "kind": "result",
        "identity": identity,
        "attempt": attempt,
        "status": "ambiguous_timeout",
        "event_hash": digest([identity, attempt, "result"]),
        "request_hash": digest([identity, attempt, "request"]),
        "wire_payload_hash": digest([identity, attempt, "wire"]),
        "provider_result": {
            "transport_evidence": {
                "retry_eligible": True,
                "http_status": 500,
                "response_http_status": 500,
                "request_id_consistent": True,
                "response_received": True,
                "output_absent": True,
                "usage_absent": True,
                "provider_request_id": request_id,
                "retry_after_seconds": 5,
            }
        },
    }


class V12MasterAmbiguousFiveXXTests(unittest.TestCase):
    _STAGE_CASES = {
        GATE1: ("calibration", 1),
        GATE2: ("smoke", 2),
        GATE3_PHASE1: ("formation", 5),
        GATE3_PHASE2: ("remaining_arms", 9),
    }

    @classmethod
    def _executor(cls, release_stage, count, *, other_stage_count=0):
        plan_stage, cap = cls._STAGE_CASES[release_stage]
        specs = [
            SimpleNamespace(
                identity=f"{plan_stage}/eligible/{index}",
                stage=plan_stage,
                dependencies=(),
                parent_checkpoint=None,
            )
            for index in range(count)
        ]
        other_specs = [
            SimpleNamespace(
                identity=f"smoke/other/{index}",
                stage="smoke" if plan_stage != "smoke" else "formation",
                dependencies=(),
                parent_checkpoint=None,
            )
            for index in range(other_stage_count)
        ]
        all_specs = [*other_specs, *specs]
        rows = [_eligible_5xx(spec.identity) for spec in all_specs]
        run = _FiveXXRun(all_specs, rows)
        executor = object.__new__(V12MasterExecutor)
        executor.run = run
        executor.projection = {
            "stage_inventories": {
                stage: {"ambiguous_5xx_retry_slots": expected}
                for stage, (_mapped, expected) in cls._STAGE_CASES.items()
            }
        }
        executor.now = lambda: datetime(2100, 1, 1, tzinfo=timezone.utc)
        return executor, specs, cap

    def test_stage_caps_are_exactly_one_two_five_nine_and_do_not_borrow(self):
        for release_stage, (_plan_stage, cap) in self._STAGE_CASES.items():
            with self.subTest(release_stage=release_stage):
                executor, specs, _ = self._executor(
                    release_stage, cap, other_stage_count=16
                )
                state, delay = executor._v1_2_5xx_retry_state(
                    specs[-1], executor.run.result_events(specs[-1].identity)
                )
                self.assertEqual((state, delay), ("retry", 5))

                over, over_specs, _ = self._executor(
                    release_stage, cap + 1, other_stage_count=16
                )
                state, delay = over._v1_2_5xx_retry_state(
                    over_specs[-1],
                    over.run.result_events(over_specs[-1].identity),
                )
                self.assertEqual(
                    (state, delay),
                    ("paused_ambiguous_5xx_release_cap", None),
                )

    def test_schedule_event_records_stage_local_cap_and_index(self):
        for release_stage, (_plan_stage, cap) in self._STAGE_CASES.items():
            with self.subTest(release_stage=release_stage):
                executor, specs, _ = self._executor(
                    release_stage, cap, other_stage_count=16
                )
                target = specs[-1]
                event = executor._v1_2_5xx_schedule_event(
                    target, executor.run.result_events(target.identity)
                )
                self.assertEqual(event["release_affected_identity_cap"], cap)
                self.assertEqual(event["release_affected_identity_index"], cap)
                self.assertEqual(event["identity"], target.identity)

    def test_projection_cap_drift_is_rejected_before_retry(self):
        executor, specs, _ = self._executor(GATE1, 1)
        executor.projection["stage_inventories"][GATE1][
            "ambiguous_5xx_retry_slots"
        ] = 16
        with self.assertRaisesRegex(
            V12MasterControllerError, "stage cap differs"
        ):
            executor._v1_2_5xx_retry_state(
                specs[0], executor.run.result_events(specs[0].identity)
            )


class V12MasterAuthorizationResumeTests(unittest.TestCase):
    def test_receipt_state_controls_open_mode_across_run_path_toctou(self):
        authorization = {
            "master_run_directory": "runs/empirical/toctou-master",
        }
        proposal = {"proposal_hash": "p" * 64}
        receipt = {"receipt_hash": "r" * 64}
        config = SimpleNamespace()
        plan = SimpleNamespace()
        dispatch = SimpleNamespace()
        projection = {"projection_hash": "x" * 64}

        def common_patches(root, *, receipt_created, mutate_path, open_error):
            run_path = root / authorization["master_run_directory"]

            def build_projection():
                mutate_path(run_path)
                return projection

            def open_run(path, _config, _plan, **kwargs):
                self.assertEqual(Path(path), run_path)
                self.assertIs(kwargs["resume"], not receipt_created)
                if receipt_created:
                    self.assertTrue(run_path.exists())
                else:
                    self.assertFalse(run_path.exists())
                raise open_error

            return (
                patch.object(controller, "ROOT", root),
                patch.object(
                    controller,
                    "load_v1_2_master_proposal",
                    return_value=proposal,
                ),
                patch.object(
                    controller,
                    "load_human_authorization",
                    return_value=authorization,
                ),
                patch.object(
                    controller,
                    "claim_human_authorization_once_with_state",
                    return_value=(receipt, receipt_created),
                ),
                patch.object(
                    controller,
                    "build_unified_projection",
                    side_effect=build_projection,
                ),
                patch.object(
                    controller, "_final_proposal", side_effect=lambda value: value
                ),
                patch.object(
                    controller,
                    "load_or_validate_authorization",
                    side_effect=lambda value, _proposal: value,
                ),
                patch.object(
                    controller,
                    "_receipt_matches",
                    side_effect=lambda value, _proposal, _authorization: value,
                ),
                patch.object(
                    controller,
                    "_master_config_plan_dispatch",
                    return_value=(config, plan, dispatch, projection),
                ),
                patch.object(
                    controller.DurableRun, "open", side_effect=open_run
                ),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()

            def appear(path):
                path.mkdir(parents=True)

            patches = common_patches(
                root,
                receipt_created=True,
                mutate_path=appear,
                open_error=FileExistsError("simulated atomic-create collision"),
            )
            with ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                stack.enter_context(no_network_or_credentials())
                with self.assertRaisesRegex(
                    FileExistsError, "atomic-create collision"
                ):
                    controller.run_v1_2_master(
                        Path("/offline/fresh-authorization.json")
                    )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run_path = root / authorization["master_run_directory"]
            run_path.mkdir(parents=True)

            def disappear(path):
                path.rmdir()

            patches = common_patches(
                root,
                receipt_created=False,
                mutate_path=disappear,
                open_error=ResumeError("simulated missing resume directory"),
            )
            with ExitStack() as stack:
                for item in patches:
                    stack.enter_context(item)
                stack.enter_context(no_network_or_credentials())
                with self.assertRaisesRegex(
                    ResumeError, "missing resume directory"
                ):
                    controller.run_v1_2_master(
                        Path("/offline/consumed-authorization.json")
                    )

    def test_existing_exact_receipt_resumes_after_fifteen_minute_window(self):
        with tempfile.TemporaryDirectory() as directory, _implemented_release():
            trusted_home = Path(directory) / "home"
            trusted_home.mkdir(mode=0o700)
            receipt_dir = (
                trusted_home
                / ".collusion-recovery-authorization-receipts"
                / "v1.2-unified"
            )
            proposal, authorization = _final_proposal_and_authorization()
            clock = [TEST_NOW]

            def fake_utc_now(value=None):
                return (
                    clock[0]
                    if value is None
                    else value.astimezone(timezone.utc)
                )

            with patch.object(
                master_release, "AUTHORIZATION_RECEIPT_DIRECTORY", receipt_dir
            ), patch.object(
                master_release, "_utc_now", side_effect=fake_utc_now
            ):
                first = claim_human_authorization_once(
                    authorization, proposal
                )
                clock[0] = TEST_NOW + timedelta(minutes=16)
                resumed = claim_human_authorization_once(
                    authorization, proposal
                )
                self.assertEqual(resumed, first)

                other_uuid = str(uuid.uuid4())
                stale = complete_human_authorization({
                    "authorization_uuid": other_uuid,
                    "authorization_id": f"v1-2-unified-human-{other_uuid}",
                    "authorization_nonce": "fedcba9876543210" * 4,
                    "confirmed_by": "offline master-controller test",
                    "confirmed_at": TEST_NOW.isoformat(),
                }, proposal)
                other_receipt_dir = trusted_home / "other" / "v1.2-unified"
                with patch.object(
                    master_release,
                    "AUTHORIZATION_RECEIPT_DIRECTORY",
                    other_receipt_dir,
                ):
                    # The registry validator deliberately fixes its exact path;
                    # use a fresh trusted tree but keep the same authorized run.
                    with self.assertRaisesRegex(
                        V12MasterReleaseError, "expired"
                    ):
                        claim_human_authorization_once(
                            stale, proposal
                        )

    def test_terminal_resume_returns_before_sdk_or_key_access(self):
        terminal_run = SimpleNamespace(manifest={"status": BUDGET_LIMITED_STATUS})
        sentinel = {
            "status": BUDGET_LIMITED_STATUS,
            "credential_accessed": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_relative = "runs/empirical/offline"
            (root / run_relative).mkdir(parents=True)
            authorization = {"master_run_directory": run_relative}
            with patch.object(
                controller, "ROOT", root
            ), patch.object(
                controller,
                "load_v1_2_master_proposal",
                return_value={"proposal_hash": "p"},
            ), patch.object(
                controller, "load_human_authorization", return_value=authorization
            ), patch.object(
                controller,
                "claim_human_authorization_once_with_state",
                return_value=({"receipt_hash": "r"}, False),
            ), patch.object(
                controller, "build_unified_projection", return_value={}
            ), patch.object(
                controller,
                "prepare_v1_2_master_run",
                return_value=(terminal_run, None, None, None, {}),
            ), patch.object(
                controller, "_verified_terminal_summary", return_value=sentinel
            ) as verified, patch.object(
                controller.importlib_metadata,
                "version",
                side_effect=AssertionError("SDK inspection must not occur"),
            ), no_network_or_credentials():
                result = controller.run_v1_2_master(
                    Path("/offline/authorization.json")
                )
        self.assertEqual(result, sentinel)
        verified.assert_called_once()

    def test_fresh_live_terminal_is_deep_verified_before_return(self):
        executor = object.__new__(V12MasterExecutor)
        executor.fake_transport_test = False
        executor.executor_provenance = controller.EMPIRICAL_EXECUTOR_PROVENANCE
        executor.proposal = {"proposal_hash": "p" * 64}
        executor.authorization_receipt = {"receipt_hash": "r" * 64}
        executor.projection = {"projection_hash": "x" * 64}
        executor.run = SimpleNamespace(
            manifest={"status": controller.FINAL_STATUS}
        )
        executor.execute = lambda: {"status": "unverified-terminal-summary"}
        verified_summary = {"status": controller.FINAL_STATUS, "verified": True}
        with patch.object(
            controller,
            "_verified_terminal_summary",
            return_value=verified_summary,
        ) as verified:
            result = controller._execute_live_controller_and_verify_terminal(
                executor
            )
        self.assertEqual(result, verified_summary)
        verified.assert_called_once_with(
            executor.run,
            executor.proposal,
            executor.authorization_receipt,
            executor.projection,
        )

        with patch.object(
            controller,
            "_verified_terminal_summary",
            side_effect=V12MasterControllerError("deep replay failed"),
        ), self.assertRaisesRegex(V12MasterControllerError, "deep replay failed"):
            controller._execute_live_controller_and_verify_terminal(executor)

        executor.run.manifest["status"] = "paused_authentication_error"
        paused = {"status": "paused_authentication_error"}
        executor.execute = lambda: paused
        with patch.object(
            controller,
            "_verified_terminal_summary",
            side_effect=AssertionError("nonterminal pauses are not terminal"),
        ):
            self.assertIs(
                controller._execute_live_controller_and_verify_terminal(executor),
                paused,
            )

    def test_receipt_creation_state_cannot_attach_or_recreate_run(self):
        authorization = {"master_run_directory": "runs/empirical/offline"}
        common = (
            patch.object(
                controller,
                "load_v1_2_master_proposal",
                return_value={"proposal_hash": "p"},
            ),
            patch.object(
                controller, "load_human_authorization", return_value=authorization
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / authorization["master_run_directory"]
            run_path.mkdir(parents=True)
            with common[0], common[1], patch.object(
                controller, "ROOT", root
            ), patch.object(
                controller,
                "claim_human_authorization_once_with_state",
                return_value=({"receipt_hash": "r"}, True),
            ), patch.object(
                controller,
                "build_unified_projection",
                side_effect=AssertionError("must fail before projection"),
            ), no_network_or_credentials(), self.assertRaisesRegex(
                V12MasterControllerError, "freshly claimed.*preexisting run"
            ):
                controller.run_v1_2_master(Path("/offline/auth.json"))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                controller, "ROOT", root
            ), patch.object(
                controller,
                "load_v1_2_master_proposal",
                return_value={"proposal_hash": "p"},
            ), patch.object(
                controller, "load_human_authorization", return_value=authorization
            ), patch.object(
                controller,
                "claim_human_authorization_once_with_state",
                return_value=({"receipt_hash": "r"}, False),
            ), patch.object(
                controller,
                "build_unified_projection",
                side_effect=AssertionError("must fail before projection"),
            ), no_network_or_credentials(), self.assertRaisesRegex(
                V12MasterControllerError, "consumed authorization.*missing"
            ):
                controller.run_v1_2_master(Path("/offline/auth.json"))


class V12MasterProvenanceTests(unittest.TestCase):
    def test_public_fake_factory_builds_only_finite_data_transport(self):
        transport_metadata = {
            "provider": "openai",
            "model": "gpt-6.1-sol",
            "service_tier": "default",
        }
        release = {
            "max_dispatch_attempts": 2,
            "max_total_input_tokens": 2,
            "max_total_output_tokens": 2,
            "max_cost_usd": "1",
            "max_in_flight": 1,
            "max_input_tokens_per_request": 1,
            "max_output_tokens_per_request": 1,
        }

        class FactoryRun:
            def __init__(self):
                self.config = SimpleNamespace(
                    source="empirical-live",
                    protocol_version="v1.2",
                    run_id=V1_2_UNIFIED_PILOT_RUN_ID,
                    authorization=SimpleNamespace(
                        approved_budget_usd=Decimal("250")
                    ),
                    limits=None,
                    pricing=None,
                    blocks=5,
                    permits_stage=lambda _stage: True,
                )
                self.plan = SimpleNamespace()
                self.events = [{"kind": controller.MASTER_CLAIM_EVENT}]
                self._manifest = {
                    "status": "prepared_v1_2_master",
                    "execution_controller": master_release.MASTER_CONTROLLER_ID,
                    "authorized_transport_hash": digest(transport_metadata),
                    "authorized_budget_usd": "250",
                    "authorized_stage": "probes",
                    "authorized_max_block": 4,
                    "authorized_release_limits": release,
                }

            @property
            def manifest(self):
                return json.loads(canonical(self._manifest))

            @contextmanager
            def execution_lock(self):
                yield

            def append(self, kind, **fields):
                event = {
                    "sequence": len(self.events),
                    "previous_event_hash": None,
                    "kind": kind,
                    "recorded_at": TEST_NOW.isoformat(),
                    **fields,
                }
                event["event_hash"] = digest(event)
                self.events.append(event)
                return event

            def set_status(self, status, **fields):
                self._manifest.update(status=status, **fields)

            @staticmethod
            def budget_records():
                return []

        proposal = {
            "proposal_hash": "p" * 64,
            "transport": transport_metadata,
        }
        response = TransportResponse.from_mapping(
            {"id": "offline-scripted-response"}, "req-offline"
        )
        run = FactoryRun()
        with patch.object(
            controller, "_final_proposal", side_effect=lambda value: value
        ), patch.object(
            controller,
            "validate_unified_projection",
            side_effect=lambda value: value,
        ), patch(
            "collusion_recovery.staged_requests.ScientificRequestFactory.__init__",
            return_value=None,
        ), patch(
            "collusion_recovery.orchestration.BudgetLedger.replay",
            return_value=SimpleNamespace(active={}),
        ), patch.object(
            V12MasterExecutor, "_validate_release", return_value=None
        ), no_network_or_credentials():
            executor = controller.build_fake_v1_2_master_executor_for_test(
                run,
                [response],
                proposal,
                {"projection_hash": "x" * 64},
                authorization_receipt={"receipt_hash": "r" * 64},
                now=lambda: TEST_NOW,
            )
            self.assertIs(type(executor), V12MasterExecutor)
            self.assertEqual(
                type(executor.provider.transport).__qualname__,
                "_OfflineScriptedTransport",
            )
            self.assertEqual(executor.provider.transport.remaining, 1)
            delivered = executor.provider.transport.send({"input": "data only"})
            self.assertEqual(delivered.body, {"id": "offline-scripted-response"})
            self.assertEqual(executor.provider.transport.remaining, 0)
            with self.assertRaisesRegex(
                V12MasterControllerError, "script is exhausted"
            ):
                executor.provider.transport.send({"input": "no network fallback"})

        second_run = FactoryRun()
        with patch.object(
            controller, "_final_proposal", side_effect=lambda value: value
        ), patch.object(
            controller,
            "validate_unified_projection",
            side_effect=lambda value: value,
        ), no_network_or_credentials(), self.assertRaisesRegex(
            V12MasterControllerError, "finite response/failure sequence"
        ):
            controller.build_fake_v1_2_master_executor_for_test(
                second_run,
                [object()],
                proposal,
                {"projection_hash": "x" * 64},
                authorization_receipt={"receipt_hash": "r" * 64},
            )
        self.assertFalse(any(
            event.get("kind") == controller.MASTER_MODE_EVENT
            for event in second_run.events
        ))

        class HostileList(list):
            iterated = False

            def __iter__(self):
                type(self).iterated = True
                raise AssertionError("hostile outcome container was iterated")

        hostile = HostileList([response])
        third_run = FactoryRun()
        with patch.object(
            controller, "_final_proposal", side_effect=lambda value: value
        ), patch.object(
            controller,
            "validate_unified_projection",
            side_effect=lambda value: value,
        ), no_network_or_credentials(), self.assertRaisesRegex(
            V12MasterControllerError, "finite response/failure sequence"
        ):
            controller.build_fake_v1_2_master_executor_for_test(
                third_run,
                hostile,
                proposal,
                {"projection_hash": "x" * 64},
                authorization_receipt={"receipt_hash": "r" * 64},
            )
        self.assertFalse(HostileList.iterated)
        self.assertFalse(any(
            event.get("kind") == controller.MASTER_MODE_EVENT
            for event in third_run.events
        ))

    def test_entry_points_expose_no_provider_executor_or_clock_injection(self):
        live = inspect.signature(controller.run_v1_2_master)
        for forbidden in (
            "provider", "transport", "client", "executor", "now",
            "workspace_root",
        ):
            self.assertNotIn(forbidden, live.parameters)
        offline = inspect.signature(
            controller.build_fake_v1_2_master_executor_for_test
        )
        self.assertIn("scripted_outcomes", offline.parameters)
        for forbidden in ("provider", "transport", "client", "executor"):
            self.assertNotIn(forbidden, offline.parameters)

    def test_public_constructor_cannot_acquire_execution_authority(self):
        with self.assertRaisesRegex(
            V12MasterControllerError, "sealed live or data-only offline"
        ):
            V12MasterExecutor(
                None, None, {}, {}, authorization_receipt={}
            )

    def test_generic_executor_and_object_new_forgery_are_rejected(self):
        run = SimpleNamespace(
            config=SimpleNamespace(
                source="empirical-live",
                protocol_version="v1.2",
                run_id=V1_2_UNIFIED_PILOT_RUN_ID,
            ),
            events=[],
            manifest={"execution_controller": master_release.MASTER_CONTROLLER_ID},
        )
        with self.assertRaisesRegex(
            AuthorizationRequiredError, "dedicated master.*wrapper"
        ):
            StagedExecutor(
                run,
                None,
                None,
                dedicated_controller=master_release.MASTER_CONTROLLER_ID,
            )

        run.events = [{
            "kind": controller.MASTER_MODE_EVENT,
            "mode": controller.EMPIRICAL_EXECUTION_MODE,
            "executor_provenance": controller.EMPIRICAL_EXECUTOR_PROVENANCE,
        }]
        forged = object.__new__(V12MasterExecutor)
        with self.assertRaisesRegex(
            AuthorizationRequiredError, "dedicated master.*wrapper"
        ):
            StagedExecutor.__init__(
                forged,
                run,
                None,
                None,
                dedicated_controller=master_release.MASTER_CONTROLLER_ID,
            )

    def test_execution_mode_cannot_change_across_resume(self):
        class ModeRun:
            def __init__(self, mode, provenance):
                self.events = [{
                    "kind": controller.MASTER_CLAIM_EVENT,
                }, {
                    "kind": controller.MASTER_MODE_EVENT,
                    "controller": master_release.MASTER_CONTROLLER_ID,
                    "proposal_hash": "p",
                    "run_id": master_release.MASTER_RUN_ID,
                    "mode": mode,
                    "executor_provenance": provenance,
                }]

            def append(self, *_args, **_kwargs):
                raise AssertionError("mode must not be rebound")

        fake = ModeRun(
            controller.FAKE_EXECUTION_MODE,
            controller.FAKE_EXECUTOR_PROVENANCE,
        )
        with self.assertRaisesRegex(ResumeError, "mode changed"):
            controller._mode_binding(
                fake,
                {"proposal_hash": "p"},
                mode=controller.EMPIRICAL_EXECUTION_MODE,
                provenance=controller.EMPIRICAL_EXECUTOR_PROVENANCE,
            )

        live = ModeRun(
            controller.EMPIRICAL_EXECUTION_MODE,
            controller.EMPIRICAL_EXECUTOR_PROVENANCE,
        )
        with self.assertRaisesRegex(ResumeError, "mode changed"):
            controller._mode_binding(
                live,
                {"proposal_hash": "p"},
                mode=controller.FAKE_EXECUTION_MODE,
                provenance=controller.FAKE_EXECUTOR_PROVENANCE,
            )

    def test_fake_terminal_cannot_pass_empirical_terminal_verifier(self):
        run = SimpleNamespace(
            events=[{
                "kind": controller.MASTER_MODE_EVENT,
                "mode": controller.FAKE_EXECUTION_MODE,
                "executor_provenance": controller.FAKE_EXECUTOR_PROVENANCE,
            }],
            manifest={
                "status": FAKE_FINAL_STATUS,
                "execution_mode": controller.FAKE_EXECUTION_MODE,
                "empirical_evidence": False,
            },
        )
        with patch.object(
            controller,
            "_MasterBudgetLedger",
            side_effect=AssertionError(
                "a fake terminal must fail before empirical replay"
            ),
        ), self.assertRaisesRegex(
            V12MasterControllerError, "not a terminal empirical release"
        ):
            controller._verified_terminal_summary(
                run,
                {"proposal_hash": "p"},
                {"receipt_hash": "r"},
                {},
            )

    def test_raw_verdict_cannot_hide_behind_forged_parsed_output(self):
        raw = {
            "id": "resp_invalid_verdict",
            "object": "response",
            "status": "completed",
            "error": None,
            "model": "gpt-6.1-sol",
            "service_tier": "default",
            "output": [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "output_text",
                    "text": "This is not a structured verdict.",
                }],
            }],
            "usage": None,
        }
        forged = {
            "status": "ok",
            "raw_response": raw,
            "output_text": "This is not a structured verdict.",
            "parsed_output": {
                "verdict": "ACCEPT",
                "evidence_record_ids": [],
                "brief_reason": "forged",
            },
            "refusal": None,
            "error": None,
            "provider_request_id": "resp_invalid_verdict",
            "returned_model": "gpt-6.1-sol",
            "model_metadata": {
                "model": "gpt-6.1-sol",
                "service_tier": "default",
            },
            "usage": None,
            "timing": {
                "started_at": "2026-10-25T20:00:00-07:00",
                "ended_at": "2026-10-25T20:00:01-07:00",
                "elapsed_seconds": 1,
            },
            "ambiguous": False,
            "known_not_completed": False,
            "transport_evidence": {},
        }
        request = {
            "response_mode": "verdict",
            "message_max_codepoints": 73,
            "message_retry_history_max_utf8_bytes": 701,
        }
        run = SimpleNamespace(request_payload=lambda _request_hash: request)
        event = {"request_hash": "request-hash", "provider_result": forged}

        # The generic verifier's omitted-mode compatibility branch would trust
        # this forged derivative.  The master verifier must recover the mode and
        # both limits from the immutable saved request.
        self.assertTrue(controller.provider_result_matches_raw_response(forged))
        self.assertFalse(controller._strict_provider_result_matches_event(run, event))
        with patch.object(
            controller,
            "provider_result_matches_raw_response",
            return_value=False,
        ) as verifier:
            self.assertFalse(
                controller._strict_provider_result_matches_event(run, event)
            )
        verifier.assert_called_once_with(
            forged,
            response_mode="verdict",
            message_max_codepoints=73,
            message_retry_history_max_utf8_bytes=701,
        )


class V12MasterTerminalIntegrityTests(unittest.TestCase):
    @staticmethod
    def _lineage_fixture():
        dispatch_value = {"authorization": "bound"}
        expected_binding = {"binding_value": "exact"}
        proposal = {
            "proposal_hash": "p" * 64,
            "transport": {"provider": "openai"},
            "transport_hash": "t" * 64,
            "projected_stage_caps": {"stage_caps": {"gate1": {}}},
            "projected_stage_caps_hash": "s" * 64,
        }
        receipt = {
            "authorization_hash": "a" * 64,
            "receipt_hash": "r" * 64,
            "claimed_at": TEST_NOW.isoformat(),
        }
        binding = {
            "sequence": 0,
            "previous_event_hash": None,
            "kind": controller.MASTER_BINDING_EVENT,
            "recorded_at": TEST_NOW.isoformat(),
            "event_hash": "b" * 64,
            **expected_binding,
        }
        claim = {
            "sequence": 1,
            "previous_event_hash": binding["event_hash"],
            "kind": controller.MASTER_CLAIM_EVENT,
            "recorded_at": TEST_NOW.isoformat(),
            "event_hash": "c" * 64,
            "controller": master_release.MASTER_CONTROLLER_ID,
            "run_id": master_release.MASTER_RUN_ID,
            "proposal_hash": proposal["proposal_hash"],
            "authorization_hash": receipt["authorization_hash"],
            "receipt_hash": receipt["receipt_hash"],
            "receipt_claimed_at": receipt["claimed_at"],
            "binding_event_hash": binding["event_hash"],
        }
        mode = {
            "sequence": 2,
            "previous_event_hash": claim["event_hash"],
            "kind": controller.MASTER_MODE_EVENT,
            "recorded_at": TEST_NOW.isoformat(),
            "event_hash": "m" * 64,
            "controller": master_release.MASTER_CONTROLLER_ID,
            "proposal_hash": proposal["proposal_hash"],
            "run_id": master_release.MASTER_RUN_ID,
            "mode": controller.EMPIRICAL_EXECUTION_MODE,
            "executor_provenance": controller.EMPIRICAL_EXECUTOR_PROVENANCE,
        }
        manifest = {
            "initial_dispatch_authorization": dispatch_value,
            "dispatch_authorization": dispatch_value,
            "dispatch_authorization_hash": digest(dispatch_value),
            "master_proposal_hash": proposal["proposal_hash"],
            "master_authorization_hash": receipt["authorization_hash"],
            "master_authorization_receipt_hash": receipt["receipt_hash"],
            "master_authorization_claim_event_hash": claim["event_hash"],
            "authorized_stage_caps": proposal["projected_stage_caps"][
                "stage_caps"
            ],
            "authorized_stage_caps_hash": proposal["projected_stage_caps_hash"],
            "master_projection_hash": "x" * 64,
            "execution_mode": controller.EMPIRICAL_EXECUTION_MODE,
            "executor_provenance": controller.EMPIRICAL_EXECUTOR_PROVENANCE,
            "empirical_evidence": True,
            "transport": proposal["transport"],
            "transport_hash": proposal["transport_hash"],
        }
        run = SimpleNamespace(
            events=[binding, claim, mode],
            manifest=manifest,
            config=SimpleNamespace(),
            plan=SimpleNamespace(),
        )
        dispatch = SimpleNamespace(to_dict=lambda: dispatch_value)
        return run, proposal, receipt, expected_binding, dispatch

    def test_binding_claim_and_mode_are_an_exact_ordered_prefix(self):
        run, proposal, receipt, expected_binding, dispatch = (
            self._lineage_fixture()
        )
        with patch.object(
            controller.DispatchAuthorizationArtifact,
            "from_mapping",
            return_value=dispatch,
        ), patch.object(
            controller, "_binding_value", return_value=expected_binding
        ):
            controller._verify_master_lineage(
                run, proposal, receipt, {"projection_hash": "x" * 64}
            )

        def mutate_missing_mode(candidate):
            candidate.events.pop()

        def mutate_claim_sequence(candidate):
            candidate.events[1]["sequence"] = 0

        def mutate_mode_chain(candidate):
            candidate.events[2]["previous_event_hash"] = "wrong"

        def mutate_extra_binding_field(candidate):
            candidate.events[0]["unexpected"] = True

        def mutate_claim_field(candidate):
            candidate.events[1]["receipt_hash"] = "forged"

        def mutate_manifest_claim_hash(candidate):
            candidate.manifest["master_authorization_claim_event_hash"] = "forged"

        cases = {
            "missing mode": mutate_missing_mode,
            "claim not sequence one": mutate_claim_sequence,
            "mode not chained after claim": mutate_mode_chain,
            "binding field set is not exact": mutate_extra_binding_field,
            "claim field mismatch": mutate_claim_field,
            "manifest claim hash mismatch": mutate_manifest_claim_hash,
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                candidate, _, _, _, _ = self._lineage_fixture()
                mutate(candidate)
                with patch.object(
                    controller.DispatchAuthorizationArtifact,
                    "from_mapping",
                    return_value=dispatch,
                ), patch.object(
                    controller, "_binding_value", return_value=expected_binding
                ), self.assertRaises(V12MasterControllerError):
                    controller._verify_master_lineage(
                        candidate,
                        proposal,
                        receipt,
                        {"projection_hash": "x" * 64},
                    )

    def test_terminal_journal_rejects_hash_chained_unknown_event_kind(self):
        run, proposal, receipt, _, _ = self._lineage_fixture()
        run.manifest["status"] = controller.FINAL_STATUS
        unknown = {
            "sequence": 3,
            "previous_event_hash": run.events[-1]["event_hash"],
            "kind": "gate4_release",
            "recorded_at": TEST_NOW.isoformat(),
            "authorized": True,
        }
        unknown["event_hash"] = digest(unknown)
        run.events.append(unknown)

        with patch.object(
            controller, "_final_proposal", side_effect=lambda value: value
        ), patch.object(
            controller,
            "validate_unified_projection",
            side_effect=lambda value: value,
        ), patch.object(
            controller,
            "_validate_receipt_evidence",
            side_effect=lambda value, _proposal: value,
        ), patch.object(
            controller,
            "_verify_master_lineage",
            side_effect=AssertionError(
                "unknown event kind must fail before lineage replay"
            ),
        ), self.assertRaisesRegex(
            V12MasterControllerError, "forbidden event kind"
        ):
            controller.verify_v1_2_master_empirical_integrity(
                run, proposal, receipt, {"projection_hash": "x" * 64}
            )

    def test_failed_gate_terminals_reject_any_later_stage_dispatch(self):
        requests = tuple(
            SimpleNamespace(identity=f"{stage}/0", stage=stage)
            for stage in _PLAN_STAGE_COUNTS
        )
        cases = (
            (CALIBRATION_FAILED_STATUS, "smoke/0"),
            (CALIBRATION_FAILED_STATUS, "formation/0"),
            (SMOKE_FAILED_STATUS, "formation/0"),
            (FORMATION_FAILED_STATUS, "remaining_arms/0"),
            (FORMATION_FAILED_STATUS, "probes/0"),
        )
        for status, later_identity in cases:
            with self.subTest(status=status, later_identity=later_identity):
                run = SimpleNamespace(
                    manifest={"status": status},
                    plan=SimpleNamespace(requests=requests),
                    events=[],
                )
                attempt = {"identity": later_identity}
                attempt_inventory = {
                    "reservations": [attempt],
                    "results": [attempt],
                    "final_rows": {},
                }
                with patch.object(
                    controller, "_final_proposal", side_effect=lambda value: value
                ), patch.object(
                    controller,
                    "validate_unified_projection",
                    side_effect=lambda value: value,
                ), patch.object(
                    controller,
                    "_validate_receipt_evidence",
                    side_effect=lambda value, _proposal: value,
                ), patch.object(
                    controller, "_verify_master_lineage", return_value=None
                ), patch.object(
                    controller,
                    "_verify_master_attempts",
                    return_value=attempt_inventory,
                ), patch.object(
                    controller, "_verify_terminal_manifest", return_value=None
                ), patch.object(
                    controller,
                    "_verify_master_checkpoints_and_artifacts",
                    side_effect=AssertionError(
                        "later-stage dispatch must fail before artifact trust"
                    ),
                ), self.assertRaisesRegex(
                    V12MasterControllerError, "dispatch after its stage boundary"
                ):
                    controller.verify_v1_2_master_empirical_integrity(
                        run, {}, {}, {}
                    )

    def test_failed_gate_terminals_require_the_complete_decision_prefix(self):
        requests = tuple(
            SimpleNamespace(identity=f"{stage}/{index}", stage=stage)
            for stage, count in _PLAN_STAGE_COUNTS.items()
            for index in range(count)
        )
        stage_prefix = {
            CALIBRATION_FAILED_STATUS: ("calibration",),
            SMOKE_FAILED_STATUS: ("calibration", "smoke"),
            FORMATION_FAILED_STATUS: ("calibration", "smoke", "formation"),
        }
        for status, stages in stage_prefix.items():
            with self.subTest(status=status):
                selected = [spec for spec in requests if spec.stage in stages]
                # Preserve one valid-looking observation while withholding the
                # rest of the exact decision prefix.
                final_rows = {
                    selected[0].identity: {
                        "identity": selected[0].identity,
                        "status": "ok",
                    }
                }
                attempts = {
                    "reservations": [],
                    "results": [],
                    "final_rows": final_rows,
                }
                run = SimpleNamespace(
                    manifest={"status": status},
                    plan=SimpleNamespace(requests=requests),
                    events=[],
                )
                with patch.object(
                    controller, "_final_proposal", side_effect=lambda value: value
                ), patch.object(
                    controller,
                    "validate_unified_projection",
                    side_effect=lambda value: value,
                ), patch.object(
                    controller,
                    "_validate_receipt_evidence",
                    side_effect=lambda value, _proposal: value,
                ), patch.object(
                    controller, "_verify_master_lineage", return_value=None
                ), patch.object(
                    controller, "_verify_master_attempts", return_value=attempts
                ), patch.object(
                    controller, "_verify_terminal_manifest", return_value=None
                ), patch.object(
                    controller,
                    "_verify_master_retry_policy",
                    side_effect=AssertionError(
                        "an incomplete decision prefix must fail before retry audit"
                    ),
                ), self.assertRaisesRegex(
                    V12MasterControllerError,
                    "decision prefix|lacks.*observations|incomplete",
                ):
                    controller.verify_v1_2_master_empirical_integrity(
                        run, {}, {}, {}
                    )

    def test_formation_gate_must_precede_every_phase2_dispatch(self):
        stages = ("calibration", "smoke", "formation", "remaining_arms")
        requests = tuple(
            SimpleNamespace(identity=f"{stage}/0", stage=stage)
            for stage in stages
        )
        reservation_sequences = (3, 6, 9, 11)
        result_sequences = (4, 7, 10, 13)
        reservations = [
            {"kind": "reservation", "identity": spec.identity,
             "sequence": sequence}
            for spec, sequence in zip(requests, reservation_sequences)
        ]
        results = [
            {"kind": "result", "identity": spec.identity,
             "sequence": sequence, "status": "ok"}
            for spec, sequence in zip(requests, result_sequences)
        ]
        final_rows = {
            row["identity"]: row for row in results
        }
        calibration = {"assessment_type": "calibration", "passed": True}
        formation = {"assessment_type": "formation", "passed": True}
        gates = {
            "calibration": {
                **self._gate_event(
                    "calibration",
                    calibration,
                    passed=True,
                    note="Frozen automatic protocol-v1.2 calibration decision.",
                ),
                "sequence": 5,
            },
            controller.V1_2_SMOKE_INTEGRITY_GATE: {
                "kind": "gate",
                "gate": controller.V1_2_SMOKE_INTEGRITY_GATE,
                "passed": True,
                "sequence": 8,
            },
            "formation": {
                **self._gate_event(
                    "formation",
                    formation,
                    passed=True,
                    note="Frozen automatic protocol-v1.2 five-block 3-of-5 decision.",
                ),
                # The phase-2 reservation above has sequence 11, so this is a
                # forged release decision written after phase 2 began.
                "sequence": 12,
            },
        }
        run = SimpleNamespace(
            manifest={"status": controller.FINAL_STATUS},
            plan=SimpleNamespace(requests=requests),
            events=[*reservations, *results, *gates.values()],
            gate_events=lambda: gates,
            _gate_prerequisites=lambda _gate: (),
        )
        attempts = {
            "reservations": reservations,
            "results": results,
            "final_rows": final_rows,
        }
        audit = {
            "automatic_checks_passed": True,
            "later_stage_release_permitted": True,
        }
        with patch.object(
            controller, "_final_proposal", side_effect=lambda value: value
        ), patch.object(
            controller,
            "validate_unified_projection",
            side_effect=lambda value: value,
        ), patch.object(
            controller,
            "_validate_receipt_evidence",
            side_effect=lambda value, _proposal: value,
        ), patch.object(
            controller, "_verify_master_lineage", return_value=None
        ), patch.object(
            controller, "_verify_master_attempts", return_value=attempts
        ), patch.object(
            controller, "_verify_terminal_manifest", return_value=None
        ), patch.object(
            controller, "_verify_master_retry_policy", return_value=None
        ), patch.object(
            controller,
            "_verify_master_checkpoints_and_artifacts",
            return_value=None,
        ), patch.object(
            controller, "calibration_assessment", return_value=calibration
        ), patch.object(
            controller, "formation_assessment", return_value=formation
        ), patch.object(
            controller, "_smoke_prefix_run", return_value=object()
        ), patch.object(
            controller, "audit_v1_2_smoke_integrity", return_value=audit
        ), self.assertRaisesRegex(
            V12MasterControllerError, "formation gate|phase.?2|release"
        ):
            controller.verify_v1_2_master_empirical_integrity(
                run, {}, {}, {}
            )

    @staticmethod
    def _attempt_fixture(provider, *, provider_identity=None,
                         request_payload=None, wire_payload=None):
        expected_request = {
            "request_identity": "calibration/0",
            "response_mode": "verdict",
        }
        expected_wire = {"model": "gpt-6.1-sol", "input": []}
        request_hash = digest(expected_request)
        wire_hash = digest(expected_wire)
        reservation_id = digest({"identity": "calibration/0", "attempt": 0})
        reservation = {
            "kind": "reservation",
            "sequence": 3,
            "previous_event_hash": "mode-event-hash",
            "recorded_at": TEST_NOW.isoformat(),
            "identity": "calibration/0",
            "attempt": 0,
            "request_hash": request_hash,
            "wire_payload_hash": wire_hash,
            "reservation": {
                "reservation_id": reservation_id,
                "identity": "calibration/0",
                "attempt": 0,
                "input_tokens": 10,
                "output_tokens": 5,
                "cost_usd": "0.01",
            },
            "event_hash": "reservation-event-hash",
        }
        result = {
            "kind": "result",
            "sequence": 4,
            "previous_event_hash": reservation["event_hash"],
            "recorded_at": TEST_NOW.isoformat(),
            "identity": "calibration/0",
            "attempt": 0,
            "reservation_id": reservation_id,
            "request_hash": request_hash,
            "wire_payload_hash": wire_hash,
            "status": provider["status"],
            "ambiguous": provider["ambiguous"],
            "known_not_completed": provider["known_not_completed"],
            "usage": provider["usage"],
            "budget_accounting": {},
            "provider_identity": provider_identity,
            "provider_result": provider,
            "event_hash": "result-event-hash",
        }
        spec = SimpleNamespace(identity="calibration/0")
        run = SimpleNamespace(
            events=[reservation, result],
            plan=SimpleNamespace(requests=(spec,)),
            config=SimpleNamespace(),
            request_payload=lambda _hash: (
                expected_request if request_payload is None else request_payload
            ),
            wire_payload=lambda _hash: (
                expected_wire if wire_payload is None else wire_payload
            ),
        )

        class Factory:
            @staticmethod
            def __call__(_spec, _context):
                return SimpleNamespace(
                    to_dict=lambda: expected_request,
                    conservative_input_token_bound=10,
                    max_output_tokens=5,
                )

        return run, Factory(), expected_wire

    @staticmethod
    def _transport_provider(status):
        return {
            "status": status,
            "raw_response": None,
            "output_text": None,
            "parsed_output": None,
            "refusal": None,
            "error": "safe offline transport failure",
            "provider_request_id": None,
            "returned_model": None,
            "model_metadata": {},
            "usage": None,
            "timing": {
                "started_at": "2026-10-25T20:00:00-07:00",
                "ended_at": "2026-10-25T20:00:01-07:00",
                "elapsed_seconds": 1,
            },
            "ambiguous": status == "ambiguous_timeout",
            "known_not_completed": status == "retryable_error",
            "transport_evidence": {},
        }

    def _verify_attempt_fixture(self, run, factory, expected_wire,
                                *, required_identity=None):
        with patch.object(
            controller, "ScientificRequestFactory", return_value=factory
        ), patch.object(
            controller.OpenAIResponsesProvider,
            "build_payload",
            return_value=expected_wire,
        ):
            return controller._verify_master_attempts(
                run,
                {"required_provider_identity": required_identity},
            )

    def test_request_and_wire_payload_tampering_is_rejected(self):
        provider = self._transport_provider("retryable_error")
        run, factory, expected_wire = self._attempt_fixture(provider)
        verified = self._verify_attempt_fixture(run, factory, expected_wire)
        self.assertEqual(len(verified["results"]), 1)

        tampered = (
            ("request", {"request_identity": "forged"}, None),
            ("wire", None, {"model": "forged"}),
        )
        for name, request_payload, wire_payload in tampered:
            with self.subTest(name=name):
                candidate, factory, expected_wire = self._attempt_fixture(
                    provider,
                    request_payload=request_payload,
                    wire_payload=wire_payload,
                )
                with self.assertRaisesRegex(
                    V12MasterControllerError, "request or wire payload differs"
                ):
                    self._verify_attempt_fixture(
                        candidate, factory, expected_wire
                    )

    def test_retryable_and_ambiguous_typed_evidence_cannot_be_forged(self):
        retryable = self._transport_provider("retryable_error")
        retryable["output_text"] = "forged provider output"
        run, factory, expected_wire = self._attempt_fixture(retryable)
        with self.assertRaisesRegex(
            V12MasterControllerError, "forbidden response fields"
        ):
            self._verify_attempt_fixture(run, factory, expected_wire)

    @staticmethod
    def _retry_policy_fixture(*, ambiguous_failures=1):
        identity = "calibration/0"
        wire_hash = "w" * 64
        request_hash = "q" * 64
        base_time = datetime.fromisoformat("2026-10-26T03:00:00+00:00")
        results = []
        reservations = [{
            "identity": identity,
            "attempt": 0,
            "sequence": 3,
            "recorded_at": base_time.isoformat(),
            "wire_payload_hash": wire_hash,
        }]
        schedules = []
        sequence = 4
        for attempt in range(ambiguous_failures):
            result = {
                "kind": "result",
                "identity": identity,
                "attempt": attempt,
                "sequence": sequence,
                "recorded_at": (
                    base_time + timedelta(seconds=attempt * 10)
                ).isoformat(),
                "status": "ambiguous_timeout",
                "event_hash": f"failure-{attempt}",
                "request_hash": request_hash,
                "wire_payload_hash": wire_hash,
                "provider_result": {
                    "transport_evidence": {
                        "provider_request_id": f"req-{attempt}",
                        "http_status": 500,
                    }
                },
            }
            results.append(result)
            recorded_at = base_time + timedelta(seconds=attempt * 10)
            schedule = {
                "sequence": sequence + 1,
                "previous_event_hash": result["event_hash"],
                "kind": "ambiguous_5xx_retry_scheduled",
                "recorded_at": recorded_at.isoformat(),
                "not_before": (recorded_at + timedelta(seconds=5)).isoformat(),
                "identity": identity,
                "failed_attempt": attempt,
                "next_attempt": attempt + 1,
                "failed_result_event_hash": result["event_hash"],
                "provider_request_id": f"req-{attempt}",
                "http_status": 500,
                "request_hash": request_hash,
                "wire_payload_hash": wire_hash,
                "retry_index": 1,
                "per_identity_retry_cap": 1,
                "release_affected_identity_cap": 1,
                "release_affected_identity_index": 1,
                "delay_seconds": 5,
                "retry_policy_hash": "retry-policy-hash",
                "event_hash": f"schedule-{attempt}",
            }
            schedules.append(schedule)
            reservations.append({
                "identity": identity,
                "attempt": attempt + 1,
                "sequence": sequence + 2,
                "recorded_at": schedule["not_before"],
                "wire_payload_hash": wire_hash,
            })
            sequence += 3
        results.append({
            "kind": "result",
            "identity": identity,
            "attempt": ambiguous_failures,
            "sequence": sequence,
            "recorded_at": (
                base_time + timedelta(seconds=ambiguous_failures * 10)
            ).isoformat(),
            "status": "ok",
            "event_hash": "success",
            "request_hash": request_hash,
            "wire_payload_hash": wire_hash,
            "provider_result": {"transport_evidence": {}},
        })
        events = sorted(
            [*reservations, *results, *schedules],
            key=lambda event: event["sequence"],
        )
        run = SimpleNamespace(
            plan=SimpleNamespace(requests=(SimpleNamespace(
                identity=identity,
                stage="calibration",
                action="verdict",
            ),)),
            config=SimpleNamespace(retry_policy=SimpleNamespace(
                message_overlength_retries=1,
                transport_retries=1,
            )),
            events=events,
        )
        attempts = {
            "reservations": reservations,
            "results": results,
            "final_rows": {identity: results[-1]},
        }
        projection = {
            "retry_policy_hash": "retry-policy-hash",
            "stage_inventories": {
                stage: {"ambiguous_5xx_retry_slots": cap}
                for stage, cap in (
                    (GATE1, 1),
                    (GATE2, 2),
                    (GATE3_PHASE1, 5),
                    (GATE3_PHASE2, 9),
                )
            },
        }
        return run, attempts, projection, schedules, reservations, results

    def test_ambiguous_5xx_per_identity_cap_cannot_be_reused(self):
        run, attempts, projection, _, _, _ = self._retry_policy_fixture(
            ambiguous_failures=2
        )
        with patch.object(
            StagedExecutor,
            "_eligible_ambiguous_5xx",
            side_effect=lambda row: row.get("status") == "ambiguous_timeout",
        ), patch.object(
            StagedExecutor, "_v1_2_5xx_delay", return_value=5
        ), self.assertRaisesRegex(
            V12MasterControllerError,
            "per.identity|more than one eligible|retry cap|unauthorized retry",
        ):
            controller._verify_master_retry_policy(run, attempts, projection)

    def test_ambiguous_5xx_schedule_and_delay_are_temporally_bound(self):
        cases = (
            "schedule_before_failure",
            "schedule_after_retry_reservation",
            "retry_before_not_before",
        )
        for mutation in cases:
            with self.subTest(mutation=mutation):
                run, attempts, projection, schedules, reservations, results = (
                    self._retry_policy_fixture()
                )
                schedule = schedules[0]
                retry_reservation = reservations[1]
                if mutation == "schedule_before_failure":
                    schedule["sequence"] = results[0]["sequence"] - 1
                elif mutation == "schedule_after_retry_reservation":
                    retry_reservation["sequence"] = schedule["sequence"] - 1
                else:
                    retry_reservation["recorded_at"] = (
                        datetime.fromisoformat(schedule["not_before"])
                        - timedelta(seconds=1)
                    ).isoformat()
                run.events = sorted(
                    [*reservations, *results, *schedules],
                    key=lambda event: event["sequence"],
                )
                with patch.object(
                    StagedExecutor,
                    "_eligible_ambiguous_5xx",
                    side_effect=(
                        lambda row: row.get("status") == "ambiguous_timeout"
                    ),
                ), patch.object(
                    StagedExecutor, "_v1_2_5xx_delay", return_value=5
                ), self.assertRaisesRegex(
                    V12MasterControllerError,
                    "schedule|retry reservation|not.before|delay",
                ):
                    controller._verify_master_retry_policy(
                        run, attempts, projection
                    )

        ambiguous = self._transport_provider("ambiguous_timeout")
        ambiguous["transport_evidence"] = {"schema_version": "v1"}
        run, factory, expected_wire = self._attempt_fixture(ambiguous)
        with self.assertRaisesRegex(
            V12MasterControllerError,
            "provider result cannot be reconstructed|typed transport-failure evidence",
        ):
            self._verify_attempt_fixture(run, factory, expected_wire)

    def test_known_not_completed_retry_between_ambiguous_and_recovery_is_valid(self):
        run, attempts, projection, schedules, reservations, results = (
            self._retry_policy_fixture()
        )
        identity = "calibration/0"
        retryable_provider = self._transport_provider("retryable_error")
        recovered_provider = self._transport_provider("ok")
        recovered_provider.update({
            "transport_recovered": True,
            "ambiguous_5xx_retry_count": 1,
            "prior_ambiguous_result_event_hashes": ["failure-0"],
            "retry_policy_hash": "retry-policy-hash",
            "dependency_closure_hash": digest([identity]),
        })
        retryable = {
            "kind": "result",
            "identity": identity,
            "attempt": 1,
            "sequence": 7,
            "recorded_at": "2026-10-26T03:00:06+00:00",
            "status": "retryable_error",
            "event_hash": "known-not-completed",
            "request_hash": "q" * 64,
            "wire_payload_hash": "w" * 64,
            "known_not_completed": True,
            "provider_result": retryable_provider,
        }
        second_retry_reservation = {
            "identity": identity,
            "attempt": 2,
            "sequence": 8,
            "recorded_at": "2026-10-26T03:00:07+00:00",
            "wire_payload_hash": "w" * 64,
        }
        recovered = results[-1]
        recovered.update(
            attempt=2,
            sequence=9,
            recorded_at="2026-10-26T03:00:08+00:00",
            provider_result=recovered_provider,
        )
        reservations[1]["previous_event_hash"] = schedules[0]["event_hash"]
        reservations.append(second_retry_reservation)
        results[:] = [results[0], retryable, recovered]
        attempts.update(
            reservations=reservations,
            results=results,
            final_rows={identity: recovered},
        )
        run.plan = SimpleNamespace(
            requests=(SimpleNamespace(
                identity=identity,
                stage="calibration",
                action="verdict",
                dependencies=(),
                parent_checkpoint=None,
            ),),
            checkpoints=(),
        )
        run.events = sorted(
            [*reservations, *results, *schedules],
            key=lambda event: event["sequence"],
        )
        with patch.object(
            StagedExecutor,
            "_eligible_ambiguous_5xx",
            side_effect=lambda row: row.get("status") == "ambiguous_timeout",
        ), patch.object(
            StagedExecutor, "_v1_2_5xx_delay", return_value=5
        ):
            controller._verify_master_retry_policy(run, attempts, projection)

    def test_provider_identity_derivative_tampering_is_rejected(self):
        required_identity = {
            "requested_model": "gpt-6.1-sol",
            "returned_model": "gpt-6.1-sol",
        }
        provider = self._transport_provider("retryable_error")
        provider.update(
            status="ok",
            raw_response={"id": "resp"},
            output_text="{}",
            parsed_output={},
            error=None,
            provider_request_id="resp",
            returned_model="gpt-6.1-sol",
            model_metadata={},
            ambiguous=False,
            known_not_completed=False,
        )
        run, factory, expected_wire = self._attempt_fixture(
            provider, provider_identity={"requested_model": "forged"}
        )
        with patch.object(
            controller, "_strict_provider_result_matches_event", return_value=True
        ), patch.object(
            controller,
            "_provider_identity_candidate",
            return_value=required_identity,
        ), self.assertRaisesRegex(
            V12MasterControllerError, "provider evidence or identity"
        ):
            self._verify_attempt_fixture(
                run, factory, expected_wire,
                required_identity=required_identity,
            )

    @staticmethod
    def _gate_event(gate, assessment, *, passed, note):
        event = {
            "sequence": 100,
            "previous_event_hash": "previous-gate-event",
            "kind": "gate",
            "recorded_at": TEST_NOW.isoformat(),
            "gate": gate,
            "passed": passed,
            "evidence": assessment,
            "evidence_hash": digest(assessment),
            "decided_by": master_release.MASTER_CONTROLLER_ID,
            "note": note,
            "prerequisite_identities": [],
        }
        event["event_hash"] = digest(event)
        return event

    def test_calibration_and_formation_gate_evidence_is_recomputed(self):
        calibration = {"assessment_type": "calibration", "passed": True}
        formation = {"assessment_type": "formation", "passed": False}
        gates = {
            "calibration": self._gate_event(
                "calibration",
                calibration,
                passed=True,
                note="Frozen automatic protocol-v1.2 calibration decision.",
            ),
            controller.V1_2_SMOKE_INTEGRITY_GATE: {
                "kind": "gate",
                "gate": controller.V1_2_SMOKE_INTEGRITY_GATE,
                "passed": True,
            },
            "formation": self._gate_event(
                "formation",
                formation,
                passed=False,
                note="Frozen automatic protocol-v1.2 five-block 3-of-5 decision.",
            ),
        }
        run = SimpleNamespace(
            gate_events=lambda: gates,
            _gate_prerequisites=lambda _gate: (),
            events=list(gates.values()),
            manifest={
                "calibration_assessment_hash": digest(calibration),
                "formation_assessment_hash": digest(formation),
            },
        )
        audit = {
            "automatic_checks_passed": True,
            "later_stage_release_permitted": True,
        }
        with patch.object(
            controller, "calibration_assessment", return_value=calibration
        ), patch.object(
            controller, "formation_assessment", return_value=formation
        ), patch.object(
            controller, "_smoke_prefix_run", return_value=object()
        ), patch.object(
            controller, "audit_v1_2_smoke_integrity", return_value=audit
        ), patch.object(
            controller, "_verify_gate_chronology", return_value=None
        ):
            controller._verify_master_gates(run, FORMATION_FAILED_STATUS)
            for gate in ("calibration", "formation"):
                with self.subTest(gate=gate):
                    original = gates[gate]["evidence_hash"]
                    gates[gate]["evidence_hash"] = "forged"
                    with self.assertRaisesRegex(
                        V12MasterControllerError, "recomputed assessment"
                    ):
                        controller._verify_master_gates(
                            run, FORMATION_FAILED_STATUS
                        )
                    gates[gate]["evidence_hash"] = original

    def test_smoke_gate_must_be_at_its_claimed_prefix(self):
        smoke = {
            "kind": "gate",
            "gate": controller.V1_2_SMOKE_INTEGRITY_GATE,
            "sequence": 1,
            "event_hash": "smoke-hash",
        }
        run = SimpleNamespace(
            events=[
                {"kind": controller.MASTER_BINDING_EVENT, "event_hash": "b"},
                {"kind": controller.MASTER_CLAIM_EVENT, "event_hash": "c"},
                smoke,
            ],
            path=Path("/offline/master"),
            config=SimpleNamespace(),
            plan=SimpleNamespace(),
            journal_path=Path("/offline/master/journal.jsonl"),
            payload_path=Path("/offline/master/payloads"),
            wire_payload_path=Path("/offline/master/wire_payloads"),
        )
        with self.assertRaisesRegex(
            V12MasterControllerError, "not at its claimed prefix"
        ):
            controller._smoke_prefix_run(run)

    def test_smoke_prefix_replays_stage_budgets_without_later_consumption(self):
        smoke_reservation = {
            "kind": "reservation",
            "identity": "smoke/0",
            "sequence": 0,
        }
        smoke_result = {
            "kind": "result",
            "identity": "smoke/0",
            "sequence": 1,
        }
        smoke_gate = {
            "kind": "gate",
            "gate": controller.V1_2_SMOKE_INTEGRITY_GATE,
            "sequence": 2,
            "event_hash": "smoke-gate-hash",
        }
        formation_reservation = {
            "kind": "reservation",
            "identity": "formation/0",
            "sequence": 3,
        }
        formation_result = {
            "kind": "result",
            "identity": "formation/0",
            "sequence": 4,
        }
        stale_terminal_stage_budgets = {
            stage: {"identities": ["later-stage-terminal-record"]}
            for stage in RELEASE_ORDER
        }
        run = SimpleNamespace(
            events=[
                smoke_reservation,
                smoke_result,
                smoke_gate,
                formation_reservation,
                formation_result,
            ],
            path=Path("/offline/master"),
            config=SimpleNamespace(limits=object(), pricing=object()),
            plan=SimpleNamespace(requests=(
                SimpleNamespace(identity="smoke/0", stage="smoke"),
                SimpleNamespace(identity="formation/0", stage="formation"),
            )),
            journal_path=Path("/offline/master/journal.jsonl"),
            payload_path=Path("/offline/master/payloads"),
            wire_payload_path=Path("/offline/master/wire_payloads"),
            manifest={
                "authorized_release_limits": {"placeholder": True},
                "authorized_budget_usd": "250",
                "authorized_stage_caps": {"placeholder": True},
                "stage_budgets": stale_terminal_stage_budgets,
            },
        )
        caps = {
            "stage_caps": {
                stage: {"max_cost_usd": "1"}
                for stage in RELEASE_ORDER
            }
        }

        def replay(_limits, _pricing, _approved, records, _release):
            identities = [row.get("identity") for row in records]
            return SimpleNamespace(
                active={},
                snapshot=lambda identities=identities: {
                    "identities": identities
                },
            )

        with patch.object(
            controller.DispatchReleaseLimits,
            "from_mapping",
            return_value=object(),
        ), patch.object(
            controller, "validate_unified_stage_caps", return_value=caps
        ), patch.object(
            controller, "_release_limits_from_caps", return_value=object()
        ), patch.object(
            controller.BudgetLedger, "replay", side_effect=replay
        ):
            prefix = controller._smoke_prefix_run(run)

        self.assertEqual(prefix.events[-1], smoke_gate)
        self.assertNotIn(formation_reservation, prefix.events)
        stage_budgets = prefix.manifest["stage_budgets"]
        self.assertEqual(
            stage_budgets[GATE2]["identities"],
            ["smoke/0", "smoke/0"],
        )
        self.assertEqual(stage_budgets[GATE3_PHASE1]["identities"], [])
        self.assertEqual(stage_budgets[GATE3_PHASE2]["identities"], [])
        self.assertNotEqual(stage_budgets, stale_terminal_stage_budgets)

    def test_terminal_manifest_cannot_claim_any_later_authority(self):
        for flag in (
            "gate4_authorized",
            "confirmation_authorized",
            "next_stage_authorized",
        ):
            with self.subTest(flag=flag):
                manifest = {
                    "status": CALIBRATION_FAILED_STATUS,
                    "next_stage_authorized": False,
                    "gate4_authorized": False,
                    "confirmation_authorized": False,
                    "operational_cost_hard_cap_usd": (
                        master_release.SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD
                    ),
                    "completed_through_stage": "calibration",
                    "completed_through_block": 0,
                    "completed_pilot": False,
                    "experiment_started": False,
                    "stop_identity": None,
                    flag: True,
                }
                run = SimpleNamespace(manifest=manifest)
                with self.assertRaisesRegex(
                    V12MasterControllerError, "hard stop|terminal"
                ):
                    controller._verify_terminal_manifest(
                        run, CALIBRATION_FAILED_STATUS
                    )

    def test_budget_limited_label_requires_next_reservation_to_exceed_250(self):
        stop = "calibration/0"
        spec = SimpleNamespace(identity=stop)
        plan = SimpleNamespace(
            requests=(spec,),
            request=lambda identity: spec if identity == stop else None,
        )
        run = SimpleNamespace(
            manifest={
                "budget_limited_before_identity": stop,
                "stop_identity": stop,
                "authorized_release_limits": {},
                "authorized_budget_usd": "250.00",
            },
            plan=plan,
            config=SimpleNamespace(),
        )
        attempts = {"reservations": [], "results": [], "final_rows": {}}
        request = SimpleNamespace(
            conservative_input_token_bound=10,
            max_output_tokens=5,
        )

        class Factory:
            @staticmethod
            def __call__(_spec, _context):
                return request

        aggregate = SimpleNamespace(
            cost_usd=Decimal("100"),
            reservation_price=lambda _input, _output: Decimal("1"),
        )
        ledger = SimpleNamespace(active={}, _aggregate=aggregate)
        with patch.object(
            controller, "ScientificRequestFactory", return_value=Factory()
        ), patch.object(
            controller.DispatchReleaseLimits,
            "from_mapping",
            return_value=object(),
        ), patch.object(
            controller, "_MasterBudgetLedger", return_value=ledger
        ), self.assertRaisesRegex(
            V12MasterControllerError,
            "not proven by the next cumulative reservation",
        ):
            controller._verify_budget_limited_stop(run, {}, attempts)

    def test_budget_limited_label_cannot_replace_nonretryable_stop_outcomes(self):
        stop = "calibration/0"
        cases = {
            "fatal": ([{"identity": stop, "status": "fatal_error"}], "verdict"),
            "authentication": (
                [{"identity": stop, "status": "authentication_error"}],
                "verdict",
            ),
            "quota": ([{"identity": stop, "status": "quota_error"}], "verdict"),
            "noneligible ambiguous": ([{
                "identity": stop,
                "status": "ambiguous_timeout",
                "event_hash": "ambiguous-failure",
            }], "verdict"),
            "verdict overlength": (
                [{"identity": stop, "status": "overlength"}],
                "verdict",
            ),
            "exhausted known-not-completed retries": ([
                {
                    "identity": stop,
                    "status": "retryable_error",
                    "known_not_completed": True,
                },
                {
                    "identity": stop,
                    "status": "retryable_error",
                    "known_not_completed": True,
                },
            ], "verdict"),
        }
        for name, (rows, action) in cases.items():
            with self.subTest(name=name):
                spec = SimpleNamespace(identity=stop, action=action)
                run = SimpleNamespace(
                    manifest={
                        "budget_limited_before_identity": stop,
                        "stop_identity": stop,
                    },
                    plan=SimpleNamespace(requests=(spec,)),
                    config=SimpleNamespace(retry_policy=SimpleNamespace(
                        transport_retries=1,
                        message_overlength_retries=1,
                    )),
                    events=[],
                )
                attempts = {
                    "reservations": [
                        {"identity": stop} for _row in rows
                    ],
                    "results": rows,
                    "final_rows": {stop: rows[-1]},
                }
                with patch.object(
                    StagedExecutor,
                    "_eligible_ambiguous_5xx",
                    return_value=False,
                ), patch.object(
                    controller,
                    "ScientificRequestFactory",
                    side_effect=AssertionError(
                        "a nonretryable outcome must fail before request rebuild"
                    ),
                ), self.assertRaisesRegex(
                    V12MasterControllerError,
                    "not eligible for another attempt",
                ):
                    controller._verify_budget_limited_stop(
                        run, {}, attempts
                    )


class V12MasterAnalysisBoundaryTests(unittest.TestCase):
    def test_fake_journal_cannot_be_relabelled_empirical_in_manifest(self):
        run = _FlowRun()

        def chained_event(sequence, previous, kind, **fields):
            event = {
                "sequence": sequence,
                "previous_event_hash": previous,
                "kind": kind,
                "recorded_at": TEST_NOW.isoformat(),
                **fields,
            }
            event["event_hash"] = digest(event)
            return event

        binding = chained_event(
            0,
            None,
            controller.MASTER_BINDING_EVENT,
        )
        claim = chained_event(
            1,
            binding["event_hash"],
            controller.MASTER_CLAIM_EVENT,
        )
        mode = chained_event(
            2,
            claim["event_hash"],
            controller.MASTER_MODE_EVENT,
            controller=master_release.MASTER_CONTROLLER_ID,
            proposal_hash="p" * 64,
            run_id=V1_2_UNIFIED_PILOT_RUN_ID,
            mode=controller.FAKE_EXECUTION_MODE,
            executor_provenance=controller.FAKE_EXECUTOR_PROVENANCE,
        )
        run.events[:] = [binding, claim, mode]
        calls = []
        with _flow_patches(calls, calibration=True, formation=True):
            report = _flow_executor(run).execute()
        self.assertEqual(report["status"], FAKE_FINAL_STATUS)
        self.assertEqual(
            run.events[2]["mode"], controller.FAKE_EXECUTION_MODE
        )

        run.set_status(
            controller.FINAL_STATUS,
            source="empirical-live",
            run_type="pilot",
            run_id=V1_2_UNIFIED_PILOT_RUN_ID,
            execution_controller=master_release.MASTER_CONTROLLER_ID,
            master_proposal_hash="p" * 64,
            execution_mode=controller.EMPIRICAL_EXECUTION_MODE,
            executor_provenance=controller.EMPIRICAL_EXECUTOR_PROVENANCE,
            empirical_evidence=True,
        )
        self.assertEqual(
            run.manifest["execution_mode"],
            controller.EMPIRICAL_EXECUTION_MODE,
        )
        self.assertEqual(
            run.events[2]["mode"], controller.FAKE_EXECUTION_MODE
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manifest.json").write_text(
                canonical(run.manifest), encoding="utf-8"
            )
            for entrypoint in (
                lambda: analysis.load_durable_and_rescore(path),
                lambda: analysis.analyze_artifact_run(path),
            ):
                with self.subTest(entrypoint=entrypoint), patch(
                    "collusion_recovery.orchestration.DurableRun.inspect",
                    return_value=run,
                ), no_network_or_credentials(), self.assertRaisesRegex(
                    ValueError, "cannot be analyzed as empirical evidence"
                ):
                    entrypoint()

    def test_fake_master_run_is_rejected_by_loader_and_auto_analyzer(self):
        manifest = {
            "source": "empirical-live",
            "run_type": "pilot",
            "run_id": V1_2_UNIFIED_PILOT_RUN_ID,
            "execution_controller": master_release.MASTER_CONTROLLER_ID,
            "execution_mode": controller.FAKE_EXECUTION_MODE,
            "executor_provenance": controller.FAKE_EXECUTOR_PROVENANCE,
            "empirical_evidence": False,
            "status": FAKE_FINAL_STATUS,
        }
        run = SimpleNamespace(manifest=manifest)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "manifest.json").write_text(
                canonical(manifest), encoding="utf-8"
            )
            for entrypoint in (
                lambda: analysis.load_durable_and_rescore(path),
                lambda: analysis.analyze_artifact_run(path),
            ):
                with self.subTest(entrypoint=entrypoint), patch(
                    "collusion_recovery.orchestration.DurableRun.inspect",
                    return_value=run,
                ), no_network_or_credentials(), self.assertRaises(ValueError):
                    entrypoint()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
