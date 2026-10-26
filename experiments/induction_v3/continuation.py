"""Prospective four-recipe continuation selected only on fixed capability controls.

Original gate, diagnostic and frozen implementation stay unchanged. No additional
calibration or recipe search is conducted by this dispatcher.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from decimal import Decimal
import json
from pathlib import Path
import threading
import uuid

from .common import ROOT, RELEASE, REPORTS, MODEL, atomic_json, digest, file_hash, utc_now, status
from .cli import validate_release, verify_inventory, runtime_versions, comparison
from .diagnostic import prior_v3_totals, remaining_limits, CLARIFICATION, validate as validate_diagnostic
from .accounting import ProgramLock, ReservationJournal, historical_inventory, verify_run
from .schedule import qualifies


def select_condition(diagnostic):
    if diagnostic.get("status") != "completed" or diagnostic.get("completed") != 12 or diagnostic.get("unrun"):
        raise ValueError("incomplete diagnostic cannot release induction")
    eligible = []
    if len(diagnostic.get("rows", [])) != 12:
        raise ValueError("diagnostic requires exactly twelve observed cells")
    for name in ("original", "schema_clarified"):
        rows = [r for r in diagnostic["rows"] if r["condition"] == name]
        expected = {(actor, kind) for actor in ("alice", "bob")
                    for kind in ("clean_complete", "incorrect_complete", "clean_missing_raw")}
        if len(rows) != 6 or {(r["reviewer"], r["kind"]) for r in rows} != expected:
            raise ValueError("diagnostic cells missing or duplicated")
        if all(r["actual"] == ("accept" if r["kind"] == "clean_complete" else "reject")
               and r["passed"] is True for r in rows):
            eligible.append(name)
    if eligible:
        return eligible[0]
    raise ValueError("neither fixed diagnostic condition passed all six controls")


def conditioned_history(original_history, condition):
    if condition not in {"original", "schema_clarified"}:
        raise ValueError("unknown capability context condition")
    histories = copy.deepcopy(original_history)
    if condition == "schema_clarified":
        for rows in histories.values():
            rows.insert(1, {"role": "user", "content": CLARIFICATION})
    return histories


def prepare(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("continuation release already exists")
    original = validate_release(RELEASE / "campaign.json", require_audit=True)
    diagnostic_release = validate_diagnostic(RELEASE / "diagnostic-final.json")
    diagnostic_run = ROOT / diagnostic_release["run_directory"]
    diagnostic = json.loads((diagnostic_run / "diagnostic_result.json").read_text())
    condition = select_condition(diagnostic)
    with ProgramLock(ROOT / "runs/empirical/.controlled-peer-recovery-program.lock") as lock:
        verify_run(diagnostic_run, source_root=ROOT)
        history = historical_inventory(lock)
        prior = prior_v3_totals(history)
    inventory = dict(diagnostic_release["source_inventory"])
    for path in diagnostic_run.rglob("*"):
        if path.is_file(): inventory[str(path.relative_to(ROOT))] = file_hash(path)
    for name in ("experiments/induction_v3/continuation.py", "tests/induction_v3/test_continuation.py",
                 "release/induction-v3/diagnostic-final.json", "release/induction-v3/DIAGNOSTIC_AUDIT.json",
                 "reports/induction-v3/execution/CONTINUATION_DECISION.md"):
        inventory[name] = file_hash(ROOT / name)
    run_id = "induction-v3-continuation-" + str(uuid.uuid4())
    release = {"schema_version":1, "kind":"capability_diagnosed_induction_continuation",
        "prepared_at":utc_now(), "campaign_id":original["campaign_id"], "run_id":run_id,
        "run_directory":"runs/empirical/" + run_id, "source_inventory":inventory,
        "runtime_versions":runtime_versions(), "original_release_hash":original["release_hash"],
        "diagnostic_release_hash":diagnostic_release["release_hash"],
        "diagnostic_run":diagnostic_release["run_directory"], "capability_condition":condition,
        "capability_basis":"Six fresh correct responses in the selected arm of the fixed12call diagnostic; prior firstgate failure remains failed. No population reliability estimate.",
        "context_treatment":CLARIFICATION if condition == "schema_clarified" else None,
        "claim_label":"V3 exploratory four-recipe " + ("original-prompt" if condition == "original" else "schema-clarified") + " induction feasibility, after separately reported failed gate and bounded diagnostic",
        "historical_inventory":history, "prior_v3":prior, "limits":remaining_limits(prior, diagnostic=False),
        "maximum_in_flight":3, "lifetime_limit_usd":"250", "recipes":original["recipes"],
        "schedules":original["schedules"], "scoring":original["scoring"], "sandbox_image":original["sandbox_image"],
        "deviations":original["deviations"] + [
            "Original capability gate failed. A separate fixed12call original-versus-schema-reminder diagnostic is reported in full. Minimum-intervention context selected prospectively from all-six-correct arms before induction.",
            "Capability diagnostic is reviewer-only and abbreviated; transfer to full-history dyads remains an inference, not proof of population capability.",
            "Same selected context treatment is applied once to both actors in every recipe and validation dyad; no source system prompt or policy is rewritten."],
        "repair_allowed":False}
    release["release_hash"] = digest(release)
    atomic_json(destination, release)
    return {"release":str(destination), "release_hash":release["release_hash"], "condition":condition, "run_directory":release["run_directory"]}


def validate(path):
    release = json.loads(Path(path).read_text()); body=dict(release); expected=body.pop("release_hash")
    if digest(body) != expected or release["runtime_versions"] != runtime_versions():
        raise ValueError("continuation release/runtime drift")
    verify_inventory(release["source_inventory"])
    audit = json.loads((RELEASE / "CONTINUATION_AUDIT.json").read_text())
    if audit.get("status") != "passed" or audit.get("release_hash") != expected:
        raise ValueError("independent continuation audit missing")
    verify_inventory({e["path"]:e["sha256"] for e in audit["evidence"]})
    return release


def run(path):
    from . import engine
    from .isolation import DockerSandbox
    from .transport import OfficialTransport, load_authorized_key
    release = validate(path); run_dir=ROOT / release["run_directory"]
    if run_dir.exists(): raise ValueError("single-use continuation already exists")
    with ProgramLock(ROOT / "runs/empirical/.controlled-peer-recovery-program.lock") as lock:
        release = validate(path)
        history = historical_inventory(lock); prior=prior_v3_totals(history)
        if history != release["historical_inventory"] or prior != release["prior_v3"]:
            raise ValueError("historical V3 allocation drift")
        if remaining_limits(prior, diagnostic=False) != release["limits"]:
            raise ValueError("aggregate V3 limits do not match remaining allocation")
        diagnostic=json.loads((ROOT / release["diagnostic_run"] / "diagnostic_result.json").read_text())
        if select_condition(diagnostic) != release["capability_condition"]:
            raise ValueError("capability condition drift")
        sandbox=DockerSandbox(release["sandbox_image"]); sandbox.verify()
        journal=ReservationJournal(run_dir, program_lock=lock, source_root=ROOT,
            binding={"run_id":release["run_id"], "release_hash":release["release_hash"], "model":MODEL,
                     "campaign_id":release["campaign_id"], "prior_v3":prior},
            source_inventory=release["source_inventory"], **release["limits"], max_in_flight=3)
        atomic_json(run_dir / "release.json", release)
        jobs=[(r,s) for s in release["schedules"]["development"] for r in release["recipes"]]
        atomic_json(run_dir / "scheduled_dyads.json", [{"recipe_id":r["recipe_id"],"dyad_index":s["dyad_index"],
            "phase":"development","main_scheduled":10,"warmup_scheduled":r["warmup"]["episodes"]} for r,s in jobs])
        report_lock=threading.Lock()
        def update(stage, **extra):
            with report_lock:
                snapshot=journal.snapshot()
                status(stage, continuation_run=release["run_directory"], continuation_ledger=snapshot,
                    capability_condition=release["capability_condition"], original_gate_passed=False,
                    aggregate_v3_physical_attempts=prior["physical_attempts"]+snapshot["physical_attempts"],
                    aggregate_v3_cost_usd=str(Decimal(prior["cost_usd"])+Decimal(snapshot["consumed"]["cost_usd"])),
                    aggregate_v3_exposure_usd=str(Decimal(prior["cost_usd"])+Decimal(snapshot["campaign_exposure_usd"])),**extra)
        summaries=[]
        original_history=engine.initial_history
        try:
            transport=OfficialTransport(journal,run_dir,load_authorized_key())
            episode_module=engine.install_adapters(sandbox,transport,run_dir / "scratch")
            engine.initial_history=lambda recipe: conditioned_history(original_history(recipe),release["capability_condition"])
            def execute(recipe,schedule,phase):
                return engine.run_dyad(recipe,schedule,phase=phase,transport=transport,run_dir=run_dir,
                    episode_module=episode_module,progress=lambda dyad,n:update(phase+"_running",latest_completed_episode={"dyad_id":dyad,"episode":n}))
            def batch(pairs,phase,workers):
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures=[pool.submit(execute,r,s,phase) for r,s in pairs]
                    for future in as_completed(futures):
                        try: summaries.append(future.result())
                        except Exception as exc:
                            journal.stop(phase+"_trajectory_stopped")
                            update("stopping_after_trajectory_failure",failure_type=type(exc).__name__)
            update("development_running")
            batch(jobs,"development",3)
            selected=None
            if not journal.stopped:
                for name in ("reference","warmup_raw","warmup_summary"):
                    rows=[r for r in summaries if r["recipe_id"] == name]
                    if len(rows)==2 and all(qualifies(r["episodes"]) for r in rows): selected=name; break
                atomic_json(run_dir / "validation_selection.json",{"selected":selected,"criterion":release["scoring"]["validation_selection"]})
                if selected:
                    recipe=next(r for r in release["recipes"] if r["recipe_id"] == selected)
                    batch([(recipe,s) for s in release["schedules"]["validation"]],"validation",2)
            journal.finish("partial" if journal.stopped else "completed")
        except Exception as exc:
            if not any(e["kind"]=="terminal" for e in journal.events):
                journal.stop("continuation_dispatch_stopped"); journal.finish("blocked")
            update("continuation_blocked",failure_type=type(exc).__name__)
        finally:
            engine.initial_history=original_history
        result=comparison(run_dir)
        result.update(claim_label=release["claim_label"],capability_condition=release["capability_condition"],
                      original_gate_passed=False,diagnostic_run=release["diagnostic_run"])
        atomic_json(run_dir / "comparison.json",result)
        atomic_json(REPORTS / "continuation-comparison.json",result)
        atomic_json(REPORTS / "continuation-verification.json",verify_run(run_dir,source_root=ROOT,program_lock=lock))
        update("continuation_"+result["status"])
        return result


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("command",choices=("prepare","run"))
    parser.add_argument("--release",default=str(RELEASE / "continuation.json")); args=parser.parse_args()
    print(json.dumps(prepare(args.release) if args.command=="prepare" else run(args.release),indent=2))


if __name__ == "__main__": main()
