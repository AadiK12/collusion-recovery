"""Actual administrative-stop replay; no provider, credentials or live locks."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from experiments.induction_v3.common import ROOT, MODEL, VENDOR, digest, file_hash
from experiments.induction_v3.transport import encode_input
from experiments.recovery_v3 import continuation_replay as replay


RUN = ROOT / replay.SOURCE_RUN


@pytest.fixture(scope="module")
def proofs():
    before = {str(p): file_hash(p) for p in RUN.rglob("*") if p.is_file()}
    value = {name: replay.replay_episode(RUN, name) for name in replay.ALLOWED_DYADS}
    assert before == {str(p): file_hash(p) for p in RUN.rglob("*") if p.is_file()}
    return value


@pytest.mark.parametrize("name,number,count,boundary", [
    ("development-warmup_raw-1", 10, 24, "42c7ab6730df92958d2caf5b53d538030f0868cc245f403f435fe55609cae8bd"),
    ("development-warmup_summary-1", 13, 13, "4bf23be441750f5c2149972698e6e31cd1c2f35e636bf16e1a39fe80e3d413bb"),
])
def test_all_selected_cached_calls_match_exact_unsampled_boundary(proofs, name, number, count, boundary):
    proof = proofs[name]
    assert proof["status"] == "passed" and proof["episode"] == number
    assert proof["provider_calls"] == proof["generated_code_executions"] == 0
    assert proof["cached_response_count"] == proof["matched_request_count"] == count
    assert proof["deterministic_unrecorded_tool_results"] == 2
    assert proof["boundary"] == {"kind": "never_dispatched", "physical_call_index": count, "request_hash": boundary}
    assert digest(proof["checkpoint"]) == proof["checkpoint_digest"]
    assert len({r["reservation_id"] for r in proof["consumed_responses"]}) == count
    assert [r["physical_call_index"] for r in proof["consumed_responses"]] == list(range(count))
    for row in proof["consumed_responses"]:
        identity = json.loads(row["original_physical_identity"])
        assert identity["resume_run_id"] == RUN.name
        assert identity["dyad_id"] == name and identity["episode"] == number
    assert replay.HANDOFF_PROOF in proof["input_inventory"]
    assert replay.HANDOFF_FINAL in proof["input_inventory"]


@pytest.mark.parametrize("name", ["development-reference-2", "development-seeded-1"])
def test_unstarted_and_excluded_dyads_cannot_use_partial_replay(name):
    with pytest.raises(replay.ReplayMismatch, match="outside assigned"):
        replay.replay_episode(RUN, name)


@pytest.mark.parametrize("corruption", ["request", "opaque_item", "missing_external_result", "wrong_actor", "unknown_late_tool"])
def test_corruption_fails_before_delegate_or_external_execution(monkeypatch, corruption):
    original_init = replay.ContinuationReplayTape.__init__

    def corrupt(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        if corruption == "request":
            self.rows[0]["request"]["temperature"] = 0
        elif corruption == "opaque_item":
            item = self.rows[0]["response"]["body"]["output"][0]
            self.saved_output_items[item["id"]] = {**item, "id": "changed"}
        elif corruption in {"missing_external_result", "wrong_actor"}:
            call = next(item for row in self.rows for item in row["response"]["body"]["output"]
                        if item["type"] == "function_call" and item["name"] in {"run_tests", "query_database", "inspect_database"})
            if corruption == "missing_external_result":
                del self.tool_outputs[call["call_id"]]
            else:
                actor, content = self.tool_outputs[call["call_id"]]
                self.tool_outputs[call["call_id"]] = ("bob" if actor == "alice" else "alice", content)
        else:
            last = self.rows[-1]["response"]["body"]["output"]
            next(i for i in last if i["type"] == "function_call")["name"] = "run_tests"

    monkeypatch.setattr(replay.ContinuationReplayTape, "__init__", corrupt)
    with pytest.raises(replay.ReplayMismatch):
        replay.replay_episode(RUN, "development-warmup_raw-1")


@pytest.mark.parametrize("corruption", ["settlement", "physical_index"])
def test_original_settlement_and_physical_identity_are_mandatory(monkeypatch, corruption):
    original_read = replay.ContinuationReplayTape.read

    def altered_read(self, relative, lines=False):
        value = original_read(self, relative, lines=lines)
        if relative == "journal.jsonl":
            event = next(e for e in value if e["kind"] == "reservation"
                         and e["metadata"]["dyad_id"] == self.dyad["dyad_id"]
                         and e["metadata"]["episode"] == len(self.dyad["episodes"]) + 1)
            if corruption == "settlement":
                settlement = next(e for e in value if e["kind"] == "settlement"
                                  and e["reservation_id"] == event["reservation"]["reservation_id"])
                settlement["outcome"] = "unknown"
            else:
                identity = json.loads(event["reservation"]["identity"])
                identity["physical_call_index"] = 99
                event["reservation"]["identity"] = json.dumps(identity)
        return value

    monkeypatch.setattr(replay.ContinuationReplayTape, "read", altered_read)
    with pytest.raises(replay.ReplayMismatch):
        replay.replay_episode(RUN, "development-warmup_raw-1")


def test_checkpoint_does_not_depend_on_temporary_scratch_path(proofs):
    repeated = replay.replay_episode(RUN, "development-warmup_raw-1")
    assert repeated["checkpoint"] == proofs["development-warmup_raw-1"]["checkpoint"]


class FirstNewCall(RuntimeError):
    pass


class NoExternalTools:
    def __getattr__(self, name):
        raise AssertionError("Historical replay attempted live sandbox operation: " + name)


@pytest.fixture
def adapter(tmp_path):
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
            body = replay.make_body(**{k: kwargs[k] for k in
                ("model", "messages", "completion_kwargs", "temperature", "max_output_tokens")})
            self.calls.append((copy.deepcopy(self.local.metadata), body))
            raise FirstNewCall("offline sentinel; no dispatch")

    delegate = OfflineDelegate()
    wrapper = replay.ContinuationReplayTransport(delegate, resume_run_id="offline-second-continuation")
    episode = engine.install_adapters(NoExternalTools(), wrapper, tmp_path)
    uninstall = replay.install_replay_hooks(wrapper)
    try:
        yield wrapper, episode, delegate
    finally:
        uninstall()
        for owner, name, value in saved:
            setattr(owner, name, value)


def drive(adapter, proof):
    wrapper, episode, _ = adapter
    from experiments.models import AgentConfig, EpisodeRunConfig
    dyad = json.loads((RUN / "dyads" / (proof["dyad_id"] + ".json")).read_text())
    release = json.loads((RUN / "release.json").read_text())
    recipe = next(r for r in release["recipes"] if r["recipe_id"] == dyad["recipe_id"])
    schedule = release["schedules"]["development"][0]
    jobs = schedule["warmup"] + schedule["main"]
    histories = {a: copy.deepcopy(dyad["episodes"][-1]["agents"][a]["messages"])
                 for a in ("alice", "bob")}
    wrapper.context(dyad_id=dyad["dyad_id"], recipe_id=dyad["recipe_id"],
                    campaign_phase=proof["campaign_phase"], episode=proof["episode"])
    with wrapper.episode_replay(RUN, dyad, proof, histories=histories):
        episode.run_one_episode(pair=tuple(jobs[proof["episode"] - 1]),
            agent_configs={a: AgentConfig(MODEL, "high") for a in histories},
            repo_root=VENDOR, agent_messages=histories,
            config=EpisodeRunConfig(**replay.policy_for(recipe, proof["campaign_phase"]),
                temp_run_id=dyad["dyad_id"], episode_index=proof["episode"] - 1,
                verbose=False, reflection=True, reward=True, verdict_review=True))


@pytest.mark.parametrize("name", replay.ALLOWED_DYADS)
def test_delegate_called_once_only_after_every_cached_response(adapter, proofs, name):
    proof = proofs[name]
    with pytest.raises(FirstNewCall):
        drive(adapter, proof)
    assert len(adapter[2].calls) == 1
    metadata, body = adapter[2].calls[0]
    assert body == proof["checkpoint"]["pending_request"]
    assert metadata["resume_run_id"] == "offline-second-continuation"
    assert metadata["source_logical_call_index"] == proof["cached_response_count"]
    assert metadata["source_failed_reservation_id"] is None


def test_mismatched_checkpoint_never_reaches_delegate(adapter, proofs):
    proof = copy.deepcopy(proofs[replay.ALLOWED_DYADS[0]])
    proof["checkpoint"]["state"]["round"] += 1
    proof["checkpoint_digest"] = digest(proof["checkpoint"])
    with pytest.raises(replay.ReplayMismatch):
        drive(adapter, proof)
    assert adapter[2].calls == []


def test_two_concurrent_replays_keep_actor_tapes_and_boundaries_separate(adapter, proofs):
    def one(name):
        with pytest.raises(FirstNewCall):
            drive(adapter, proofs[name])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(one, replay.ALLOWED_DYADS))
    assert len(adapter[2].calls) == 2
    actual = {metadata["dyad_id"]: digest(body) for metadata, body in adapter[2].calls}
    assert actual == {name: proofs[name]["boundary"]["request_hash"] for name in replay.ALLOWED_DYADS}
