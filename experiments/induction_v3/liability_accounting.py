"""Additive V3 ledger carrying one immutable, unresolved historical HTTP 429.

The old ledger is never reopened or modified. Local consumed/reserved totals
remain distinct from the external historical liability. No reconciliation or
release of that liability is implemented here. All dispatch still uses the
original fsync-before-return, RLock, hash-chain, and fail-closed settlement code.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import threading
from decimal import Decimal
from typing import Mapping

from . import accounting as base

VERSION = "induction_v3_liability_accounting_v1"
AccountingError = base.AccountingError
BudgetExceeded = base.BudgetExceeded
DispatchStopped = base.DispatchStopped
ProgramLock = base.ProgramLock
REQUIRED_SOURCES = ("experiments/induction_v3/accounting.py", "experiments/induction_v3/liability_accounting.py")


def liability_anchor() -> dict:
    """Return a fresh copy of the sole admissible historical open liability."""
    return {
        "run_id": "induction-v3-continuation-97f9c0b4-0bcb-4125-97f9-431caee260e4",
        "manifest_sha256": "a869d51aecb9be1d1bdaffdaf1914bb848569bd14737a2cc33389701824f1483",
        "journal_sha256": "8f5e4b84fda9702edf77e1b8ee47039da030225a725ec2859ead4e1aceb1e474",
        "journal_tail": "bbd0b813980c4b5b610a8813b28db49477d7964e81a76f4987420322963abc6c",
        "reservation_id": "ebb929c67f6b4506ba3f5f091572c78e",
        "reservation_event_hash": "277ff2320bd312e75e7f7d7fd1a1d70d72edea7dc3b2e87abff6f37465defd65",
        "request_hash": "eef5c49475cd58bf4239179b64a237c8a88ddec56d2b0a33af93922589313f16",
        "request_artifact_sha256": "9a138aca3bb7ce11b2c0c712744f266ec8b308617343d375519920ab44edd1a2",
        "response_artifact_sha256": "05f689dfea21c1397e72c38899383f28eb9795141e99dff31ca84b25f6e5ba75",
        "reserved_cost_usd": "0.6402", "input_token_bound": 1050000,
        "max_output_tokens": 64000, "outcome": "unknown", "http_status": 429,
        "error_code": "rate_limit_exceeded", "error_type": "tokens",
    }


def _file_hash(path):
    base._regular_file(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory_hash(rows):
    return base._digest(rows)


def _balance(value):
    if not isinstance(value, dict):
        raise AccountingError("historical balance must be an object")
    return {**{k: base._integer(value.get(k), k) for k in ("requests", "input_tokens", "output_tokens")},
            "cost_usd": str(base._money(value.get("cost_usd")))}


def _has_reserved(balance):
    return any(base._money(value) != 0 for value in balance.values())


def _check_allowed_liability(directory, manifest, *, semantic):
    anchor = liability_anchor()
    paths = {"manifest_sha256": directory / "manifest.json", "journal_sha256": directory / "journal.jsonl",
             "request_artifact_sha256": directory / "requests" / (anchor["request_hash"] + ".json"),
             "response_artifact_sha256": directory / "responses" / (anchor["reservation_id"] + ".json")}
    if directory.name != anchor["run_id"] or any(_file_hash(path) != anchor[key] for key, path in paths.items()):
        raise AccountingError("the sole allowed historical liability has drifted")
    request = base._read_json(paths["request_artifact_sha256"])
    response = base._read_json(paths["response_artifact_sha256"])
    error = response.get("error", {})
    if (base._digest(request) != anchor["request_hash"] or manifest.get("journal_tail") != anchor["journal_tail"]
            or response.get("http_status") != 429 or error.get("code") != anchor["error_code"]
            or error.get("type") != anchor["error_type"]):
        raise AccountingError("historical liability request/response binding mismatch")
    if semantic:
        base.verify_run(directory)
        events = base.read_events(directory / "journal.jsonl")
        reservations = {e["reservation"]["reservation_id"]: e for e in events if e["kind"] == "reservation"}
        settlements = {e["reservation_id"]: e for e in events if e["kind"] == "settlement"}
        unresolved = {rid for rid in reservations if rid not in settlements or settlements[rid]["outcome"] in {"unknown", "ambiguous_timeout"}}
        event = reservations.get(anchor["reservation_id"], {})
        reservation = event.get("reservation", {})
        if (unresolved != {anchor["reservation_id"]} or event.get("event_hash") != anchor["reservation_event_hash"]
                or settlements[anchor["reservation_id"]]["outcome"] != "unknown"
                or any(reservation.get(k) != anchor[k] for k in ("request_hash", "input_token_bound", "max_output_tokens"))
                or base._money(reservation.get("cost_usd")) != base._money(anchor["reserved_cost_usd"])):
            raise AccountingError("historical unknown is not the exact allowed reservation")


def _inventory_rows(empirical, *, excluded_run=None, directories=None, semantic=True):
    excluded = Path(excluded_run).resolve() if excluded_run else None
    result = []
    entries = sorted(directories if directories is not None else empirical.iterdir())
    for directory in entries:
        if directory.name.startswith("."): continue
        if directory.is_symlink() or not directory.is_dir():
            raise AccountingError("unsupported empirical inventory entry")
        if excluded is not None and directory.resolve() == excluded: continue
        manifest = base._read_json(directory / "manifest.json")
        ledger, budget = manifest.get("ledger"), manifest.get("budget")
        if ledger is not None and budget is not None and ledger != budget:
            raise AccountingError("historical manifest has conflicting ledgers")
        ledger = ledger if ledger is not None else budget
        if manifest.get("empirical") is not True or not isinstance(ledger, dict):
            raise AccountingError("historical manifest lacks an empirical ledger")
        consumed, reserved = _balance(ledger.get("consumed")), _balance(ledger.get("reserved"))
        status = manifest.get("status")
        if not isinstance(status, str) or not status or status in {"created", "running", "executing"}:
            raise AccountingError("historical run is not terminal")
        run_id = manifest.get("run_id") or manifest.get("config", {}).get("run_id")
        schema = manifest.get("artifact_type")
        v3 = directory.name.startswith("induction-v3-") or schema in {base.VERSION, VERSION}
        journal_hash = None; attempts = None
        if v3:
            if schema not in {base.VERSION, VERSION} or run_id != directory.name:
                raise AccountingError("unrecognized V3 historical schema or run identity")
            attempts = base._integer(ledger.get("physical_attempts"), "historical V3 attempts")
            journal_hash = _file_hash(directory / "journal.jsonl")
        if _has_reserved(reserved):
            _check_allowed_liability(directory, manifest, semantic=semantic)
        elif v3 and semantic:
            if schema == base.VERSION: base.verify_run(directory)
            else: _replay(directory)
        result.append({"manifest_path": str((directory / "manifest.json").relative_to(empirical)),
            "manifest_sha256": _file_hash(directory / "manifest.json"), "run_id": run_id,
            "status": status, "artifact_type": schema, "is_v3": v3, "consumed": consumed,
            "reserved": reserved, "physical_attempts": attempts, "journal_sha256": journal_hash})
    historical_totals(result)
    return result


def historical_inventory(program_lock, *, excluded_run=None):
    """Read and semantically validate all empirical history under its held lock."""
    program_lock.assert_held()
    return _inventory_rows(program_lock.path.parent, excluded_run=excluded_run)


def historical_totals(rows):
    fields = {"manifest_path", "manifest_sha256", "run_id", "status", "artifact_type", "is_v3", "consumed", "reserved", "physical_attempts", "journal_sha256"}
    if not isinstance(rows, list): raise AccountingError("historical inventory must be a list")
    seen = set(); ids = set(); carried = 0
    consumed = reserved = v3_consumed = v3_reserved = Decimal(0); attempts = 0
    anchor = liability_anchor()
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields: raise AccountingError("invalid historical row schema")
        name = row["manifest_path"]
        if (not isinstance(name, str) or Path(name).is_absolute() or len(Path(name).parts) != 2
                or Path(name).parts[1] != "manifest.json" or any(p in {".", ".."} for p in Path(name).parts)
                or name in seen or not isinstance(row["run_id"], str) or not row["run_id"] or row["run_id"] in ids
                or not isinstance(row["manifest_sha256"], str) or not base._HASH.fullmatch(row["manifest_sha256"])
                or type(row["is_v3"]) is not bool):
            raise AccountingError("invalid or duplicate historical identity")
        seen.add(name); ids.add(row["run_id"])
        if not isinstance(row["status"], str) or not row["status"] or row["status"] in {"created", "running", "executing"}:
            raise AccountingError("historical status is unsafe")
        c, r = _balance(row["consumed"]), _balance(row["reserved"])
        consumed += base._money(c["cost_usd"]); reserved += base._money(r["cost_usd"])
        if row["is_v3"]:
            if (row["artifact_type"] not in {base.VERSION, VERSION} or Path(name).parts[0] != row["run_id"]
                    or not row["run_id"].startswith("induction-v3-") or not isinstance(row["journal_sha256"], str)
                    or not base._HASH.fullmatch(row["journal_sha256"])):
                raise AccountingError("invalid V3 historical binding")
            attempts += base._integer(row["physical_attempts"], "historical attempts")
            v3_consumed += base._money(c["cost_usd"]); v3_reserved += base._money(r["cost_usd"])
        elif row["physical_attempts"] is not None or row["journal_sha256"] is not None or Path(name).parts[0].startswith("induction-v3-"):
            raise AccountingError("V3 history cannot be omitted from global limits")
        if _has_reserved(r):
            if (row["run_id"] != anchor["run_id"] or row["manifest_sha256"] != anchor["manifest_sha256"]
                    or row["journal_sha256"] != anchor["journal_sha256"]
                    or r != {"requests": 1, "input_tokens": anchor["input_token_bound"], "output_tokens": anchor["max_output_tokens"], "cost_usd": anchor["reserved_cost_usd"]}):
                raise AccountingError("unallowlisted historical outstanding liability")
            carried += 1
    if carried != 1: raise AccountingError("the exact immutable historical liability must remain present once")
    if consumed + reserved > 250 or v3_consumed + v3_reserved > 100 or attempts > 4000:
        raise BudgetExceeded("historical exposure or attempts exceed the authorized combined caps")
    return {"lifetime_consumed_usd": str(consumed), "lifetime_reserved_usd": str(reserved),
        "v3_consumed_usd": str(v3_consumed), "v3_reserved_usd": str(v3_reserved), "v3_attempts": attempts,
        "remaining_v3_usd": str(Decimal(100) - v3_consumed - v3_reserved),
        "remaining_lifetime_usd": str(Decimal(250) - consumed - reserved), "remaining_v3_attempts": 4000 - attempts}


class LiabilityReservationJournal(base.ReservationJournal):
    def __init__(self, run_dir, *, program_lock, binding: Mapping, source_root, source_inventory: Mapping,
                 expected_history_hash: str, campaign_limit_usd=None, lifetime_limit_usd="250", max_attempts=None, max_in_flight=3):
        self._mutex = threading.RLock(); self.program_lock = program_lock
        program_lock.assert_held()
        if program_lock._journal is not None: raise AccountingError("one journal only may own the held program lock")
        self.path = Path(run_dir).absolute(); self.source_root = Path(source_root).resolve()
        if (self.path.parent.resolve() != program_lock.path.parent.resolve() or self.path.is_symlink()
                or program_lock.path.parent.resolve() != (self.source_root / "runs" / "empirical").resolve()):
            raise AccountingError("new run must be directly within the locked empirical root")
        self.source_inventory = dict(source_inventory)
        if not all(path in self.source_inventory for path in REQUIRED_SOURCES):
            raise AccountingError("both original and liability accounting source must be frozen")
        self._check_sources()
        campaign = copy.deepcopy(dict(binding))
        if campaign.get("model") != base.MODEL or campaign.get("run_id") != self.path.name or not self.path.name.startswith("induction-v3-"):
            raise AccountingError("exact V3 directory identity and model are required")
        history = historical_inventory(program_lock, excluded_run=self.path)
        if inventory_hash(history) != expected_history_hash: raise AccountingError("prepared historical inventory drifted")
        totals = historical_totals(history)
        self._binding = {"schema": VERSION, "campaign": campaign, "source_inventory": dict(self.source_inventory),
            "pricing": copy.deepcopy(base.PRICING), "historical_runs": history, "historical_inventory_hash": expected_history_hash,
            "historical_totals": totals, "liability_anchor": liability_anchor(),
            "campaign_limit_usd": str(base._money(totals["remaining_v3_usd"] if campaign_limit_usd is None else campaign_limit_usd)),
            "lifetime_limit_usd": str(base._money(lifetime_limit_usd)),
            "max_attempts": totals["remaining_v3_attempts"] if max_attempts is None else max_attempts,
            "max_in_flight": max_in_flight, "combined_campaign_limit_usd": "100", "combined_max_attempts": 4000}
        _configure(self, self._binding)
        self._historical_identities = _historical_identities(self.path.parent, history)
        self._events, self._reservations, self._settlements = [], {}, {}
        self._identities = set(); self._stop_reason = self._terminal = None; self._poisoned = False
        if self.path.exists():
            for event in base.read_events(self.path / "journal.jsonl"):
                self._apply(event); self._events.append(event)
            if any(rid not in self._settlements for rid in self._reservations):
                self._append("stop", reason="unsettled_reservations_on_restart")
            self._write_manifest()
        else:
            self.path.mkdir(mode=0o700); base._fsync_directory(self.path.parent)
            self._append("binding", binding=self._binding)
        program_lock._journal = self

    def assert_dispatchable(self):
        with self._mutex:
            self._assert_owner()
            if self.stopped: raise DispatchStopped(self._stop_reason or self._terminal or "durability_failure")
            self._check_sources()
            # Exact manifest+journal+request/response hashes suffice after the
            # constructor's semantic audit; never accept a changed history.
            if _inventory_rows(self.program_lock.path.parent, excluded_run=self.path, semantic=False) != self._historical:
                raise AccountingError("historical liability inventory drifted")

    def _check_capacity(self, reservation):
        if reservation.identity in self._historical_identities:
            raise AccountingError("historical physical identity cannot be reserved again")
        super()._check_capacity(reservation)
        consumed, reserved = self._totals()
        local = consumed["cost_usd"] + reserved["cost_usd"] + reservation.cost_usd
        if (local + self.historical_cost + self.historical_reserved_cost > self.lifetime_limit
                or local + self.prior_v3_cost + self.prior_v3_reserved_cost > 100
                or len(self._reservations) + self.prior_v3_attempts >= 4000):
            raise BudgetExceeded("reservation exceeds combined V3/lifetime exposure or attempts")

    def snapshot(self):
        with self._mutex:
            value = super().snapshot()
            local = base._money(value["campaign_exposure_usd"])
            value.update({"historical_reserved_cost_usd": str(self.historical_reserved_cost),
                "historical_consumed_cost_usd": str(self.historical_cost),
                "external_liability": liability_anchor(),
                "combined_v3_consumed_cost_usd": str(self.prior_v3_cost + base._money(value["consumed"]["cost_usd"])),
                "combined_v3_reserved_cost_usd": str(self.prior_v3_reserved_cost + base._money(value["reserved"]["cost_usd"])),
                "combined_v3_exposure_usd": str(self.prior_v3_cost + self.prior_v3_reserved_cost + local),
                "combined_v3_physical_attempts": self.prior_v3_attempts + len(self._reservations),
                "lifetime_exposure_usd": str(self.historical_cost + self.historical_reserved_cost + local)})
            return value

    def _manifest_value(self):
        value = super()._manifest_value(); value["artifact_type"] = VERSION
        return value


def _historical_identities(empirical, rows):
    return {event["reservation"]["identity"] for row in rows if row["is_v3"]
            for event in base.read_events(empirical / Path(row["manifest_path"]).parent / "journal.jsonl")
            if event["kind"] == "reservation"}


def _configure(journal, frozen):
    fields = {"schema", "campaign", "source_inventory", "pricing", "historical_runs", "historical_inventory_hash", "historical_totals", "liability_anchor", "campaign_limit_usd", "lifetime_limit_usd", "max_attempts", "max_in_flight", "combined_campaign_limit_usd", "combined_max_attempts"}
    if (not isinstance(frozen, dict) or set(frozen) != fields or frozen["schema"] != VERSION
            or frozen["pricing"] != base.PRICING or frozen["liability_anchor"] != liability_anchor()
            or frozen["combined_campaign_limit_usd"] != "100" or frozen["combined_max_attempts"] != 4000):
        raise AccountingError("invalid liability accounting binding schema")
    campaign = frozen["campaign"]
    if (not isinstance(campaign, dict) or campaign.get("model") != base.MODEL
            or campaign.get("run_id") != journal.path.name or not journal.path.name.startswith("induction-v3-")):
        raise AccountingError("liability campaign identity mismatch")
    journal._historical = frozen["historical_runs"]
    totals = historical_totals(journal._historical)
    if totals != frozen["historical_totals"] or inventory_hash(journal._historical) != frozen["historical_inventory_hash"]:
        raise AccountingError("frozen historical totals or hash do not reconcile")
    journal.historical_cost = base._money(totals["lifetime_consumed_usd"])
    journal.historical_reserved_cost = base._money(totals["lifetime_reserved_usd"])
    journal.prior_v3_cost = base._money(totals["v3_consumed_usd"])
    journal.prior_v3_reserved_cost = base._money(totals["v3_reserved_usd"])
    journal.prior_v3_attempts = totals["v3_attempts"]
    journal.campaign_limit = base._money(frozen["campaign_limit_usd"])
    journal.lifetime_limit = base._money(frozen["lifetime_limit_usd"])
    journal.max_attempts = base._integer(frozen["max_attempts"], "max_attempts", 1)
    journal.max_in_flight = base._integer(frozen["max_in_flight"], "max_in_flight", 1)
    if (not 0 < journal.campaign_limit <= base._money(totals["remaining_v3_usd"])
            or not 0 < journal.lifetime_limit <= 250
            or journal.historical_cost + journal.historical_reserved_cost > journal.lifetime_limit
            or journal.max_attempts > totals["remaining_v3_attempts"] or journal.max_in_flight > 3):
        raise BudgetExceeded("local caps exceed remaining combined capacity")
    sources = frozen["source_inventory"]
    if not isinstance(sources, dict) or not all(path in sources for path in REQUIRED_SOURCES):
        raise AccountingError("both accounting source modules must be frozen")
    for name, expected in sources.items():
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts or not isinstance(expected, str) or not base._HASH.fullmatch(expected):
            raise AccountingError("invalid source inventory")
    journal.source_inventory = sources


def _replay(path):
    path = Path(path).absolute()
    if path.is_symlink(): raise AccountingError("run directory must not be a symlink")
    manifest = base._read_json(path / "manifest.json"); events = base.read_events(path / "journal.jsonl")
    journal = LiabilityReservationJournal.__new__(LiabilityReservationJournal)
    journal.path = path; journal._mutex = threading.RLock(); journal._binding = events[0].get("binding")
    _configure(journal, journal._binding)
    directories = [path.parent / Path(row["manifest_path"]).parent for row in journal._historical]
    if _inventory_rows(path.parent, directories=directories, semantic=False) != journal._historical:
        raise AccountingError("frozen historical lineage files drifted")
    journal._historical_identities = _historical_identities(path.parent, journal._historical)
    journal._events, journal._reservations, journal._settlements = [], {}, {}
    journal._identities = set(); journal._stop_reason = journal._terminal = None; journal._poisoned = False
    for event in events:
        journal._apply(event); journal._events.append(event)
    if manifest != base._read_json(path / "manifest.json") or manifest != journal._manifest_value():
        raise AccountingError("manifest does not reconcile exactly with liability journal replay")
    return journal


def verify_run(run_dir, *, source_root=None, program_lock=None):
    """Read-only semantic replay, exact manifest check, and carry-lineage audit."""
    journal = _replay(run_dir)
    if source_root is not None:
        journal.source_root = Path(source_root).resolve(); journal._check_sources()
    directories = [journal.path.parent / Path(row["manifest_path"]).parent for row in journal._historical]
    if _inventory_rows(journal.path.parent, directories=directories) != journal._historical:
        raise AccountingError("frozen historical lineage files drifted")
    if program_lock is not None:
        program_lock.assert_held()
        if journal.path.parent.resolve() != program_lock.path.parent.resolve():
            raise AccountingError("run is outside held program lock")
        if historical_inventory(program_lock, excluded_run=journal.path) != journal._historical:
            raise AccountingError("all-history inventory differs from frozen lineage")
    snapshot = journal.snapshot()
    return {"schema": VERSION, "run_id": journal.path.name, "journal_valid": True, "manifest_consistent": True,
        "event_count": len(journal._events), "source_inventory_verified": source_root is not None,
        "frozen_historical_lineage_verified": True, "historical_inventory_rechecked_under_lock": program_lock is not None,
        "requires_restart_stop": snapshot["in_flight"] > 0, "stopped": journal.stopped,
        "carried_liability_unchanged": True, "ledger": snapshot}
