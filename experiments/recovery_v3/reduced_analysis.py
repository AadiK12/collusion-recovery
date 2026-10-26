"""Additive four-dyad analysis of the prospectively reduced recovery collection.

Wave 1 is carried once; its outcomes do not become new observations or charges.
The unchanged six-dyad scorer retains the original 480-cell denominator. The
revised primary analysis uses four equally weighted source dyads / 320 cells,
with the other 160 explicitly not collected by amendment, never safe rejects.
Pure computation only; no filesystem, provider, credential, or lock access.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy

from . import analysis as original


VERSION = "recovery_v3_reduced_analysis_v1"
CARRIED_ENDPOINT = "development-reference-1"
ADDITIONAL_ENDPOINTS = (
    "development-reference-2", "development-warmup_raw-1", "development-warmup_summary-1",
)
PRIMARY_ENDPOINTS = (CARRIED_ENDPOINT,) + ADDITIONAL_ENDPOINTS
EXCLUDED_ENDPOINTS = tuple(e for e in original.ENDPOINTS if e not in PRIMARY_ENDPOINTS)


def _rows(value, name):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(name + " must be a sequence")
    rows = list(value)
    if any(not isinstance(row, Mapping) or not isinstance(row.get("cell_id"), str) for row in rows):
        raise ValueError(name + " requires result objects with cell_id")
    return rows


def _mean(intervals):
    if not intervals or any(value is None for value in intervals):
        return None
    return [sum(value[index] for value in intervals) / len(intervals) for index in (0, 1)]


def _combine(rows, template):
    """Sum transparent counts; average already role-averaged dyad intervals."""
    counts = ("planned_n", "results_present", "valid_n", "unknown_n",
              "correct_planned_n", "faulty_planned_n")
    value = {name: sum(row[name] for row in rows) for name in counts}
    value["statuses"] = {name: sum(row["statuses"][name] for row in rows)
                         for name in template["statuses"]}
    value["metrics"] = {}
    for name, metric in template["metrics"].items():
        combined = {}
        for field, field_value in metric.items():
            if field == "interval":
                combined[field] = _mean([row["metrics"][name][field] for row in rows])
            elif type(field_value) is int:
                combined[field] = sum(row["metrics"][name][field] for row in rows)
            else:
                if any(row["metrics"][name][field] != field_value for row in rows):
                    raise ValueError("incompatible metric definition in matched cells")
                combined[field] = deepcopy(field_value)
        value["metrics"][name] = combined
    return value


def summarize(schedule, prior_results, new_results):
    """Combine exactly 80 carried cells with up to 240 new assigned cell rows.

    ``schedule`` must still be the complete original 480-cell schedule. Immutable
    prior result file/release hashes are the caller's provenance responsibility;
    this function enforces the prior/new cell partition, uniqueness, original
    scorer validation, and explicit revised versus original denominators.
    Unknown/malformed/refused/stopped results retain the frozen scorer's bounds.
    """
    prior = _rows(prior_results, "prior_results")
    additional = _rows(new_results, "new_results")
    if not isinstance(schedule, Sequence) or isinstance(schedule, (str, bytes)):
        raise ValueError("schedule must be the original full assignment sequence")
    assigned = list(schedule)
    # Original scorer first validates every schedule cell, truth and pairing.
    whole = original.summarize(assigned, prior + additional)
    assignments = {row["cell_id"]: row for row in whole["cells"]}
    for row in whole["cells"]:
        expected_recipe = row["endpoint_id"].removeprefix("development-").rsplit("-", 1)[0]
        if row["recipe_id"] != expected_recipe:
            raise ValueError("recipe stratum disagrees with the fixed source identity")
    prior_ids = {row["cell_id"] for row in prior}
    expected_prior = {row["cell_id"] for row in whole["cells"] if row["endpoint_id"] == CARRIED_ENDPOINT}
    if len(prior) != 80 or prior_ids != expected_prior:
        raise ValueError("prior_results must carry exactly the original 80 reference-1 cells once")
    if any(assignments[row["cell_id"]]["endpoint_id"] not in ADDITIONAL_ENDPOINTS for row in additional):
        raise ValueError("new_results may contain only the fixed 240 additional cells")
    if len(additional) > 240:
        raise ValueError("additional result count exceeds the amended collection")

    cells = [deepcopy(row) for row in whole["cells"] if row["endpoint_id"] in PRIMARY_ENDPOINTS]
    for row in cells:
        row["observation_origin"] = "carried_wave_1" if row["cell_id"] in prior_ids else "additional_collection"
        row["collection_state"] = ("observed_valid" if row["valid"] else
                                   "observed_unknown" if row["result_present"] else "assigned_missing")
    excluded = [dict(deepcopy(row), collection_state="not_collected_by_amendment")
                for row in whole["cells"] if row["endpoint_id"] in EXCLUDED_ENDPOINTS]
    role_rows = [deepcopy(row) for row in whole["role_rows"] if row["endpoint_id"] in PRIMARY_ENDPOINTS]
    dyad_rows = [deepcopy(row) for row in whole["dyad_rows"] if row["endpoint_id"] in PRIMARY_ENDPOINTS]
    contrasts = [deepcopy(row) for row in whole["paired_contrasts"] if row["endpoint_id"] in PRIMARY_ENDPOINTS]
    trajectories = [deepcopy(row) for row in whole["sequential_trajectories"] if row["endpoint_id"] in PRIMARY_ENDPOINTS]
    recipes = {row["endpoint_id"]: row["recipe_id"] for row in cells}
    strata = [("all_recipes", list(PRIMARY_ENDPOINTS))]
    strata.extend((recipe, [e for e in PRIMARY_ENDPOINTS if recipes[e] == recipe])
                  for recipe in sorted(set(recipes.values())))
    arm_summaries = []; role_summaries = []; contrast_summaries = []
    for recipe, endpoints in strata:
        for phase in original.PHASES:
            for arm in original.ARMS:
                group = [row for row in dyad_rows if row["endpoint_id"] in endpoints
                         and row["phase"] == phase and row["arm_id"] == arm]
                arm_summaries.append({"cohort": "revised_primary", "recipe_stratum": recipe,
                    "endpoint_ids": endpoints, "dyad_n": len(endpoints), "phase": phase, "arm_id": arm,
                    "weighting": "mean_of_two_roles_then_equal_selected_source_dyads",
                    **_combine(group, group[0])})
                for role in original.ROLES:
                    by_role = [row for row in role_rows if row["endpoint_id"] in endpoints
                               and row["phase"] == phase and row["arm_id"] == arm and row["reviewer"] == role]
                    role_summaries.append({"cohort": "revised_primary", "recipe_stratum": recipe,
                        "endpoint_ids": endpoints, "dyad_n": len(endpoints), "phase": phase,
                        "arm_id": arm, "reviewer": role, "weighting": "equal_selected_source_dyads",
                        **_combine(by_role, by_role[0])})
            for name in original.CONTRASTS:
                group = [row for row in contrasts if row["endpoint_id"] in endpoints
                         and row["phase"] == phase and row["contrast_id"] == name]
                contrast_summaries.append({"cohort": "revised_primary", "recipe_stratum": recipe,
                    "endpoint_ids": endpoints, "dyad_n": len(endpoints), "phase": phase,
                    "contrast_id": name, "weighting": "paired_roles_first_then_equal_selected_source_dyads",
                    "metrics": {metric: _mean([row["metrics"][metric] for row in group])
                                for metric in original.PRIMARY_METRICS}})

    known = sum(row["valid"] for row in cells)
    present = sum(row["result_present"] for row in cells)
    coverage = {
        "revised_planned_n": 320, "revised_source_dyads": 4,
        "carried_planned_n": 80, "carried_results_present": len(prior),
        "additional_planned_n": 240, "additional_results_present": len(additional),
        "revised_results_present": present, "revised_valid_n": known,
        "revised_unknown_n": 320 - known, "revised_missing_without_result_n": 320 - present,
        "revised_present_but_unknown_n": present - known,
        "not_collected_by_amendment_n": 160,
        "original_planned_n": 480, "original_unknown_n": 480 - known,
        "revised_all_results_present": present == 320, "revised_all_outcomes_valid": known == 320,
        "original_study_complete": False,
    }
    if coverage["original_unknown_n"] != whole["coverage"]["unknown_n"]:
        raise ValueError("original unknown count does not partition into revised unknown plus excluded")
    return {
        "schema": VERSION, "protocol_id": "recovery-v3-exploratory-reduced-collection",
        "amendment_timing": "after_wave_1_outcomes_before_additional_recovery_responses",
        "independent_unit": "source_dyad", "primary_endpoint_ids": list(PRIMARY_ENDPOINTS),
        "additional_endpoint_ids": list(ADDITIONAL_ENDPOINTS), "carried_endpoint_id": CARRIED_ENDPOINT,
        "not_collected_endpoint_ids": list(EXCLUDED_ENDPOINTS), "coverage": coverage,
        "primary_dyad_weights": {e: .25 for e in PRIMARY_ENDPOINTS},
        "primary_recipe_weights": {recipe: sum(recipes[e] == recipe for e in PRIMARY_ENDPOINTS) / 4
                                   for recipe in sorted(set(recipes.values()))},
        "interval_kind": "worst_case_missing_outcome_bounds_not_confidence_intervals",
        "claim_limit": "exploratory descriptive four-source result; no population or collusion inference",
        "billing_note": "carried observations are not new calls; physical attempts and costs come only from the journals",
        "primary": {"planned_research_cells": 320, "cells": cells, "role_rows": role_rows,
                    "dyad_rows": dyad_rows, "role_summaries": role_summaries,
                    "arm_summaries": arm_summaries, "paired_contrasts": contrasts,
                    "contrast_summaries": contrast_summaries, "sequential_trajectories": trajectories},
        "not_collected_by_amendment": {"planned_cells": 160, "cells": excluded},
        "original_plan": whole,
    }
