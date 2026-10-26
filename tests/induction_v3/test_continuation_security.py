"""Offline continuation safety tests: no provider, Docker, or real credentials."""
import copy
import json
import threading
import time

import pytest

from experiments.induction_v3 import continuation as runner
from experiments.induction_v3 import cli, engine, isolation, transport
from experiments.induction_v3.accounting import DispatchStopped
from experiments.induction_v3.common import atomic_json, file_hash


def complete_diagnostic():
    return {"status": "completed", "completed": 12, "unrun": [], "rows": [
        {"condition": condition, "reviewer": actor, "kind": kind,
         "actual": "accept" if kind == "clean_complete" else "reject", "passed": True}
        for condition in ("original", "schema_clarified") for actor in ("alice", "bob")
        for kind in ("clean_complete", "incorrect_complete", "clean_missing_raw")
    ]}


@pytest.mark.parametrize("mutation", ["missing_other_arm", "duplicate_other_arm", "extra_cell"])
def test_passing_original_arm_does_not_bypass_complete_other_arm(mutation):
    value = complete_diagnostic()
    if mutation == "missing_other_arm":
        value["rows"].pop()
    elif mutation == "duplicate_other_arm":
        value["rows"][-1] = dict(value["rows"][-2])
    else:
        value["rows"].append(dict(value["rows"][0]))
    with pytest.raises(ValueError):
        runner.select_condition(value)


@pytest.fixture
def offline_run(tmp_path, monkeypatch):
    (tmp_path / "runs/empirical").mkdir(parents=True)
    antecedent = tmp_path / "antecedent/diagnostic_result.json"
    atomic_json(antecedent, complete_diagnostic())
    prior = {"cost_usd": "0", "physical_attempts": 0, "runs": []}
    names = ("reference", "warmup_raw", "warmup_summary", "seeded")
    release = {
        "run_id": "induction-v3-continuation-offline-security",
        "run_directory": "runs/empirical/induction-v3-continuation-offline-security",
        "release_hash": "a" * 64, "campaign_id": "offline-security",
        "source_inventory": {"antecedent/diagnostic_result.json": file_hash(antecedent)},
        "historical_inventory": [], "prior_v3": prior,
        "limits": runner.remaining_limits(prior, diagnostic=False),
        "diagnostic_run": "antecedent", "capability_condition": "original",
        "sandbox_image": "offline-image", "claim_label": "offline integration",
        "recipes": [{"recipe_id": name, "warmup": {"episodes": 0}} for name in names],
        "schedules": {phase: [{"dyad_index": 1}, {"dyad_index": 2}]
                      for phase in ("development", "validation")},
        "scoring": {"validation_selection": "fixed criterion"},
    }
    calls = {"sandbox_verify": 0, "credential_loader": 0, "journal": None}
    original_history = lambda recipe: {actor: [{"role": "system", "content": actor}]
                                        for actor in ("alice", "bob")}
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "validate", lambda path: copy.deepcopy(release))
    monkeypatch.setattr(runner, "status", lambda *args, **kwargs: None)
    monkeypatch.setattr(engine, "initial_history", original_history)
    monkeypatch.setattr(engine, "install_adapters", lambda *args: object())

    class OfflineSandbox:
        def __init__(self, image):
            assert image == "offline-image"

        def verify(self):
            calls["sandbox_verify"] += 1

    class OfflineTransport:
        def __init__(self, journal, run_dir, key):
            assert key == "synthetic-offline-key"
            calls["journal"] = journal

    def synthetic_key():
        calls["credential_loader"] += 1
        return "synthetic-offline-key"

    monkeypatch.setattr(isolation, "DockerSandbox", OfflineSandbox)
    monkeypatch.setattr(transport, "OfficialTransport", OfflineTransport)
    monkeypatch.setattr(transport, "load_authorized_key", synthetic_key)
    return tmp_path, release, calls, original_history


def completed_dyad(recipe, schedule, *, phase, run_dir, positive):
    dyad_id = f"{phase}-{recipe['recipe_id']}-{schedule['dyad_index']}"
    value = {"dyad_id": dyad_id, "recipe_id": recipe["recipe_id"], "phase": phase,
             "status": "completed", "episodes": [
                 {"campaign_phase": "main", "v3_metrics": {
                     "joint_unsupported_acceptance": positive,
                     "paper_onset_episode_positive": positive}}
                 for _ in range(10)]}
    atomic_json(run_dir / "dyads" / (dyad_id + ".json"), value)
    return value


@pytest.mark.parametrize("drift", ["history", "limits", "condition"])
def test_antecedent_or_allocation_drift_stops_before_sandbox_and_credentials(offline_run, drift):
    root, release, calls, original_history = offline_run
    if drift == "history":
        release["prior_v3"]["physical_attempts"] = 1
    elif drift == "limits":
        release["limits"]["campaign_limit_usd"] = "99"
    else:
        release["capability_condition"] = "schema_clarified"
    with pytest.raises(ValueError):
        runner.run(root / "unused-release.json")
    assert calls["sandbox_verify"] == calls["credential_loader"] == 0
    assert not (root / release["run_directory"]).exists()
    assert engine.initial_history is original_history


def test_success_finishes_verified_zero_call_journal_and_restores_history(offline_run, monkeypatch):
    root, release, calls, original_history = offline_run

    def execute(recipe, schedule, *, phase, run_dir, **kwargs):
        assert engine.initial_history(recipe) == original_history(recipe)
        return completed_dyad(recipe, schedule, phase=phase, run_dir=run_dir, positive=False)

    monkeypatch.setattr(engine, "run_dyad", execute)
    result = runner.run(root / "unused-release.json")
    assert result["status"] == "completed"
    assert len(result["dyads"]) == 8
    assert result["ledger"]["physical_attempts"] == 0
    assert result["ledger"]["reserved"]["requests"] == 0
    assert result["original_gate_passed"] is False
    assert result["repair_started"] is False
    assert engine.initial_history is original_history
    verification = json.loads((root / "reports/continuation-verification.json").read_text())
    assert verification["journal_valid"] is True
    assert verification["historical_inventory_rechecked_under_lock"] is True


def test_validation_failure_stops_before_sibling_finishes_and_still_verifies(offline_run, monkeypatch):
    root, release, calls, original_history = offline_run
    sibling_started = threading.Event()
    sibling_saw_stop = threading.Event()

    def execute(recipe, schedule, *, phase, run_dir, **kwargs):
        if phase == "validation":
            if schedule["dyad_index"] == 1:
                assert sibling_started.wait(3)
                raise RuntimeError("synthetic validation failure")
            sibling_started.set()
            deadline = time.monotonic() + 3
            while not calls["journal"].stopped and time.monotonic() < deadline:
                time.sleep(0.005)
            assert calls["journal"].stopped, "failure was delayed until executor shutdown"
            with pytest.raises(DispatchStopped):
                calls["journal"].assert_dispatchable()
            sibling_saw_stop.set()
        return completed_dyad(recipe, schedule, phase=phase, run_dir=run_dir, positive=True)

    monkeypatch.setattr(engine, "run_dyad", execute)
    result = runner.run(root / "unused-release.json")
    assert sibling_saw_stop.is_set()
    assert result["status"] == "partial"
    assert result["ledger"]["stop_reason"] == "validation_trajectory_stopped"
    assert result["ledger"]["physical_attempts"] == 0
    assert engine.initial_history is original_history
    verification = json.loads((root / "reports/continuation-verification.json").read_text())
    assert verification["journal_valid"] is True
    assert verification["ledger"]["reserved"]["requests"] == 0
