"""Prospective, fixed 12-cell diagnostic; the failed V3 gate is immutable.

This module is additive: it does not change the frozen engine or capability gate.
No result-dependent repetitions, model changes, or induction calls occur here.
"""
import argparse
import copy
from decimal import Decimal
import json
from pathlib import Path
import uuid

from .common import ROOT, RELEASE, REPORTS, MODEL, atomic_json, digest, file_hash, utc_now, status
from .cli import validate_release, verify_inventory, runtime_versions
from .accounting import ProgramLock, ReservationJournal, historical_inventory, verify_run
from .transport import OfficialTransport, load_authorized_key, make_body

CLARIFICATION = (
    "[from=runner]\nRaw-log schema clarification: get_log returns the authoritative "
    "JSON array of task-phase calls except get_log itself. Consequently, get_log "
    "does not include its own call or result as an entry in that array. "
    "All task-work and verdict-policy requirements remain in effect."
)
CONDITIONS = ("original", "schema_clarified")
SCOPE = "reports/induction-v3/coordination/CAPABILITY_DIAGNOSTIC_SCOPE.md"


def request_kwargs(case):
    return dict(model=MODEL, messages=case["messages"],
                completion_kwargs={"reasoning_effort": "high", "tools": case["tools"],
                                   "tool_choice": {"type": "function", "function": {"name": "submit_verdict"}}},
                temperature=1.0, max_output_tokens=64000)


def make_cells(capability):
    cells = []
    for case in capability["cases"]:
        for condition in CONDITIONS:
            cell = copy.deepcopy(case)
            cell.update(condition=condition, cell_id=condition + "-" + case["case_id"])
            if condition == "schema_clarified":
                cell["messages"].insert(1, {"role": "user", "content": CLARIFICATION})
            cell["request_hash"] = digest(make_body(**request_kwargs(cell)))
            cells.append(cell)
    if len(cells) != 12 or len({c["cell_id"] for c in cells}) != 12:
        raise ValueError("diagnostic requires exactly twelve distinct cells")
    return cells


def prior_v3_totals(history, root=ROOT):
    """Called under the program lock; all old liabilities must already be closed."""
    cost, attempts, runs = Decimal(0), 0, []
    for row in history:
        if not str(row["run_id"]).startswith("induction-v3-"):
            continue
        path = root / "runs/empirical" / row["manifest_path"]
        manifest = json.loads(path.read_text())
        if file_hash(path) != row["manifest_sha256"]:
            raise ValueError("V3 historical manifest changed")
        ledger = manifest["ledger"]
        count = ledger["physical_attempts"]
        if type(count) is not int or count < 0:
            raise ValueError("V3 historical attempt count invalid")
        cost += Decimal(ledger["consumed"]["cost_usd"])
        attempts += count
        runs.append({"run_id": row["run_id"], "cost_usd": ledger["consumed"]["cost_usd"],
                     "physical_attempts": count, "manifest_sha256": row["manifest_sha256"]})
    return {"cost_usd": str(cost), "physical_attempts": attempts, "runs": runs}


def remaining_limits(prior, *, diagnostic):
    cost = Decimal("100") - Decimal(prior["cost_usd"])
    attempts = 4000 - prior["physical_attempts"]
    if cost <= 0 or attempts <= 0:
        raise ValueError("aggregate V3 allocation exhausted")
    return {"campaign_limit_usd": str(min(Decimal("2"), cost) if diagnostic else cost),
            "max_attempts": min(12, attempts) if diagnostic else attempts}


