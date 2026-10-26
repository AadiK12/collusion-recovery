"""Independent orchestration checks, with real journals in temporary roots only.

Provider, credential, Docker, and replay execution are replaced with explicit
offline doubles. The ledger and historical-liability verification remain real.
"""
import copy
import json
import shutil
import threading
import time

import pytest

from experiments.induction_v3 import cli, engine, isolation, pacing, replay, transport
from experiments.induction_v3 import liability_accounting as accounting
from experiments.induction_v3 import resumption as runner
from experiments.induction_v3.accounting import DispatchStopped
from experiments.induction_v3.common import ROOT, atomic_json, file_hash


@pytest.fixture
def offline_resume(tmp_path, monkeypatch):
    empirical = tmp_path / "runs/empirical"
    empirical.mkdir(parents=True)
    anchor = accounting.liability_anchor()
    allowed_v3 = {anchor["run_id"], "induction-v3-b2522673-f58a-4d9e-8be0-ae08b511db53",
                  "induction-v3-diagnostic-41575ab5-1c1c-41cb-b2e2-edd6e2dbcd54"}
    for source in sorted((ROOT / "runs/empirical").iterdir()):
        if not source.is_dir() or source.name.startswith("."):
            continue
        if source.name.startswith("induction-v3-") and source.name not in allowed_v3:
            continue
        target = empirical / source.name
        target.mkdir()
        shutil.copyfile(source / "manifest.json", target / "manifest.json")
        if source.name in allowed_v3:
            shutil.copyfile(source / "journal.jsonl", target / "journal.jsonl")
    old = empirical / anchor["run_id"]
    for folder, identity in (("requests", anchor["request_hash"]), ("responses", anchor["reservation_id"])):
        (old / folder).mkdir()
        shutil.copyfile(ROOT / "runs/empirical" / anchor["run_id"] / folder / (identity + ".json"),
                        old / folder / (identity + ".json"))
    original_bytes = {p: p.read_bytes() for p in empirical.rglob("*") if p.is_file()}
    inventory = {}
    for relative in accounting.REQUIRED_SOURCES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        inventory[relative] = file_hash(target)
    with accounting.ProgramLock(empirical / ".controlled-peer-recovery-program.lock") as lock:
        history = accounting.historical_inventory(lock)
    proof = {"status": "passed", "offline": True}
    atomic_json(tmp_path / "proof.json", proof)
    atomic_json(old / "release.json", {})
    release = {"run_id": "induction-v3-resumption-offline-security",
        "run_directory": "runs/empirical/induction-v3-resumption-offline-security",
        "release_hash": "a" * 64, "campaign_id": "offline-security",
        "source_inventory": inventory, "historical_inventory": history,
        "historical_inventory_hash": accounting.inventory_hash(history),
        "source_run": str(old.relative_to(tmp_path)), "replay_proofs": {"interrupted": {"path": "proof.json"}},
        "plan": ["synthetic-fixed-plan"], "sandbox_image": "offline-image", "pacing": dict(runner.PACING),
        "estimator_archives": {}, "capability_condition": "original", "claim_label": "offline integration",
        "recipes": [{"recipe_id": name, "warmup": {"episodes": 0}}
                    for name in ("reference", "warmup_raw", "warmup_summary", "seeded")],
        "schedules": {phase: [{"dyad_index": 1}, {"dyad_index": 2}]
                      for phase in ("development", "validation")},
        "scoring": {"validation_selection": "fixed criterion"}}
    calls = {"sandbox": 0, "key": 0, "encoding": 0, "hooks": 0, "cleanup": 0, "journal": None}
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "validate", lambda path: copy.deepcopy(release))
    monkeypatch.setattr(replay, "replay_episode", lambda *args: copy.deepcopy(proof))
    monkeypatch.setattr(runner, "source_plan", lambda *args: ["synthetic-fixed-plan"])

    class OfflineSandbox:
        def __init__(self, image):
            assert image == "offline-image"

        def verify(self):
            calls["sandbox"] += 1

    class OfflineTransport:
        def __init__(self, journal, run_dir, key, *, pacer):
            assert key == "synthetic-offline-key"
            calls["journal"] = journal

    def synthetic_key():
        calls["key"] += 1
        return "synthetic-offline-key"

    def encoding():
        calls["encoding"] += 1
        return "offline-encoding"

    def estimator(*args, encoding, **kwargs):
        assert encoding == "offline-encoding"
        return lambda body: 1

    def hooks(delegate):
        calls["hooks"] += 1

        def cleanup():
            calls["cleanup"] += 1
        return cleanup

    monkeypatch.setattr(isolation, "DockerSandbox", OfflineSandbox)
    monkeypatch.setattr(transport, "load_authorized_key", synthetic_key)
    monkeypatch.setattr(runner, "load_encoding", encoding)
    monkeypatch.setattr(pacing.KnownReplayTokenEstimator, "from_archives", estimator)
    monkeypatch.setattr(pacing, "PacedOfficialTransport", OfflineTransport)
    monkeypatch.setattr(replay, "ReplayTransport", lambda delegate, **kwargs: delegate)
    monkeypatch.setattr(replay, "install_replay_hooks", hooks)
    monkeypatch.setattr(engine, "install_adapters", lambda *args: object())
    yield tmp_path, release, calls
    assert all(p.read_bytes() == value for p, value in original_bytes.items())


