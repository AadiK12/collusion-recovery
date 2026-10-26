"""Single-source, single-use recovery waves under the deadline amendment.

Reuses the frozen assay and ledger. No capability resampling, endpoint shopping,
or fresh recovery envelope. Only the sole dispatcher may execute prepare/run.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid

from experiments.induction_v3.common import ROOT, MODEL, atomic_json, digest, file_hash, utc_now
from experiments.induction_v3.liability_accounting import (
    ProgramLock, LiabilityReservationJournal, historical_inventory, inventory_hash,
    liability_anchor, verify_run,
)
from experiments.induction_v3.transport import DispatchStopped
from . import runner as prior

PROTOCOL = "recovery-v3-deadline-continuation"
AMENDMENT = "docs/RECOVERY_V3_DEADLINE_CONTINUATION.md"
PRIORITY = ("development-warmup_summary-1", "development-warmup_raw-1", "development-reference-2")
DISPATCH_CUTOFF = "2026-10-26T11:47:00+00:00"
PRIMARY_FREEZE = "2026-10-26T11:50:00+00:00"
LOCK = prior.LOCK


def now():
    return datetime.now(timezone.utc)


def check_deadline():
    if now() >= datetime.fromisoformat(DISPATCH_CUTOFF):
        raise DispatchStopped("deadline_no_new_dispatch")


class DeadlineJournal(LiabilityReservationJournal):
    def assert_dispatchable(self):
        # A deadline forbids new work but must not invalidate settled in-flight
        # results. The coordinator records stop only after all workers drain.
        check_deadline()
        super().assert_dispatchable()
        check_deadline()

    def _check_capacity(self, reservation):
        super()._check_capacity(reservation)
        check_deadline()


class DeadlineHTTPClient:
    """Last check after reservation fsync and immediately before HTTP dispatch.

