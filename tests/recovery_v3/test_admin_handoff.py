"""No signals, API calls or real program lock: temporary real-ledger fixtures."""
import copy
import json
import os
from pathlib import Path
import shutil

import pytest

from experiments.induction_v3 import accounting as base
from experiments.induction_v3 import liability_accounting as ledger
from experiments.induction_v3.common import ROOT, atomic_json, file_hash
from experiments.recovery_v3 import admin_handoff as admin


class Observer:
    def __init__(self):
        self.row = {"pid": 901, "ppid": 900, "uid": os.getuid(), "state": "T",
                    "start": "Mon Oct 26 03:00:00 2026",
                    "command": "/python" + admin.RUN_COMMAND}
        self.alive = True
        self.boxes = []
        self.extra = {}

    def processes(self):
        return {**({901: copy.deepcopy(self.row)} if self.alive else {}), **self.extra}

    def lock_holders(self, path):
        return {901} if self.alive else set()

    def lock_is_held(self, path):
        return self.alive

    def containers(self):
        return list(self.boxes)


@pytest.fixture
def case(tmp_path):
    empirical = tmp_path / "runs/empirical"
    empirical.mkdir(parents=True)
    anchor = ledger.liability_anchor()
    allowed = {anchor["run_id"], "induction-v3-b2522673-f58a-4d9e-8be0-ae08b511db53",
               "induction-v3-diagnostic-41575ab5-1c1c-41cb-b2e2-edd6e2dbcd54"}
    for source in sorted((ROOT / "runs/empirical").iterdir()):
        if not source.is_dir() or source.name.startswith("."):
            continue
        if source.name.startswith("induction-v3-") and source.name not in allowed:
            continue
        target = empirical / source.name
        target.mkdir()
        shutil.copyfile(source / "manifest.json", target / "manifest.json")
        if source.name in allowed:
            shutil.copyfile(source / "journal.jsonl", target / "journal.jsonl")
    old = empirical / anchor["run_id"]
    for folder, name in (("requests", anchor["request_hash"]), ("responses", anchor["reservation_id"])):
        (old / folder).mkdir()
        shutil.copyfile(ROOT / "runs/empirical" / anchor["run_id"] / folder / (name + ".json"),
                        old / folder / (name + ".json"))
    inventory = {}
    for relative in ledger.REQUIRED_SOURCES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        inventory[relative] = file_hash(target)
    run = empirical / "induction-v3-resumption-offline-admin"
    with ledger.ProgramLock(empirical / base.LOCK_NAME) as lock:
        history = ledger.historical_inventory(lock)
        journal = ledger.LiabilityReservationJournal(run, program_lock=lock, source_root=tmp_path,
            binding={"run_id": run.name, "model": base.MODEL, "release_hash": "f" * 64},
            source_inventory=inventory, expected_history_hash=ledger.inventory_hash(history))
        reservation = journal.reserve("settled-fixture", input_token_bound=1000,
            max_output_tokens=100, request_hash="a" * 64)
        journal.settle(reservation.reservation_id, usage={"input_tokens": 100, "output_tokens": 20})
    atomic_json(run / "release.json", {"run_id": run.name, "release_hash": "f" * 64})
    atomic_json(run / "dyads/complete.json", {"episodes": [{"complete": True}]})
    return tmp_path, run, Observer()


def proof(case):
    root, run, observer = case
    return admin.build_readiness_proof(run, 901, root=root, observer=observer, clock=lambda: 1000)


def test_finalize_appends_only_stop_partial_preserves_binding_and_liability(case, monkeypatch):
    root, run, observer = case
    monkeypatch.setattr(os, "kill", lambda *args: pytest.fail("signal forbidden"))
    before = {p: p.read_bytes() for p in (root / "runs/empirical").rglob("*") if p.is_file()}
    prepared = proof(case)
    observer.alive = False
    result = admin.finalize_partial(prepared, root=root, observer=observer, clock=lambda: 1001)
    assert result["status"] == "administratively_stopped_partial"
    assert result["verification"]["ledger"]["historical_reserved_cost_usd"] == "0.6402"
    assert result["verification"]["ledger"]["physical_attempts"] == 1
    assert result["provider_calls"] == 0 and not result["repair_started"]
    events = base.read_events(run / "journal.jsonl")
    assert events[0]["binding"] == prepared["binding"]
    assert events[-2]["reason"] == admin.REASON and events[-1]["status"] == "partial"
    for path, data in before.items():
        if path == run / "journal.jsonl":
            assert path.read_bytes().startswith(data)
        elif path != run / "manifest.json":
            assert path.read_bytes() == data


@pytest.mark.parametrize("failure", ["running", "identity", "shell_wrapper", "descendant", "container", "reserve"])
def test_readiness_rejects_nonquiescent_or_incomplete_state_without_writes(case, failure):
    root, run, observer = case
    if failure == "running": observer.row["state"] = "S"
    if failure == "identity": observer.row["command"] = "/python unrelated.py"
    if failure == "shell_wrapper": observer.row["command"] = "/bin/sh -c /python" + admin.RUN_COMMAND
    if failure == "descendant": observer.extra[902] = {**observer.row, "pid": 902, "ppid": 901}
    if failure == "container": observer.boxes = ["induction-v3-live"]
    if failure == "reserve":
        frozen = base.read_events(run / "journal.jsonl")[0]["binding"]
        with ledger.ProgramLock(root / "runs/empirical" / base.LOCK_NAME) as lock:
            journal = ledger.LiabilityReservationJournal(run, program_lock=lock, source_root=root,
                binding=frozen["campaign"], source_inventory=frozen["source_inventory"],
                expected_history_hash=frozen["historical_inventory_hash"])
            journal.reserve("open", input_token_bound=1000, max_output_tokens=100, request_hash="b" * 64)
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(admin.HandoffError): proof(case)
    assert before == {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}


@pytest.mark.parametrize("failure", ["alive", "reused_pid", "changed_artifact", "changed_proof", "expired", "container"])
def test_finalizer_rejects_uncertain_death_or_proof_drift_without_writes(case, failure):
    root, run, observer = case
    prepared = proof(case)
    observer.alive = failure in {"alive", "reused_pid"}
    if failure == "reused_pid": observer.row["start"] = "different process"
    if failure == "changed_artifact": atomic_json(run / "dyads/complete.json", {"changed": True})
    if failure == "changed_proof": prepared["binding"]["campaign"]["release_hash"] = "tampered"
    if failure == "container": observer.boxes = ["induction-v3-live"]
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    with pytest.raises(admin.HandoffError):
        admin.finalize_partial(prepared, root=root, observer=observer,
                               clock=lambda: 2000 if failure == "expired" else 1001)
    assert before == {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
