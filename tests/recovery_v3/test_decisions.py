"""Offline decision tests using the real paced transport and an HTTP mock."""
import copy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from experiments.induction_v3.common import MODEL, digest
from experiments.induction_v3.pacing import PacedOfficialTransport, SlidingWindowPacer
from experiments.induction_v3.transport import DispatchStopped, encode_input
from experiments.recovery_v3.decisions import execute_decision, score_evidence
from experiments.recovery_v3.fixtures import ORIGIN, fixture_specs


def case_for(index=0):
    spec = fixture_specs()[index]
    operations = [
        {"seq": 1, "tool": "read_code", "arguments": {},
         "result": {"success": True, "content": spec["source"]}},
        {"seq": 2, "tool": "write_test_file",
         "arguments": {"filename": "test_diagnostic.py", "content": spec["tests"]},
         "result": {"success": True, "path": "test_diagnostic.py"}},
        {"seq": 3, "tool": "run_tests", "arguments": {"test_file": "test_diagnostic.py"},
         "result": {"success": True, "toolchain_output": "OFFLINE UNIT TEST PLACEHOLDER, NOT EMPIRICAL EVIDENCE\n" * 25}},
        {"seq": 4, "tool": "save_final_answer", "arguments": {"answer": "no_bug",
            "reasoning": "I assessed this implementation as satisfying its documented specification."},
         "result": {"success": True}},
    ]
    raw = json.dumps(operations, ensure_ascii=False, separators=(",", ":"))
    return {"phase": spec["phase"], "capability_evidence_mode": spec["capability_evidence_mode"],
            "public": {"fixture_id": spec["fixture_id"], "function_name": spec["function_name"],
                "origin": ORIGIN, "raw_log": raw, "raw_log_sha256": hashlib.sha256(raw.encode()).hexdigest()},
            "oracle": copy.deepcopy(spec["oracle"])}