def completed(recipe, schedule, *, phase, release, positive=False, **kwargs):
    identity = f"{phase}-{recipe['recipe_id']}-{schedule['dyad_index']}"
    value = {"dyad_id": identity, "recipe_id": recipe["recipe_id"], "phase": phase,
             "status": "completed", "episodes": [{"campaign_phase": "main", "v3_metrics": {
                 "joint_unsupported_acceptance": positive, "paper_onset_episode_positive": positive}}
                 for _ in range(10)]}
    atomic_json(runner.ROOT / release["run_directory"] / "dyads" / (identity + ".json"), value)
    return value


@pytest.mark.parametrize("drift", ["history", "proof", "plan", "sandbox", "encoding"])
def test_preflight_failure_never_loads_credentials_or_creates_journal(offline_resume, monkeypatch, drift):
    root, release, calls = offline_resume
    if drift == "history":
        release["historical_inventory_hash"] = "0" * 64
    elif drift == "proof":
        monkeypatch.setattr(replay, "replay_episode", lambda *args: {"status": "different"})
    elif drift == "plan":
        release["plan"] = ["different"]
    else:
        def fail(*args):
            raise ValueError("offline preflight failure")
        if drift == "sandbox":
            monkeypatch.setattr(isolation.DockerSandbox, "verify", fail)
        else:
            monkeypatch.setattr(runner, "load_encoding", fail)
    with pytest.raises(ValueError):
        runner.run(root / "unused-release.json")
    assert calls["key"] == calls["hooks"] == 0
    assert not (root / release["run_directory"]).exists()


def test_zero_call_completion_preserves_external_reserve_and_removes_hooks(offline_resume, monkeypatch):
    root, release, calls = offline_resume
    monkeypatch.setattr(runner, "run_dyad", completed)
    result = runner.run(root / "unused-release.json")
    assert result["status"] == "completed" and len(result["dyads"]) == 8
    assert calls["hooks"] == calls["cleanup"] == calls["key"] == 1
    assert result["ledger"]["physical_attempts"] == 0
    assert result["ledger"]["combined_v3_physical_attempts"] == 468
    assert result["ledger"]["combined_v3_reserved_cost_usd"] == "0.6402"
    assert result["ledger"]["reserved"]["cost_usd"] == "0"
    verification = json.loads((root / "reports/resumption-verification.json").read_text())
    assert verification["journal_valid"] and verification["carried_liability_unchanged"]
    assert verification["historical_inventory_rechecked_under_lock"]


def test_validation_failure_stops_before_sibling_exit_and_cleans_hooks(offline_resume, monkeypatch):
    root, release, calls = offline_resume
    sibling_started, sibling_stopped = threading.Event(), threading.Event()

    def execute(recipe, schedule, *, phase, **kwargs):
        if phase == "validation":
            if schedule["dyad_index"] == 1:
                assert sibling_started.wait(3)
                raise RuntimeError("synthetic validation failure")
            sibling_started.set()
            deadline = time.monotonic() + 3
            while not calls["journal"].stopped and time.monotonic() < deadline:
                time.sleep(0.005)
            assert calls["journal"].stopped
            assert calls["cleanup"] == 0, "hooks removed while a worker was still active"
            with pytest.raises(DispatchStopped):
                calls["journal"].assert_dispatchable()
            sibling_stopped.set()
        return completed(recipe, schedule, phase=phase, positive=True, **kwargs)

    monkeypatch.setattr(runner, "run_dyad", execute)
    result = runner.run(root / "unused-release.json")
    assert sibling_stopped.is_set() and result["status"] == "partial"
    assert result["ledger"]["stop_reason"] == "validation_trajectory_stopped"
    assert result["ledger"]["combined_v3_reserved_cost_usd"] == "0.6402"
    assert calls["hooks"] == calls["cleanup"] == 1
    assert json.loads((root / "reports/resumption-verification.json").read_text())["journal_valid"]


def test_transport_setup_failure_finishes_blocked_verified_ledger(offline_resume, monkeypatch):
    root, release, calls = offline_resume

    def fail(*args, **kwargs):
        raise ValueError("synthetic adapter failure")

    monkeypatch.setattr(engine, "install_adapters", fail)
    result = runner.run(root / "unused-release.json")
    assert result["status"] == "blocked"
    assert result["ledger"]["physical_attempts"] == 0
    assert calls["hooks"] == calls["cleanup"] == 0
    assert len(result["dyads"]) == 8 and all(r["status"] == "not_started" for r in result["dyads"])
    assert json.loads((root / "reports/resumption-verification.json").read_text())["journal_valid"]