If the clock crosses during fsync, the unchanged transport conservatively keeps
the unused reservation as unknown. It never sends after the cutoff or invents
usage/noncompletion proof to release that reservation.
"""
    def __init__(self, delegate):
        self.delegate = delegate

    def post(self, *args, **kwargs):
        check_deadline()
        return self.delegate.post(*args, **kwargs)

    def close(self):
        self.delegate.close()


def capability_lineage():
    release, _, _ = prior.validate(prior.DEFAULT_RELEASE)
    directory = ROOT / release["run_directory"]
    value = prior.read_json(directory / "capability_result.json")
    expected = {r["cell_id"]: r["expected_verdict"] for r in release["capability_schedule"]}
    rows = value.get("results", [])
    if (value.get("passed") is not True or value.get("planned") != 6 or len(rows) != 6
            or {r.get("cell_id") for r in rows} != set(expected)
            or any(r.get("valid") is not True or r.get("verdict") not in {"accept", "reject"}
                   or r.get("verdict") != expected.get(r.get("cell_id"))
                   or r.get("expected") != expected.get(r.get("cell_id")) for r in rows)):
        raise ValueError("exact prior six-case capability gate did not pass")
    return {"run_directory": release["run_directory"], "release_hash": release["release_hash"],
            "path": str((directory / "capability_result.json").relative_to(ROOT)),
            "sha256": file_hash(directory / "capability_result.json"), "new_capability_calls": 0}


def attempted_sources(history):
    sources = set()
    for row in history:
        if row["run_id"].startswith("induction-v3-recovery-"):
            # The run's immutable release, not journal summaries, owns assignment.
            path = ROOT / "runs/empirical" / Path(row["manifest_path"]).parent / "release.json"
            release = prior.read_json(path)
            assigned = release.get("wave_source_assignment")
            if not isinstance(assigned, list) or any(s not in prior.SOURCE_IDS for s in assigned):
                raise ValueError("prior recovery assignment missing or invalid")
            sources.update(assigned)
    return sources


def check_selection(endpoint_id, history):
    if endpoint_id not in PRIORITY:
        raise ValueError("source is outside the deadline priority cohort")
    used = attempted_sources(history)
    if endpoint_id in used:
        raise ValueError("source assignment already attempted; no rerun")
    if any(source not in used for source in PRIORITY[:PRIORITY.index(endpoint_id)]):
        raise ValueError("source priority cannot be skipped")


def limits_for(history):
    limits = prior.remaining_recovery_allocation(history)
    if limits["max_attempts"] < 80:
        raise ValueError("insufficient remaining attempts for one complete assignment")
    return {**limits, "max_attempts": 80}


def feasibility(lineage):
    events = [json.loads(s) for s in (ROOT / lineage["run_directory"] / "journal.jsonl").read_text().splitlines()]
    reserved = [e for e in events if e["kind"] == "reservation"]
    settled = [e for e in events if e["kind"] == "settlement"]
    seconds = (datetime.fromisoformat(settled[-1]["recorded_at"]) - datetime.fromisoformat(reserved[0]["recorded_at"])).total_seconds()
    if len(reserved) != 86 or len(settled) != 86 or seconds <= 0:
        raise ValueError("prior timing evidence incomplete")
    estimated = 60 + 80 * seconds / len(reserved)
    remaining = (datetime.fromisoformat(DISPATCH_CUTOFF) - now()).total_seconds()
    if remaining < estimated:
        raise ValueError("insufficient deadline time for observed-rate complete wave")
    return {"recorded_at": utc_now(), "basis": "all prior wave calls; no outcome selection",
            "prior_calls": 86, "prior_active_seconds": seconds,
            "estimated_wave_seconds_including_cooldown": estimated,
            "remaining_dispatch_seconds": remaining, "guarantee": False}


def source_candidate(source_run, endpoint_id):
    from .endpoints import inventory_endpoints, load_endpoint, discover_response_archives
    source_run = str(Path(source_run))
    if Path(source_run).is_absolute() or ".." in Path(source_run).parts:
        raise ValueError("source run must be repository-relative")
    source = prior.read_json(ROOT / source_run / "release.json")
    if (source.get("protocol_id") != "recovery-v3-source-completion"
            or endpoint_id not in source.get("selected_sources", [])):
        raise ValueError("source is not an assigned source-completion run")
    old = inventory_endpoints(root=ROOT, run_dir=ROOT / prior.SOURCE_RUN)
    new = inventory_endpoints(root=ROOT, run_dir=ROOT / source_run)
    selected = next(e for e in new if e["endpoint_id"] == endpoint_id)
    data = {endpoint_id: load_endpoint(selected, root=ROOT)}
    endpoints = [selected if e["endpoint_id"] == endpoint_id else e for e in old]
    responses, bindings = {}, {}
    for directory in (ROOT / prior.SOURCE_RUN, ROOT / source_run):
        archives = discover_response_archives(root=ROOT, run_dir=directory)
        responses.update(archives["response_bindings"])
        bindings.update(archives["response_bindings"])
        bindings.update(archives["request_bindings"])
        bindings.update(archives["run_release_bindings"])
    return endpoints, data, responses, bindings


def prepare(path, *, source_run, endpoint_id):
    path = Path(path)
    source_run = str(Path(source_run))
    if path.exists():
        raise ValueError("immutable successor release already exists")
    check_deadline()
    lineage = capability_lineage()
    estimate = feasibility(lineage)
    endpoints, data, archives, archive_bindings = source_candidate(source_run, endpoint_id)
    bundle = prior.read_json(ROOT / prior.FIXTURES)
    with ProgramLock(LOCK) as lock:
        history = historical_inventory(lock)
        check_selection(endpoint_id, history)
        source_row = next((r for r in history if r["manifest_path"] == str(Path(source_run).relative_to("runs/empirical") / "manifest.json")), None)
        if source_row is None:
            raise ValueError("source run not in terminal historical inventory")
        inventory = dict(prior.read_json(prior.DEFAULT_RELEASE)["source_inventory"])
        inventory.update(archive_bindings)
        names = [AMENDMENT, "reports/recovery-v3/science/DEADLINE_CONTRACT.json",
                 "experiments/recovery_v3/successor.py", "tests/recovery_v3/test_successor.py",
                 source_run + "/manifest.json", source_run + "/journal.jsonl",
                 "release/recovery-v3/wave-1.json", "release/recovery-v3/wave-1.audit.json",
                 lineage["path"], lineage["run_directory"] + "/manifest.json",
                 lineage["run_directory"] + "/journal.jsonl"]
        names += [e["path"] for e in endpoints if e["path"]]
        for name in names:
            inventory[name] = file_hash(ROOT / name)
        prior.verify_files(inventory)
        run_id = "induction-v3-recovery-successor-" + str(uuid.uuid4())
        release = {"schema_version": 1, "protocol_id": PROTOCOL, "claim_label": prior.CLAIM,
            "run_id": run_id, "run_directory": "runs/empirical/" + run_id,
            "source_run": source_run, "endpoint_id": endpoint_id, "endpoints": endpoints,
            "schedule": prior.build_schedule(bundle), "capability_schedule": [], "capability_lineage": lineage,
            "available_sources": [endpoint_id], "wave_source_assignment": [endpoint_id],
            "wave_planned_calls": 80, "planned_research_calls": 480, "maximum_additional_research_calls": 240,
            "source_inventory": inventory, "estimator_archives": archives,
            "historical_inventory": history, "historical_inventory_hash": inventory_hash(history),
            "limits": limits_for(history), "carried_liability": liability_anchor(),
            "runtime_versions": prior.runtime(), "pacing": prior.PACING, "model_settings": prior.SETTINGS,
            "instructions": {"neutral": prior.NEUTRAL, "correction": prior.CORRECTION},
            "dispatch_cutoff": DISPATCH_CUTOFF, "primary_freeze_target": PRIMARY_FREEZE,
            "feasibility": estimate, "prepared_at": utc_now()}
        release["initial_request_hashes"] = prior.initial_request_hashes(release, bundle, data)
        release["release_hash"] = digest(release)
        atomic_json(path, release)
    return {"release_hash": release["release_hash"], "run_directory": release["run_directory"]}


def validate(path, *, require_audit=True):
    release = prior.read_json(path); body = dict(release); expected = body.pop("release_hash")
    if (digest(body) != expected or release["protocol_id"] != PROTOCOL
            or release["claim_label"] != prior.CLAIM):
        raise ValueError("successor release identity/digest drift")
    prior.verify_files(release["source_inventory"])
    if (release["runtime_versions"] != prior.runtime() or release["pacing"] != prior.PACING
            or release["model_settings"] != prior.SETTINGS or release["carried_liability"] != liability_anchor()
            or release["instructions"] != {"neutral": prior.NEUTRAL, "correction": prior.CORRECTION}
            or release["dispatch_cutoff"] != DISPATCH_CUTOFF or release["primary_freeze_target"] != PRIMARY_FREEZE
            or release["capability_lineage"] != capability_lineage() or release["capability_schedule"] != []):
        raise ValueError("settings/capability/deadline drift")
    endpoints, data, archives, _ = source_candidate(release["source_run"], release["endpoint_id"])
    bundle = prior.read_json(ROOT / prior.FIXTURES)
    check_selection(release["endpoint_id"], release["historical_inventory"])
    if (release["endpoints"] != endpoints or release["schedule"] != prior.build_schedule(bundle)
            or release["available_sources"] != [release["endpoint_id"]]
            or release["wave_source_assignment"] != [release["endpoint_id"]]
            or release["wave_planned_calls"] != 80 or release["planned_research_calls"] != 480
            or release["maximum_additional_research_calls"] != 240
            or release["estimator_archives"] != archives
            or release["limits"] != limits_for(release["historical_inventory"])
            or release["initial_request_hashes"] != prior.initial_request_hashes(release, bundle, data)):
        raise ValueError("assignment, endpoint, request or allocation drift")
    if require_audit:
        audit = prior.read_json(Path(path).with_suffix(".audit.json"))
        if (audit.get("status") != "passed" or audit.get("release_hash") != expected
                or set(audit.get("domains", [])) != {"science", "accounting", "security"}
                or len(audit.get("evidence", [])) < 3):
            raise ValueError("independent exact-release audit required")
        prior.verify_files({e["path"]: e["sha256"] for e in audit["evidence"]})
    return release, bundle, data


def run(path):
    import httpx
    from .decisions import execute_decision
    from .analysis import summarize
    from experiments.induction_v3.pacing import KnownReplayTokenEstimator, SlidingWindowPacer, PacedOfficialTransport
    from experiments.induction_v3.transport import load_authorized_key
    release, bundle, endpoints = validate(path)
    check_deadline(); feasibility(release["capability_lineage"])
    directory = ROOT / release["run_directory"]
    if directory.exists():
        raise ValueError("single-use successor run already exists")
    with ProgramLock(LOCK) as lock:
        release, bundle, endpoints = validate(path)
        if historical_inventory(lock) != release["historical_inventory"]:
            raise ValueError("historical allocation changed")
        check_deadline()
        estimator = KnownReplayTokenEstimator.from_archives(release["estimator_archives"], root=ROOT, encoding=prior.load_encoding())
        cases = {c["public"]["fixture_id"]: c for c in bundle["cases"]}
        for cell in release["schedule"]:
            if cell["cell_id"] in release["initial_request_hashes"]:
                h = prior.branch_history(endpoints[cell["endpoint_id"]]["histories"], cell["reviewer"], cell["arm_id"])
                estimator(prior.request_body(h, cell, cases))
        pacer = SlidingWindowPacer(**release["pacing"], estimator=estimator)
        journal = DeadlineJournal(directory, program_lock=lock, source_root=ROOT,
            binding={"run_id": release["run_id"], "release_hash": release["release_hash"], "model": MODEL,
                     "protocol_id": PROTOCOL, "source_run": release["source_run"]},
            source_inventory=release["source_inventory"], expected_history_hash=release["historical_inventory_hash"],
            **{k: release["limits"][k] for k in ("campaign_limit_usd", "lifetime_limit_usd", "max_attempts", "max_in_flight")})
        atomic_json(directory / "release.json", release)
        atomic_json(directory / "scheduled_cells.json", release["schedule"])
        atomic_json(directory / "capability_reused.json", release["capability_lineage"])
        results, mutex = [], threading.Lock()
        def progress(stage):
            with mutex:
                atomic_json(directory / "STATUS.json", {"status": stage, "updated_at": utc_now(),
                    "research_completed": len(results), "research_valid": sum(r["valid"] for r in results),
                    "wave_planned_calls": 80, "full_planned_calls": 480, "endpoint_id": release["endpoint_id"],
                    "dispatch_cutoff": DISPATCH_CUTOFF, "ledger": journal.snapshot(), "pacing": pacer.snapshot()})
        def perform(cell, history):
            journal.assert_dispatchable()
            expected = release["initial_request_hashes"].get(cell["cell_id"])
            if expected and digest(prior.request_body(history, cell, cases)) != expected:
                raise ValueError("frozen initial request changed")
            transport.context(run_id=release["run_id"], protocol_id=PROTOCOL,
                **{k: cell[k] for k in ("cell_id", "phase", "reviewer", "fixture_id", "endpoint_id", "arm_id")})
            value = execute_decision(transport=transport, history=history, case=cases[cell["fixture_id"]],
                reviewer=cell["reviewer"], evidence_mode=cell["evidence_mode"], episode_number=cell["episode_number"])
            if expected and value["provenance"]["request_hash"] != expected:
                raise ValueError("dispatched initial request changed")
            value.update(cell_id=cell["cell_id"], cell=copy.deepcopy(cell))
            decision_path = directory / "decisions" / (digest(cell["cell_id"]) + ".json")
            atomic_json(decision_path, value)
            # This timestamp is taken only after the response's first fsync.
            # A concurrent freeze excludes the brief untimestamped first write.
            value["durably_recorded_at"] = utc_now()
            atomic_json(decision_path, value)
            with mutex:
                results.append(value)
            progress("research_running")
            return value
        client = None
        deadline_seen = False
        try:
            client = DeadlineHTTPClient(httpx.Client(trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(180.0, connect=20.0)))
            transport = PacedOfficialTransport(journal, directory, load_authorized_key(), pacer=pacer, http_client=client)
            progress("research_running")
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(prior.execute_branch, cells, endpoints, perform, lambda: journal.stopped)
                           for cells in prior.group_jobs(release["schedule"], endpoints)]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as error:
                        if isinstance(error, DispatchStopped) and str(error) == "deadline_no_new_dispatch":
                            deadline_seen = True
                        else:
                            journal.stop("successor_dispatch_stopped")
                        atomic_json(directory / "last_failure.json", {"type": type(error).__name__, "at": utc_now()})
            if deadline_seen:
                journal.stop("deadline_no_new_dispatch_after_drain")
            journal.finish("partial" if journal.stopped or len(results) != 80 or not all(r["valid"] for r in results) else "completed")
        except Exception as error:
            if not any(e["kind"] == "terminal" for e in journal.events):
                journal.stop("successor_failure"); journal.finish("blocked")
            atomic_json(directory / "last_failure.json", {"type": type(error).__name__, "at": utc_now()})
        finally:
            if client:
                client.close()
        available = {e["endpoint_id"] for e in release["endpoints"] if e["status"] == "completed"}
        report = summarize([dict(c, source_available=c["endpoint_id"] in available) for c in release["schedule"]], results)
        report.update(release_hash=release["release_hash"], run_directory=release["run_directory"],
            wave_source=release["endpoint_id"], wave_complete=len(results) == 80 and all(r["valid"] for r in results),
            full_study_complete=False, primary_freeze_target=PRIMARY_FREEZE,
            primary_freeze_automatically_applied=False)
        atomic_json(directory / "comparison.json", report)
        atomic_json(directory / "verification.json", verify_run(directory, source_root=ROOT, program_lock=lock))
        progress("wave_finished")
        return {"run_directory": release["run_directory"], "research_responses": len(results),
                "wave_complete": report["wave_complete"], "ledger": journal.snapshot()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "validate", "run"))
    parser.add_argument("--release", required=True)
    parser.add_argument("--source-run")
    parser.add_argument("--endpoint-id", choices=PRIORITY)
    args = parser.parse_args()
    if args.command == "prepare":
        if not args.source_run or not args.endpoint_id:
            parser.error("prepare requires --source-run and --endpoint-id")
        result = prepare(args.release, source_run=args.source_run, endpoint_id=args.endpoint_id)
    elif args.command == "validate":
        result = {"status": "passed", "release_hash": validate(args.release)[0]["release_hash"]}
    else:
        result = run(args.release)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
