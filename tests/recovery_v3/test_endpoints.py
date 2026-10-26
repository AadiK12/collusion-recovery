import copy
import json
from pathlib import Path

import pytest

from experiments.induction_v3.common import digest
from experiments.recovery_v3 import endpoints as ep


RUN = Path("runs/resumption")
SOURCE = Path("runs/continuation")


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return path


def actor_slot(actor):
    return {"agent_id": actor, "peer_id": "bob" if actor == "alice" else "alice",
            "outcome_feedback": "recorded outcome feedback", "reflection_prompt": "recorded reflection prompt",
            "reflection": "recorded reflection",
            "messages": [
                {"role": "system", "content": f"## Role\n\nYou are {actor.title()}.\n\nTask policy."},
                {"role": "user", "content": actor + "-only-private-task"},
                {"role": "assistant", "content": "", "provider_output_items": [
                    {"id": actor + "-msg", "type": "message", "phase": "commentary", "content": []},
                    {"id": actor + "-reason", "type": "reasoning", "encrypted_content": actor + "-opaque-秘密", "summary": []},
                    {"id": actor + "-call", "type": "function_call", "name": "save_final_answer", "call_id": actor + "-cid", "arguments": "{}"}]},
                {"role": "tool", "tool_call_id": actor + "-cid", "content": "{\"success\":true}"},
                {"role": "user", "content": "recorded outcome feedback"},
                {"role": "user", "content": "recorded reflection prompt"},
                {"role": "assistant", "content": "recorded reflection", "provider_output_items": [
                    {"id": actor + "-reflection", "type": "message", "phase": "final_answer", "content": []}]}]}


def dyad(recipe="reference", index=1, *, count=10, status="completed"):
    warmup = 3 if recipe != "reference" else 0
    phases = ["warmup"] * warmup + ["main"] * count
    return {"dyad_id": f"development-{recipe}-{index}", "recipe_id": recipe,
            "phase": "development", "synthetic_seed": False, "status": status,
            "episodes": [{"episode_id": f"task-{i}-a+task-{i}-b", "task_type": "data_search",
                          "task_ids": [f"task-{i}-a", f"task-{i}-b"], "campaign_phase": phase,
                          "agents": {a: actor_slot(a) for a in ep.ACTORS},
                          "v3_metrics": {"joint_unsupported_acceptance": False}}
                         for i, phase in enumerate(phases)]}


def save_dyad(root, value):
    return write(root, RUN / "dyads" / (value["dyad_id"] + ".json"), value)


def test_fixed_six_pending_inventory_never_substitutes_earlier_endpoint(tmp_path):
    rows = ep.inventory_endpoints(root=tmp_path, run_dir=RUN)
    assert [(r["recipe"], r["dyad_index"]) for r in rows] == [(r, i) for r in ep.RECIPES for i in (1, 2)]
    assert len(rows) == 6 and all(r["status"] == "pending" and r["path"] is None for r in rows)
    save_dyad(tmp_path, dyad(count=9, status="stopped"))
    rows = ep.inventory_endpoints(root=tmp_path, run_dir=RUN)
    assert rows[0]["main_completed"] == 9 and rows[0]["status"] == "pending"
    with pytest.raises(ValueError, match="pending"):
        ep.load_endpoint(rows[0], root=tmp_path)


def test_complete_main10_preserves_exact_role_isolated_opaque_histories(tmp_path):
    value = dyad("warmup_raw")
    save_dyad(tmp_path, value)
    row = ep.inventory_endpoints(root=tmp_path, run_dir=RUN)[2]
    assert row["main_completed"] == 10 and row["status"] == "completed"
    assert row["warmup_completed"] == 3 and row["episode_id"] == value["episodes"][-1]["episode_id"]
    assert row["checkpoint_sha256"] == digest(value["episodes"][-1])
    loaded = ep.load_endpoint(row, root=tmp_path)
    assert loaded["checkpoint"]["source_episode_index"] == 12
    for actor in ep.ACTORS:
        original = value["episodes"][-1]["agents"][actor]["messages"]
        assert loaded["histories"][actor] == original
        assert row["role_history_sha256"][actor] == digest(original)
        assert row["reflection_complete"][actor] and row["outcome_feedback_complete"][actor]
        assert ("bob-only-private-task" if actor == "alice" else "alice-only-private-task") not in json.dumps(loaded["histories"][actor])
    loaded["histories"]["alice"][2]["provider_output_items"][1]["encrypted_content"] = "mutated-local-copy"
    fresh = ep.load_endpoint(row, root=tmp_path)
    assert fresh["histories"]["alice"][2]["provider_output_items"][1]["encrypted_content"] == "alice-opaque-秘密"
    assert fresh["histories"]["bob"] == value["episodes"][-1]["agents"]["bob"]["messages"]
    assert "agents" not in loaded["checkpoint"] and "histories" not in loaded["checkpoint"]


def test_availability_does_not_select_on_acceptance_or_task_outcomes(tmp_path):
    for recipe in ep.RECIPES:
        for index in (1, 2):
            value = dyad(recipe, index)
            value["episodes"][-1]["v3_metrics"] = {"joint_unsupported_acceptance": index == 2,
                                                   "task_correct": {"alice": index == 1, "bob": index == 2}}
            save_dyad(tmp_path, value)
    rows = ep.inventory_endpoints(root=tmp_path, run_dir=RUN)
    assert len(rows) == 6 and all(r["status"] == "completed" for r in rows)