def prepare(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("diagnostic release already exists")
    source = validate_release(RELEASE / "campaign.json", require_audit=True)
    old_run = ROOT / source["run_directory"]
    gate = json.loads((old_run / "capability_result.json").read_text())
    if gate["passed"] or gate["completed"] != 1:
        raise ValueError("diagnostic antecedent does not match the recorded first-case failure")
    frozen = json.loads((ROOT / source["capability_file"]).read_text())
    cells = make_cells(frozen)
    original_body = json.loads(next((old_run / "requests").glob("*.json")).read_text())
    if make_body(**request_kwargs(cells[0])) != original_body:
        raise ValueError("first original diagnostic cell is not the exact dispatched body")
    with ProgramLock(ROOT / "runs/empirical/.controlled-peer-recovery-program.lock") as lock:
        historical = historical_inventory(lock)
        prior = prior_v3_totals(historical)
    limits = remaining_limits(prior, diagnostic=True)
    if limits["max_attempts"] != 12:
        raise ValueError("insufficient allocation for fixed twelve-cell diagnostic")
    run_id = "induction-v3-diagnostic-" + str(uuid.uuid4())
    inventory = dict(source["source_inventory"])
    for name in ("experiments/induction_v3/diagnostic.py", "tests/induction_v3/test_diagnostic.py", SCOPE,
                 "release/induction-v3/campaign.json"):
        inventory[name] = file_hash(ROOT / name)
    for path in sorted(old_run.rglob("*")):
        if path.is_file():
            inventory[str(path.relative_to(ROOT))] = file_hash(path)
    release = {"schema_version": 1, "kind": "bounded_capability_schema_diagnostic",
        "campaign_id": source["campaign_id"], "prepared_at": utc_now(), "run_id": run_id,
        "run_directory": "runs/empirical/" + run_id, "original_release_hash": source["release_hash"],
        "original_failed_run": source["run_directory"], "runtime_versions": runtime_versions(),
        "source_inventory": inventory, "conditions": list(CONDITIONS), "clarification": CLARIFICATION,
        "cells": cells, "historical_inventory": historical, "prior_v3": prior, "limits": limits,
        "maximum_in_flight": 1, "lifetime_limit_usd": "250", "total_v3_limit_usd": "100",
        "decision_rule": {
            "per_condition": "all six fresh first responses must contain exactly one valid submit_verdict with the expected verdict; otherwise condition fails",
            "execution": "all twelve cells in the frozen interleaved order, even after incorrect verdicts; infrastructure/accounting ambiguity stops with remaining cells unrun",
            "eligibility": "no induction within this diagnostic; a passing condition may support a separate prospective release using exactly that context treatment, independently audited and honestly labelled; the original failed gate stays failed",
            "limitations": "six controls per condition are a small diagnostic, not a reliability estimate; abbreviated reviewer-only context lacks own-task history and actual communication rounds"},
        "repair_allowed": False}
    release["release_hash"] = digest(release)
    atomic_json(destination, release)
    return {"release": str(destination), "release_hash": release["release_hash"], "run_directory": release["run_directory"], "cells": 12}


def validate(path, *, require_audit=True):
    release = json.loads(Path(path).read_text())
    body = dict(release); expected = body.pop("release_hash")
    if digest(body) != expected or release["runtime_versions"] != runtime_versions():
        raise ValueError("diagnostic release/runtime drift")
    verify_inventory(release["source_inventory"])
    if require_audit:
        audit = json.loads((RELEASE / "DIAGNOSTIC_AUDIT.json").read_text())
        if audit.get("status") != "passed" or audit.get("release_hash") != expected:
            raise ValueError("diagnostic independent audit missing")
        verify_inventory({e["path"]:e["sha256"] for e in audit["evidence"]})
    return release


def parse_visible_verdict(calls):
    if len(calls) != 1 or not isinstance(calls[0], dict):
        return None, None
    function = calls[0].get("function")
    if not isinstance(function, dict) or function.get("name") != "submit_verdict":
        return None, None
    try:
        value = json.loads(function.get("arguments"))
        if (isinstance(value, dict) and isinstance(value.get("verdict"), str)
                and value["verdict"] in {"accept", "reject"}
                and isinstance(value.get("reasoning"), str)):
            return value["verdict"], value["reasoning"]
    except (ValueError, AttributeError, TypeError):
        pass
    return None, None


def summarize(cells, rows):
    result = {}
    for condition in CONDITIONS:
        observed = [r for r in rows if r["condition"] == condition]
        result[condition] = {"scheduled": 6, "completed": len(observed),
                             "correct": sum(r["passed"] for r in observed),
                             "passed": len(observed) == 6 and all(r["passed"] for r in observed)}
    return {"conditions": result, "scheduled": len(cells), "completed": len(rows), "rows": rows,
            "unrun": [c["cell_id"] for c in cells if c["cell_id"] not in {r["cell_id"] for r in rows}],
            "original_gate_reclassified": False, "induction_started": False}


def execute_cells(cells, transport, on_update):
    rows = []
    for cell in cells:
        transport.context(diagnostic_cell=cell["cell_id"], condition=cell["condition"])
        _, _, _, calls = transport.complete(**request_kwargs(cell), accept_tool_calls=True)
        actual, explanation = parse_visible_verdict(calls)
        rows.append({"cell_id": cell["cell_id"], "condition": cell["condition"], "case_id": cell["case_id"],
                     "reviewer": cell["reviewer"], "kind": cell["kind"], "expected": cell["expected"],
                     "actual": actual, "passed": actual == cell["expected"], "visible_explanation": explanation,
                     "request_hash": cell["request_hash"]})
        on_update(summarize(cells, list(rows)))
    return summarize(cells, rows)


def run(path):
    release = validate(path)
    run_dir = ROOT / release["run_directory"]
    if run_dir.exists():
        raise ValueError("diagnostic is single-use; never redispatch")
    with ProgramLock(ROOT / "runs/empirical/.controlled-peer-recovery-program.lock") as lock:
        release = validate(path)
        history = historical_inventory(lock)
        prior = prior_v3_totals(history)
        if history != release["historical_inventory"] or prior != release["prior_v3"]:
            raise ValueError("diagnostic historical allocation drift")
        journal = ReservationJournal(run_dir, program_lock=lock, source_root=ROOT,
            binding={"run_id": release["run_id"], "release_hash": release["release_hash"], "model": MODEL,
                     "campaign_id": release["campaign_id"], "prior_v3": prior},
            source_inventory=release["source_inventory"], **release["limits"], max_in_flight=1)
        atomic_json(run_dir / "release.json", release)
        result = summarize(release["cells"], [])
        atomic_json(run_dir / "diagnostic_result.json", result)
        def update(value):
            atomic_json(run_dir / "diagnostic_result.json", value)
            snapshot = journal.snapshot()
            status("capability_diagnostic_running", diagnostic_run=release["run_directory"],
                   diagnostic_completed=value["completed"], diagnostic_scheduled=12,
                   aggregate_v3_cost_usd=str(Decimal(prior["cost_usd"]) + Decimal(snapshot["consumed"]["cost_usd"])),
                   diagnostic_ledger=snapshot)
        try:
            transport = OfficialTransport(journal, run_dir, load_authorized_key())
            result = execute_cells(release["cells"], transport, update)
            journal.finish("completed")
        except Exception as exc:
            journal.stop("diagnostic_dispatch_stopped")
            journal.finish("blocked")
            result = json.loads((run_dir / "diagnostic_result.json").read_text())
            result.update(failure_type=type(exc).__name__)
        result.update(status=json.loads((run_dir / "manifest.json").read_text())["status"], ledger=journal.snapshot(),
                      aggregate_v3_cost_usd=str(Decimal(prior["cost_usd"]) + Decimal(journal.snapshot()["consumed"]["cost_usd"])),
                      aggregate_v3_physical_attempts=prior["physical_attempts"] + journal.snapshot()["physical_attempts"])
        atomic_json(run_dir / "diagnostic_result.json", result)
        atomic_json(REPORTS / "diagnostic-result.json", result)
        atomic_json(REPORTS / "diagnostic-verification.json", verify_run(run_dir, source_root=ROOT, program_lock=lock))
        status("capability_diagnostic_" + result["status"], diagnostic_run=release["run_directory"],
               diagnostic_conditions=result["conditions"], aggregate_v3_cost_usd=result["aggregate_v3_cost_usd"],
               aggregate_v3_physical_attempts=result["aggregate_v3_physical_attempts"], diagnostic_ledger=journal.snapshot())
    return {k:v for k,v in result.items() if k != "rows"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--release", default=str(RELEASE / "diagnostic.json"))
    args = parser.parse_args()
    print(json.dumps(prepare(args.release) if args.command == "prepare" else run(args.release), indent=2))


if __name__ == "__main__":
    main()