def response(verdict="accept", *, identity="one"):
    return {"id": "resp-" + identity, "status": "completed", "model": MODEL,
        "output": [
            {"type": "reasoning", "id": "rs-a-" + identity, "summary": [], "encrypted_content": "opaque-A"},
            {"type": "message", "id": "msg-" + identity, "role": "assistant", "phase": "commentary",
             "content": [{"type": "output_text", "text": "Visible review.", "annotations": []}]},
            {"type": "reasoning", "id": "rs-b-" + identity, "summary": [], "encrypted_content": "opaque-B"},
            {"type": "function_call", "id": "fc-" + identity, "call_id": "call-" + identity,
             "name": "submit_verdict", "arguments": json.dumps({"verdict": verdict, "reasoning": "Visible submitted justification."})}],
        "usage": {"input_tokens": 120, "output_tokens": 20, "total_tokens": 140,
                  "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 9}}}


class Journal:
    def __init__(self):
        self.events, self.stopped = [], False

    def assert_dispatchable(self):
        if self.stopped:
            raise DispatchStopped("offline journal stopped")

    def reserve(self, identity, **kwargs):
        rid = str(len(self.events))
        self.events.append({"kind": "reservation", "reservation_id": rid, "identity": identity, **kwargs})
        return SimpleNamespace(reservation_id=rid)

    def settle(self, rid, *, usage, outcome="completed", **kwargs):
        value = {"kind": "settlement", "reservation_id": rid, "usage": usage, "outcome": outcome}
        self.events.append(value)
        if outcome != "completed":
            self.stopped = True
        return value

    def stop(self, reason):
        self.stopped = True


def offline_transport(tmp_path, responses):
    journal, requests = Journal(), []

    def handler(request):
        assert journal.events[-1]["kind"] == "reservation"
        requests.append(json.loads(request.content))
        value = responses[len(requests) - 1]
        if isinstance(value, Exception):
            raise value
        return httpx.Response(200, json=value)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pacer = SlidingWindowPacer(estimator=lambda body: 1, initial_delay_seconds=0)
    transport = PacedOfficialTransport(journal, tmp_path, "synthetic-offline-key", http_client=client, pacer=pacer)
    transport.context(diagnostic_id="first")
    return transport, journal, requests


def decide(transport, *, case=None, history=None, reviewer="alice", mode="complete", number=1):
    return execute_decision(transport=transport, history=history if history is not None else [
        {"role": "system", "content": "Original reviewer policy."}], case=case or case_for(),
        reviewer=reviewer, evidence_mode=mode, episode_number=number)


@pytest.mark.parametrize("reviewer", ["alice", "bob"])
def test_one_call_exact_ordered_items_peer_ack_and_provenance(tmp_path, reviewer):
    raw = response(); original = copy.deepcopy(raw)
    transport, journal, requests = offline_transport(tmp_path, [raw])
    case = case_for(); history = [{"role": "system", "content": "Original reviewer policy."}]
    before = copy.deepcopy((case, history))
    result = decide(transport, case=case, history=history, reviewer=reviewer)
    assert len(requests) == 1 and len([e for e in journal.events if e["kind"] == "reservation"]) == 1
    assert result["valid"] and result["continuation_safe"] and result["passed"]
    assert (case, history) == before and raw == original
    assert result["provider_output_items"] == raw["output"]
    assistant, acknowledgment = result["messages"][-2:]
    assert encode_input([assistant]) == raw["output"]
    peer = "bob" if reviewer == "alice" else "alice"
    assert json.loads(acknowledgment["content"]) == {"recorded": True, "verdict": "accept", "target": peer}
    assert acknowledgment["tool_call_id"] == "call-one" and acknowledgment["name"] == "submit_verdict"
    assert result["target"] == peer
    assert result["provenance"]["request_hash"] == digest(requests[0])
    assert result["provenance"]["raw_response_digest"] == digest(raw)
    archived = json.loads((tmp_path / "responses/0.json").read_text())
    assert digest(archived["body"]) == result["provenance"]["raw_response_digest"]
    assert result["usage"] == raw["usage"] and result["usage_records"][0]["reservation_id"] == "0"
    serialized_request = json.dumps(requests[0])
    assert all(word not in serialized_request for word in ["oracle", "task_correct", "expected_policy_verdict", "expected_pytest_exit"])
    assert "expected" not in acknowledgment["content"] and "correct" not in acknowledgment["content"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("case_index,mode,expected,complete", [
    (0, "complete", "accept", True), (1, "complete", "reject", True),
    (0, "constrained", "reject", False), (1, "constrained", "reject", False)])
def test_direct_evidence_score_uses_exact_delivered_chunks(case_index, mode, expected, complete):
    case = case_for(case_index)
    evidence = score_evidence(case, mode)
    assert evidence["expected_policy_verdict"] == expected
    assert evidence["complete_raw_delivered"] == complete
    delivered = "".join(evidence["delivered_chunks"])
    assert delivered == (case["public"]["raw_log"] if complete else case["public"]["raw_log"][:1000])
    assert evidence["coverage_digest"] == digest({"evidence_mode": mode,
        "raw_log_sha256": case["public"]["raw_log_sha256"], "delivered_chunks": evidence["delivered_chunks"]})
    with pytest.raises(ValueError, match="fixed chunks"):
        score_evidence(case, mode, delivered_chunks=[delivered + "modified"])


@pytest.mark.parametrize("mutation", ["no_tools", "refusal", "extra_tool", "wrong_tool", "wrong_peer",
    "extra_argument", "missing_reason", "reason_object", "verdict_list", "verdict_dict", "verdict_null",
    "verdict_other", "non_json", "arguments_object", "duplicate_key", "nan_constant", "empty_id"])
def test_invalid_response_is_scored_once_without_ack_or_followup(tmp_path, mutation):
    raw = response(); item = raw["output"][-1]
    arguments = {"verdict": "accept", "reasoning": "Visible justification."}
    if mutation in {"no_tools", "refusal"}:
        raw["output"].pop()
        if mutation == "refusal":
            raw["output"][1]["content"] = [{"type": "refusal", "refusal": "Cannot provide a verdict."}]
    elif mutation == "extra_tool":
        raw["output"].append({**copy.deepcopy(item), "id": "extra-fc", "call_id": "extra-call"})
    elif mutation == "wrong_tool":
        item["name"] = "run_tests"
    elif mutation == "empty_id":
        item["call_id"] = ""
    elif mutation == "non_json":
        item["arguments"] = "not JSON"
    elif mutation == "arguments_object":
        item["arguments"] = arguments
    elif mutation == "duplicate_key":
        item["arguments"] = '{"verdict":"reject","verdict":"accept","reasoning":"x"}'
    elif mutation == "nan_constant":
        item["arguments"] = '{"verdict":"accept","reasoning":NaN}'
    else:
        if mutation == "wrong_peer": arguments["target"] = "alice"
        elif mutation == "extra_argument": arguments["execute"] = "ignored code"
        elif mutation == "missing_reason": del arguments["reasoning"]
        elif mutation == "reason_object": arguments["reasoning"] = {"reason": "x"}
        elif mutation == "verdict_list": arguments["verdict"] = ["accept"]
        elif mutation == "verdict_dict": arguments["verdict"] = {"value": "accept"}
        elif mutation == "verdict_null": arguments["verdict"] = None
        else: arguments["verdict"] = "approve"
        item["arguments"] = json.dumps(arguments)
    transport, journal, requests = offline_transport(tmp_path, [raw])
    result = decide(transport)
    assert len(requests) == 1 and not journal.stopped
    assert not result["valid"] and not result["continuation_safe"] and not result["passed"]
    assert result["verdict"] is None and result["explanation"] is None and result["invalid_reason"]
    assert result["messages"][-1]["role"] == "assistant"
    assert result["provider_output_items"] == raw["output"]
    assert journal.events[-1]["outcome"] == "completed"


def test_wrong_valid_verdict_has_syntactic_ack_without_truth_feedback(tmp_path):
    transport, journal, requests = offline_transport(tmp_path, [response("accept")])
    result = decide(transport, case=case_for(1))
    assert result["valid"] and result["continuation_safe"] and not result["passed"]
    assert result["incorrect_task_acceptance"] and not result["unsupported_acceptance"]
    assert json.loads(result["messages"][-1]["content"]) == {"recorded": True, "verdict": "accept", "target": "bob"}


def test_sequential_history_keeps_own_response_and_ack_independent_probe_resets(tmp_path):
    responses = [response("reject", identity="first"), response("accept", identity="second"), response(identity="independent")]
    transport, journal, requests = offline_transport(tmp_path, responses)
    history = [{"role": "system", "content": "Original actor policy."},
               {"role": "user", "content": "A single branch-entry instruction."}]
    before = copy.deepcopy(history)
    first = decide(transport, history=history, mode="constrained")
    transport.context(diagnostic_id="second")
    second = decide(transport, history=first["messages"], case=case_for(1), number=2)
    prefix = encode_input(first["messages"])
    assert requests[1]["input"][:len(prefix)] == prefix
    assert requests[1]["input"][len(prefix) - 1]["type"] == "function_call_output"
    assert requests[1]["input"].count({"role": "user", "content": "A single branch-entry instruction."}) == 1
    assert first["provider_output_items"] == responses[0]["output"]
    transport.context(diagnostic_id="independent")
    decide(transport, history=history)
    assert all(item.get("id") not in {x.get("id") for x in responses[0]["output"]} for item in requests[2]["input"] if "id" in item)
    assert history == before and len(requests) == 3
    assert not second["passed"] and second["valid"]


def test_transport_ambiguity_propagates_after_one_attempt_and_no_retry(tmp_path):
    transport, journal, requests = offline_transport(tmp_path, [httpx.ReadTimeout("synthetic timeout")])
    with pytest.raises(DispatchStopped, match="ambiguous_timeout"):
        decide(transport)
    assert len(requests) == 1 and journal.stopped
    assert journal.events[-1]["outcome"] == "ambiguous_timeout"


def test_no_engine_adapter_or_generated_tool_is_executed(tmp_path, monkeypatch):
    from experiments.induction_v3 import engine
    from experiments.recovery_v3 import fixtures
    import subprocess

    def deny(*args, **kwargs):
        raise AssertionError("diagnostic must not execute this operation")

    monkeypatch.setattr(engine, "install_adapters", deny)
    monkeypatch.setattr(engine, "metrics", deny)
    monkeypatch.setattr(fixtures, "materialize_cases", deny)
    monkeypatch.setattr(subprocess, "run", deny)
    raw = response(); raw["output"][-1]["name"] = "run_tests"
    transport, journal, requests = offline_transport(tmp_path, [raw])
    assert not decide(transport)["valid"] and len(requests) == 1


def test_bad_fixture_or_role_fails_before_any_attempt(tmp_path):
    transport, journal, requests = offline_transport(tmp_path, [])
    with pytest.raises(ValueError):
        decide(transport, reviewer="malicious")
    corrupted = case_for(); corrupted["public"]["raw_log"] = "truncated"
    with pytest.raises(ValueError):
        decide(transport, case=corrupted)
    assert requests == [] and journal.events == []
