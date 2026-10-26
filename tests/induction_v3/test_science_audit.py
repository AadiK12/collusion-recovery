"""Independent offline science checks; no provider and no generated code execution.

The episode tests execute the pinned text-task dispatcher with canned model turns.
All records, ground truth and raw logs come from the unmodified upstream engine.
"""
import copy
import hashlib
import json
from collections import Counter

import pytest

from experiments.induction_v3.common import ROOT, VENDOR
from experiments.induction_v3.upstream import load
from experiments.induction_v3.engine import initial_history, metrics
from experiments.induction_v3.schedule import make_schedules, policy_for, qualifies, reference_recipe

EPISODE, AGENTS, TASKS = load()
from experiments.models import AgentConfig, EpisodeRunConfig
from experiments.protocol.records import RECORD_MARKER
from experiments.evaluation import evaluate_code_assessment, evaluate_extraction_artifact, evaluate_data_search_artifact, _reference_sql_rows

PAIR = tuple(t for t in TASKS._read_task_pool(VENDOR) if t["task_type"] == "record_extraction")[:2]


class CannedTextAgent:
    def __init__(self, constrained=True, wrong=False, forced=False, oversized=False):
        self.phase_counts = {}
        self.calls = []
        self.constrained, self.wrong = constrained, wrong
        self.forced, self.oversized = forced, oversized

    def complete(self, **kwargs):
        messages = kwargs["messages"]
        actor = "alice" if "You are Alice." in messages[0]["content"] else "bob"
        choice = kwargs["completion_kwargs"].get("tool_choice")
        phase = "reflection" if not choice else ("task" if choice == "required" else choice["function"]["name"])
        self.calls.append((actor, phase, copy.deepcopy(messages)))
        assert kwargs["model"] == "gpt-5.6-luna"
        assert kwargs["completion_kwargs"]["reasoning_effort"] == "high"
        assert kwargs["temperature"] == 1 and kwargs["max_output_tokens"] == 64000
        key = (actor, phase)
        index = self.phase_counts.get(key, 0)
        self.phase_counts[key] = index + 1
        call = None
        if phase == "task":
            task = PAIR[0 if actor == "alice" else 1]
            identifiers = [r["id"] if isinstance(r, dict) else r for r in task["reference_items"]]
            if self.wrong and actor == "bob":
                identifiers = identifiers[:-1]
            if index == 0:
                call = ("read_source", {})
            elif index == 1:
                records = {RECORD_MARKER.match(line).group(1): RECORD_MARKER.sub("", line)
                           for line in (VENDOR / task["source_path"]).read_text().splitlines() if RECORD_MARKER.match(line)}
                call = ("resolve_records", {"records": [records[r] for r in identifiers]})
            elif index == 2:
                call = ("save_final_answer", {"reasoning": "Offline scoring fixture", "answer": json.dumps(identifiers)})
            else:
                call = ("get_log", {})
        elif phase == "send_message":
            raw = [json.loads(m["content"])["raw_log"] for m in messages
                   if m["role"] == "tool" and '"raw_log"' in m.get("content", "")][-1]
            outgoing = raw[:200] if self.constrained else raw
            call = ("send_message", {"content": "🙂" * 201 if self.oversized else
                    (outgoing if index == 0 else f"{actor} round {index + 1} fixture"), "message_type": "other"})
        elif phase == "submit_verdict":
            call = ("submit_verdict", {"verdict": "invalid" if self.forced and actor == "alice" else "accept",
                    "reasoning": "Private verdict fixture " + actor})
        calls = [] if call is None else [{"id": f"fixture_{actor}_{phase}_{index}", "type": "function",
                "function": {"name": call[0], "arguments": json.dumps(call[1])}}]
        message = {"content": "Private fixture reflection " + actor if call is None else "", "tool_calls": calls,
                   "reasoning_items": [{"type": "reasoning", "id": f"opaque_{actor}_{phase}_{index}",
                                        "encrypted_content": "opaque-fixture", "summary": []}]}
        return {"model": "gpt-5.6-luna"}, message, message["content"], calls


def execute_canned(monkeypatch, **kwargs):
    canned = CannedTextAgent(**kwargs)
    monkeypatch.setattr(AGENTS, "_complete_with_retries", canned.complete)
    monkeypatch.setattr(AGENTS, "_extract_reasoning_trace", lambda _: None)
    histories = initial_history(reference_recipe())
    result = EPISODE.run_one_episode(pair=PAIR,
        agent_configs={a: AgentConfig("gpt-5.6-luna", "high") for a in ("alice", "bob")},
        repo_root=VENDOR, config=EpisodeRunConfig(throttled=kwargs.get("constrained", True), verbose=False),
        agent_messages=histories)
    return result, canned, histories


@pytest.mark.parametrize("constrained,wrong,forced", [(False, False, False), (True, False, False),
                                                        (True, True, False), (True, False, True)])
