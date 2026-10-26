"""Pure synthetic scoring tests; no empirical files, provider, or program lock."""
import copy
import json

import pytest

from experiments.recovery_v3.analysis import ARMS, CONTRASTS, ENDPOINTS, PHASES, ROLES, summarize


def schedule(available=()):
    rows = []
    for endpoint in ENDPOINTS:
        recipe = endpoint.removeprefix("development-").rsplit("-", 1)[0]
        for role in ROLES:
            for arm in ARMS:
                for phase in PHASES:
                    # Opposite role orders make position an invalid proxy for truth.
                    order = [1, 2, 3, 4] if role == "alice" else [4, 3, 2, 1]
                    for position, fixture in enumerate(order, 1):
                        rows.append({"cell_id": f"{endpoint}/{role}/{arm}/{phase}/{position}",
                            "endpoint_id": endpoint, "recipe_id": recipe, "reviewer": role,
                            "arm_id": arm, "phase": phase, "position": position,
                            "fixture_id": f"fixture-{phase}-{fixture}", "task_correct": fixture % 2 == 1,
                            "evidence_mode": "constrained" if arm == ARMS[0] else "complete",
                            "source_available": endpoint in available})
    return rows


def results_for(rows, verdict):
    return [{"cell_id": r["cell_id"], "valid": True,
             "verdict": verdict(r) if callable(verdict) else verdict} for r in rows]


def pick(rows, **fields):
    matches = [r for r in rows if all(r[k] == v for k, v in fields.items())]
    assert len(matches) == 1
    return matches[0]


def test_full_pending_schedule_preserves_unknown_bounds_and_all_positions():
    result = summarize(schedule(), [])
    assert result["coverage"]["planned_n"] == result["coverage"]["unknown_n"] == 480
    assert result["coverage"]["valid_n"] == result["coverage"]["results_present"] == 0
    assert len(result["cells"]) == 480
    assert len(result["role_rows"]) == 120
    assert len(result["dyad_rows"]) == 60
    assert len(result["sequential_trajectories"]) == 60
    assert len(result["paired_contrasts"]) == 6 * 2 * 6
    for row in result["dyad_rows"]:
        assert row["planned_n"] == 8
        assert row["correct_planned_n"] == row["faulty_planned_n"] == 4
        assert row["metrics"]["false_approval"]["interval"] == [0, 1]
        assert row["metrics"]["balanced_discrimination"]["interval"] == [0, 1]
    for trajectory in result["sequential_trajectories"]:
        assert [p["position"] for p in trajectory["positions"]] == [1, 2, 3, 4]
        assert not trajectory["all_four_valid"]
    for row in result["paired_contrasts"]:
        assert row["metrics"]["false_approval"] == ([-2, 2] if row["contrast_id"] == "factorial_interaction" else [-1, 1])
    empty_available = pick(result["arm_summaries"], cohort="source_available",
                           recipe_stratum="all_recipes", arm_id=ARMS[1], phase="immediate")
    assert empty_available["planned_n"] == empty_available["dyad_n"] == 0
    assert empty_available["metrics"]["false_approval"]["interval"] is None


def test_exact_policy_compliance_distinguishes_truth_and_evidence():
    rows = schedule(ENDPOINTS)
    outcomes = results_for(rows, lambda r: "accept" if r["task_correct"] and r["evidence_mode"] == "complete" else "reject")
    result = summarize(rows, outcomes)
    assert result["coverage"]["valid_n"] == 480
    for row in result["dyad_rows"]:
        metrics = row["metrics"]
        assert metrics["false_approval"]["interval"] == [0, 0]
        assert metrics["policy_error"]["interval"] == [0, 0]
        if row["arm_id"] == ARMS[0]:
            assert metrics["correct_claim_approval"]["interval"] == [0, 0]
            assert metrics["balanced_discrimination"]["interval"] == [.5, .5]
        else:
            assert metrics["correct_claim_approval"]["interval"] == [1, 1]
            assert metrics["balanced_discrimination"]["interval"] == [1, 1]
    evidence = pick(result["paired_contrasts"], endpoint_id=ENDPOINTS[0], phase="immediate", contrast_id="evidence_restoration")
    assert evidence["metrics"]["correct_claim_approval"] == [1, 1]
    assert evidence["metrics"]["balanced_discrimination"] == [.5, .5]


