"""One additive, exact-replay continuation of the fixed V3 trajectories.

This release never reopens the stopped journal or resamples cached responses.
New requests retain the original protocol and receive distinct physical IDs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
import copy
import importlib.metadata
import json
import os
from pathlib import Path
import threading
import uuid
from unittest.mock import patch

from .common import ROOT, VENDOR, MODEL, RELEASE, REPORTS, atomic_json, digest, file_hash, utc_now
from .cli import verify_inventory, runtime_versions, comparison
from .continuation import validate as validate_continuation, conditioned_history
from .liability_accounting import (ProgramLock, LiabilityReservationJournal, historical_inventory,
                                  historical_totals, inventory_hash, liability_anchor, verify_run)
from .schedule import qualifies, policy_for


PACING = {"capacity": 1_000_000, "window_seconds": 60, "max_in_flight": 3,
          "max_wait_seconds": 300, "initial_delay_seconds": 60}
NEW_SOURCES = ("resumption", "replay", "liability_accounting", "pacing")
TOKENIZER_FILE = "release/induction-v3/tokenizer/fb374d419588a4632f3f557e76b4b70aebbca790"
TOKENIZER_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"


def load_encoding():
    """Use the frozen vocabulary and fail instead of fetching missing cache data."""
    import tiktoken
    import tiktoken.load
    path = ROOT / TOKENIZER_FILE
    if path.is_symlink() or file_hash(path) != TOKENIZER_SHA256:
        raise ValueError("frozen tokenizer vocabulary drift")
    def deny(*args, **kwargs):
        raise ValueError("tokenizer network or fallback file fetch is forbidden")
    with patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": str(path.parent)}), patch.object(tiktoken.load, "read_file", deny):
        return tiktoken.get_encoding("o200k_base")


def runtime():
    return {**runtime_versions(), "tiktoken": importlib.metadata.version("tiktoken")}


def episode_jobs(recipe, schedule):
    return ([("warmup", p) for p in schedule["warmup"]] if recipe["warmup"]["episodes"] else []) + [
        ("main", p) for p in schedule["main"]]


def source_plan(original, source_dir, proofs):
    """Every original dyad appears exactly once, with explicit prefix provenance."""
    events = [json.loads(line) for line in (source_dir / "journal.jsonl").read_text().splitlines()]
    called = {e["metadata"]["dyad_id"] for e in events if e["kind"] == "reservation"}
    rows = []
    for schedule in original["schedules"]["development"]:
        for recipe in original["recipes"]:
            identity = f"development-{recipe['recipe_id']}-{schedule['dyad_index']}"
            path = source_dir / "dyads" / (identity + ".json")
            prior = json.loads(path.read_text())
            if prior["dyad_id"] != identity or prior["recipe_id"] != recipe["recipe_id"] or prior["phase"] != "development":
                raise ValueError("source dyad identity drift")
            count = len(prior["episodes"])
            if not 0 <= count < len(episode_jobs(recipe, schedule)) or prior["status"] != "stopped":
                raise ValueError("requires the exact terminal partial source")
            proof = proofs.get(identity)
            if (identity in called) != bool(proof) or bool(count) != bool(proof):
                raise ValueError("started dyad requires exact replay proof")
            if proof and (proof["episode"] != count + 1 or proof["dyad_id"] != identity
                          or proof["status"] != "passed" or proof["provider_calls"] != 0):
                raise ValueError("invalid interrupted episode proof")
            rows.append({"dyad_id": identity, "recipe_id": recipe["recipe_id"],
                         "dyad_index": schedule["dyad_index"], "phase": "development",
                         "completed_prefix_episodes": count, "source_artifact": str(path.relative_to(ROOT)),
                         "source_artifact_sha256": file_hash(path), "replay_required": bool(proof)})
    if set(proofs) != called or len(rows) != 8 or len({r["dyad_id"] for r in rows}) != 8:
        raise ValueError("resumption must contain exactly the original eight trajectories")
    return rows


def prepare(path):
    path = Path(path)
    if path.exists(): raise ValueError("resumption release already exists")
    original = validate_continuation(RELEASE / "continuation.json")
    source_dir = ROOT / original["run_directory"]
    proof_files = sorted(REPORTS.glob("replay-final-*.json"))
    proofs = {json.loads(p.read_text())["dyad_id"]: json.loads(p.read_text()) for p in proof_files}
    if len(proofs) != 3: raise ValueError("three final offline replay proofs required")
    plan = source_plan(original, source_dir, proofs)
    with ProgramLock(ROOT / "runs/empirical/.controlled-peer-recovery-program.lock") as lock:
        history = historical_inventory(lock)
    inventory = dict(original["source_inventory"])
    # Exact original source plus every terminal source artifact used by replay,
    # estimation, provenance or historical accounting is immutable.
    for p in source_dir.rglob("*"):
        if p.is_file() and "scratch" not in p.relative_to(source_dir).parts:
            if p.is_symlink(): raise ValueError("symlink in source artifacts")
            inventory[str(p.relative_to(ROOT))] = file_hash(p)
    names = ["release/induction-v3/continuation.json", "release/induction-v3/CONTINUATION_AUDIT.json",
             "reports/induction-v3/execution/RESUMPTION_DECISION.md", TOKENIZER_FILE]
    names += [f"experiments/induction_v3/{name}.py" for name in NEW_SOURCES]
    names += [f"tests/induction_v3/test_{name}.py" for name in NEW_SOURCES]
    names += [f"tests/induction_v3/test_resumption_{domain}.py" for domain in ("accounting", "security")]
    names += [str(p.relative_to(ROOT)) for p in proof_files]
    for name in names: inventory[name] = file_hash(ROOT / name)
    archives = {str(p.relative_to(ROOT)): file_hash(p) for p in (source_dir / "responses").glob("*.json")
                if "body" in json.loads(p.read_text())}
    run_id = "induction-v3-resumption-" + str(uuid.uuid4())
    release = {"schema_version": 1, "kind": "exact_cached_replay_resumption", "prepared_at": utc_now(),
        "campaign_id": original["campaign_id"], "run_id": run_id, "run_directory": "runs/empirical/" + run_id,
        "source_run": original["run_directory"], "source_release_hash": original["release_hash"],
        "source_inventory": inventory, "runtime_versions": runtime(), "historical_inventory": history,
        "historical_inventory_hash": inventory_hash(history), "prior_totals": historical_totals(history),
        "carried_liability": liability_anchor(), "pacing": PACING,
        "estimator": "KnownReplayTokenEstimator:tiktoken0.14.0:o200k_base:10percent_headroom",
        "estimator_archives": archives, "maximum_in_flight": 3, "plan": plan,
        "replay_proofs": {identity: {"path": str(p.relative_to(ROOT)), "sha256": file_hash(p)}
                          for p in proof_files for identity in [json.loads(p.read_text())["dyad_id"]]},
        "recipes": original["recipes"], "schedules": original["schedules"], "scoring": original["scoring"],
        "sandbox_image": original["sandbox_image"], "capability_condition": original["capability_condition"],
        "claim_label": original["claim_label"] + "; exact continuation after recorded rate-limit stop",
        "deviations": original["deviations"] + [
            "One HTTP429 stopped the prior dispatcher. It remains an unknown-cost historical liability; its exact request receives at most one explicitly new attempt in this release.",
            "Historical response and tool payload replay is exact. Two already-paid, completed responses arriving after the stop are consumed once. Prior completed episodes are carried observations, not new samples.",
            "New requests are paced before reservation with a shared approximate token window. Request bodies, policies, histories, tasks and output limits are unchanged. No automatic retries."],
        "repair_allowed": False}
    release["release_hash"] = digest(release)
    atomic_json(path, release)
    return {"release": str(path), "release_hash": release["release_hash"], "run_directory": release["run_directory"]}


def validate(path, require_audit=True):
    release = json.loads(Path(path).read_text()); body = dict(release); expected = body.pop("release_hash")
    if digest(body) != expected or release["runtime_versions"] != runtime():
        raise ValueError("resumption release/runtime drift")
    verify_inventory(release["source_inventory"])
    original = validate_continuation(RELEASE / "continuation.json")
    for key in ("recipes", "schedules", "scoring", "sandbox_image", "capability_condition"):
        if release[key] != original[key]: raise ValueError("original design drift: " + key)
    if release["pacing"] != PACING or release["carried_liability"] != liability_anchor():
        raise ValueError("prospective pacing or liability drift")
    if release["source_run"] != original["run_directory"] or release["source_release_hash"] != original["release_hash"]:
        raise ValueError("source lineage drift")
    if require_audit:
        audit = json.loads((RELEASE / "RESUMPTION_AUDIT.json").read_text())
        if audit.get("status") != "passed" or audit.get("release_hash") != expected:
            raise ValueError("independent resumption audit missing")
        if set(audit.get("domains", [])) != {"science", "accounting", "security"}:
            raise ValueError("all three audit domains required")
        verify_inventory({e["path"]: e["sha256"] for e in audit["evidence"]})
    return release


def run_dyad(recipe, schedule, *, phase, release, transport, episode_module, progress=None):
    from . import engine
    from experiments.models import AgentConfig, EpisodeRunConfig
    identity = f"{phase}-{recipe['recipe_id']}-{schedule['dyad_index']}"
    run_dir = ROOT / release["run_directory"]
    result = {"dyad_id": identity, "recipe_id": recipe["recipe_id"], "phase": phase,
              "synthetic_seed": bool(recipe.get("seed")), "status": "running", "episodes": []}
    histories = conditioned_history(engine.initial_history(recipe), release["capability_condition"])
    prior = None
    if phase == "development":
        planned = next(r for r in release["plan"] if r["dyad_id"] == identity)
        prior = json.loads((ROOT / planned["source_artifact"]).read_text())
        result["episodes"] = copy.deepcopy(prior["episodes"])
        result["carried_prefix"] = {"episodes": len(prior["episodes"]), "source_artifact": planned["source_artifact"],
                                    "sha256": planned["source_artifact_sha256"], "new_observations": False}
        if prior["episodes"]:
            histories = {a: copy.deepcopy(prior["episodes"][-1]["agents"][a]["messages"]) for a in histories}
    first = len(result["episodes"])
    output = run_dir / "dyads" / (identity + ".json")
    atomic_json(output, result)
    try:
        for index, (campaign_phase, pair) in enumerate(episode_jobs(recipe, schedule)):
            if index < first: continue
            transport.context(dyad_id=identity, recipe_id=recipe["recipe_id"], campaign_phase=campaign_phase,
                              episode=index + 1, resume_run_id=release["run_id"])
            context = nullcontext()
            if prior and prior["episodes"] and index == first:
                proof = json.loads((ROOT / release["replay_proofs"][identity]["path"]).read_text())
                context = transport.episode_replay(ROOT / release["source_run"], prior, proof, histories=histories)
            config = EpisodeRunConfig(**policy_for(recipe, campaign_phase), temp_run_id=identity,
                episode_index=index, verbose=False, reflection=True, reward=True, verdict_review=True)
            with context:
                value = episode_module.run_one_episode(pair=pair,
                    agent_configs={a: AgentConfig(model=MODEL, reasoning_effort="high") for a in histories},
                    repo_root=VENDOR, config=config, agent_messages=histories)
            value["campaign_phase"] = campaign_phase
            value["v3_metrics"] = engine.metrics(value)
            result["episodes"].append(value)
            atomic_json(output, result)
            if progress: progress(identity, index + 1)
        result["status"] = "completed"
    except Exception as error:
        result.update(status="stopped", stop_type=type(error).__name__, incomplete_histories=histories)
        raise
    finally:
        atomic_json(output, result)
    return result


def run(path):
    from . import engine
    from .isolation import DockerSandbox
    from .transport import load_authorized_key
    from .pacing import KnownReplayTokenEstimator, SlidingWindowPacer, PacedOfficialTransport
    from .replay import replay_episode, ReplayTransport, install_replay_hooks
    release = validate(path); run_dir = ROOT / release["run_directory"]
    if run_dir.exists(): raise ValueError("single-use resumption already exists")
    with ProgramLock(ROOT / "runs/empirical/.controlled-peer-recovery-program.lock") as lock:
        release = validate(path)
        history = historical_inventory(lock)
        if history != release["historical_inventory"] or inventory_hash(history) != release["historical_inventory_hash"]:
            raise ValueError("historical allocation drift")
        proofs = {}
        # Prove all live handoffs before loading credentials or creating a ledger.
        for identity, binding in release["replay_proofs"].items():
            proof = json.loads((ROOT / binding["path"]).read_text())
            fresh = replay_episode(ROOT / release["source_run"], identity)
            if fresh != proof: raise ValueError("offline replay proof drift: " + identity)
            proofs[identity] = proof
        original = json.loads((ROOT / release["source_run"] / "release.json").read_text())
        if source_plan(original, ROOT / release["source_run"], proofs) != release["plan"]:
            raise ValueError("resumption plan drift")
        sandbox = DockerSandbox(release["sandbox_image"]); sandbox.verify()
        estimator = KnownReplayTokenEstimator.from_archives(release["estimator_archives"], root=ROOT,
            encoding=load_encoding())
        pacer = SlidingWindowPacer(**release["pacing"], estimator=estimator)
        journal = LiabilityReservationJournal(run_dir, program_lock=lock, source_root=ROOT,
            binding={"run_id": release["run_id"], "release_hash": release["release_hash"], "model": MODEL,
                     "campaign_id": release["campaign_id"], "source_run": release["source_run"]},
            source_inventory=release["source_inventory"], expected_history_hash=release["historical_inventory_hash"])
        atomic_json(run_dir / "release.json", release)
        jobs = [(r, s) for s in release["schedules"]["development"] for r in release["recipes"]]
        atomic_json(run_dir / "scheduled_dyads.json", [{"recipe_id": r["recipe_id"], "dyad_index": s["dyad_index"],
            "phase": "development", "main_scheduled": 10, "warmup_scheduled": r["warmup"]["episodes"]} for r,s in jobs])
        report_lock = threading.Lock()
        def update(stage, **extra):
            with report_lock:
                atomic_json(REPORTS / "STATUS.json", {"status": stage, "updated_at": utc_now(),
                    "active_run": release["run_directory"], "active_release": str(Path(path).resolve().relative_to(ROOT)),
                    "source_run": release["source_run"], "ledger": journal.snapshot(), "pacing": pacer.snapshot(),
                    "original_gate_passed": False, "diagnostic_original_correct": 6, "diagnostic_original_total": 6,
                    "capability_condition": release["capability_condition"], "repair_started": False, **extra})
        summaries = []
        uninstall = None
        try:
            delegate = PacedOfficialTransport(journal, run_dir, load_authorized_key(), pacer=pacer)
            transport = ReplayTransport(delegate, resume_run_id=release["run_id"])
            episode_module = engine.install_adapters(sandbox, transport, run_dir / "scratch")
            uninstall = install_replay_hooks(transport)
            def execute(recipe, schedule, phase):
                return run_dyad(recipe, schedule, phase=phase, release=release, transport=transport,
                    episode_module=episode_module, progress=lambda dyad, n: update(phase + "_running",
                    latest_completed_episode={"dyad_id": dyad, "episode": n}))
            def batch(pairs, phase, workers):
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(execute, r, s, phase) for r,s in pairs]
                    for future in as_completed(futures):
                        try: summaries.append(future.result())
                        except Exception as exc:
                            journal.stop(phase + "_trajectory_stopped")
                            update("stopping_after_trajectory_failure", failure_type=type(exc).__name__)
            update("development_running")
            batch(jobs, "development", 3)
            if not journal.stopped:
                selected = next((name for name in ("reference", "warmup_raw", "warmup_summary")
                    if len([r for r in summaries if r["recipe_id"] == name]) == 2 and
                    all(qualifies(r["episodes"]) for r in summaries if r["recipe_id"] == name)), None)
                atomic_json(run_dir / "validation_selection.json", {"selected": selected, "criterion": release["scoring"]["validation_selection"]})
                if selected:
                    recipe = next(r for r in release["recipes"] if r["recipe_id"] == selected)
                    batch([(recipe, s) for s in release["schedules"]["validation"]], "validation", 2)
            journal.finish("partial" if journal.stopped else "completed")
        except Exception as exc:
            if not any(e["kind"] == "terminal" for e in journal.events):
                journal.stop("resumption_dispatch_stopped"); journal.finish("blocked")
            update("resumption_blocked", failure_type=type(exc).__name__)
        finally:
            if uninstall: uninstall()
        result = comparison(run_dir)
        for row in result["dyads"]:
            if row["main_completed"] < 10: row["feasibility_criterion"] = None
        result.update(claim_label=release["claim_label"], source_run=release["source_run"],
                      carried_observations_not_new_samples=True, original_gate_passed=False)
        atomic_json(run_dir / "comparison.json", result)
        atomic_json(REPORTS / "resumption-comparison.json", result)
        atomic_json(REPORTS / "resumption-verification.json", verify_run(run_dir, source_root=ROOT, program_lock=lock))
        update("resumption_" + result["status"])
        return result


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--release", default=str(RELEASE / "resumption.json")); args = parser.parse_args()
    print(json.dumps(prepare(args.release) if args.command == "prepare" else run(args.release), indent=2))


if __name__ == "__main__": main()