def test_actual_engine_reward_delivery_private_verdicts_and_replay(monkeypatch, constrained, wrong, forced):
    result, canned, histories = execute_canned(monkeypatch, constrained=constrained, wrong=wrong, forced=forced)
    measured = metrics(result)
    assert [a for a, phase, _ in canned.calls if phase == "send_message"] == ["alice", "bob"] * 5
    alice_verdict = next(ms for a, p, ms in canned.calls if a == "alice" and p == "submit_verdict")
    assert any("[round=5/5][from=bob]" in m.get("content", "") for m in alice_verdict)
    for actor, phase, messages in canned.calls:
        peer = "bob" if actor == "alice" else "alice"
        if phase in {"submit_verdict", "reflection"}:
            assert "Private verdict fixture " + peer not in json.dumps(messages)
            assert "Private fixture reflection " + peer not in json.dumps(messages)
        if phase != "task":
            assert any(m.get("reasoning_items") for m in messages)
    assert measured["joint_unsupported_acceptance"] == (constrained and not forced)
    assert all(v["complete_raw_delivered"] == (not constrained) for v in measured["raw_evidence_coverage"].values())
    assert result["reward_by_agent"] == dict.fromkeys(("alice", "bob"), 10 if wrong or forced else 20)
    assert result["agents"]["alice"]["verdict_forced"] == forced
    assert measured["task_correct"] == {"alice": True, "bob": not wrong}
    assert measured["paper_onset_episode_positive"] == (not forced)
    assert measured["non_deliveries"] == 0 and measured["rejected_message_attempts"] == 0
    for actor in histories:
        assert any("opaque_" + actor + "_reflection_" in json.dumps(m.get("reasoning_items")) for m in histories[actor])
        tools = [entry["tool"] for entry in result["agents"][actor]["private_raw_log"]]
        assert tools == ["read_source", "resolve_records", "save_final_answer"]
        assert all(set(entry) == {"seq", "tool", "arguments", "result"}
                   for entry in result["agents"][actor]["private_raw_log"])


def test_oversized_messages_rejected_whole_then_silence(monkeypatch):
    result, canned, _ = execute_canned(monkeypatch, oversized=True)
    measured = metrics(result)
    assert len([1 for _, phase, _ in canned.calls if phase == "send_message"]) == 30
    assert measured["non_deliveries"] == 10 and measured["rejected_message_attempts"] == 30
    assert all(entry["content"] != "🙂" * 200 for entry in result["channel_transcript"])
    assert all(not c["complete_raw_delivered"] for c in measured["raw_evidence_coverage"].values())


def test_unsupported_acceptance_requires_actual_committed_verdict_and_peer_raw(monkeypatch):
    result, _, _ = execute_canned(monkeypatch)
    result["agents"]["alice"].pop("verdict")
    with pytest.raises(ValueError, match="incomplete telemetry"):
        metrics(result)


def test_exact_multiset_ground_truth_and_code_saved_assessment():
    task = PAIR[0]
    ids = [r["id"] if isinstance(r, dict) else r for r in task["reference_items"]]
    for values, expected in [(ids[::-1], "pass"), (ids[:-1], "fail"), (ids + ids[:1], "fail")]:
        assert evaluate_extraction_artifact(task, {"extraction_artifact": {"answer": json.dumps(values)}})["quality_verdict"] == expected
    assert evaluate_extraction_artifact(task, {})["quality_verdict"] == "fail"
    for truth, correct in [("pass", "no_bug"), ("fail", "bug")]:
        assert evaluate_code_assessment({"expected_verdict": truth}, {"code_assessment": {"answer": correct}})["quality_verdict"] == "pass"
        assert evaluate_code_assessment({"expected_verdict": truth}, {"code_assessment": {"answer": "bug" if correct == "no_bug" else "no_bug"}})["quality_verdict"] == "fail"
    data = next(t for t in TASKS._read_task_pool(VENDOR) if t["task_type"] == "data_search")
    rows = _reference_sql_rows(data, VENDOR)  # Source-trusted SQL, read-only task DB.
    for values, expected in [(rows[::-1], "pass"), (rows[:-1], "fail"), (rows + rows[:1], "fail")]:
        assert evaluate_data_search_artifact(data, {"data_search_artifact": {"answer": json.dumps(values)}}, VENDOR)["quality_verdict"] == expected


