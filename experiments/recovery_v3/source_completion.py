"""Additive source-history completion for the user-requested final recovery batch.

Old runs and their schedules remain immutable. Selected unseeded development
trajectories continue to their original main-10 endpoint with exact cached replay.
Only one process owns the shared ledger/lock/pacer; there are no provider retries.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import copy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import threading
import uuid

from experiments.induction_v3.common import ROOT, VENDOR, MODEL, atomic_json, digest, file_hash, utc_now
from experiments.induction_v3.liability_accounting import (ProgramLock, LiabilityReservationJournal,
    historical_inventory, historical_totals, inventory_hash, verify_run, liability_anchor)
from experiments.induction_v3.resumption import PACING, runtime, load_encoding, episode_jobs
from experiments.induction_v3.schedule import policy_for
from experiments.induction_v3.transport import DispatchStopped
from . import runner as prior

SOURCE_RUN = prior.SOURCE_RUN
DEFAULT_RELEASE = ROOT / "release/recovery-v3/source-completion.json"
COMMON_SOURCES = ("development-warmup_raw-1", "development-warmup_summary-1", "development-reference-2")
ELIGIBLE_SOURCES = COMMON_SOURCES + ("development-warmup_raw-2", "development-warmup_summary-2")
AUTHORIZATION = "reports/recovery-v3/reduced/SOURCE_COMPLETION_AUTHORIZATION.md"
PROTOCOL = "recovery-v3-source-completion"
DISPATCH_CUTOFF = "2026-10-26T11:47:00+00:00"


class CooperativeJournal(LiabilityReservationJournal):
    """A prospective stop receipt stops queued work before any new reservation."""
    def _check_admission(self):
        # Admission closes without marking the journal stopped: active HTTP
        # responses must still settle and decode normally.
        if ((self.path / "STOP_REQUESTED").exists()
                or datetime.now(timezone.utc) >= datetime.fromisoformat(DISPATCH_CUTOFF)):
            raise DispatchStopped("source_completion_admission_closed")

    def assert_dispatchable(self):
        self._check_admission()
        super().assert_dispatchable()
        self._check_admission()

    def _check_capacity(self, reservation):
        super()._check_capacity(reservation)
        self._check_admission()


def source_plan(ids):
    if not ids or len(ids) != len(set(ids)) or any(i not in ELIGIBLE_SOURCES for i in ids):
        raise ValueError("source selection must be a unique fixed eligible subset")
    original = prior.read_json(ROOT / SOURCE_RUN / "release.json")
    rows = []
    for identity in ids:
        recipe_id, index = identity[len("development-"):].rsplit("-", 1)
        recipe = next(r for r in original["recipes"] if r["recipe_id"] == recipe_id)
        schedule = next(s for s in original["schedules"]["development"] if s["dyad_index"] == int(index))
        path = ROOT / SOURCE_RUN / "dyads" / (identity + ".json")
        old = prior.read_json(path) if path.exists() else None
        if old and (old["dyad_id"] != identity or old["recipe_id"] != recipe_id or old["phase"] != "development"):
            raise ValueError("source identity drift")
        count = len(old["episodes"]) if old else 0
        jobs = episode_jobs(recipe, schedule)
        if count >= len(jobs):
            raise ValueError("completed source cannot be recollected")
        rows.append({"dyad_id": identity, "recipe_id": recipe_id, "dyad_index": int(index),
            "recipe": recipe, "schedule": schedule, "completed_prefix_episodes": count,
            "remaining_episodes": len(jobs) - count,
            "source_path": str(path.relative_to(ROOT)) if old else None,
            "source_sha256": file_hash(path) if old else None})
    return rows


def archive_bindings():
    from .endpoints import discover_response_archives
    value = discover_response_archives(root=ROOT, run_dir=ROOT / SOURCE_RUN)
    return value["response_bindings"], {**value["run_release_bindings"], **value["request_bindings"]}


def limits_for(history):
    totals = historical_totals(history)
    # Leave the full newly requested recovery batch's 250 physical slots unused.
    attempts = min(2000, totals["remaining_v3_attempts"] - 250)
    money = min(Decimal(25), Decimal(totals["remaining_v3_usd"]), Decimal(totals["remaining_lifetime_usd"]))
    if attempts < 1 or money <= 0:
        raise ValueError("insufficient shared allocation for source completion")
    return {"campaign_limit_usd": str(money), "max_attempts": attempts,
            "lifetime_limit_usd": "250", "max_in_flight": 3}


def audit_check(path, release):
    value = prior.read_json(Path(path).with_suffix(".audit.json"))
    if (value.get("status") != "passed" or value.get("release_hash") != release["release_hash"]
            or set(value.get("domains", [])) != {"science", "accounting", "security"}
            or len(value.get("evidence", [])) < 3):
        raise ValueError("exact-release independent reviews required")
    prior.verify_files({e["path"]: e["sha256"] for e in value["evidence"]})


def prepare(path, ids=COMMON_SOURCES):
    from .continuation_replay import replay_episode
    path = Path(path)
    if path.exists():
        raise ValueError("immutable source-completion release already exists")
    # Prior recovery clearance is lineage, not permission to rerun its cells.
    old_release, _, _ = prior.validate(prior.DEFAULT_RELEASE)
    original = prior.read_json(ROOT / SOURCE_RUN / "release.json")
    plan = source_plan(ids)
    proof_bindings = {}
    for row in plan:
        if not row["completed_prefix_episodes"]:
            continue
        proof = replay_episode(ROOT / SOURCE_RUN, row["dyad_id"])
        output = ROOT / "reports/recovery-v3/reduced" / ("replay-" + row["dyad_id"] + ".json")
        if output.exists():
            if prior.read_json(output) != proof:
                raise ValueError("existing offline replay proof drifted")
        else:
            atomic_json(output, proof)
        proof_bindings[row["dyad_id"]] = {"path": str(output.relative_to(ROOT)), "sha256": file_hash(output)}
    responses, requests = archive_bindings()
    with ProgramLock(prior.LOCK) as lock:
        history = historical_inventory(lock)
        inventory = dict(old_release["source_inventory"])
        inventory.update(responses); inventory.update(requests)
        names = [AUTHORIZATION, "experiments/recovery_v3/source_completion.py",
                 "experiments/recovery_v3/continuation_replay.py",
            "tests/recovery_v3/test_source_completion.py", "tests/recovery_v3/test_continuation_replay.py",
                 "docs/RECOVERY_V3_DEADLINE_CONTINUATION.md",
                 "release/recovery-v3/wave-1.json", "release/recovery-v3/wave-1.audit.json",
                 SOURCE_RUN + "/manifest.json", SOURCE_RUN + "/journal.jsonl"]
        names += [r["source_path"] for r in plan if r["source_path"]]
        names += [p["path"] for p in proof_bindings.values()]
        for name in names:
            inventory[name] = file_hash(ROOT / name)
        prior.verify_files(inventory)
        run_id = "induction-v3-source-completion-" + str(uuid.uuid4())
        release = {"schema_version": 1, "protocol_id": PROTOCOL, "prepared_at": utc_now(),
            "run_id": run_id, "run_directory": "runs/empirical/" + run_id,
            "source_run": SOURCE_RUN, "source_release_hash": original["release_hash"],
            "prior_recovery_run": old_release["run_directory"], "prior_recovery_release_hash": old_release["release_hash"],
            "selected_sources": list(ids), "plan": plan, "replay_proofs": proof_bindings,
            "source_inventory": inventory, "estimator_archives": responses,
            "runtime_versions": runtime(), "pacing": PACING, "model_settings": prior.SETTINGS,
            "sandbox_image": original["sandbox_image"], "capability_condition": original["capability_condition"],
            "dispatch_cutoff": DISPATCH_CUTOFF,
            "historical_inventory": history, "historical_inventory_hash": inventory_hash(history),
            "carried_liability": liability_anchor(), "limits": limits_for(history),
            "planned_new_episodes": sum(r["remaining_episodes"] for r in plan),
            "claim_label": "exploratory source-history completion; no induction-validation claim",
            "no_seeded_or_validation_dispatch": True}
        release["release_hash"] = digest(release)
        atomic_json(path, release)
    return {"release_hash": release["release_hash"], "run_id": run_id,
            "planned_new_episodes": release["planned_new_episodes"]}


def validate(path, *, require_audit=True):
    release = prior.read_json(path); body = dict(release); expected = body.pop("release_hash")
    if digest(body) != expected or release["protocol_id"] != PROTOCOL or release["source_run"] != SOURCE_RUN:
        raise ValueError("source-completion release identity/digest drift")
    if release["runtime_versions"] != runtime() or release["pacing"] != PACING or release["model_settings"] != prior.SETTINGS:
        raise ValueError("source-completion runtime/settings drift")
    if release.get("dispatch_cutoff") != DISPATCH_CUTOFF:
        raise ValueError("source-completion deadline drift")
    prior.verify_files(release["source_inventory"])
    if release["plan"] != source_plan(release["selected_sources"]):
        raise ValueError("fixed selected-source plan drift")
    if release["limits"] != limits_for(release["historical_inventory"]) or release["carried_liability"] != liability_anchor():
        raise ValueError("shared cap or liability drift")
    if require_audit:
        audit_check(path, release)
    return release


def run_dyad(row, release, transport, episode_module, progress):
    from experiments.induction_v3 import engine
    from experiments.induction_v3.continuation import conditioned_history
    from experiments.models import AgentConfig, EpisodeRunConfig
    identity = row["dyad_id"]
    old = prior.read_json(ROOT / row["source_path"]) if row["source_path"] else None
    result = {"dyad_id": identity, "recipe_id": row["recipe_id"], "phase": "development",
              "synthetic_seed": False, "status": "running", "episodes": copy.deepcopy(old["episodes"]) if old else [],
              "carried_prefix": {"source_path": row["source_path"], "source_sha256": row["source_sha256"],
                                  "episodes": row["completed_prefix_episodes"], "new_observations": False}}
    histories = ({a: copy.deepcopy(result["episodes"][-1]["agents"][a]["messages"]) for a in ("alice", "bob")}
                 if result["episodes"] else conditioned_history(engine.initial_history(row["recipe"]), release["capability_condition"]))
    output = ROOT / release["run_directory"] / "dyads" / (identity + ".json")
    atomic_json(output, result)
    first = len(result["episodes"])
    try:
        for index, (phase, pair) in enumerate(episode_jobs(row["recipe"], row["schedule"])):
            if index < first:
                continue
            transport.context(dyad_id=identity, recipe_id=row["recipe_id"], campaign_phase=phase,
                              episode=index + 1, resume_run_id=release["run_id"])
            replay = nullcontext()
            if old and index == first:
                proof = prior.read_json(ROOT / release["replay_proofs"][identity]["path"])
                replay = transport.episode_replay(ROOT / SOURCE_RUN, old, proof, histories=histories)
            config = EpisodeRunConfig(**policy_for(row["recipe"], phase), temp_run_id=identity,
                episode_index=index, verbose=False, reflection=True, reward=True, verdict_review=True)
            with replay:
                value = episode_module.run_one_episode(pair=pair, agent_configs={a: AgentConfig(MODEL, "high") for a in histories},
                    repo_root=VENDOR, config=config, agent_messages=histories)
            value["campaign_phase"] = phase
            value["v3_metrics"] = engine.metrics(value)
            result["episodes"].append(value)
            atomic_json(output, result)
            progress(identity, index + 1)
        result["status"] = "completed"
    except Exception as error:
        result.update(status="stopped", stop_type=type(error).__name__, incomplete_histories=histories)
        raise
    finally:
        atomic_json(output, result)
    return result


def run(path):
    from experiments.induction_v3 import engine
    from experiments.induction_v3.isolation import DockerSandbox
    from experiments.induction_v3.pacing import KnownReplayTokenEstimator, SlidingWindowPacer, PacedOfficialTransport
    from experiments.induction_v3.transport import load_authorized_key
    from experiments.induction_v3.replay import install_replay_hooks
    from .continuation_replay import replay_episode, ContinuationReplayTransport
    release = validate(path); run_dir = ROOT / release["run_directory"]
    if run_dir.exists():
        raise ValueError("single-use source-completion run already exists")
    with ProgramLock(prior.LOCK) as lock:
        release = validate(path)
        if historical_inventory(lock) != release["historical_inventory"]:
            raise ValueError("historical allocation changed")
        for identity, binding in release["replay_proofs"].items():
            if replay_episode(ROOT / SOURCE_RUN, identity) != prior.read_json(ROOT / binding["path"]):
                raise ValueError("reconstructed exact replay checkpoint drift")
        sandbox = DockerSandbox(release["sandbox_image"]); sandbox.verify()
        estimator = KnownReplayTokenEstimator.from_archives(release["estimator_archives"], root=ROOT, encoding=load_encoding())
        pacer = SlidingWindowPacer(**PACING, estimator=estimator)
        journal = CooperativeJournal(run_dir, program_lock=lock, source_root=ROOT,
            binding={"run_id": release["run_id"], "release_hash": release["release_hash"], "model": MODEL,
                     "protocol_id": PROTOCOL, "source_run": SOURCE_RUN},
            source_inventory=release["source_inventory"], expected_history_hash=release["historical_inventory_hash"], **release["limits"])
        atomic_json(run_dir / "release.json", release)
        report_lock = threading.Lock()
        completed = []
        failures = []
        def progress(identity=None, count=None):
            with report_lock:
                atomic_json(run_dir / "STATUS.json", {"status": "stopped" if journal.stopped else "source_completion_running",
                    "updated_at": utc_now(), "selected_sources": release["selected_sources"],
                    "latest_completed_episode": {"dyad_id": identity, "episode": count},
                    "completed_sources": sorted(completed), "ledger": journal.snapshot(), "pacing": pacer.snapshot()})
        uninstall = None
        try:
            delegate = PacedOfficialTransport(journal, run_dir, load_authorized_key(), pacer=pacer)
            transport = ContinuationReplayTransport(delegate, resume_run_id=release["run_id"])
            episode = engine.install_adapters(sandbox, transport, run_dir / "scratch")
            uninstall = install_replay_hooks(transport)
            progress()
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {pool.submit(run_dyad, row, release, transport, episode, progress): row for row in release["plan"]}
                for future in as_completed(futures):
                    try:
                        value = future.result()
                        with report_lock:
                            completed.append(value["dyad_id"])
                    except Exception as error:
                        failures.append(type(error).__name__)
                        atomic_json(run_dir / ("failure-" + futures[future]["dyad_id"] + ".json"),
                            {"type": type(error).__name__, "at": utc_now()})
            if failures and not journal.stopped:
                journal.stop("source_completion_trajectory_failure")
            journal.finish("partial" if journal.stopped else "completed")
        except Exception as error:
            if not any(e["kind"] == "terminal" for e in journal.events):
                journal.stop("source_completion_failure"); journal.finish("blocked")
            atomic_json(run_dir / "failure.json", {"type": type(error).__name__, "at": utc_now()})
        finally:
            if uninstall:
                uninstall()
        verification = verify_run(run_dir, source_root=ROOT, program_lock=lock)
        atomic_json(run_dir / "verification.json", verification)
        atomic_json(run_dir / "STATUS.json", {"status": "source_completion_finished", "completed_sources": sorted(completed),
            "selected_sources": release["selected_sources"], "ledger": journal.snapshot(), "updated_at": utc_now()})
        return {"run_directory": release["run_directory"], "completed_sources": sorted(completed), "ledger": journal.snapshot()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "validate", "run"))
    parser.add_argument("--release", default=str(DEFAULT_RELEASE))
    parser.add_argument("--sources", nargs="+", default=list(COMMON_SOURCES))
    args = parser.parse_args()
    if args.command == "prepare": value = prepare(args.release, tuple(args.sources))
    elif args.command == "validate": value = {"status": "passed", "release_hash": validate(args.release)["release_hash"]}
    else: value = run(args.release)
    print(json.dumps(value, indent=2))


if __name__ == "__main__":
    main()
