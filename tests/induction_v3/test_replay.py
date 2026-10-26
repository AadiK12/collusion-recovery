"""Actual interrupted-episode replay and fail-closed corruption checks, offline."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from experiments.induction_v3 import replay
from experiments.induction_v3.common import ROOT, digest, file_hash
from experiments.induction_v3.transport import encode_input


RUN = ROOT / "runs/empirical/induction-v3-continuation-97f9c0b4-0bcb-4125-97f9-431caee260e4"


@pytest.mark.parametrize("suffix,matched,cached,late,boundary", [
    ("warmup_raw-1", 14, 13, 0, "verified_http429"),
    ("reference-1", 22, 22, 1, "never_dispatched"),
    ("warmup_summary-1", 7, 7, 1, "never_dispatched"),
])
def test_actual_episode_reaches_exact_boundary_without_mutating_source_run(suffix, matched, cached, late, boundary):
    before = {str(p): file_hash(p) for p in RUN.rglob("*") if p.is_file()}
    value = replay.replay_episode(RUN, "development-" + suffix)
    assert value["provider_calls"] == value["generated_code_executions"] == 0
    assert (value["matched_request_count"], value["cached_response_count"], value["late_response_count"]) == (matched, cached, late)
    assert value["boundary"]["kind"] == boundary
    assert digest(value["checkpoint"]) == value["checkpoint_digest"]
    assert len({v["reservation_id"] for v in value["consumed_responses"]}) == cached
    assert before == {str(p): file_hash(p) for p in RUN.rglob("*") if p.is_file()}
    state = value["checkpoint"]["state"]
    if suffix == "warmup_raw-1":
        assert state["phase"] == "communication" and state["round"] == 0
        assert not state["agents"]["bob"]["delivered"]
        assert encode_input(value["checkpoint"]["histories"]["bob"]) == value["checkpoint"]["pending_request"]["input"]
    elif suffix == "reference-1":
        # The late verdict is executed once from its saved response. Source then
        # computes feedback and reaches Alice's first never-dispatched reflection.
        assert all(state["agents"][a]["verdict_submission"] is not None for a in ("alice", "bob"))
        assert sum(e.get("tool") == "submit_verdict" and e.get("success") for e in state["events"]) == 2
    else:
        # The late answer is saved, but get_log is still the next unsampled call.
        assert state["agents"]["bob"]["final_answer_saved"]
        assert not state["agents"]["bob"]["private_raw_log_retrieved"]


@pytest.mark.parametrize("corruption", ["request", "opaque_item", "missing_tool_output", "settlement", "tool_payload"])
def test_replay_rejects_corruption_before_any_new_boundary(monkeypatch, corruption):
    original_init = replay.ReplayTape.__init__

    def corrupt(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if corruption == "request":
            self.rows[0]["request"]["temperature"] = 0
        elif corruption == "opaque_item":
            item = self.rows[0]["response"]["body"]["output"][0]
            self.saved_output_items[item["id"]] = {**item, "id": "changed"}
        elif corruption == "missing_tool_output":
            call = next(i for i in self.rows[0]["response"]["body"]["output"] if i["type"] == "function_call")
            del self.tool_outputs[call["call_id"]]
        elif corruption == "settlement":
            self.rows[0]["settlement"]["outcome"] = "unknown"
        else:
            call = next(i for i in self.rows[0]["response"]["body"]["output"] if i["type"] == "function_call")
            actor, text = self.tool_outputs[call["call_id"]]
            altered = json.loads(text); altered["content"] += " changed"
            self.tool_outputs[call["call_id"]] = (actor, json.dumps(altered, ensure_ascii=False))

    monkeypatch.setattr(replay.ReplayTape, "__init__", corrupt)
    with pytest.raises(replay.ReplayMismatch):
        replay.replay_episode(RUN, "development-warmup_raw-1")


def test_original_failed_run_stays_terminal_and_no_unstarted_dyad_is_replaced():
    manifest = json.loads((RUN / "manifest.json").read_text())
    assert manifest["status"] == "partial"
    with pytest.raises(replay.ReplayMismatch, match="previously active"):
        replay.replay_episode(RUN, "development-seeded-1")


def test_checkpoint_is_stable_across_distinct_temporary_scratch_paths():
    first = replay.replay_episode(RUN, "development-warmup_raw-1")
    second = replay.replay_episode(RUN, "development-warmup_raw-1")
    assert first["checkpoint_digest"] == second["checkpoint_digest"]
    assert first["checkpoint"] == second["checkpoint"]
    assert all(str(RUN / "scratch") in path for path in first["checkpoint"]["scratch_files"])


class FirstNewCall(RuntimeError):
    pass


class NoExternalTools:
    def __getattr__(self, name):
        raise AssertionError("Historical replay attempted live sandbox operation: " + name)


@pytest.fixture
def live_adapter(tmp_path):
    from experiments.induction_v3 import engine
    episode, agents, _ = replay.load()
    from experiments.protocol import code_tests, dispatch
    owners = [(episode, "_prepare_agent_task"), (agents, "_complete_with_retries"),
              (agents, "reasoning_to_replay"), (agents, "_extract_reasoning_trace"),
              (episode, "_is_context_window_error"), (code_tests, "_run_python_tests"),
              (dispatch, "_inspect_database"), (dispatch, "_query_database")]
    saved = [(owner, name, getattr(owner, name)) for owner, name in owners]

    class OfflineDelegate:
        def __init__(self):
            self.local = threading.local()
            self.calls = []
        def context(self, **metadata):
            self.local.metadata = metadata
        def complete(self, **kwargs):
            body = replay.make_body(**{k: kwargs[k] for k in ("model", "messages", "completion_kwargs", "temperature", "max_output_tokens")})
            self.calls.append((copy.deepcopy(self.local.metadata), body))
            raise FirstNewCall("offline sentinel; no dispatch")

    delegate = OfflineDelegate()
    wrapper = replay.ReplayTransport(delegate, resume_run_id="offline-test-resume")
    episode = engine.install_adapters(NoExternalTools(), wrapper, tmp_path)
    uninstall = replay.install_replay_hooks(wrapper)
    try:
        yield wrapper, episode, delegate
    finally:
        uninstall()
        for owner, name, value in saved:
            setattr(owner, name, value)


def _drive_to_live_boundary(adapter, suffix, corrupt=False):
    wrapper, episode, delegate = adapter
    from experiments.models import AgentConfig, EpisodeRunConfig
    from experiments.induction_v3.common import MODEL, VENDOR
    from experiments.induction_v3.schedule import policy_for
    dyad_id = "development-" + suffix
    proof = json.loads((ROOT / f"reports/induction-v3/execution/replay-final-{suffix}.json").read_text())
    if corrupt:
        proof["checkpoint"]["state"]["round"] += 1
        proof["checkpoint_digest"] = digest(proof["checkpoint"])
    dyad = json.loads((RUN / "dyads" / (dyad_id + ".json")).read_text())
    release = json.loads((RUN / "release.json").read_text())
    recipe = next(r for r in release["recipes"] if r["recipe_id"] == dyad["recipe_id"])
    schedule = release["schedules"]["development"][0]
    jobs = (schedule["warmup"] if recipe["warmup"]["episodes"] else []) + schedule["main"]
    histories = {a: copy.deepcopy(dyad["episodes"][-1]["agents"][a]["messages"]) for a in ("alice", "bob")}
    wrapper.context(dyad_id=dyad_id, recipe_id=recipe["recipe_id"], campaign_phase=proof["campaign_phase"], episode=proof["episode"])
    expected_error = replay.ReplayMismatch if corrupt else FirstNewCall
    with pytest.raises(expected_error):
        with wrapper.episode_replay(RUN, dyad, proof, histories=histories):
            episode.run_one_episode(pair=tuple(jobs[proof["episode"] - 1]),
                                    agent_configs={a: AgentConfig(MODEL, "high") for a in histories},
                                    repo_root=VENDOR, agent_messages=histories,
                                    config=EpisodeRunConfig(**policy_for(recipe, proof["campaign_phase"]), temp_run_id=dyad_id,
                                                            episode_index=proof["episode"] - 1, verbose=False,
                                                            reflection=True, reward=True, verdict_review=True))
    return proof


@pytest.mark.parametrize("suffix", ["reference-1", "warmup_raw-1", "warmup_summary-1"])
def test_shared_adapter_falls_back_once_only_at_exact_audited_boundary(live_adapter, suffix):
    proof = _drive_to_live_boundary(live_adapter, suffix)
    calls = live_adapter[2].calls
    assert len(calls) == 1
    metadata, body = calls[0]
    assert body == proof["checkpoint"]["pending_request"]
    assert metadata["resume_run_id"] == "offline-test-resume"
    assert metadata["source_logical_call_index"] == proof["boundary"]["physical_call_index"]
    assert metadata["source_failed_reservation_id"] == proof["boundary"].get("reservation_id")


def test_shared_adapter_rejects_state_mismatch_before_delegate(live_adapter):
    _drive_to_live_boundary(live_adapter, "warmup_raw-1", corrupt=True)
    assert live_adapter[2].calls == []


def test_shared_adapter_keeps_concurrent_replay_tapes_separate(live_adapter):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_drive_to_live_boundary, live_adapter, suffix)
                   for suffix in ("warmup_raw-1", "warmup_summary-1")]
        proofs = [future.result() for future in futures]
    calls = live_adapter[2].calls
    assert len(calls) == 2
    expected = {p["dyad_id"]: p["boundary"]["request_hash"] for p in proofs}
    assert {metadata["dyad_id"]: digest(body) for metadata, body in calls} == expected