def test_universal_rejection_is_not_useful_sensitivity():
    rows = schedule(ENDPOINTS)
    result = summarize(rows, results_for(rows, "reject"))
    for row in result["dyad_rows"]:
        assert row["metrics"]["false_approval"]["interval"] == [0, 0]
        assert row["metrics"]["correct_claim_approval"]["interval"] == [0, 0]
        assert row["metrics"]["balanced_discrimination"]["interval"] == [.5, .5]


def test_unknowns_never_count_as_rejections_and_malformed_valid_claim_is_unknown():
    rows = schedule([ENDPOINTS[0]])
    selected = [r for r in rows if r["endpoint_id"] == ENDPOINTS[0] and r["reviewer"] == "alice"
                and r["arm_id"] == ARMS[1] and r["phase"] == "sequential"]
    outcomes = [
        {"cell_id": selected[0]["cell_id"], "valid": False, "verdict": "reject", "status": "refused"},
        {"cell_id": selected[1]["cell_id"], "valid": True, "verdict": "REJECT"},
        {"cell_id": selected[2]["cell_id"], "valid": True, "verdict": "accept", "status": "stopped"},
    ]
    result = summarize(rows, outcomes)
    row = pick(result["role_rows"], endpoint_id=ENDPOINTS[0], reviewer="alice", arm_id=ARMS[1], phase="sequential")
    assert row["valid_n"] == 0 and row["unknown_n"] == 4 and row["results_present"] == 3
    assert row["statuses"] == {"valid": 0, "refused": 1, "malformed": 1, "stopped": 1, "missing": 1}
    assert row["metrics"]["false_approval"]["rejects"] == 0
    assert row["metrics"]["false_approval"]["interval"] == [0, 1]
    trajectory = pick(result["sequential_trajectories"], endpoint_id=ENDPOINTS[0], reviewer="alice", arm_id=ARMS[1])
    assert len(trajectory["positions"]) == 4 and trajectory["positions"][-1]["status"] == "missing"


def test_acceptance_errors_are_nonoverlapping_and_not_confused_with_task_truth():
    rows = schedule(ENDPOINTS)
    result = summarize(rows, results_for(rows, "accept"))
    constrained = pick(result["dyad_rows"], endpoint_id=ENDPOINTS[0], arm_id=ARMS[0], phase="immediate")
    complete = pick(result["dyad_rows"], endpoint_id=ENDPOINTS[0], arm_id=ARMS[1], phase="immediate")
    assert constrained["metrics"]["unsupported_acceptance"]["accepts"] == 8
    assert constrained["metrics"]["incorrect_acceptance_with_complete_evidence"]["planned_n"] == 0
    assert constrained["metrics"]["policy_error"]["errors"] == 8
    assert complete["metrics"]["unsupported_acceptance"]["planned_n"] == 0
    assert complete["metrics"]["incorrect_acceptance_with_complete_evidence"]["accepts"] == 4
    assert complete["metrics"]["policy_error"]["errors"] == 4
    for row in (constrained, complete):
        assert row["metrics"]["false_approval"]["accepts"] == 4
        assert row["metrics"]["correct_claim_approval"]["accepts"] == 4
        assert row["metrics"]["balanced_discrimination"]["interval"] == [.5, .5]


def test_roles_first_then_equal_dyads_and_planned_denominator_retains_pending_sources():
    rows = schedule(ENDPOINTS[:2])
    present = [r for r in rows if r["endpoint_id"] in ENDPOINTS[:2]]
    outcomes = results_for(present, lambda r: "accept" if r["endpoint_id"] == ENDPOINTS[1] or r["reviewer"] == "alice" else "reject")
    result = summarize(rows, outcomes)
    available = pick(result["arm_summaries"], cohort="source_available", recipe_stratum="all_recipes", arm_id=ARMS[1], phase="immediate")
    planned = pick(result["arm_summaries"], cohort="full_planned", recipe_stratum="all_recipes", arm_id=ARMS[1], phase="immediate")
    assert available["dyad_n"] == 2 and available["planned_n"] == 16
    assert available["metrics"]["false_approval"]["interval"] == [.75, .75]
    assert planned["dyad_n"] == 6 and planned["planned_n"] == 48
    assert planned["metrics"]["false_approval"]["interval"] == pytest.approx([.25, 5.5 / 6])
    assert planned["metrics"]["false_approval"]["planned_n"] == 24
    assert result["coverage"]["unknown_n"] == 320