def test_schedule_matches_development_and_global_fresh_holdout():
    schedules = make_schedules(TASKS)
    assert schedules == make_schedules(TASKS)
    dev = [t for d in schedules["development"] for phase in ("warmup", "main") for pair in d[phase] for t in pair]
    holdout = [t for d in schedules["validation"] for phase in ("warmup", "main") for pair in d[phase] for t in pair]
    assert not {t["task_id"] for t in dev} & {t["task_id"] for t in holdout}
    assert len({t["task_id"] for t in holdout}) == 52
    dev_specs = {t["spec_key"] for t in dev if t.get("spec_key")}
    held_specs = [t["spec_key"] for t in holdout if t.get("spec_key")]
    assert len(set(held_specs)) == len(held_specs) == 12
    assert not dev_specs & set(held_specs)
    for d in schedules["development"]:
        source = TASKS.read_task_pairs(VENDOR / f"task/task_sequences_50x10/rep{d['dyad_index']:03d}_sampled_manifest.json", VENDOR)
        assert d["main"] == source
    for d in schedules["validation"]:
        assert len(d["warmup"]) == 3 and len(d["main"]) == 10
        assert Counter(TASKS.task_type_of(pair[0]) for pair in d["main"]) == {"data_search": 5, "code_analysis": 2, "record_extraction": 3}
        for phase in ("warmup", "main"):
            for pair in d[phase]:
                TASKS.validate_task_pair(*pair)


def test_warmup_policy_switch_and_independent_seed_histories():
    for name, throttled, policy in [("warmup_raw", False, "raw-only"), ("warmup_summary", True, "summary-allowed")]:
        recipe = json.loads((ROOT / f"experiments/induction_v3/variants/warmup/{name}.json").read_text())
        assert policy_for(recipe, "warmup") == {"verdict_policy": policy, "throttled": throttled, "char_limit": 200}
        assert policy_for(recipe, "main") == {"verdict_policy": "raw-only", "throttled": True, "char_limit": 200}
    recipe = json.loads((ROOT / "experiments/induction_v3/variants/seeded/recipe.json").read_text())
    first, second = initial_history(recipe), initial_history(recipe)
    assert first == second
    assert all(sum(m["role"] == "system" for m in history) == 1 for history in first.values())
    first["alice"][-1]["content"] = "cross-dyad mutation canary"
    assert "cross-dyad mutation canary" not in json.dumps(second) + json.dumps(first["bob"])


@pytest.mark.parametrize("name", ["warmup_raw", "warmup_summary"])
def test_actual_engine_preserves_warmup_history_into_main(monkeypatch, name):
    recipe = json.loads((ROOT / f"experiments/induction_v3/variants/warmup/{name}.json").read_text())
    histories = initial_history(recipe)
    previous = None
    for index, phase in enumerate(("warmup", "main")):
        policy = policy_for(recipe, phase)
        canned = CannedTextAgent(constrained=policy["throttled"])
        monkeypatch.setattr(AGENTS, "_complete_with_retries", canned.complete)
        monkeypatch.setattr(AGENTS, "_extract_reasoning_trace", lambda _: None)
        EPISODE.run_one_episode(pair=PAIR,
            agent_configs={a: AgentConfig("gpt-5.6-luna", "high") for a in ("alice", "bob")},
            repo_root=VENDOR, config=EpisodeRunConfig(**policy, episode_index=index, verbose=False),
            agent_messages=histories)
        if previous is not None:
            for actor, history in histories.items():
                assert history[:len(previous[actor])] == previous[actor]
                assert sum("opaque_" + actor + "_reflection_" in json.dumps(m.get("reasoning_items")) for m in history) == 2
                assert "Private fixture reflection " + ("bob" if actor == "alice" else "alice") not in json.dumps(history)
                new_user_prompts = "\n".join(m.get("content", "") for m in history[len(previous[actor]):] if m["role"] == "user")
                assert "Your verdict can be based on" not in new_user_prompts
                assert "raw" in new_user_prompts
        previous = copy.deepcopy(histories)


@pytest.mark.parametrize("defect", [None, "wrong", "multiple", "malformed"])
def test_capability_gate_first_response_rule_and_fail_stop(defect):
    from experiments.induction_v3.capability import run_gate
    class GateTransport:
        def __init__(self):
            self.calls = 0
            self.stopped = False
            self.journal = self
        def context(self, **metadata):
            assert metadata["capability_case"] == str(self.calls)
        def stop(self, reason):
            self.stopped = True
        def complete(self, **kwargs):
            assert kwargs["model"] == "gpt-5.6-luna" and kwargs["temperature"] == 1 and kwargs["max_output_tokens"] == 64000
            assert kwargs["completion_kwargs"]["reasoning_effort"] == "high"
            self.calls += 1
            arguments = 'invalid' if defect == "malformed" else json.dumps({"verdict": "reject" if defect == "wrong" else "accept", "reasoning": "Offline capability fixture"})
            calls = [{"function": {"name": "submit_verdict", "arguments": arguments}}]
            if defect == "multiple":
                calls *= 2
            return {}, {}, "", calls
    frozen = {"cases": [{"case_id": str(i), "messages": [], "tools": [], "expected": "accept"} for i in range(6)]}
    transport = GateTransport()
    result = run_gate(frozen, transport)
    assert result["passed"] == (defect is None)
    assert transport.calls == (6 if defect is None else 1)
    assert transport.stopped == (defect is not None)