def test_completed_file_binding_and_complete_checkpoint_digest_fail_closed(tmp_path):
    path = save_dyad(tmp_path, dyad())
    row = ep.inventory_endpoints(root=tmp_path, run_dir=RUN)[0]
    wrong = copy.deepcopy(row); wrong["checkpoint_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checkpoint hash"):
        ep.load_endpoint(wrong, root=tmp_path)
    wrong = copy.deepcopy(row); wrong["role_history_sha256"]["bob"] = "0" * 64
    with pytest.raises(ValueError, match="role history"):
        ep.load_endpoint(wrong, root=tmp_path)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="file hash"):
        ep.load_endpoint(row, root=tmp_path)
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        ep.load_endpoint(row, root=tmp_path)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(synthetic_seed=True),
    lambda d: d.update(phase="validation"),
    lambda d: d.update(recipe_id="seeded"),
    lambda d: d["episodes"].pop(),
    lambda d: d["episodes"].append(copy.deepcopy(d["episodes"][-1])),
    lambda d: d["episodes"][-1].update(agents=[]),
    lambda d: d["episodes"][-1]["agents"]["alice"].update(agent_id="bob"),
    lambda d: d["episodes"][-1]["agents"]["alice"].update(messages=actor_slot("bob")["messages"]),
    lambda d: d["episodes"][-1]["agents"]["alice"]["messages"][2].pop("provider_output_items"),
    lambda d: d["episodes"][-1]["agents"]["alice"]["messages"][2]["provider_output_items"][1].pop("id"),
    lambda d: d["episodes"][-1]["agents"]["alice"].update(reflection=""),
    lambda d: d["episodes"][-1]["agents"]["alice"].pop("outcome_feedback"),
    lambda d: d["episodes"][-1]["agents"]["alice"]["messages"][-3].update(content="different outcome"),
    lambda d: d["episodes"][-1]["agents"]["alice"]["messages"].pop(),
])
def test_malformed_wrong_role_and_incomplete_completed_dyads_rejected(tmp_path, mutation):
    value = dyad(); mutation(value); save_dyad(tmp_path, value)
    with pytest.raises(ValueError):
        ep.inventory_endpoints(root=tmp_path, run_dir=RUN)


def test_running_main10_not_exposed_before_completion_marker(tmp_path):
    save_dyad(tmp_path, dyad(status="running"))
    row = ep.inventory_endpoints(root=tmp_path, run_dir=RUN)[0]
    assert row["main_completed"] == 10 and row["status"] == "pending" and row["sha256"] is None


def test_symlink_or_parent_traversal_rejected(tmp_path):
    path = save_dyad(tmp_path, dyad()); saved = path.read_text(); path.unlink()
    target = tmp_path / "elsewhere.json"; target.write_text(saved); path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        ep.inventory_endpoints(root=tmp_path, run_dir=RUN)
    with pytest.raises(ValueError, match="traverse"):
        ep.inventory_endpoints(root=tmp_path, run_dir="../escape")


def prepare_archives(root):
    write(root, SOURCE / "release.json", {"kind": "original"})
    write(root, RUN / "release.json", {"source_run": str(SOURCE)})
    bindings = {}
    for i, run in enumerate((SOURCE, RUN)):
        body = {"model": "test-offline", "input": [{"role": "user", "content": str(i)}]}
        request_hash = digest(body)
        write(root, run / "requests" / (request_hash + ".json"), body)
        path = write(root, run / "responses" / f"response-{i}.json",
                     {"request_hash": request_hash, "body": {"status": "completed", "output": [
                         {"id": f"reason-{i}", "type": "reasoning", "encrypted_content": "opaque-uninterpreted"}]}})
        bindings[run] = path
        write(root, run / "manifest.json", {"status": "running"})
        write(root, run / "responses" / "error.json", {"http_status": 429, "error": "preserved"})
    return bindings


def test_archives_include_both_origins_and_no_mutable_or_failed_files(tmp_path):
    prepare_archives(tmp_path)
    result = ep.discover_response_archives(root=tmp_path, run_dir=RUN)
    assert result["source_runs"] == [str(SOURCE), str(RUN)]
    assert len(result["response_bindings"]) == 2 and len(result["request_bindings"]) == 2
    assert len(result["run_release_bindings"]) == 2 and result["mutable_manifests_included"] is False
    assert not any("manifest" in p or "error.json" in p for field in ("response_bindings", "request_bindings", "run_release_bindings") for p in result[field])


def test_archives_fail_closed_on_missing_or_changed_origin_request(tmp_path):
    bindings = prepare_archives(tmp_path)
    response = json.loads(bindings[SOURCE].read_text())
    request = tmp_path / SOURCE / "requests" / (response["request_hash"] + ".json")
    request.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="request canonical hash"):
        ep.discover_response_archives(root=tmp_path, run_dir=RUN)
    request.unlink()
    with pytest.raises(ValueError, match="missing"):
        ep.discover_response_archives(root=tmp_path, run_dir=RUN)
