"""Independent continuation science delta; no provider or generated-code execution."""
import copy
import json
from types import SimpleNamespace

import pytest

from experiments.induction_v3.common import ROOT
from experiments.induction_v3.continuation import conditioned_history, select_condition
from experiments.induction_v3.diagnostic import CLARIFICATION
from experiments.induction_v3.upstream import load


def complete_diagnostic():
    return {"status": "completed", "completed": 12, "unrun": [], "rows": [
        {"condition": condition, "reviewer": actor, "kind": kind,
         "actual": "accept" if kind == "clean_complete" else "reject", "passed": True}
        for condition in ("original", "schema_clarified") for actor in ("alice", "bob")
        for kind in ("clean_complete", "incorrect_complete", "clean_missing_raw")]}


@pytest.mark.parametrize("defect", ["duplicate_clarified", "missing_clarified", "unknown_arm", "extra_row", "unrun", "blocked"])
def test_all_twelve_cells_validated_before_original_arm_can_win(defect):
    value = complete_diagnostic()
    if defect == "duplicate_clarified":
        value["rows"][-1] = copy.deepcopy(value["rows"][-2])
    elif defect == "missing_clarified":
        value["rows"].pop()
    elif defect == "unknown_arm":
        value["rows"][-1]["condition"] = "unplanned"
    elif defect == "extra_row":
        value["rows"].append(copy.deepcopy(value["rows"][0]))
    elif defect == "unrun":
        value["unrun"] = ["one cell"]
    else:
        value["status"] = "blocked"
    with pytest.raises(ValueError):
        select_condition(value)


def test_selection_recomputes_actual_labels_instead_of_trusting_passed_flag():
    value = complete_diagnostic()
    value["rows"][0]["actual"] = "reject"  # Stale true passed flag cannot hide wrong clean verdict.
    assert select_condition(value) == "schema_clarified"
    value["rows"][6]["actual"] = "reject"
    with pytest.raises(ValueError):
        select_condition(value)


@pytest.mark.parametrize("recipe_id", ["reference", "warmup_raw", "warmup_summary", "seeded"])
@pytest.mark.parametrize("condition", ["original", "schema_clarified"])
def test_actual_dyad_wrapper_keeps_fixed_recipe_policy_and_single_condition_injection(monkeypatch, tmp_path, recipe_id, condition):
    # Exercise actual campaign wrapper/configuration/history construction while
    # substituting a pure episode recorder; full source episode mechanics are
    # separately covered in test_science_audit.py.
    load()
    from experiments.induction_v3 import engine
    original_release = json.loads((ROOT / "release/induction-v3/campaign.json").read_text())
    recipe = next(r for r in original_release["recipes"] if r["recipe_id"] == recipe_id)
    schedule = original_release["schedules"]["development"][0]
    original_builder = engine.initial_history
    pristine = original_builder(recipe)
    monkeypatch.setattr(engine, "initial_history", lambda r: conditioned_history(original_builder(r), condition))
    observed = []
    prior_histories = None

    class EpisodeRecorder:
        def run_one_episode(self, **kwargs):
            nonlocal prior_histories
            histories = kwargs["agent_messages"]
            index = len(observed)
            observed.append({"pair": copy.deepcopy(kwargs["pair"]), "config": kwargs["config"]})
            for actor, history in histories.items():
                assert history[0] == pristine[actor][0]
                assert sum(m.get("content") == CLARIFICATION for m in history) == (condition == "schema_clarified")
                expected_initial = conditioned_history(pristine, condition)[actor]
                assert history[:len(expected_initial)] == expected_initial
                if prior_histories:
                    assert history[:len(prior_histories[actor])] == prior_histories[actor]
                peer = "bob" if actor == "alice" else "alice"
                assert peer + " private-recorder" not in json.dumps(history)
                history.append({"role": "assistant", "content": actor + f" private-recorder {index}"})
            prior_histories = copy.deepcopy(histories)
            return {"agents": {actor: {"private_raw_log": [{"seq": 1, "tool": "fixture", "arguments": {}, "result": {"success": True}}],
                                       "verdict": "reject", "expected_verdict": "pass"} for actor in ("alice", "bob")},
                    "channel_transcript": [], "events": [], "max_rounds": 5}

    class NoProvider:
        def context(self, **metadata):
            assert metadata["recipe_id"] == recipe_id
        def complete(self, **kwargs):
            raise AssertionError("This independent wrapper test cannot call a provider")

    result = engine.run_dyad(recipe, schedule, phase="development", transport=NoProvider(),
                            run_dir=tmp_path, episode_module=EpisodeRecorder())
    warm_count = recipe["warmup"]["episodes"]
    assert result["status"] == "completed" and len(observed) == warm_count + 10
    expected_pairs = (schedule["warmup"] if warm_count else []) + schedule["main"]
    assert [row["pair"] for row in observed] == expected_pairs
    for index, row in enumerate(observed):
        config = row["config"]
        assert config.max_rounds == 5 and config.reflection and config.reward and config.verdict_review
        if index >= warm_count:
            assert config.throttled and config.char_limit == 200 and config.verdict_policy == "raw-only"
        elif recipe_id == "warmup_raw":
            assert not config.throttled and config.verdict_policy == "raw-only"
        else:
            assert config.throttled and config.char_limit == 200 and config.verdict_policy == "summary-allowed"
    # Each new dyad starts from a new history with no prior dyad's private records.
    assert "private-recorder" not in json.dumps(engine.initial_history(recipe))
