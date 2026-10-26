"""Offline scientific branch and dispatch-gate checks; no credentials or HTTP."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from experiments.recovery_v3 import runner as r


@pytest.fixture
def bundle():
    return r.read_json(r.ROOT / r.FIXTURES)


@pytest.fixture
def histories():
    return {actor: r.fresh_history(actor) + [
        {"role": "user", "content": actor + " prior review"},
        {"role": "assistant", "content": actor + " prior reflection", "provider_output_items": [
            {"type": "reasoning", "id": actor + "-opaque", "encrypted_content": "opaque-unchanged"},
            {"type": "message", "id": actor + "-output", "content": []}]}]
        for actor in ("alice", "bob")}


def test_all_six_sources_and_480_cells_are_prespecified(bundle):
    rows = r.build_schedule(bundle)
    assert len(rows) == len({c["cell_id"] for c in rows}) == 480
    assert {c["endpoint_id"] for c in rows} == set(r.SOURCE_IDS)
    for endpoint in r.SOURCE_IDS:
        cells = [c for c in rows if c["endpoint_id"] == endpoint]
        assert len(cells) == 80
        for actor in ("alice", "bob"):
            for phase in ("immediate", "sequential"):
                matched = [[c["fixture_id"] for c in cells if c["reviewer"] == actor
                            and c["phase"] == phase and c["arm_id"] == arm[0]] for arm in r.ARMS]
                assert all(order == matched[0] for order in matched)
                subset = [c for c in cells if c["reviewer"] == actor and c["phase"] == phase and c["arm_id"] == r.ARMS[0][0]]
                assert sum(c["task_correct"] for c in subset) == 2


def test_roles_counterbalanced_before_outcomes(bundle):
    rows = r.build_schedule(bundle)
    orders = [[c["fixture_id"] for c in rows if c["endpoint_id"] == r.SOURCE_IDS[0]
               and c["reviewer"] == role and c["arm_id"] == r.ARMS[0][0] and c["phase"] == "immediate"]
              for role in ("alice", "bob")]
    assert orders[0] != orders[1]
    assert set(orders[0]) == set(orders[1])


def test_reset_keeps_identical_system_and_no_provider_state(histories):
    value = r.branch_history(histories, "alice", "full_reset_correction")
    assert value == r.fresh_history("alice") + [{"role": "user", "content": r.CORRECTION}]
    assert "opaque" not in json.dumps(value)


def test_retained_is_exact_own_prefix_and_independent_copy(histories):
    before = copy.deepcopy(histories)
    a = r.branch_history(histories, "alice", "full_retained_neutral")
    assert a[:-1] == histories["alice"]
    assert "bob prior" not in json.dumps(a)
    a[2]["provider_output_items"][0]["encrypted_content"] = "mutated test copy"
    assert histories == before


def test_system_mismatch_fails(histories):
    histories["alice"][0]["content"] = "different policy"
    with pytest.raises(ValueError, match="system policy"):
        r.branch_history(histories, "alice", "full_reset_neutral")


def test_one_available_source_produces_40_independent_and_10_serial_jobs(bundle):
    jobs = r.group_jobs(r.build_schedule(bundle), [r.SOURCE_IDS[0]])
    assert len(jobs) == 50
    assert sum(len(j) == 1 for j in jobs) == 40
    assert sum(len(j) == 4 for j in jobs) == 10
    assert sum(map(len, jobs)) == 80
    for job in jobs:
        if len(job) == 4:
            assert [c["position"] for c in job] == [1, 2, 3, 4]
            assert all(c["phase"] == "sequential" for c in job)


def test_sequential_correction_once_and_only_own_carryover(bundle, histories):
    jobs = r.group_jobs(r.build_schedule(bundle), [r.SOURCE_IDS[0]])
    job = next(j for j in jobs if len(j) == 4 and j[0]["arm_id"] == "full_retained_correction")
    seen = []
    def perform(cell, h):
        seen.append(copy.deepcopy(h))
        return {"valid": True, "messages": h + [{"role": "assistant", "content": "response-" + str(cell["position"])}]}
    endpoints = {r.SOURCE_IDS[0]: {"histories": histories}}
    r.execute_branch(job, endpoints, perform)
    assert len(seen) == 4
    assert all(sum(m.get("content") == r.CORRECTION for m in h) == 1 for h in seen)
    assert not any(m.get("content") == "response-1" for m in seen[0])
    assert any(m.get("content") == "response-1" for m in seen[1])
    assert seen[0][:-1] == histories[job[0]["reviewer"]]
    other = r.branch_history(histories, job[0]["reviewer"], job[0]["arm_id"])
    assert not any(m.get("content", "").startswith("response-") for m in other)


def test_invalid_verdict_leaves_sequential_tail_unknown(bundle, histories):
    job = next(j for j in r.group_jobs(r.build_schedule(bundle), [r.SOURCE_IDS[0]]) if len(j) == 4)
    called = []
    def perform(cell, h):
        called.append(cell["cell_id"])
        return {"valid": False, "messages": h}
    r.execute_branch(job, {r.SOURCE_IDS[0]: {"histories": histories}}, perform)
    assert len(called) == 1


def test_stopped_queue_makes_zero_calls(bundle, histories):
    job = r.group_jobs(r.build_schedule(bundle), [r.SOURCE_IDS[0]])[0]
    def forbidden(*args):
        raise AssertionError("dispatch after stop")
    r.execute_branch(job, {r.SOURCE_IDS[0]: {"histories": histories}}, forbidden, stopped=lambda: True)


def test_capability_is_six_separate_fresh_cells(bundle):
    cells = r.capability_schedule(bundle)
    assert len(cells) == 6
    research_ids = {row["fixture_id"] for row in r.build_schedule(bundle)}
    assert not research_ids & {c["fixture_id"] for c in cells}
    for role in ("alice", "bob"):
        assert [c["expected_verdict"] for c in cells if c["reviewer"] == role] == ["accept", "reject", "reject"]


def test_request_body_keeps_exact_retained_provider_items(bundle, histories):
    cells = r.build_schedule(bundle)
    cell = cells[0]
    cases = {c["public"]["fixture_id"]: c for c in bundle["cases"]}
    h = r.branch_history(histories, "alice", cell["arm_id"])
    body = r.request_body(h, cell, cases)
    assert body["input"][2:4] == histories["alice"][2]["provider_output_items"]
    assert body["max_output_tokens"] == 64000
    assert body["reasoning"] == {"effort": "high"}
    assert "expected_verdict" not in json.dumps(body)
    assert "task_correct" not in json.dumps(body)


def test_run_validation_fails_before_credentials_or_lock():
    with patch.object(r, "validate", side_effect=ValueError("tampered release")), \
            patch("experiments.induction_v3.transport.load_authorized_key", side_effect=AssertionError("credential read")), \
            patch.object(r, "ProgramLock", side_effect=AssertionError("lock acquired")):
        with pytest.raises(ValueError, match="tampered"):
            r.run("missing.json")


def test_prepare_lock_exclusion_precedes_candidate(tmp_path):
    with patch.object(r, "ProgramLock", side_effect=BlockingIOError("induction owns lock")), \
            patch.object(r, "candidate", side_effect=AssertionError("prepared while live")):
        with pytest.raises(BlockingIOError):
            r.prepare(tmp_path / "new.json")


def test_local_allocation_preserves_minimum_of_all_caps():
    totals = {"remaining_v3_usd": "19", "remaining_lifetime_usd": "12", "remaining_v3_attempts": 550}
    history = [{"run_id": "induction-v3-recovery-old", "consumed": {"cost_usd": "3"},
                "reserved": {"cost_usd": "1"}, "physical_attempts": 80}]
    with patch.object(r, "historical_totals", return_value=totals):
        limits = r.remaining_recovery_allocation(history)
    assert limits["campaign_limit_usd"] == "12"
    assert limits["max_attempts"] == 520


def test_overwritten_release_is_rejected_before_any_preparation(tmp_path):
    path = tmp_path / "release.json"
    path.write_text("{}")
    with patch.object(r, "ProgramLock", side_effect=AssertionError("lock requested")):
        with pytest.raises(ValueError, match="already exists"):
            r.prepare(path)
