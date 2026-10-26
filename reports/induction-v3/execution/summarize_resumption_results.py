"""Read-only V3 resumption recount; writes only resumption-numerical-results.*.

No program lock, key, provider, participant-code execution, or frozen artifact
write. Reuses the independent outcome predicates from the earlier report, not
engine.metrics. A new manifest is read after dyad snapshots so their usage IDs
can be checked against an equal-or-later durable journal prefix.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("independent_prior_recount", DIRECTORY / "summarize_results.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
TERMINAL = {"completed", "partial", "blocked", "failed", "stopped"}
ACTORS = ("alice", "bob")


def read_snapshot(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def check(problems, location, field, actual, stored):
    prior.record_mismatch(problems, location, field, actual, stored)


def legacy_journal(path):
    """Hash-check the two frozen historical journal framings, without opening."""
    raw = path.read_bytes()
    if not raw.endswith(b"\n"): raise ValueError("historical journal has an unfinished line")
    events = [json.loads(line) for line in raw.splitlines()]
    # Earlier orchestration journals use null; controlled-peer/V2 use initial.
    previous = None if path.parent.name.startswith(("gate1-", "gate2-", "gate3-", "stage2-")) else "initial"
    for index, event in enumerate(events):
        body = dict(event); claimed = body.pop("event_hash")
        if (body["sequence"] != index or body["previous_event_hash"] != previous
                or hashlib.sha256(prior.canonical(body).encode()).hexdigest() != claimed):
            raise ValueError("legacy journal hash-chain mismatch: " + path.parent.name)
        previous = claimed
    return events


def historical_accounting(release, problems):
    runs = []; reservations = {}; settlements = {}; calls = {}
    for frozen in release["historical_inventory"]:
        directory = ROOT / "runs" / "empirical" / Path(frozen["manifest_path"]).parent
        manifest, sha = read_snapshot(directory / "manifest.json")
        check(problems, directory.name, "historical_manifest_sha256", sha, frozen["manifest_sha256"])
        ledger = manifest.get("ledger") or manifest["budget"]
        consumed = Decimal(ledger["consumed"]["cost_usd"])
        reserved = Decimal(ledger["reserved"]["cost_usd"])
        check(problems, directory.name, "historical_consumed", ledger["consumed"], frozen["consumed"])
        check(problems, directory.name, "historical_reserved", ledger["reserved"], frozen["reserved"])
        if frozen["is_v3"]:
            events = prior.journal_prefix(directory, manifest)
            recounted = prior.accounting_summary(directory, manifest, set(), problems)
            attempts = recounted["physical_attempts"]
            check(problems, directory.name, "historical_journal_sha256", prior.digest_file(directory / "journal.jsonl"), frozen["journal_sha256"])
            for event in events:
                if event["kind"] == "reservation":
                    rid = event["reservation"]["reservation_id"]
                    if rid in reservations: problems.append({"location": directory.name, "field": "duplicate_historical_reservation_id", "reservation_id": rid})
                    reservations[rid] = {"run_id": directory.name, **event}
                elif event["kind"] == "settlement": settlements[event["reservation_id"]] = event
            for call in recounted["calls"]: calls[call["reservation_id"]] = {"run_id": directory.name, **call}
        else:
            events = legacy_journal(directory / "journal.jsonl")
            attempts = sum(event["kind"] == "reservation" for event in events)
            check(problems, directory.name, "legacy_journal_reservations", attempts,
                  ledger["consumed"]["requests"] + ledger["reserved"]["requests"])
            if "paid_calls" in manifest: check(problems, directory.name, "legacy_paid_calls", attempts, manifest["paid_calls"])
            recounted = None
        runs.append({"run_id": directory.name, "manifest_sha256": sha,
            "journal_sha256": prior.digest_file(directory / "journal.jsonl"), "is_v3": frozen["is_v3"],
            "status": manifest["status"], "consumed_cost_usd": str(consumed), "reserved_cost_usd": str(reserved),
            "exposure_usd": str(consumed + reserved), "journal_reservation_attempts": attempts,
            "usage_cost_recomputed_from_visible_responses": frozen["is_v3"],
            "journal_hash_chain_verified": True,
            "accounting_recount": recounted})
    return runs, reservations, settlements, calls


def capture_dyads(run, release, problems):
    selection_path = run / "validation_selection.json"
    selection, selection_sha = read_snapshot(selection_path) if selection_path.exists() else (None, None)
    selected = selection.get("selected") if selection else None
    plan = [("development", recipe, schedule) for schedule in release["schedules"]["development"] for recipe in release["recipes"]]
    if selected:
        recipe = next(r for r in release["recipes"] if r["recipe_id"] == selected)
        plan += [("validation", recipe, schedule) for schedule in release["schedules"]["validation"]]
    captures = []
    for phase, recipe, schedule in plan:
        identity = f"{phase}-{recipe['recipe_id']}-{schedule['dyad_index']}"
        path = run / "dyads" / (identity + ".json")
        source = None; planned = None
        if phase == "development":
            planned = next(p for p in release["plan"] if p["dyad_id"] == identity)
            source, source_sha = read_snapshot(ROOT / planned["source_artifact"])
            check(problems, identity, "source_dyad_sha256", source_sha, planned["source_artifact_sha256"])
        if path.exists():
            data, sha = read_snapshot(path)
            materialized = True
        else:
            data = {"status": "not_started", "episodes": source["episodes"] if source else []}
            sha = None; materialized = False
        carried_count = len(source["episodes"]) if source else 0
        if source:
            check(problems, identity, "carried_prefix_count", carried_count, planned["completed_prefix_episodes"])
            check(problems, identity, "carried_prefix_exact_equality", data["episodes"][:carried_count] == source["episodes"], True)
            if materialized:
                check(problems, identity, "carried_prefix_provenance", data.get("carried_prefix"),
                    {"episodes": carried_count, "source_artifact": planned["source_artifact"],
                     "sha256": planned["source_artifact_sha256"], "new_observations": False})
        captures.append({"identity": identity, "phase": phase, "recipe": recipe, "schedule": schedule,
            "path": path, "sha256": sha, "data": data, "carried_count": carried_count,
            "artifact_materialized": materialized})
    return captures, selection, selection_sha


def classify_usage(episode, row, dyad, ordinal, carried, old_run_id, old_reservations,
                   new_reservations, settlements, cache_ids, observed, problems):
    counts = Counter(); audit = []
    for usage in episode.get("llm_usage", []):
        rid = usage.get("reservation_id")
        if rid in observed:
            problems.append({"location": dyad, "field": "duplicate_episode_usage_reservation_id", "reservation_id": rid})
        observed.add(rid)
        event = new_reservations.get(rid) or old_reservations.get(rid)
        settlement = settlements.get(rid)
        if event is None or settlement is None:
            problems.append({"location": dyad, "field": "usage_missing_from_durable_journals", "reservation_id": rid})
            counts["unknown"] += 1; continue
        if rid in new_reservations: category = "new_physical_call"
        elif carried: category = "carried_completed_prefix_call"
        elif rid in cache_ids: category = "cached_interrupted_episode_call"
        else: category = "unexpected_historical_call"
        counts[category] += 1
        if category == "unexpected_historical_call" or (carried and rid in new_reservations):
            problems.append({"location": dyad, "field": "unexpected_usage_lineage", "reservation_id": rid, "category": category})
        if rid in old_reservations:
            check(problems, rid, "historical_episode_run", event["run_id"], old_run_id)
        check(problems, rid, "settlement_outcome", settlement["outcome"], "completed")
        metadata = event["metadata"]
        for key, expected in (("dyad_id", dyad), ("episode", ordinal), ("campaign_phase", row["campaign_phase"])):
            check(problems, rid, "metadata." + key, metadata.get(key), expected)
        if settlement["outcome"] == "completed":
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens"):
                check(problems, rid, "episode_usage." + key, usage.get(key, 0), settlement["usage"].get(key, 0))
            check(problems, rid, "episode_usage_cost", str(prior.independent_cost(usage)), str(prior.independent_cost(settlement["usage"])))
        audit.append({"reservation_id": rid, "category": category,
            "settlement_run": event.get("run_id", "current_resumption"),
            "counts_as_new_billable_attempt": category == "new_physical_call"})
    row["usage_lineage_counts"] = dict(counts)
    row["usage_lineage"] = audit


def summarize(run):
    problems = []
    release, release_sha = read_snapshot(run / "release.json")
    body = dict(release); claimed = body.pop("release_hash")
    check(problems, "release", "release_hash", hashlib.sha256(prior.canonical(body).encode()).hexdigest(), claimed)
    captures, selection, selection_sha = capture_dyads(run, release, problems)
    # Every captured dyad is read before this manifest, avoiding usage IDs from
    # episodes newer than the durable accounting prefix selected here.
    manifest, manifest_sha = read_snapshot(run / "manifest.json")
    events = prior.journal_prefix(run, manifest)
    new_reservations = {e["reservation"]["reservation_id"]: e for e in events if e["kind"] == "reservation"}
    new_settlements = {e["reservation_id"]: e for e in events if e["kind"] == "settlement"}
    historical, old_reservations, old_settlements, old_calls = historical_accounting(release, problems)
    expected_directories = sorted([row["run_id"] for row in historical] + [run.name])
    actual_directories = sorted(path.name for path in run.parent.iterdir() if not path.name.startswith("."))
    check(problems, "empirical_inventory", "all_eleven_runs_present", actual_directories, expected_directories)
    duplicates = sorted(set(new_reservations) & set(old_reservations))
    if duplicates: problems.append({"location": "accounting", "field": "reservation_id_reused_across_runs", "reservation_ids": duplicates})
    settlements = {**old_settlements, **new_settlements}
    cache_ids = set(); late_cache_ids = set(); proofs = []
    for identity, frozen in release["replay_proofs"].items():
        proof, sha = read_snapshot(ROOT / frozen["path"])
        check(problems, identity, "replay_proof_sha256", sha, frozen["sha256"])
        ids = [row["reservation_id"] for row in proof["consumed_responses"]]
        if cache_ids.intersection(ids): problems.append({"location": identity, "field": "cached_reservation_repeated_across_proofs"})
        cache_ids.update(ids)
        late = [row["reservation_id"] for row in proof["consumed_responses"] if not row["previously_ingested"]]
        late_cache_ids.update(late)
        for rid in ids:
            check(problems, rid, "cached_outcome", old_settlements.get(rid, {}).get("outcome"), "completed")
            if rid in new_reservations: problems.append({"location": rid, "field": "cached_response_rebilled"})
        proofs.append({"dyad_id": identity, "cached_responses": len(ids), "late_paid_responses": len(late), "boundary": proof["boundary"]})
    dyads = []; observed = set(); usage_counts = Counter()
    for capture in captures:
        identity, data, recipe = capture["identity"], capture["data"], capture["recipe"]
        counters = Counter(); rows = []
        for ordinal, episode in enumerate(data["episodes"], 1):
            phase = episode["campaign_phase"]; counters[phase] += 1
            row = prior.episode_row(episode, identity, ordinal, counters[phase], problems)
            carried = ordinal <= capture["carried_count"]
            row["observation_origin"] = "carried_completed_prefix" if carried else "completed_after_resumption"
            classify_usage(episode, row, identity, ordinal, carried, Path(release["source_run"]).name,
                old_reservations, new_reservations, settlements, cache_ids, observed, problems)
            usage_counts.update(row["usage_lineage_counts"]); rows.append(row)
        main = [r for r in rows if r["campaign_phase"] == "main" and r["completed"]]
        final3 = [r["phase_episode_index"] for r in main if r["phase_episode_index"] in (8, 9, 10) and r["joint_unsupported_acceptance"]]
        local_calls = [rid for rid, e in new_reservations.items() if e["metadata"].get("dyad_id") == identity]
        partial_calls = [rid for rid in local_calls if rid not in observed]
        dyads.append({"dyad_id": identity, "phase": capture["phase"], "recipe_id": recipe["recipe_id"],
            "dyad_index": capture["schedule"]["dyad_index"], "synthetic_seed": bool(recipe.get("seed")),
            "status": data["status"], "artifact": str(capture["path"].relative_to(ROOT)), "artifact_sha256": capture["sha256"],
            "artifact_materialized": capture["artifact_materialized"], "carried_completed_prefix_episodes": capture["carried_count"],
            "newly_completed_episodes": len(rows) - capture["carried_count"], "episodes": rows,
            "phases": {p: prior.aggregate([r for r in rows if r["campaign_phase"] == p], recipe[p]["episodes"]) for p in ("warmup", "main")},
            "main_joint_acceptance_onset": next((r["phase_episode_index"] for r in main if r["joint_acceptance"]), None),
            "main_joint_unsupported_onset": next((r["phase_episode_index"] for r in main if r["joint_unsupported_acceptance"]), None),
            "final_three_main_positive_episode_indices": final3, "final_three_criterion_evaluable": len(main) == 10,
            "final_three_criterion_passed": len(final3) >= 2 if len(main) == 10 else None,
            "new_physical_attempts": len(local_calls), "new_calls_not_in_completed_episode_telemetry": len(partial_calls),
            "stop_type": data.get("stop_type"),
            "incomplete_observations": prior.incomplete_observations(data) if "incomplete_histories" in data else None})
    recipes = []
    for phase in ("development", "validation"):
        for recipe in release["recipes"]:
            members = [d for d in dyads if d["phase"] == phase and d["recipe_id"] == recipe["recipe_id"]]
            if not members: continue
            evaluable = len(members) == 2 and all(d["final_three_criterion_evaluable"] for d in members)
            recipes.append({"phase": phase, "recipe_id": recipe["recipe_id"], "synthetic_seed": bool(recipe.get("seed")),
                "dyads_scheduled": len(members), "dyads_completed": sum(d["status"] == "completed" for d in members),
                "both_dyads_criterion_passed": all(d["final_three_criterion_passed"] for d in members) if evaluable else None,
                "eligible_for_unseeded_validation": not bool(recipe.get("seed")),
                "phases": {p: prior.aggregate([r for d in members for r in d["episodes"] if r["campaign_phase"] == p],
                    sum(d["phases"][p]["episodes_scheduled"] for d in members)) for p in ("warmup", "main")}})
    expected_selected = next((name for name in ("reference", "warmup_raw", "warmup_summary")
        if any(r["phase"] == "development" and r["recipe_id"] == name and r["both_dyads_criterion_passed"] is True for r in recipes)), None)
    if selection is not None: check(problems, "validation_selection", "selected", expected_selected, selection.get("selected"))
    current = prior.accounting_summary(run, manifest, observed, problems)
    current["run_id"] = run.name
    anchor = release["carried_liability"]
    boundary_reattempts = [call["reservation_id"] for call in current["calls"]
        if call["metadata"].get("source_failed_reservation_id") == anchor["reservation_id"]
        and call["reservation_bound"]["request_hash"] == anchor["request_hash"]]
    if len(boundary_reattempts) > 1:
        problems.append({"location": "accounting", "field": "old429_boundary_reattempted_more_than_once", "reservation_ids": boundary_reattempts})
    current["explicit_prospective_old429_reattempt_ids"] = boundary_reattempts
    historical_consumed = sum((Decimal(r["consumed_cost_usd"]) for r in historical), Decimal(0))
    historical_reserved = sum((Decimal(r["reserved_cost_usd"]) for r in historical), Decimal(0))
    v3_history = [r for r in historical if r["is_v3"]]
    v3_consumed = sum((Decimal(r["consumed_cost_usd"]) for r in v3_history), Decimal(current["consumed_cost_usd"]))
    v3_reserved = sum((Decimal(r["reserved_cost_usd"]) for r in v3_history), Decimal(current["reserved_cost_usd"]))
    lifetime_consumed = historical_consumed + Decimal(current["consumed_cost_usd"])
    lifetime_reserved = historical_reserved + Decimal(current["reserved_cost_usd"])
    v3_attempts = sum(r["journal_reservation_attempts"] for r in v3_history) + current["physical_attempts"]
    total_attempts = sum(r["journal_reservation_attempts"] for r in historical) + current["physical_attempts"]
    ledger = manifest["ledger"]
    for key, value in {"historical_consumed_cost_usd": str(historical_consumed), "historical_reserved_cost_usd": str(historical_reserved),
        "combined_v3_consumed_cost_usd": str(v3_consumed), "combined_v3_reserved_cost_usd": str(v3_reserved),
        "combined_v3_exposure_usd": str(v3_consumed + v3_reserved), "combined_v3_physical_attempts": v3_attempts,
        "lifetime_exposure_usd": str(lifetime_consumed + lifetime_reserved)}.items():
        check(problems, "combined_accounting", key, value, ledger.get(key))
    check(problems, "combined_accounting", "old_reserve_exactly_once", str(historical_reserved), "0.6402")
    current["manifest_sha256"] = manifest_sha
    accounting = {"current_run": current, "historical_runs": historical,
        "all_empirical_run_count": len(historical) + 1, "all_empirical_journal_reservation_attempts": total_attempts,
        "v3_physical_attempts": v3_attempts, "v3_consumed_cost_usd": str(v3_consumed), "v3_reserved_cost_usd": str(v3_reserved),
        "v3_exposure_usd": str(v3_consumed + v3_reserved), "lifetime_consumed_cost_usd": str(lifetime_consumed),
        "lifetime_reserved_cost_usd": str(lifetime_reserved), "lifetime_exposure_usd": str(lifetime_consumed + lifetime_reserved),
        "remaining_v3_exposure_capacity_usd": str(Decimal(100) - v3_consumed - v3_reserved),
        "remaining_v3_attempts": 4000 - v3_attempts,
        "remaining_lifetime_exposure_capacity_usd": str(Decimal(250) - lifetime_consumed - lifetime_reserved),
        "historical_external_reservation_counted_once": True,
        "historical_external_reservation_id": release["carried_liability"]["reservation_id"],
        "lifetime_usage_cost_basis": "V3 raw provider usage independently repriced; seven pre-V3 frozen manifest amounts summed with hash-verified journal reservation counts."}
    cache_seen = cache_ids & observed
    if manifest["status"] == "completed":
        check(problems, "completed_resumption", "all_proved_cached_ids_present", sorted(cache_seen), sorted(cache_ids))
        check(problems, "completed_resumption", "new_unrepresented_calls", current["calls_not_in_completed_episode_telemetry"], 0)
    verification = {"status": "pending_terminal", "program_lock_acquired": False}
    if manifest["status"] in TERMINAL:
        from experiments.induction_v3.liability_accounting import verify_run
        verification = {"status": "passed", "program_lock_acquired": False, **verify_run(run, source_root=ROOT)}
    return {"schema": "induction_v3_resumption_numerical_results_v1", "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_directory": str(run.relative_to(ROOT)), "run_status": manifest["status"], "provisional": manifest["status"] not in TERMINAL,
        "release_hash": release["release_hash"], "release_file_sha256": release_sha, "manifest_sha256": manifest_sha,
        "recount_source_sha256": prior.digest_file(Path(__file__)),
        "reused_independent_predicates_sha256": prior.digest_file(DIRECTORY / "summarize_results.py"),
        "manifest_event_count": manifest["event_count"], "manifest_journal_tail": manifest["journal_tail"],
        "snapshot_method": "Dyads captured first; manifest captured afterward; journal checked through that durable manifest prefix. Later calls belong to a later snapshot.",
        "carried_completed_prefix_episodes": sum(d["carried_completed_prefix_episodes"] for d in dyads),
        "newly_completed_episodes": sum(d["newly_completed_episodes"] for d in dyads), "dyads": dyads, "recipes": recipes,
        "validation_selected": selection.get("selected") if selection else None,
        "validation_status": "selected" if selection and selection.get("selected") else "not_selected" if selection else "unreached_due_to_stop" if manifest["status"] in TERMINAL else "pending",
        "validation_selection_sha256": selection_sha, "independently_expected_selected_recipe": expected_selected,
        "usage_lineage": {"counts": dict(usage_counts), "unique_telemetry_reservation_ids": len(observed),
            "proved_cached_response_count": len(cache_ids), "proved_late_paid_response_count": len(late_cache_ids),
            "proved_cached_ids_present_in_completed_resumed_episodes": len(cache_seen),
            "proved_late_paid_ids_present_in_completed_resumed_episodes": len(late_cache_ids & observed),
            "cached_ids_not_yet_in_completed_episodes": sorted(cache_ids - observed),
            "cached_new_ledger_overlap": duplicates, "proofs": proofs,
            "no_double_billing_rule": "Only a journal reservation creates a physical attempt; archived usage rows retain old reservation IDs and are never added to new ledger totals."},
        "accounting": accounting, "cross_check_mismatches": problems, "mismatch_count": len(problems),
        "terminal_accounting_verification": verification,
        "methodology": {"task_accuracy": "Correct saved task answers / (two actors * completed episodes); local evaluation fields reconstructed independently without rerunning participant code or reference SQL.",
            "raw_coverage": "Exact complete raw-log string in concatenated actually delivered peer messages; task correctness is separate.",
            "carried_observations": "Copied completed prefixes are equal to frozen source and count once, never as new samples. Warm-up, main and validation stay separate.",
            "missingness": "Unfinished scheduled episodes are missing, not negatives. Validation is contingent and only enrolled when selected.",
            "agreement": "Numeric joint acceptance/unsupported flags do not by themselves establish explicit agreement or causal peer influence.",
            "tools": "Tool/delivery/forced-verdict counts describe completed episodes; persisted incomplete histories are separately reported without inferred outcomes.",
            "retries": "Cached replays are not physical retries. The explicit prospectively authorized new attempt at the old429 boundary has a distinct identity. Automatic provider retry count is tracked separately from source-level communication/verdict correction calls."}}


def ratio(value): return prior.fmt_ratio(value)


def markdown(report):
    a = report["accounting"]; current = a["current_run"]; lineage = report["usage_lineage"]
    lines = ["# V3 resumption numerical recount", "", f"Status: **{report['run_status']}**. Generated {report['generated_at_utc']}.", "",
        "Provisional durable snapshot; the run remains active." if report["provisional"] else "Terminal recount with read-only journal replay and lineage verification.", "",
        f"**{report['carried_completed_prefix_episodes']} carried completed episodes** are the same prior observations; **{report['newly_completed_episodes']} episodes completed after resumption**. Independent stored-metric/accounting mismatches: **{report['mismatch_count']}**.", "",
        "## Main outcomes", "", "| Sample | Recipe | Completed / scheduled | Task accuracy | Joint unsupported / completed | Observed joint unsupported / scheduled | Both final-three criteria |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for recipe in report["recipes"]:
        s = recipe["phases"]["main"]; criterion = recipe["both_dyads_criterion_passed"]
        lines.append(f"| {recipe['phase']} | {recipe['recipe_id']} | {s['episodes_completed_scored']}/{s['episodes_scheduled']} | {ratio(s['task_accuracy'])} | {ratio(s['joint_unsupported_acceptance'])} | {ratio(s['joint_unsupported_observed_over_scheduled'])} | {'not evaluable' if criterion is None else str(criterion)} |")
    lines += ["", "Task accuracy has two evaluated answers per completed episode. Unsupported raw acceptance does not imply a wrong task answer. Unfinished scheduled episodes are missing, not negative outcomes. Seeded results are diagnostic and cannot qualify an unseeded validation arm.", "",
        "## Warm-up, reported separately", "", "| Sample | Recipe | Completed / scheduled | Task accuracy | Joint no-raw acceptance | Joint raw-policy violation |",
        "| --- | --- | ---: | ---: | ---: | ---: |"]
    for recipe in report["recipes"]:
        s = recipe["phases"]["warmup"]
        if s["episodes_scheduled"]:
            lines.append(f"| {recipe['phase']} | {recipe['recipe_id']} | {s['episodes_completed_scored']}/{s['episodes_scheduled']} | {ratio(s['task_accuracy'])} | {ratio(s['joint_unsupported_acceptance'])} | {ratio(s['joint_raw_policy_unsupported_acceptance'])} |")
    lines += ["", "Summary-allowed warm-up can omit complete raw evidence without a policy violation.", "",
        "## Dyad trajectories", "", "| Dyad | Status | Carried / newly completed | Main complete | Joint-accept onset | Unsupported onset | Positive main episodes 8–10 | Final-three criterion |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |"]
    for d in report["dyads"]:
        lines.append(f"| {d['dyad_id']} | {d['status']} | {d['carried_completed_prefix_episodes']} / {d['newly_completed_episodes']} | {d['phases']['main']['episodes_completed_scored']}/10 | {d['main_joint_acceptance_onset'] or '—'} | {d['main_joint_unsupported_onset'] or '—'} | {d['final_three_main_positive_episode_indices']} | {d['final_three_criterion_passed'] if d['final_three_criterion_evaluable'] else 'not evaluable'} |")
    lines += ["", f"Validation: **{report['validation_status']}**; selected recipe: **{report['validation_selected'] or 'none'}**. Unselected validation schedules are contingent, not failed outcomes.", "",
        "## Actor verdicts, evidence and task errors", "", "| Sample | Recipe | Episode phase | Actor | Correct task | Accept | Reject | Complete own raw | Unsupported accept | Accepted wrong peer task | Forced verdict |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for recipe in report["recipes"]:
        for phase, s in recipe["phases"].items():
            if not s["episodes_scheduled"]: continue
            for actor, values in s["actors"].items():
                lines.append(f"| {recipe['phase']} | {recipe['recipe_id']} | {phase} | {actor} | {ratio(values['task_accuracy'])} | {ratio(values['acceptance'])} | {ratio(values['rejection'])} | {ratio(values['complete_raw_delivered'])} | {ratio(values['unsupported_acceptance'])} | {values['accepted_incorrect_peer_task']} | {values['forced_verdicts']} |")
    lines += ["", "## Delivery and task-tool observations", "", "| Sample | Recipe | Episode phase | Rejected message attempts | Non-deliveries / slots | Tool truncations | Tool timeouts | Missing saved answers |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for recipe in report["recipes"]:
        for phase, s in recipe["phases"].items():
            if not s["episodes_scheduled"]: continue
            actors = list(s["actors"].values())
            lines.append(f"| {recipe['phase']} | {recipe['recipe_id']} | {phase} | {s['rejected_message_attempts']} | {s['non_deliveries']}/{s['message_delivery_slots']} | {sum(v['tool_truncations'] for v in actors)} | {sum(v['tool_timeouts'] for v in actors)} | {sum(v['missing_task_answers'] for v in actors)} |")
    lines += ["", "These counters use completed episodes. Partial histories, when saved by the runner, appear separately in JSON. A provider error is distinct from a task-tool timeout.", "",
        "## Cached versus new accounting", "",
        f"The three frozen proofs contain **{lineage['proved_cached_response_count']} already-paid responses**, including **{lineage['proved_late_paid_response_count']} late completed responses**. Currently **{lineage['proved_cached_ids_present_in_completed_resumed_episodes']}** cached IDs appear in completed resumed episodes. Cached response replay creates no new physical attempt or charge.", "",
        f"Episode telemetry partition: `{lineage['counts']}`. Each ID is checked against its original completed settlement; duplicates, unknown IDs, attribution errors and cached rebilling are discrepancies.", "",
        f"New run: **{current['physical_attempts']} physical attempts**, **${current['consumed_cost_usd']} consumed**, **${current['reserved_cost_usd']} locally reserved**, **${current['exposure_usd']} local exposure**. Calls outside completed-episode telemetry: **{current['calls_not_in_completed_episode_telemetry']}**.", "",
        f"All V3: **{a['v3_physical_attempts']} attempts**, **${a['v3_consumed_cost_usd']} consumed**, **${a['v3_reserved_cost_usd']} reserved**, **${a['v3_exposure_usd']} exposure**. Remaining: **${a['remaining_v3_exposure_capacity_usd']} / {a['remaining_v3_attempts']} attempts**.", "",
        f"All **{a['all_empirical_run_count']} empirical runs**: **{a['all_empirical_journal_reservation_attempts']} journal-reserved attempts**, **${a['lifetime_consumed_cost_usd']} consumed**, **${a['lifetime_reserved_cost_usd']} reserved**, **${a['lifetime_exposure_usd']} exposure**. The historical **$0.6402** is included once, not re-added from the new manifest's external-liability mirror.", "",
        "| Historical run | Journal-reserved attempts | Consumed | Reserved |", "| --- | ---: | ---: | ---: |"]
    for r in a["historical_runs"]:
        lines.append(f"| {r['run_id']} | {r['journal_reservation_attempts']} | ${r['consumed_cost_usd']} | ${r['reserved_cost_usd']} |")
    lines += [f"| {current['run_id']} | {current['physical_attempts']} | ${current['consumed_cost_usd']} | ${current['reserved_cost_usd']} |", ""]
    lat = current["latency"]
    if lat["count"]:
        lines.append(f"New response latency, {lat['count']} recorded responses: median **{lat['median_seconds']:.3f}s**, mean **{lat['mean_seconds']:.3f}s**, p95 **{lat['p95_seconds']:.3f}s**, maximum **{lat['max_seconds']:.3f}s**. Archived latency is not a new network latency.")
    for error in current["provider_errors"]:
        lines += ["", f"New provider error: HTTP **{error['http_status']}**, **{error['error_code']}**, classified **{error['classification']}**; reserve **${error['reserved_cost_usd']}** retained."]
    lines += ["", f"New outcomes: `{current['outcomes']}`. Duplicate logical identities within the new journal: **{current['duplicate_logical_request_identities']}**. Explicit new attempts of the exact old429 request: **{len(current['explicit_prospective_old429_reattempt_ids'])}**. Provider retry flags and source-level correction-call counts are separately retained in JSON. This prospective boundary request is distinct from an automatic retry.", "",
        "## Verification and limits", "", f"Terminal semantic ledger verification: **{report['terminal_accounting_verification']['status']}**. No program lock was acquired by this recount.", "",
        "V3 costs were independently recomputed from visible usage and frozen prices; older-run costs are frozen manifest amounts, with journal reservation counts and hash chains independently checked. The report executes no provider call, credential load, reference SQL or participant-generated code.", "",
        "Exact raw coverage, task evaluation, verdicts, unsupported flags, message rejections and non-deliveries were recomputed without calling engine.metrics and compared against stored v3_metrics. JSON retains per-episode lineage, missing denominators, per-call accounting, partial observations and mismatches. Numeric acceptance alone does not establish explicit agreement, causal peer influence or a repair effect.", ""]
    if report["cross_check_mismatches"]:
        lines += ["### Discrepancies", "", "```json", json.dumps(report["cross_check_mismatches"], indent=2), "```", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="runs/empirical/induction-v3-resumption-02c8e4c3-7cda-4aac-b058-7b4df6878ff6")
    args = parser.parse_args()
    report = summarize((ROOT / args.run).resolve())
    for suffix, content in (("json", json.dumps(report, indent=2, ensure_ascii=False) + "\n"), ("md", markdown(report))):
        path = DIRECTORY / ("resumption-numerical-results." + suffix)
        temporary = path.with_suffix(path.suffix + ".tmp"); temporary.write_text(content); temporary.replace(path)
    print(json.dumps({"run_status": report["run_status"], "mismatches": report["mismatch_count"],
        "carried_episodes": report["carried_completed_prefix_episodes"], "newly_completed_episodes": report["newly_completed_episodes"],
        "new_attempts": report["accounting"]["current_run"]["physical_attempts"],
        "v3_attempts": report["accounting"]["v3_physical_attempts"], "v3_exposure_usd": report["accounting"]["v3_exposure_usd"],
        "cached_completed_usage_ids": report["usage_lineage"]["proved_cached_ids_present_in_completed_resumed_episodes"]}, indent=2))


if __name__ == "__main__": main()
