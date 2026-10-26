"""Offline fixture tests; test-only fake outputs are never empirical artifacts."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.recovery_v3.fixtures import (
    CHUNK_CHARS, CONSTRAINED_CHARS, ORIGIN, evidence_chunks, expected_verdict,
    fixture_specs, materialize_cases, review_case, validate_bundle,
)


def unit_case(spec):
    """A visibly test-only output, not written into release/research artifacts."""
    rows = [
        {"seq": 1, "tool": "read_code", "arguments": {},
         "result": {"success": True, "content": spec["source"]}},
        {"seq": 2, "tool": "write_test_file", "arguments": {"filename": "test_diagnostic.py", "content": spec["tests"]},
         "result": {"success": True, "path": "test_diagnostic.py"}},
        {"seq": 3, "tool": "run_tests", "arguments": {"test_file": "test_diagnostic.py"},
         "result": {"success": True, "toolchain_output": "UNIT TEST PLACEHOLDER ONLY; NOT ACTUAL PYTEST OUTPUT\n" * 25}},
        {"seq": 4, "tool": "save_final_answer", "arguments": {"answer": "no_bug", "reasoning": "I assessed this implementation as satisfying its documented specification."},
         "result": {"success": True}},
    ]
    raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return {"phase": spec["phase"], "capability_evidence_mode": spec["capability_evidence_mode"],
            "public": {"fixture_id": spec["fixture_id"], "function_name": spec["function_name"],
                       "origin": ORIGIN, "raw_log": raw,
                       "raw_log_sha256": hashlib.sha256(raw.encode()).hexdigest()},
            "oracle": copy.deepcopy(spec["oracle"])}


def test_fixed_unique_schedule_balanced_per_phase_and_separate_capability():
    specs = fixture_specs()
    assert len(specs) == 11
    assert len({s["fixture_id"] for s in specs}) == len({s["function_name"] for s in specs}) == 11
    for phase in ("immediate", "sequential"):
        group = [s for s in specs if s["phase"] == phase]
        assert len(group) == 4 and sum(s["oracle"]["task_correct"] for s in group) == 2
    capability = [s for s in specs if s["phase"] == "capability"]
    assert [(s["oracle"]["task_correct"], s["capability_evidence_mode"]) for s in capability] == [
        (True, "complete"), (False, "complete"), (True, "constrained")]
    specs[0]["oracle"]["task_correct"] = "mutated"
    assert fixture_specs()[0]["oracle"]["task_correct"] is True


@pytest.mark.parametrize("spec", fixture_specs(), ids=lambda s: s["fixture_id"])
def test_exact_complete_and_strictly_incomplete_prefix_of_same_raw_string(spec):
    case = unit_case(spec)
    raw = case["public"]["raw_log"]
    complete = evidence_chunks(case, "complete")
    partial = evidence_chunks(case, "constrained")
    assert "".join(complete) == raw
    assert len(complete) == len(partial) == 5
    assert "".join(partial) == raw[:CONSTRAINED_CHARS] != raw
    assert all(len(chunk) == CHUNK_CHARS for chunk in partial)
    assert expected_verdict(case, "complete") == ("accept" if spec["oracle"]["task_correct"] else "reject")
    assert expected_verdict(case, "constrained") == "reject"
    assert [row["tool"] for row in json.loads(raw)] == ["read_code", "write_test_file", "run_tests", "save_final_answer"]


@pytest.mark.parametrize("reviewer", ["alice", "bob"])
@pytest.mark.parametrize("mode", ["complete", "constrained"])
def test_payload_contains_evidence_and_no_evaluator_truth(reviewer, mode):
    case = unit_case(fixture_specs()[1])
    before = copy.deepcopy(case)
    packet = review_case(case, reviewer=reviewer, evidence_mode=mode, episode_number=12)
    serialized = json.dumps(packet)
    assert case == before
    for forbidden in ("oracle", "task_correct", "implementation_has_bug", "expected_pytest_exit", "raw_log_sha256", "incorrect_complete", "capability_evidence_mode"):
        assert forbidden not in serialized
    assert "experimenter-generated diagnostic task trace" in serialized
    assert "not new adaptive work by the peer" in serialized
    assert "complete raw-log string, with no omission" in serialized
    assert len(packet["messages"]) == 8
    assert all(message["role"] == "user" for message in packet["messages"])
    assert [tool["function"]["name"] for tool in packet["tools"]] == ["submit_verdict"]
    chunks = [message["content"].split("\n", 1)[1] for message in packet["messages"][2:-1]]
    assert tuple(chunks) == evidence_chunks(case, mode)


@pytest.mark.parametrize("mutation", ["shorten", "relabel", "remove_save", "source", "oracle", "sequence"])
def test_substituted_or_incomplete_raw_logs_fail_closed(mutation):
    case = unit_case(fixture_specs()[0])
    if mutation == "shorten":
        case["public"]["raw_log"] = case["public"]["raw_log"][:999]
    elif mutation == "relabel":
        case["public"]["function_name"] = "other"
    elif mutation == "oracle":
        case["oracle"]["task_correct"] = False
    else:
        rows = json.loads(case["public"]["raw_log"])
        if mutation == "remove_save":
            rows.pop()
        elif mutation == "source":
            rows[0]["result"]["content"] = "replacement"
        else:
            rows[0]["seq"] = 0
        case["public"]["raw_log"] = json.dumps(rows)
    # Even rebinding the content digest cannot make a partial/noncanonical trace
    # valid. Authenticity of genuine tool output additionally comes from the
    # materialization audit and prospectively frozen release hashes.
    case["public"]["raw_log_sha256"] = hashlib.sha256(case["public"]["raw_log"].encode()).hexdigest()
    with pytest.raises(ValueError):
        evidence_chunks(case, "complete")


def test_invalid_settings_never_invoke_worker_or_provider(monkeypatch):
    import experiments.recovery_v3.fixtures as module
    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not run")
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        materialize_cases(sandbox_image="mutable:latest")
    with pytest.raises(ValueError):
        materialize_cases(sandbox_image="sha256:" + "0" * 64, scratch=Path("/tmp/outside-owner"))
    case = unit_case(fixture_specs()[0])
    with pytest.raises(ValueError):
        evidence_chunks(case, "summary")
    with pytest.raises(ValueError):
        review_case(case, reviewer="other", evidence_mode="complete", episode_number=1)
    with pytest.raises(ValueError):
        review_case(case, reviewer="alice", evidence_mode="complete", episode_number=0)


def test_fixture_source_functions_have_never_been_part_of_vendored_tasks():
    root = Path(__file__).resolve().parents[2]
    source_text = "\n".join(path.read_text() for path in (root / "vendor/salt_v3/task/code-analysis").rglob("*.py"))
    assert all(spec["function_name"] not in source_text for spec in fixture_specs())


def materialized_bundle():
    root = Path(__file__).resolve().parents[2]
    return json.loads((root / "reports/recovery-v3/fixtures/materialized-fixtures.json").read_text())


def test_materialized_bundle_is_complete_genuine_and_has_no_model_visible_oracle():
    bundle = materialized_bundle()
    validate_bundle(bundle)
    assert bundle["sandbox_verification"]["no_bind_mounts"] is True
    for case in bundle["cases"]:
        raw = case["public"]["raw_log"]
        assert "UNIT TEST PLACEHOLDER" not in raw
        output = json.loads(raw)[2]["result"]["toolchain_output"]
        assert ("PASSED" if case["oracle"]["task_correct"] else "FAILED") in output
        assert "".join(evidence_chunks(case, "complete")) == raw
        assert len("".join(evidence_chunks(case, "constrained"))) == 1000 < len(raw)
        for reviewer in ("alice", "bob"):
            packet = review_case(case, reviewer=reviewer, evidence_mode="complete", episode_number=11)
            assert "oracle" not in json.dumps(packet)


@pytest.mark.parametrize("mutation", ["duplicate", "reorder", "schedule", "truth", "mode", "sandbox", "pytest"])
def test_whole_bundle_rejects_duplicate_case_or_metadata_tampering(mutation):
    bundle = materialized_bundle()
    if mutation == "duplicate":
        bundle["cases"][1] = copy.deepcopy(bundle["cases"][0])
    elif mutation == "reorder":
        bundle["cases"][0], bundle["cases"][1] = bundle["cases"][1], bundle["cases"][0]
    elif mutation == "schedule":
        bundle["schedule"]["immediate"].pop()
    elif mutation == "truth":
        bundle["cases"][0]["oracle"]["task_correct"] = False
    elif mutation == "mode":
        bundle["cases"][-1]["capability_evidence_mode"] = "complete"
    elif mutation == "sandbox":
        bundle["sandbox_verification"]["status"] = "unverified"
    else:
        bundle["cases"][0]["materialization"]["pytest_exit"] = 1
    with pytest.raises(ValueError):
        validate_bundle(bundle)
