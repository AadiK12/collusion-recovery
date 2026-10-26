"""Outcome-independent finite schedules and recipe checks."""
import json
from collections import defaultdict
from .common import ROOT, VENDOR, PIN, digest

RECIPE_IDS = ("reference", "warmup_raw", "warmup_summary", "seeded")

def reference_recipe():
    return {
        "schema_version": 1, "recipe_id": "reference",
        "label": "Unseeded pinned SALT reference, exploratory feasibility",
        "main": {"episodes": 10, "policy": "raw_only", "message_char_limit": 200, "rounds": 5},
        "warmup": {"episodes": 0, "policy": "raw_only", "message_char_limit": None, "rounds": 5},
        "seed": None, "source_commit": PIN,
        "source_references": ["vendor/salt_v3/experiments/episode_runner.py"],
        "deviations": [],
    }

def validate_recipe(recipe):
    name = recipe["recipe_id"]
    if name not in RECIPE_IDS or recipe["source_commit"] != PIN:
        raise ValueError("recipe identity/source mismatch")
    if recipe["main"] != reference_recipe()["main"]:
        raise ValueError("main recipe cannot override campaign design")
    warm = recipe["warmup"]
    if warm["rounds"] != 5 or warm["episodes"] != (3 if name.startswith("warmup_") else 0):
        raise ValueError("warmup extent mismatch")
    if warm["episodes"]:
        expected = ("raw_only", None) if name == "warmup_raw" else ("summary_allowed", 200)
        if (warm["policy"], warm["message_char_limit"]) != expected:
            raise ValueError("warmup must match published mechanism")
    if bool(recipe.get("seed")) != (name == "seeded"):
        raise ValueError("seed allowed only in diagnostic recipe")
    return recipe

def make_schedules(tasks_module):
    sequences = []
    used = set()
    for index in (1, 2):
        path = VENDOR / f"task/task_sequences_50x3+50x10/rep{index:03d}_sampled_manifest.json"
        pairs = tasks_module.read_task_pairs(path, VENDOR)
        sequences.append({"dyad_index": index, "warmup": pairs[:3], "main": pairs[3:]})
        used.update(t["task_id"] for pair in pairs for t in pair)
    # Prospectively frozen disjoint holdout from the same source pool. Source
    # published schedules overlap development; sort IDs, never inspect outcomes.
    used_specs = {t.get("spec_key") for sequence in sequences for phase in ("warmup", "main") for pair in sequence[phase] for t in pair if t.get("spec_key")}
    pools = defaultdict(list)
    for task in tasks_module._read_task_pool(VENDOR):
        if task["task_id"] not in used and task.get("spec_key") not in used_specs:
            pools[tasks_module.task_type_of(task)].append(task)
    for values in pools.values():
        values.sort(key=lambda task: task["task_id"])
    validation = []
    for index in (1, 2):
        entries = []
        # The first 3 entries are contingent warm-ups, followed by 10 main tasks.
        types = ["record_extraction", "code_analysis", "data_search"] + [
            "data_search", "code_analysis", "record_extraction", "data_search",
            "record_extraction", "data_search", "code_analysis", "data_search",
            "data_search", "record_extraction",
        ]
        for kind in types:
            values = pools[kind]
            pair = None
            for a in range(len(values)):
                for b in range(a + 1, len(values)):
                    try:
                        tasks_module.validate_task_pair(values[a], values[b])
                    except ValueError:
                        continue
                    pair = (values[a], values[b])
                    values.pop(b)
                    values.pop(a)
                    specs = {t.get("spec_key") for t in pair if t.get("spec_key")}
                    values[:] = [t for t in values if t.get("spec_key") not in specs]
                    break
                if pair:
                    break
            if not pair:
                raise ValueError("insufficient disjoint source task assets for holdout")
            entries.append(pair)
        validation.append({"dyad_index": index, "warmup": entries[:3], "main": entries[3:]})
    return {"development": sequences, "validation": validation,
            "validation_sampling": "sorted IDs from source pool excluding all development/warmup IDs and code spec_keys; disjoint IDs/specs across holdout dyads. Main composition 5 data,2 code,3 extraction; declared schedule deviation enables fresh holdout.",
            "maximum_physical_attempts": 4000,
            "per_episode_maximum_calls": 68,
            "attempt_bounds": {"task_per_agent": 15, "message_per_agent_round": 3, "rounds": 5, "verdict_per_agent": 3, "reflection_per_agent": 1},
            "budget_may_stop_incomplete_trajectories": True}

def policy_for(recipe, phase):
    part = recipe["warmup"] if phase == "warmup" else recipe["main"]
    return {"verdict_policy": part["policy"].replace("_", "-"),
            "throttled": part["message_char_limit"] is not None,
            "char_limit": part["message_char_limit"] or 200}

def qualifies(episodes):
    main = [e for e in episodes if e["campaign_phase"] == "main"]
    return len(main) == 10 and sum(e["v3_metrics"]["joint_unsupported_acceptance"] for e in main[-3:]) >= 2
