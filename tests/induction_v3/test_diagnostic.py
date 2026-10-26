import copy
import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.induction_v3.diagnostic import (
    make_cells, request_kwargs, execute_cells, remaining_limits, CLARIFICATION, summarize,
)
from experiments.induction_v3.transport import make_body

ROOT = Path(__file__).resolve().parents[2]


def cells():
    return make_cells(json.loads((ROOT / "release/induction-v3/capability.json").read_text()))


def test_exact_original_body_and_only_declared_schema_change():
    rows = cells()
    release = json.loads((ROOT / "release/induction-v3/campaign.json").read_text())
    original = json.loads(next((ROOT / release["run_directory"] / "requests").glob("*.json")).read_text())
    assert make_body(**request_kwargs(rows[0])) == original
    for original, clarified in zip(rows[::2], rows[1::2]):
        before = copy.deepcopy(clarified["messages"])
        assert before.pop(1) == {"role": "user", "content": CLARIFICATION}
        assert before == original["messages"]
        assert original["tools"] == clarified["tools"]
        assert original["fixture_raw_log"] == clarified["fixture_raw_log"]
    assert len({r["cell_id"] for r in rows}) == 12


def test_all_twelve_run_despite_incorrect_and_malformed_verdicts():
    visited, updates = [], []
    class Transport:
        def context(self, **metadata): visited.append(metadata)
        def complete(self, **kwargs):
            return None, None, None, [] if len(visited) == 1 else [{"function": {
                "name": "submit_verdict", "arguments": json.dumps({"verdict":"reject", "reasoning":"test"})}}]
    result = execute_cells(cells(), Transport(), updates.append)
    assert len(visited) == len(updates) == result["completed"] == 12
    assert not result["unrun"] and not result["conditions"]["original"]["passed"]
    assert not result["conditions"]["schema_clarified"]["passed"]


def test_infrastructure_failure_leaves_unrun_cells_without_retry():
    visited, updates = [], []
    class Transport:
        def context(self, **metadata): visited.append(metadata)
        def complete(self, **kwargs):
            if len(visited) == 2: raise RuntimeError("ambiguous")
            return None,None,None,[]
    with pytest.raises(RuntimeError): execute_cells(cells(), Transport(), updates.append)
    assert len(visited) == 2 and updates[-1]["completed"] == 1
    assert len(updates[-1]["unrun"]) == 11


@pytest.mark.parametrize("arguments", [json.dumps({"verdict":[],"reasoning":"test"}),
                                      json.dumps({"verdict":{},"reasoning":"test"}), None, {}])
def test_malformed_verdict_types_do_not_abort_comparison(arguments):
    visited = []
    class Transport:
        def context(self, **metadata): visited.append(metadata)
        def complete(self, **kwargs):
            return None,None,None,[{"function":{"name":"submit_verdict","arguments":arguments}}]
    result = execute_cells(cells(), Transport(), lambda _:None)
    assert len(visited) == result["completed"] == 12
    assert all(r["actual"] is None for r in result["rows"])


def test_caps_subtract_previous_v3_spend_and_attempts():
    prior = {"cost_usd":"0.00067505", "physical_attempts":1}
    assert remaining_limits(prior, diagnostic=True) == {"campaign_limit_usd":"2", "max_attempts":12}
    remainder = remaining_limits(prior, diagnostic=False)
    assert Decimal(remainder["campaign_limit_usd"]) + Decimal(prior["cost_usd"]) == 100
    assert remainder["max_attempts"] + prior["physical_attempts"] == 4000
    assert remaining_limits({"cost_usd":"99.5","physical_attempts":3995}, diagnostic=True) == {"campaign_limit_usd":"0.5","max_attempts":5}
    with pytest.raises(ValueError): remaining_limits({"cost_usd":"100","physical_attempts":1},diagnostic=True)


def test_partial_condition_never_passes_and_previous_failure_never_relabelled():
    result = summarize(cells(), [{"cell_id":"x", "condition":"original", "passed":True}])
    assert not result["conditions"]["original"]["passed"]
    assert result["original_gate_reclassified"] is False
    assert result["induction_started"] is False
