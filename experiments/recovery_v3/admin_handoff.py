"""Offline administrative partial-stop; never sends signals or provider calls.

The executor owns SIGSTOP and must identity-check/SIGCONT the same process after
ANY readiness failure. It may terminate only after readiness succeeds. This
module refuses finalization until process death, idle sandbox, unchanged durable
artifacts and exclusive ownership of the original program lock are proven.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time

from experiments.induction_v3 import accounting as base
from experiments.induction_v3 import liability_accounting as ledger
from experiments.induction_v3.common import ROOT, atomic_json, digest, file_hash

SCHEMA = "recovery_v3_administrative_partial_stop_v1"
REASON = "user_requested_early_exploratory_recovery_handoff"
RUN_COMMAND = " -m experiments.induction_v3.resumption run"
MAX_PROOF_AGE_SECONDS = 180


class HandoffError(RuntimeError):
    pass


class SystemObserver:
    """Bounded read-only OS queries; no credentials, network or signal methods."""

    @staticmethod
    def _run(args, *, allowed=(0,), env=None):
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10, check=False, env=env)
        if result.returncode not in allowed or len(result.stdout) > 8_000_000:
            raise HandoffError("OS inspection unavailable: " + Path(args[0]).name)
        return result.stdout.decode("utf-8", errors="strict")

    def processes(self):
        output = self._run(["/bin/ps", "-axo", "pid=,ppid=,uid=,stat=,lstart=,command="])
        rows = {}
        for line in output.splitlines():
            fields = line.split(None, 9)
            if len(fields) != 10:
                raise HandoffError("incomplete process identity")
            pid, parent, uid = map(int, fields[:3])
            rows[pid] = {"pid": pid, "ppid": parent, "uid": uid, "state": fields[3],
                         "start": " ".join(fields[4:9]), "command": fields[9]}
        return rows

    def lock_holders(self, path):
        output = self._run(["/usr/sbin/lsof", "-n", "-Fp", str(path)], allowed=(0, 1))
        if any(not line.startswith("p") or not line[1:].isdigit() for line in output.splitlines()):
            raise HandoffError("unrecognized lock holder output")
        return {int(line[1:]) for line in output.splitlines()}

    def containers(self):
        socket = next((p for p in (Path.home() / ".docker/run/docker.sock",
                                   Path("/var/run/docker.sock")) if p.is_socket()), None)
        if socket is None:
            raise HandoffError("local sandbox daemon unavailable")
        # Inspect the same local daemon as the frozen sandbox, not a user's
        # remote Docker context or credential-bearing client environment.
        with tempfile.TemporaryDirectory(prefix="recovery-handoff-docker-") as client:
            result = self._run(["/usr/local/bin/docker", "--host", "unix://" + str(socket),
                                "ps", "-a", "--filter", "name=induction-v3-",
                                "--format", "{{.ID}} {{.Names}} {{.State}}"],
                               env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": client,
                                    "DOCKER_CONFIG": client, "LANG": "C.UTF-8"})
        return result.splitlines()

    def lock_is_held(self, path):
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)


def _paths(root, run_dir):
    root = Path(root).resolve()
    run = Path(run_dir).absolute()
    if (run.is_symlink() or run.parent.resolve() != root / "runs/empirical"
            or not run.name.startswith("induction-v3-resumption-")):
        raise HandoffError("requires exact resumption directory in this empirical root")
    return root, run, root / "runs/empirical" / base.LOCK_NAME


def _identity(row):
    return {key: row[key] for key in ("pid", "ppid", "uid", "start", "command")}


def _is_runner_command(command):
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    return (len(argv) == 4 and Path(argv[0]).name.lower().startswith("python")
            and argv[1:] == ["-m", "experiments.induction_v3.resumption", "run"])


def _stopped_owner(observer, pid, lock):
    rows = observer.processes()
    row = rows.get(pid)
    if (row is None or "T" not in row["state"] or row["uid"] != os.getuid()
            or not _is_runner_command(row["command"])):
        raise HandoffError("exact stopped resumption owner not established; executor must resume same identity")
    descendants = {pid}
    while True:
        expanded = descendants | {p for p, r in rows.items() if r["ppid"] in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    if len(descendants) != 1:
        raise HandoffError("runner still has subprocesses; executor must resume same identity")
    if observer.containers():
        raise HandoffError("sandbox containers still exist; executor must resume same identity")
    if observer.lock_holders(lock) != {pid} or not observer.lock_is_held(lock):
        raise HandoffError("sole program-lock ownership not established")
    return _identity(row)


def _files(run):
    result = {}
    for path in sorted(run.rglob("*")):
        if "scratch" in path.relative_to(run).parts:
            continue
        if path.is_symlink():
            raise HandoffError("run artifact symlink")
        if path.is_file():
            if path.name.endswith(".tmp"):
                raise HandoffError("unfinished atomic artifact write; resume same identity")
            result[str(path.relative_to(run))] = file_hash(path)
    if not {"manifest.json", "journal.jsonl", "release.json"} <= result.keys():
        raise HandoffError("required durable artifacts missing")
    return result


def _idle_ledger(run, root):
    report = ledger.verify_run(run, source_root=root)
    events = base.read_events(run / "journal.jsonl")
    local = report["ledger"]
    if (any(event["kind"] == "terminal" for event in events) or report["stopped"]
            or local["in_flight"] != 0
            or any(base._money(value) != 0 for value in local["reserved"].values())):
        raise HandoffError("requires unstopped ledger with zero local open or unresolved reservations")
    frozen = events[0]["binding"]
    release = json.loads((run / "release.json").read_text())
    if (release["run_id"] != run.name
            or release["release_hash"] != frozen["campaign"]["release_hash"]):
        raise HandoffError("release and ledger identity mismatch")
    return frozen, events, report


def build_readiness_proof(run_dir, pid, *, root=ROOT, observer=None, clock=time.time):
    """Read-only. Caller MUST resume same identity on any exception."""
    observer = observer or SystemObserver()
    root, run, lock = _paths(root, run_dir)
    identity = _stopped_owner(observer, pid, lock)
    files = _files(run)
    frozen, events, report = _idle_ledger(run, root)
    if _files(run) != files or _stopped_owner(observer, pid, lock) != identity:
        raise HandoffError("quiescent proof drift; executor must resume same identity")
    proof = {"schema": SCHEMA, "created_epoch": clock(), "run_id": run.name,
             "run_directory": str(run.relative_to(root)), "process": identity,
             "lock_device": lock.stat().st_dev, "lock_inode": lock.stat().st_ino,
             "artifact_hashes": files, "binding": frozen,
             "journal_events": len(events), "journal_tail": events[-1]["event_hash"],
             "ledger_snapshot": report["ledger"],
             "signals_sent_by_tool": False, "required_terminal_status": "partial"}
    return {**proof, "proof_hash": digest(proof)}


def _dead_and_idle(proof, observer):
    rows = observer.processes()
    # Even a reused PID is an unsafe ambiguity, not proof of process death.
    if proof["process"]["pid"] in rows:
        raise HandoffError("original PID still exists or was reused")
    if any(_is_runner_command(row["command"]) for row in rows.values()):
        raise HandoffError("another resumption dispatcher exists")
    if observer.containers():
        raise HandoffError("sandbox containers still exist")


def finalize_partial(proof, *, root=ROOT, observer=None, clock=time.time):
    """Append only administrative stop/partial after verified owner death."""
    observer = observer or SystemObserver()
    value = dict(proof)
    supplied = value.pop("proof_hash", None)
    if supplied != digest(value) or value.get("schema") != SCHEMA:
        raise HandoffError("invalid readiness proof")
    age = clock() - value["created_epoch"]
    if not 0 <= age <= MAX_PROOF_AGE_SECONDS:
        raise HandoffError("readiness proof expired")
    root, run, lock_path = _paths(root, Path(root) / value["run_directory"])
    if run.name != value["run_id"]:
        raise HandoffError("proof run identity mismatch")
    _dead_and_idle(value, observer)
    if observer.lock_holders(lock_path):
        raise HandoffError("program lock still has an owner")
    with ledger.ProgramLock(lock_path) as lock:
        _dead_and_idle(value, observer)
        if (lock_path.stat().st_dev, lock_path.stat().st_ino) != (value["lock_device"], value["lock_inode"]):
            raise HandoffError("program lock inode changed")
        if _files(run) != value["artifact_hashes"]:
            raise HandoffError("durable artifacts changed after readiness")
        frozen, events, before = _idle_ledger(run, root)
        if (frozen != value["binding"] or len(events) != value["journal_events"]
                or events[-1]["event_hash"] != value["journal_tail"]
                or before["ledger"] != value["ledger_snapshot"]):
            raise HandoffError("durable accounting proof mismatch")
        prefix = (run / "journal.jsonl").read_bytes()
        journal = ledger.LiabilityReservationJournal(run, program_lock=lock,
            binding=frozen["campaign"], source_root=root, source_inventory=frozen["source_inventory"],
            expected_history_hash=frozen["historical_inventory_hash"],
            campaign_limit_usd=frozen["campaign_limit_usd"], lifetime_limit_usd=frozen["lifetime_limit_usd"],
            max_attempts=frozen["max_attempts"], max_in_flight=frozen["max_in_flight"])
        journal.stop(REASON)
        journal.finish("partial")
        report = ledger.verify_run(run, source_root=root, program_lock=lock)
        after = base.read_events(run / "journal.jsonl")
        if (not (run / "journal.jsonl").read_bytes().startswith(prefix)
                or len(after) != len(events) + 2 or after[-2]["kind"] != "stop"
                or after[-2]["reason"] != REASON or after[-1]["status"] != "partial"):
            raise HandoffError("administrative append verification failed")
        artifacts = _files(run)
        if any(artifacts.get(name) != sha for name, sha in value["artifact_hashes"].items()
               if name not in {"journal.jsonl", "manifest.json"}):
            raise HandoffError("non-accounting artifacts changed")
        return {"schema": SCHEMA, "status": "administratively_stopped_partial",
                "proof_hash": supplied, "run_id": run.name, "reason": REASON,
                "provider_calls": 0, "signals_sent_by_tool": False,
                "original_journal_prefix_preserved": True, "verification": report,
                "induction_validation_complete": False, "repair_started": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("readiness", "finalize"))
    parser.add_argument("--run")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--proof", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()
    proof_path = Path(args.proof).absolute()
    if args.command == "readiness":
        if not args.run or args.pid is None or proof_path.exists():
            raise HandoffError("readiness requires run, PID and a fresh external proof path")
        run = Path(args.run).absolute()
        if proof_path.is_relative_to(run):
            raise HandoffError("proof must be outside source run")
        result = build_readiness_proof(run, args.pid)
        atomic_json(proof_path, result)
        print(json.dumps({"status": "ready_for_executor_termination", "proof_hash": result["proof_hash"],
                          "expires_in_seconds": MAX_PROOF_AGE_SECONDS}))
    else:
        if not args.report or Path(args.report).exists() or proof_path.is_symlink():
            raise HandoffError("finalize requires a fresh external report and regular proof")
        proof = json.loads(proof_path.read_text())
        report_path = Path(args.report).absolute()
        if report_path.is_relative_to(ROOT / proof["run_directory"]):
            raise HandoffError("report must be outside source run")
        result = finalize_partial(proof)
        atomic_json(report_path, result)
        print(json.dumps({"status": result["status"], "run_id": result["run_id"]}))


if __name__ == "__main__":
    main()
