import copy
import pytest
from experiments.induction_v3.continuation import select_condition, conditioned_history
from experiments.induction_v3.diagnostic import CLARIFICATION


def diagnostic():
    return {"status":"completed","completed":12,"unrun":[],"rows":[
        {"condition":condition,"reviewer":actor,"kind":kind,"actual":"accept" if kind=="clean_complete" else "reject","passed":True}
        for condition in ("original","schema_clarified") for actor in ("alice","bob")
        for kind in ("clean_complete","incorrect_complete","clean_missing_raw")]}


def test_selection_is_minimum_intervention_and_requires_all_controls():
    value=diagnostic(); assert select_condition(value)=="original"
    value["rows"][0].update(actual="reject",passed=False)
    assert select_condition(value)=="schema_clarified"
    value["rows"][6].update(actual="reject",passed=False)
    with pytest.raises(ValueError): select_condition(value)


def test_incomplete_diagnostic_and_duplicate_cells_cannot_release():
    value=diagnostic(); value["completed"]=11
    with pytest.raises(ValueError): select_condition(value)
    value=diagnostic(); value["rows"][1]=dict(value["rows"][0])
    with pytest.raises(ValueError): select_condition(value)
    value=diagnostic(); value["rows"][7]=dict(value["rows"][6])
    with pytest.raises(ValueError): select_condition(value)


def test_clarification_is_once_after_system_and_before_fixed_seed_for_each_actor():
    original={actor:[{"role":"system","content":actor+" system"},{"role":"user","content":"synthetic seed"}]
              for actor in ("alice","bob")}
    frozen=copy.deepcopy(original)
    assert conditioned_history(original,"original")==original
    modified=conditioned_history(original,"schema_clarified")
    assert original==frozen
    for actor in modified:
        assert modified[actor][0]==original[actor][0]
        assert modified[actor][1]=={"role":"user","content":CLARIFICATION}
        assert modified[actor][2:]==original[actor][1:]
    modified["alice"][1]["content"]="changed"
    assert modified["bob"][1]["content"]==CLARIFICATION