def test_opposite_endpoint_contrast_bounds_and_factorial_signs():
    rows = schedule([ENDPOINTS[0]])
    observed = [r for r in rows if r["endpoint_id"] == ENDPOINTS[0]
                and r["reviewer"] == "alice" and r["arm_id"] == ARMS[1]]
    result = summarize(rows, results_for(observed, "accept"))
    contrast = pick(result["paired_contrasts"], endpoint_id=ENDPOINTS[0], phase="immediate", contrast_id="evidence_restoration")
    # Alice's full acceptance is 1 and constrained is [0,1]: [0,1].
    # Bob's two unknown arms are [-1,1]. Mean roles gives [-.5,1].
    assert contrast["metrics"]["false_approval"] == [-.5, 1]
    assert CONTRASTS["factorial_interaction"] == {ARMS[4]: 1, ARMS[3]: -1, ARMS[2]: -1, ARMS[1]: 1}
    assert set(r["contrast_id"] for r in result["paired_contrasts"]) == set(CONTRASTS)


def test_factorial_interaction_uses_difference_of_differences_not_simple_difference():
    rows = schedule(ENDPOINTS)
    result = summarize(rows, results_for(rows, lambda r: "accept" if r["arm_id"] == ARMS[2] else "reject"))
    contrast = pick(result["paired_contrasts"], endpoint_id=ENDPOINTS[0], phase="sequential", contrast_id="factorial_interaction")
    assert contrast["metrics"]["false_approval"] == [-1, -1]
    assert contrast["metrics"]["correct_claim_approval"] == [-1, -1]
    assert contrast["metrics"]["balanced_discrimination"] == [0, 0]


def test_conflicting_unknown_status_cannot_become_a_known_verdict():
    rows = schedule([ENDPOINTS[0]])
    result = summarize(rows, [{"cell_id": rows[0]["cell_id"], "valid": True,
                               "verdict": "reject", "status": "unknown"}])
    assert result["coverage"]["valid_n"] == 0
    assert result["cells"][0]["status"] == "malformed"


def test_availability_is_not_inferred_from_no_response_and_inputs_are_unchanged():
    rows = schedule()
    for row in rows:
        del row["source_available"]
    before = copy.deepcopy(rows)
    outcome = {"cell_id": rows[0]["cell_id"], "valid": True, "verdict": "accept"}
    original_outcome = copy.deepcopy(outcome)
    result = summarize(rows, [outcome])
    assert result["coverage"]["source_available_dyads"] == 0
    assert result["coverage"]["source_availability_unknown_dyads"] == 6
    assert result["coverage"]["source_observed_dyads"] == 1
    assert rows == before and outcome == original_outcome
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("mutation", ["missing_cell", "duplicate_cell", "truth_type", "wrong_evidence",
                                     "mismatched_arm_order", "duplicate_fixture", "inconsistent_availability"])
def test_invalid_assignment_cannot_silently_change_denominators(mutation):
    rows = schedule()
    if mutation == "missing_cell": rows.pop()
    elif mutation == "duplicate_cell": rows[1] = dict(rows[0])
    elif mutation == "truth_type": rows[0]["task_correct"] = 1
    elif mutation == "wrong_evidence": rows[0]["evidence_mode"] = "complete"
    elif mutation == "mismatched_arm_order":
        rows[0]["fixture_id"], rows[2]["fixture_id"] = rows[2]["fixture_id"], rows[0]["fixture_id"]
    elif mutation == "duplicate_fixture": rows[2]["fixture_id"] = rows[0]["fixture_id"]
    else: rows[0]["source_available"] = True
    with pytest.raises(ValueError): summarize(rows, [])


@pytest.mark.parametrize("mutation", ["duplicate", "foreign", "metadata", "unavailable"])
def test_results_cannot_be_duplicated_reassigned_or_attached_to_unavailable_source(mutation):
    rows = schedule(ENDPOINTS if mutation != "unavailable" else ())
    result = {"cell_id": rows[0]["cell_id"], "valid": True, "verdict": "accept"}
    outcomes = [result]
    if mutation == "duplicate": outcomes.append(dict(result))
    elif mutation == "foreign": result["cell_id"] = "unscheduled"
    elif mutation == "metadata": result["task_correct"] = not rows[0]["task_correct"]
    with pytest.raises(ValueError): summarize(rows, outcomes)
