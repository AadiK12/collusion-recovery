"""Forty ground-truth fixtures through the actual agent parser and scorer."""

from .agents import ScriptedAgent
from .analysis import cell_rates
from .fixtures import DEFECTS, generate_fixture
from .history import History
from .scoring import score_review
from .visibility import delivered_coverage, request_for


def validate_controls():
    fixtures = [generate_fixture(f"validation/v1/{i}", None if i < 20 else DEFECTS[i % 3])
                for i in range(40)]
    own = generate_fixture("validation/v1/own").packet
    result = {"source": "mock", "warning": "SCRIPTED CONTROL VALIDATION, NOT MODEL RESULTS.",
              "fixtures": 40, "correct": 20, "faulty": 20, "controls": {}}
    for control, evidence in (("evidence_policy", "full"), ("evidence_policy", "constrained"),
                              ("always_accept", "full"), ("always_reject", "full")):
        agent, rows = ScriptedAgent(control), []
        for fixture in fixtures:
            packet = fixture.packet
            request = request_for(History(), own, packet, "alice", "evaluation", evidence)
            raw = agent.verdict(request)
            rows.append(score_review(packet, raw, "ok", delivered_coverage(request, packet)))
        rates = cell_rates(rows)
        expected = {("evidence_policy", "full"): (0, 1),
                    ("evidence_policy", "constrained"): (0, 0),
                    ("always_accept", "full"): (1, 1), ("always_reject", "full"): (0, 0)}[(control, evidence)]
        if (rates["far"]["estimate"], rates["cpa"]["estimate"]) != expected:
            raise AssertionError("Scripted control did not recover its known rates")
        result["controls"][f"{control}/{evidence}"] = rates
    result["passed"] = True
    return result
