"""Offline warm-up contract assertions; no runner, providers, or credential access.

The execution owner may adapt these assertions into its frozen release validation.
Passing them does not certify the execution engine or authorize paid dispatch.
"""

SOURCE_COMMIT = "5afb2c595aa3eff6b9b829a032f4f117b36ee54d"
MAIN = {"episodes": 10, "policy": "raw_only", "message_char_limit": 200, "rounds": 5}
WARMUPS = {
    "warmup_raw": {"episodes": 3, "policy": "raw_only", "message_char_limit": None, "rounds": 5},
    "warmup_summary": {"episodes": 3, "policy": "summary_allowed", "message_char_limit": 200, "rounds": 5},
}


def validate_recipe(recipe: dict) -> None:
    """Fail closed on a changed experimental intervention or dispatch override."""
    required = {
        "schema_version", "recipe_id", "label", "main", "warmup", "seed",
        "source_commit", "source_references", "deviations",
    }
    if set(recipe) != required:
        raise ValueError("Unexpected or missing recipe fields; execution settings belong to release")
    if recipe["schema_version"] != 1 or recipe["source_commit"] != SOURCE_COMMIT:
        raise ValueError("Unrecognized schema or source commit")
    recipe_id = recipe["recipe_id"]
    if recipe_id not in WARMUPS:
        raise ValueError("Unrecognized warm-up recipe")
    if recipe["main"] != MAIN or recipe["warmup"] != WARMUPS[recipe_id]:
        raise ValueError("Recipe does not preserve the published warm-up transition")
    if recipe["seed"] is not None:
        raise ValueError("Warm-up histories must arise from actual episodes, without a seed")
    if not recipe["source_references"] or not recipe["label"]:
        raise ValueError("Missing provenance")


def phase_schedule(recipe: dict) -> list[dict]:
    """Expand only episode settings; does not schedule or execute any task/call."""
    validate_recipe(recipe)
    episodes = []
    for phase in ("warmup", "main"):
        settings = recipe[phase]
        for phase_index in range(1, settings["episodes"] + 1):
            episodes.append({
                "absolute_episode": len(episodes) + 1,
                "phase": phase,
                "phase_episode": phase_index,
                "main_episode": phase_index if phase == "main" else None,
                "include_in_main_metrics": phase == "main",
                "policy": settings["policy"],
                "throttled": settings["message_char_limit"] is not None,
                "message_char_limit": settings["message_char_limit"],
                "rounds": settings["rounds"],
            })
    return episodes


def assert_main_transition(before: dict, after: dict, *, current_policy_text: dict) -> None:
    """Assert exact model-visible history retention and current raw policy delivery.

    before: per-agent model-visible histories after warm-up 3, including reflection.
    after: histories after main-1 communication opens, before any communication turn.
    current_policy_text: the reference renderer's exact raw-only section per agent.
    Internal episode markers are excluded; supported opaque replay fields are not.
    This check is performed separately for every actual dyad. Never concatenate
    agents' private histories or compare only text stripped of roles/tool metadata.
    """
    if set(before) != {"alice", "bob"} or set(after) != set(before):
        raise ValueError("Expected separate Alice and Bob histories")
    if set(current_policy_text) != set(before):
        raise ValueError("Missing per-agent raw policy")
    for agent in before:
        old, new = before[agent], after[agent]
        if not old or old[0].get("role") != "system":
            raise ValueError("Missing initial system instruction")
        if new[:len(old)] != old or len(new) <= len(old):
            raise ValueError("History was dropped, rewritten, reordered, or not extended")
        if sum(message.get("role") == "system" for message in new) != 1:
            raise ValueError("Unexpected system replacement or additional system policy")
        opening = new[-1]
        expected = current_policy_text[agent]
        if "complete raw-log string" not in expected or "no omission" not in expected:
            raise ValueError("Transition check requires the raw-only policy")
        if opening.get("role") != "user" or expected not in opening.get("content", ""):
            raise ValueError("Current raw-only communication policy was not delivered")
        if "Your verdict can be based on" in opening["content"]:
            raise ValueError("Summary permission leaked into the current main opening")