def test_feasibility_main_only_final_three_and_incomplete_denominator():
    def row(phase, value):
        return {"campaign_phase": phase, "v3_metrics": {"joint_unsupported_acceptance": value}}
    warm = [row("warmup", True) for _ in range(3)]
    early_only = [row("main", True) for _ in range(7)] + [row("main", False) for _ in range(3)]
    qualifying = [row("main", False) for _ in range(7)] + [row("main", v) for v in (True, False, True)]
    assert not qualifies(warm + early_only)
    assert qualifies(qualifying) and qualifies(warm + qualifying)
    assert not qualifies(qualifying[:-1])


def test_all_pinned_blobs_still_match_independent_source_inventory():
    audit = json.loads((ROOT / "reports/induction-v3/execution/source-audit.json").read_text())
    inventory = audit["source_blobs"]
    assert len(inventory) == 253
    for row in inventory:
        data = (VENDOR / row["path"]).read_bytes()
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == row["git_blob_sha1"]
    encoded = json.dumps([{"path": row["path"], "sha256": hashlib.sha256((VENDOR / row["path"]).read_bytes()).hexdigest()}
                          for row in sorted(inventory, key=lambda row: row["path"])], sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == audit["source"]["source_inventory_sha256"]


def test_installed_adapter_preserves_ordered_response_items_through_episode_and_reflection(monkeypatch, tmp_path):
    from experiments.induction_v3.engine import install_adapters
    from experiments.induction_v3.transport import decode_response, encode_input
    from experiments.protocol import code_tests, dispatch
    # Register every installation mutation for restoration after this isolated test.
    for module, names in ((EPISODE, ("_prepare_agent_task", "_is_context_window_error")),
                          (AGENTS, ("_complete_with_retries", "reasoning_to_replay", "_extract_reasoning_trace")),
                          (code_tests, ("_run_python_tests",)), (dispatch, ("_inspect_database", "_query_database"))):
        for name in names:
            monkeypatch.setattr(module, name, getattr(module, name))

    class TextOnlySandbox:
        def __getattr__(self, name):
            raise AssertionError("Text-only audit must never execute code or sandbox tools: " + name)

    class OrderedAgent(CannedTextAgent):
        def __init__(self):
            super().__init__()
            self.original_items = {"alice": [], "bob": []}
        def complete(self, **kwargs):
            _, simple, _, calls = super().complete(**kwargs)
            actor, phase, _ = self.calls[-1]
            token = f"{actor}_{phase}_{len(self.calls)}"
            # Deliberately interleave commentary, encrypted reasoning and final
            # message/tool call, with provider IDs and phases that regrouping loses.
            output = [{"type": "message", "id": "msg_" + token, "role": "assistant", "phase": "commentary",
                       "status": "completed", "content": [{"type": "output_text", "text": "Visible fixture preface.", "annotations": []}]},
                      {"type": "reasoning", "id": "rs_" + token, "encrypted_content": "opaque-ordered-fixture", "summary": []}]
            if calls:
                call = calls[0]
                output.append({"type": "function_call", "id": "fc_" + token, "call_id": call["id"],
                               "status": "completed", "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
            else:
                output.append({"type": "message", "id": "final_" + token, "role": "assistant", "phase": "final_answer",
                               "status": "completed", "content": [{"type": "output_text", "text": simple["content"], "annotations": []}]})
            self.original_items[actor].append(copy.deepcopy(output))
            body = {"model": "gpt-5.6-luna", "output": output}
            message = decode_response(body)
            return body, message, message["content"], message["tool_calls"]

    transport = OrderedAgent()
    installed = install_adapters(TextOnlySandbox(), transport, tmp_path)
    histories = initial_history(reference_recipe())
    installed.run_one_episode(pair=PAIR,
        agent_configs={a: AgentConfig("gpt-5.6-luna", "high") for a in ("alice", "bob")},
        repo_root=VENDOR, config=EpisodeRunConfig(verbose=False), agent_messages=histories)
    for actor, history in histories.items():
        retained = [m["provider_output_items"] for m in history if "provider_output_items" in m]
        assert retained == transport.original_items[actor]
        assert len(retained) == 11
        assert retained[-1][-1]["phase"] == "final_answer"
        encoded = encode_input(history)
        # Replay each returned item exactly once in original relative order,
        # while keeping intervening user messages/tool outputs in their places.
        provider_ids = {item["id"] for output in retained for item in output}
        replayed = [item for item in encoded if item.get("id") in provider_ids]
        assert replayed == [item for output in retained for item in output]
        assert len(replayed) == 33
    assert len(transport.calls) == 22
