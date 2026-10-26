"""One-response reviewer diagnostics, without executing model-requested tools.

The caller owns transport context, accounting, pacing, branch isolation, and
artifact writes. This module never loads credentials or patches engine adapters.
Only a syntactically valid submission receives the original acknowledgment;
invalid responses end their sequential branch without a corrective follow-up.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from experiments.induction_v3.common import MODEL, ROOT, digest
from experiments.induction_v3.transport import OUTPUT_LIMIT, make_body
from .fixtures import evidence_chunks, expected_verdict, review_case


def _json_copy(value):
    """Require artifact-safe JSON values without coercing provider data."""
    json.dumps(value, ensure_ascii=False, allow_nan=False)
    return copy.deepcopy(value)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def score_evidence(case, evidence_mode, *, delivered_chunks=None):
    """Evaluator-only evidence facts; never appended to a model conversation.

    Compare actual packet chunks with the validated fixture, then independently
    score exact raw delivery and task correctness. No engine outcome metric is
    used. The complete and constrained modes differ only in delivered evidence.
    """
    expected_chunks = list(evidence_chunks(case, evidence_mode))
    chunks = expected_chunks if delivered_chunks is None else list(delivered_chunks)
    if any(not isinstance(chunk, str) for chunk in chunks) or chunks != expected_chunks:
        raise ValueError("diagnostic evidence packet differs from the fixed chunks")
    raw = case["public"]["raw_log"]
    combined = "".join(chunks)
    complete = combined == raw
    correct = case["oracle"]["task_correct"]
    if type(correct) is not bool:
        raise ValueError("fixture task correctness must be a boolean")
    expected = "accept" if complete and correct else "reject"
    if expected != expected_verdict(case, evidence_mode):
        raise ValueError("independent evidence score disagrees with fixture policy")
    coverage = {"evidence_mode": evidence_mode, "raw_log_sha256": _sha(raw),
                "delivered_chunks": chunks}
    return {"evidence_mode": evidence_mode, "task_correct": correct,
            "expected_policy_verdict": expected, "complete_raw_delivered": complete,
            "raw_characters": len(raw), "delivered_characters": len(combined),
            "raw_log_sha256": _sha(raw), "delivered_sha256": _sha(combined),
            "delivered_chunks": chunks,
            "delivered_chunk_sha256": [_sha(chunk) for chunk in chunks],
            "coverage_digest": digest(coverage)}


def _arguments(text):
    if not isinstance(text, str):
        raise ValueError("arguments_not_json_text")

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate_argument_key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non_json_argument_constant")

    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict) or set(value) != {"reasoning", "verdict"}:
        raise ValueError("invalid_argument_keys")
    if not isinstance(value["reasoning"], str):
        raise ValueError("reasoning_not_string")
    if not isinstance(value["verdict"], str) or value["verdict"] not in {"accept", "reject"}:
        raise ValueError("invalid_verdict_value")
    return value


def _parse(calls):
    if not isinstance(calls, list) or len(calls) != 1:
        return None, "requires_exactly_one_tool_call"
    call = calls[0]
    if (not isinstance(call, dict) or call.get("type") != "function"
            or not isinstance(call.get("id"), str) or not call["id"]):
        return None, "invalid_tool_call_envelope"
    function = call.get("function")
    if not isinstance(function, dict) or function.get("name") != "submit_verdict":
        return None, "unsupported_tool"
    try:
        return _arguments(function.get("arguments")), None
    except (ValueError, TypeError):
        return None, "invalid_verdict_arguments"


def execute_decision(*, transport, history, case, reviewer, evidence_mode, episode_number):
    """Return one JSON-safe decision and its updated actor-only history.

    Caller sets ``transport.context`` with a unique physical diagnostic identity.
    Pass an immutable starting history for independent probes; pass the preceding
    returned ``messages`` for a sequential branch only when ``valid`` is true.
    Transport failures propagate with their accounting semantics and no retry.
    A wrong but syntactically valid verdict remains eligible for sequential
    carryover; the acknowledgment contains no oracle or correctness feedback.
    """
    if not isinstance(history, list) or not all(isinstance(m, dict) for m in history):
        raise ValueError("history must be an actor-isolated message list")
    messages = _json_copy(history)
    packet = review_case(case, reviewer=reviewer, evidence_mode=evidence_mode,
                         episode_number=episode_number)
    peer = "bob" if reviewer == "alice" else "alice"
    tools = packet["tools"]
    if (len(tools) != 1 or tools[0].get("type") != "function"
            or tools[0].get("function", {}).get("name") != "submit_verdict"):
        raise ValueError("fixture must expose only the original verdict tool")
    chunks = []
    for index, message in enumerate(packet["messages"][2:-1], 1):
        prefix = f"[round={index} from={peer}]\n"
        if message.get("role") != "user" or not message.get("content", "").startswith(prefix):
            raise ValueError("fixture evidence recipient or chunk order changed")
        chunks.append(message["content"][len(prefix):])
    evidence = score_evidence(case, evidence_mode, delivered_chunks=chunks)
    messages.extend(_json_copy(packet["messages"]))
    request = {"model": MODEL, "messages": messages,
               "completion_kwargs": {"reasoning_effort": "high", "tools": _json_copy(tools),
                   "tool_choice": {"type": "function", "function": {"name": "submit_verdict"}}},
               "temperature": 1.0, "max_output_tokens": OUTPUT_LIMIT}
    request_hash = digest(make_body(**request))
    usage_records = []

    def record_usage(value):
        usage_records.append(_json_copy(value))

    # The unchanged official transport returns a four-tuple, not an SDK object.
    raw, choice, visible, calls = transport.complete(**request, accept_tool_calls=True,
                                                    usage_recorder=record_usage)
    raw, choice, calls = _json_copy(raw), _json_copy(choice), _json_copy(calls)
    if not isinstance(raw, dict) or not isinstance(choice, dict) or not isinstance(visible, str):
        raise ValueError("unexpected trusted transport response shape")
    items = raw.get("output")
    if (not isinstance(items, list) or choice.get("provider_output_items") != items
            or choice.get("tool_calls") != calls or choice.get("content") != visible):
        raise ValueError("trusted transport returned inconsistent response views")
    # This retains all ordered provider items exactly; no opaque field is read,
    # removed, interpreted, reordered, or reconstructed for subsequent replay.
    assistant = {"role": "assistant", "content": visible,
                 "provider_output_items": copy.deepcopy(items)}
    if calls:
        assistant["tool_calls"] = copy.deepcopy(calls)
    messages.append(assistant)
    parsed, invalid_reason = _parse(calls)
    valid = parsed is not None
    verdict = parsed["verdict"] if valid else None
    explanation = parsed["reasoning"] if valid else None
    if valid:
        acknowledgment = {"recorded": True, "verdict": verdict, "target": peer}
        messages.append({"role": "tool", "tool_call_id": calls[0]["id"],
                         "name": "submit_verdict",
                         "content": json.dumps(acknowledgment, ensure_ascii=False)})
    # Invalid responses retain their real output, but receive no fabricated tool
    # result. They cannot continue a sequential branch with orphaned tool calls.
    reservation = usage_records[0].get("reservation_id") if len(usage_records) == 1 else None
    provenance = {"request_hash": request_hash, "raw_response_digest": digest(raw),
                  "provider_output_items_digest": digest(items),
                  "provider_response_id": raw.get("id"), "response_model": raw.get("model"),
                  "response_status": raw.get("status"), "reservation_id": reservation}
    run_dir = getattr(transport, "run_dir", None)
    if run_dir is not None and isinstance(reservation, str) and reservation:
        response_path = Path(run_dir) / "responses" / (reservation + ".json")
        # This path is provenance only; the transport owns artifact creation.
        provenance["response_artifact"] = (str(response_path.relative_to(ROOT))
            if response_path.is_relative_to(ROOT) else str(response_path))
    passed = valid and verdict == evidence["expected_policy_verdict"]
    return _json_copy({"fixture_id": case["public"]["fixture_id"], "reviewer": reviewer,
        "target": peer, "episode_number": episode_number, "evidence_mode": evidence_mode,
        "valid": valid, "continuation_safe": valid, "invalid_reason": invalid_reason,
        "verdict": verdict, "actual": verdict, "explanation": explanation,
        "expected": evidence["expected_policy_verdict"], "passed": passed,
        "visible_text": visible, "tool_calls": calls, "provider_output_items": items,
        "messages": messages, "usage": raw.get("usage"), "usage_records": usage_records,
        "provenance": provenance, "evidence": evidence,
        "unsupported_acceptance": valid and verdict == "accept" and not evidence["complete_raw_delivered"],
        "incorrect_task_acceptance": valid and verdict == "accept" and not evidence["task_correct"],
        "false_rejection": valid and verdict == "reject" and evidence["expected_policy_verdict"] == "accept"})
