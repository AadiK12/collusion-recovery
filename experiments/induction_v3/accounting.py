"""V3 accounting: one locked process, interleaved calls, durable reservations.

This module has no provider dependency and never reads credentials. The transport
must dispatch only after ``reserve`` returns, and settle every physical attempt.
The journal is authoritative; the atomic manifest is a recoverable materialized
view. Unknown charges remain reserved and stop further dispatch, including after
a process restart. A returned output token count must already include reasoning.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
import uuid


VERSION = "induction_v3_accounting_v1"
MODEL = "gpt-5.6-luna"
PRICING = {
    "model": MODEL,
    "source": "https://developers.openai.com/api/docs/models/gpt-5.6-luna",
    "input_usd_per_million": "0.20",
    "cached_input_usd_per_million": "0.02",
    "cache_write_input_usd_per_million": "0.25",
    "output_usd_per_million": "1.20",
    "long_context_threshold_input_tokens": 272000,
    "long_context_input_multiplier": "2",
    "long_context_output_multiplier": "1.5",
    "reasoning_included_in_output_tokens": True,
    "missing_cache_write_detail": "assume_all_non_cached_input_is_cache_write",
    "cost_basis": "estimate_from_provider_usage_and_frozen_list_prices",
}
LOCK_NAME = ".controlled-peer-recovery-program.lock"
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class AccountingError(RuntimeError):
    """Malformed, drifted, or unsafe durable accounting state."""


class BudgetExceeded(AccountingError):
    """The next reservation cannot fit within an approved cap."""


class DispatchStopped(AccountingError):
    """New dispatch has been durably stopped."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _money(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise AccountingError("money must be a finite nonnegative decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AccountingError("invalid monetary amount") from exc
    if not result.is_finite() or result < 0:
        raise AccountingError("money must be finite and nonnegative")
    return result


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AccountingError(f"{label} must be an integer >= {minimum}")
    return value


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AccountingError("duplicate JSON object key")
        result[key] = value
    return result


def _read_json(path: Path) -> dict:
    _regular_file(path)
    try:
        result = json.loads(path.read_text(encoding="utf-8"),
                            object_pairs_hook=_object_pairs)
    except (ValueError, UnicodeError) as exc:
        raise AccountingError(f"invalid JSON: {path.name}") from exc
    if not isinstance(result, dict):
        raise AccountingError(f"JSON object required: {path.name}")
    return result


def _regular_file(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AccountingError(f"required file unavailable: {path.name}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise AccountingError(f"regular nonsymlink file required: {path.name}")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write((_canonical(value) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class ProgramLock:
    """Nonblocking process lock shared with every earlier empirical runner."""

    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        if self.path.name != LOCK_NAME:
            raise AccountingError("the existing shared program lock name is required")
        self._fd: int | None = None
        self._pid: int | None = None
        self._journal = None

    def __enter__(self):
        if self._fd is not None:
            raise AccountingError("program lock is already entered")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077):
                raise AccountingError("shared program lock must be an owner-only regular file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self._fd, self._pid = fd, os.getpid()
        return self

    def assert_held(self) -> None:
        if self._fd is None or self._pid != os.getpid():
            raise AccountingError("the shared program lock is not held by this process")
        opened, named = os.fstat(self._fd), self.path.lstat()
        if (opened.st_ino, opened.st_dev) != (named.st_ino, named.st_dev):
            raise AccountingError("shared program lock file was replaced")

    def __exit__(self, *_):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
            self._journal = None


def historical_inventory(program_lock: ProgramLock, *, excluded_run: Path | None = None) -> list[dict]:
    """Reconcile every empirical manifest, refusing open historical liabilities."""
    program_lock.assert_held()
    excluded = excluded_run.resolve() if excluded_run is not None else None
    rows = []
    for directory in sorted(program_lock.path.parent.iterdir()):
        if directory.name.startswith("."):
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise AccountingError("unsupported empirical inventory entry")
        if excluded is not None and directory.resolve() == excluded:
            continue
        path = directory / "manifest.json"
        manifest = _read_json(path)
        ledger, budget = manifest.get("ledger"), manifest.get("budget")
        if ledger is not None and budget is not None and ledger != budget:
            raise AccountingError("historical manifest has conflicting ledgers")
        ledger = ledger if ledger is not None else budget
        if (manifest.get("empirical") is not True or not isinstance(ledger, dict)
                or not isinstance(ledger.get("consumed"), dict)
                or not isinstance(ledger.get("reserved"), dict)):
            raise AccountingError("historical manifest has no valid empirical ledger")
        consumed, reserved = ledger["consumed"], ledger["reserved"]
        for key in ("requests", "input_tokens", "output_tokens"):
            _integer(consumed.get(key), f"historical consumed {key}")
            _integer(reserved.get(key), f"historical reserved {key}")
        for key in ("requests", "input_tokens", "output_tokens", "cost_usd"):
            if _money(reserved.get(key)) != 0:
                raise AccountingError("historical manifest has outstanding reservations")
        status = manifest.get("status")
        if (not isinstance(status, str) or not status
                or status in {"created", "running", "executing"}):
            raise AccountingError("historical run is not at a safe stop")
        cost = _money(consumed.get("cost_usd"))
        rows.append({
            "manifest_path": str(path.relative_to(program_lock.path.parent)),
            "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "run_id": manifest.get("run_id") or manifest.get("config", {}).get("run_id"),
            "status": status, "consumed_cost_usd": str(cost), "reserved_cost_usd": "0",
        })
    return rows


def normalized_usage(value: Mapping[str, Any]) -> dict:
    if not isinstance(value, Mapping):
        raise AccountingError("provider usage must be an object")
    result = {name: _integer(value.get(name), name)
              for name in ("input_tokens", "output_tokens")}
    result["cached_input_tokens"] = _integer(value.get("cached_input_tokens", 0), "cached_input_tokens")
    if result["cached_input_tokens"] > result["input_tokens"]:
        raise AccountingError("cache categories exceed input tokens")
    writes_reported = value.get("cache_write_input_tokens") is not None
    result["cache_write_input_tokens"] = (
        _integer(value["cache_write_input_tokens"], "cache_write_input_tokens")
        if writes_reported else result["input_tokens"] - result["cached_input_tokens"])
    result["cache_write_accounting"] = value.get("cache_write_accounting", (
        "provider_reported" if writes_reported else "assumed_all_non_cached_write"))
    if result["cache_write_accounting"] not in {"provider_reported", "assumed_all_non_cached_write"}:
        raise AccountingError("unrecognized cache-write accounting basis")
    if ((not writes_reported and result["cache_write_accounting"] != "assumed_all_non_cached_write")
            or (result["cache_write_accounting"] == "assumed_all_non_cached_write"
                and result["cache_write_input_tokens"] != result["input_tokens"] - result["cached_input_tokens"])):
        raise AccountingError("cache-write assumption is inconsistent")
    for name in ("reasoning_tokens", "total_tokens"):
        result[name] = None if value.get(name) is None else _integer(value[name], name)
    if result["cached_input_tokens"] + result["cache_write_input_tokens"] > result["input_tokens"]:
        raise AccountingError("cache categories exceed input tokens")
    if result["reasoning_tokens"] is not None and result["reasoning_tokens"] > result["output_tokens"]:
        raise AccountingError("output tokens must include reasoning tokens")
    if result["total_tokens"] is not None and result["total_tokens"] != result["input_tokens"] + result["output_tokens"]:
        raise AccountingError("total token count does not reconcile")
    return result


def _price(input_tokens: int, output_tokens: int, cached: int = 0, writes: int = 0) -> Decimal:
    long = input_tokens > 272000
    return ((Decimal(input_tokens - cached - writes) * Decimal("0.20")
             + Decimal(cached) * Decimal("0.02") + Decimal(writes) * Decimal("0.25"))
            * (2 if long else 1)
            + Decimal(output_tokens) * Decimal("1.20") * (Decimal("1.5") if long else 1)) / Decimal(1000000)


def reservation_price(input_token_bound: int, max_output_tokens: int) -> Decimal:
    _integer(input_token_bound, "input_token_bound", 1)
    _integer(max_output_tokens, "max_output_tokens", 1)
    return _price(input_token_bound, max_output_tokens, writes=input_token_bound)


def usage_price(usage: Mapping[str, Any]) -> Decimal:
    value = normalized_usage(usage)
    return _price(value["input_tokens"], value["output_tokens"],
                  value["cached_input_tokens"], value["cache_write_input_tokens"])


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    identity: str
    attempt_number: int
    input_token_bound: int
    max_output_tokens: int
    cost_usd: Decimal
    request_hash: str

    def to_dict(self) -> dict:
        return {**self.__dict__, "cost_usd": str(self.cost_usd)}


def read_events(path: str | Path) -> list[dict]:
    """Verify the complete JSONL hash chain; semantic replay is done by the journal."""
    path = Path(path)
    _regular_file(path)
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise AccountingError("empty or partially written journal")
    events = []
    for line in raw.splitlines():
        try:
            row = json.loads(line, object_pairs_hook=_object_pairs)
        except (ValueError, UnicodeError) as exc:
            raise AccountingError("invalid journal JSON") from exc
        if not isinstance(row, dict):
            raise AccountingError("journal event must be an object")
        body = {k: v for k, v in row.items() if k != "event_hash"}
        if (type(row.get("sequence")) is not int or row.get("sequence") != len(events)
                or row.get("previous_event_hash") != (events[-1]["event_hash"] if events else "initial")
                or row.get("event_hash") != _digest(body)):
            raise AccountingError("journal hash chain is invalid")
        events.append(row)
    return events


class ReservationJournal:
    """Serialized reserve/settle ledger; holds no secrets or provider payloads."""

    def __init__(self, run_dir: str | Path, *, program_lock: ProgramLock,
                 binding: Mapping[str, Any], source_root: str | Path,
                 source_inventory: Mapping[str, str], campaign_limit_usd="100",
                 lifetime_limit_usd="250", max_attempts=4000, max_in_flight=3):
        self._mutex = threading.RLock()
        self.program_lock = program_lock
        program_lock.assert_held()
        if program_lock._journal is not None:
            raise AccountingError("one journal only may own the held program lock")
        self.path = Path(run_dir).absolute()
        if self.path.parent.resolve() != program_lock.path.parent.resolve() or self.path.is_symlink():
            raise AccountingError("campaign must be a direct nonsymlink empirical run directory")
        self.source_root = Path(source_root).resolve()
        if program_lock.path.parent.resolve() != (self.source_root / "runs" / "empirical").resolve():
            raise AccountingError("shared program lock must cover this source root's empirical runs")
        self.source_inventory = dict(source_inventory)
        if not self.source_inventory:
            raise AccountingError("a nonempty frozen source inventory is required")
        self._check_sources()
        binding = json.loads(_canonical(binding))
        if binding.get("model") != MODEL or not isinstance(binding.get("run_id"), str) or not binding["run_id"]:
            raise AccountingError("binding requires exact model identity and a run_id")
        self.campaign_limit = _money(campaign_limit_usd)
        self.lifetime_limit = _money(lifetime_limit_usd)
        self.max_attempts = _integer(max_attempts, "max_attempts", 1)
        self.max_in_flight = _integer(max_in_flight, "max_in_flight", 1)
        if (self.campaign_limit > 100 or self.lifetime_limit > 250
                or self.campaign_limit == 0 or self.lifetime_limit == 0
                or self.max_attempts > 4000 or self.max_in_flight > 3):
            raise AccountingError("configured cap exceeds the authorized campaign bounds")
        self._historical = historical_inventory(program_lock, excluded_run=self.path)
        self.historical_cost = sum((_money(row["consumed_cost_usd"]) for row in self._historical), Decimal(0))
        if self.historical_cost > self.lifetime_limit:
            raise BudgetExceeded("historical cost already exceeds the lifetime cap")
        self._binding = {
            "schema": VERSION, "campaign": binding, "source_inventory": dict(self.source_inventory),
            "pricing": copy.deepcopy(PRICING), "historical_runs": self._historical,
            "historical_inventory_hash": _digest(self._historical),
            "historical_cost_usd": str(self.historical_cost),
            "campaign_limit_usd": str(self.campaign_limit),
            "lifetime_limit_usd": str(self.lifetime_limit),
            "max_attempts": self.max_attempts, "max_in_flight": self.max_in_flight,
        }
        self._events: list[dict] = []
        self._reservations: dict[str, Reservation] = {}
        self._settlements: dict[str, dict] = {}
        self._identities: set[str] = set()
        self._stop_reason: str | None = None
        self._terminal: str | None = None
        self._poisoned = False
        existing = self.path.exists()
        if existing:
            for event in read_events(self.path / "journal.jsonl"):
                self._apply(event)
                self._events.append(event)
            if any(rid not in self._settlements for rid in self._reservations):
                self._append("stop", reason="unsettled_reservations_on_restart")
            self._write_manifest()
        else:
            self.path.mkdir(mode=0o700)
            _fsync_directory(self.path.parent)
            self._append("binding", binding=self._binding)
        program_lock._journal = self

    @property
    def events(self) -> list[dict]:
        with self._mutex:
            return copy.deepcopy(self._events)

    @property
    def stopped(self) -> bool:
        return self._stop_reason is not None or self._terminal is not None or self._poisoned

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    def _check_sources(self) -> None:
        for name, expected in self.source_inventory.items():
            path = Path(name)
            if (not isinstance(expected, str) or not _HASH.fullmatch(expected)
                    or path.is_absolute() or ".." in path.parts):
                raise AccountingError("invalid frozen source inventory")
            absolute = self.source_root / path
            if not absolute.resolve().is_relative_to(self.source_root):
                raise AccountingError("frozen source escapes its root")
            _regular_file(absolute)
            if hashlib.sha256(absolute.read_bytes()).hexdigest() != expected:
                raise AccountingError(f"frozen source drift: {name}")

    def assert_dispatchable(self) -> None:
        with self._mutex:
            self._assert_owner()
            if self.stopped:
                raise DispatchStopped(self._stop_reason or self._terminal or "durability_failure")
            self._check_sources()
            if historical_inventory(self.program_lock, excluded_run=self.path) != self._historical:
                raise AccountingError("historical empirical manifest inventory drifted")

    def _assert_owner(self) -> None:
        self.program_lock.assert_held()
        if self.program_lock._journal is not self:
            raise AccountingError("journal no longer owns the held program lock")

    def _totals(self) -> tuple[dict, dict]:
        consumed = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": Decimal(0)}
        reserved = dict(consumed)
        for rid, reservation in self._reservations.items():
            settled = self._settlements.get(rid)
            if settled is None or settled["outcome"] in {"ambiguous_timeout", "unknown"}:
                reserved["requests"] += 1
                reserved["input_tokens"] += reservation.input_token_bound
                reserved["output_tokens"] += reservation.max_output_tokens
                reserved["cost_usd"] += reservation.cost_usd
            else:
                consumed["requests"] += 1
                usage = settled["usage"]
                if usage is not None:
                    consumed["input_tokens"] += usage["input_tokens"]
                    consumed["output_tokens"] += usage["output_tokens"]
                    consumed["cost_usd"] += usage_price(usage)
        return consumed, reserved

    def _check_capacity(self, reservation: Reservation) -> None:
        if self.stopped:
            raise DispatchStopped(self._stop_reason or self._terminal or "durability_failure")
        if len(self._reservations) >= self.max_attempts:
            raise BudgetExceeded("physical attempt cap reached")
        if sum(rid not in self._settlements for rid in self._reservations) >= self.max_in_flight:
            raise BudgetExceeded("maximum in-flight provider calls reached")
        consumed, reserved = self._totals()
        cost = consumed["cost_usd"] + reserved["cost_usd"] + reservation.cost_usd
        if cost > self.campaign_limit or cost + self.historical_cost > self.lifetime_limit:
            raise BudgetExceeded("reservation would exceed campaign or lifetime dollar cap")

    def reserve(self, identity: str, *, input_token_bound: int, max_output_tokens: int,
                request_hash: str, metadata: Mapping[str, Any] | None = None) -> Reservation:
        with self._mutex:
            self.assert_dispatchable()
            if not isinstance(identity, str) or not identity or identity in self._identities:
                raise AccountingError("identity must be nonempty and never previously attempted")
            if not isinstance(request_hash, str) or not _HASH.fullmatch(request_hash):
                raise AccountingError("request_hash must be a SHA-256 digest")
            metadata = dict(metadata or {})
            if any(not isinstance(key, str) or type(value) not in (str, int, bool, type(None))
                   for key, value in metadata.items()):
                raise AccountingError("metadata accepts only safe scalar identifiers")
            reservation = Reservation(uuid.uuid4().hex, identity, len(self._reservations) + 1,
                                      input_token_bound, max_output_tokens,
                                      reservation_price(input_token_bound, max_output_tokens), request_hash)
            self._check_capacity(reservation)
            self._append("reservation", reservation=reservation.to_dict(), metadata=metadata)
            return reservation

    def settle(self, reservation_id: str, *, usage: Mapping[str, Any] | None = None,
               outcome: str = "completed", provider_request_id: str | None = None,
               noncompletion_evidence: str | None = None) -> dict:
        with self._mutex:
            self._assert_owner()
            if self._poisoned:
                raise DispatchStopped("durability_failure")
            if reservation_id not in self._reservations or reservation_id in self._settlements:
                raise AccountingError("settlement must name exactly one open reservation")
            if outcome not in {"completed", "known_not_completed", "ambiguous_timeout", "unknown"}:
                raise AccountingError("unsupported settlement outcome")
            if provider_request_id is not None and not isinstance(provider_request_id, str):
                raise AccountingError("provider request ID must be a string or null")
            if outcome == "known_not_completed" and usage is not None:
                raise AccountingError("proven noncompletion requires null usage")
            if outcome == "known_not_completed":
                if not isinstance(noncompletion_evidence, str) or not noncompletion_evidence:
                    raise AccountingError("proven noncompletion requires a safe evidence identifier")
            elif noncompletion_evidence is not None:
                raise AccountingError("noncompletion evidence is only valid for proven noncompletion")
            reason = None
            normalized = None
            if outcome == "completed":
                try:
                    normalized = normalized_usage(usage)
                except AccountingError:
                    outcome, reason = "unknown", "missing_or_invalid_provider_usage"
            elif outcome in {"ambiguous_timeout", "unknown"}:
                reason = outcome
            event = self._append("settlement", reservation_id=reservation_id, outcome=outcome,
                                 usage=normalized, provider_request_id=provider_request_id,
                                 uncertainty_reason=reason,
                                 noncompletion_evidence=noncompletion_evidence)
            return copy.deepcopy(event)

    def stop(self, reason: str) -> None:
        with self._mutex:
            self._assert_owner()
            if not isinstance(reason, str) or not reason:
                raise AccountingError("stop reason must be a nonempty safe identifier")
            self._append("stop", reason=reason)

    def finish(self, status: str = "completed") -> None:
        with self._mutex:
            self._assert_owner()
            if status not in {"completed", "blocked", "failed", "partial", "stopped"}:
                raise AccountingError("unsupported terminal status")
            if status == "completed" and (self.stopped or self._totals()[1]["requests"]):
                raise AccountingError("cannot complete with stopped dispatch or unresolved charges")
            self._append("terminal", status=status)

    def snapshot(self) -> dict:
        with self._mutex:
            consumed, reserved = self._totals()
            campaign_total = consumed["cost_usd"] + reserved["cost_usd"]
            return {
                "consumed": {**consumed, "cost_usd": str(consumed["cost_usd"])},
                "reserved": {**reserved, "cost_usd": str(reserved["cost_usd"])},
                "physical_attempts": len(self._reservations),
                "in_flight": sum(rid not in self._settlements for rid in self._reservations),
                "campaign_exposure_usd": str(campaign_total),
                "historical_cost_usd": str(self.historical_cost),
                "lifetime_exposure_usd": str(campaign_total + self.historical_cost),
                "stop_reason": self._stop_reason, "pricing": copy.deepcopy(PRICING),
            }

    def _append(self, kind: str, **fields) -> dict:
        if self._poisoned:
            raise DispatchStopped("durability_failure")
        body = {"sequence": len(self._events),
                "previous_event_hash": self._events[-1]["event_hash"] if self._events else "initial",
                "recorded_at": datetime.now(timezone.utc).isoformat(), "kind": kind, **fields}
        event = {**body, "event_hash": _digest(body)}
        # Apply validation before write. Any write/manifest failure poisons this
        # object, so it can never dispatch with an undurable in-memory state.
        self._apply(event)
        try:
            fd = os.open(self.path / "journal.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "ab") as stream:
                stream.write((_canonical(event) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            self._events.append(event)
            self._write_manifest()
        except BaseException:
            self._poisoned = True
            raise
        return event

    def _apply(self, event: dict) -> None:
        kind = event.get("kind")
        expected = {
            "binding": {"binding"}, "reservation": {"reservation", "metadata"},
            "settlement": {"reservation_id", "outcome", "usage", "provider_request_id", "uncertainty_reason", "noncompletion_evidence"},
            "stop": {"reason"}, "terminal": {"status"},
        }
        base = {"sequence", "previous_event_hash", "recorded_at", "kind", "event_hash"}
        if kind not in expected or set(event) != base | expected[kind]:
            raise AccountingError("unrecognized journal event schema")
        if (type(event["sequence"]) is not int or event["sequence"] != len(self._events)
                or not isinstance(event["recorded_at"], str) or not event["recorded_at"]):
            raise AccountingError("invalid journal sequence or timestamp")
        if not self._events and kind != "binding":
            raise AccountingError("journal must begin with its frozen binding")
        if kind == "binding":
            if self._events or event["binding"] != self._binding:
                raise AccountingError("frozen accounting binding drifted")
        elif kind == "reservation":
            value = event["reservation"]
            metadata = event["metadata"]
            if not isinstance(metadata, dict) or any(
                    not isinstance(key, str) or type(value) not in (str, int, bool, type(None))
                    for key, value in metadata.items()):
                raise AccountingError("invalid reservation metadata")
            keys = {"reservation_id", "identity", "attempt_number", "input_token_bound",
                    "max_output_tokens", "cost_usd", "request_hash"}
            if not isinstance(value, dict) or set(value) != keys:
                raise AccountingError("invalid reservation schema")
            rid, identity = value["reservation_id"], value["identity"]
            if (not isinstance(rid, str) or not re.fullmatch(r"[0-9a-f]{32}", rid)
                    or rid in self._reservations or not isinstance(identity, str) or not identity
                    or identity in self._identities
                    or _integer(value["attempt_number"], "attempt_number", 1) != len(self._reservations) + 1
                    or not isinstance(value["request_hash"], str) or not _HASH.fullmatch(value["request_hash"])):
                raise AccountingError("invalid or duplicate reservation binding")
            cost = reservation_price(value["input_token_bound"], value["max_output_tokens"])
            if cost != _money(value["cost_usd"]):
                raise AccountingError("reservation cost does not match frozen pricing")
            reservation = Reservation(**{**value, "cost_usd": cost})
            self._check_capacity(reservation)
            self._reservations[rid] = reservation
            self._identities.add(identity)
        elif kind == "settlement":
            rid = event["reservation_id"]
            if rid not in self._reservations or rid in self._settlements:
                raise AccountingError("settlement has no unique matching reservation")
            outcome, usage = event["outcome"], event["usage"]
            if (event["provider_request_id"] is not None
                    and not isinstance(event["provider_request_id"], str)):
                raise AccountingError("invalid provider request identifier")
            if outcome != "known_not_completed" and event["noncompletion_evidence"] is not None:
                raise AccountingError("invalid noncompletion evidence")
            if outcome == "completed":
                if usage != normalized_usage(usage) or event["uncertainty_reason"] is not None:
                    raise AccountingError("invalid completed usage normalization")
                reservation = self._reservations[rid]
                if (usage["input_tokens"] > reservation.input_token_bound
                        or usage["output_tokens"] > reservation.max_output_tokens
                        or usage_price(usage) > reservation.cost_usd):
                    self._stop_reason = "provider_usage_exceeds_reserved_bound"
            elif outcome in {"ambiguous_timeout", "unknown"}:
                if usage is not None or not isinstance(event["uncertainty_reason"], str):
                    raise AccountingError("invalid uncertain settlement")
                self._stop_reason = event["uncertainty_reason"]
            elif outcome == "known_not_completed":
                if (usage is not None or event["uncertainty_reason"] is not None
                        or not isinstance(event["noncompletion_evidence"], str)
                        or not event["noncompletion_evidence"]):
                    raise AccountingError("noncompletion cannot consume usage")
            else:
                raise AccountingError("unrecognized settlement outcome")
            self._settlements[rid] = event
        elif kind == "stop":
            if not isinstance(event["reason"], str) or not event["reason"]:
                raise AccountingError("invalid stop reason")
            self._stop_reason = event["reason"]
        elif kind == "terminal":
            if self._terminal is not None or event["status"] not in {"completed", "blocked", "failed", "partial", "stopped"}:
                raise AccountingError("invalid terminal state")
            if event["status"] == "completed" and (self.stopped or self._totals()[1]["requests"]):
                raise AccountingError("invalid completed state")
            self._terminal = event["status"]

    def _manifest_value(self) -> dict:
        snapshot = self.snapshot()
        return {
            "artifact_type": VERSION, "run_id": self._binding["campaign"]["run_id"],
            "empirical": True, "execution_mode": "empirical_live",
            "status": self._terminal or ("stopped" if self.stopped else "running"),
            "binding_hash": _digest(self._binding),
            "journal_tail": self._events[-1]["event_hash"], "event_count": len(self._events),
            "ledger": snapshot, "paid_calls": len(self._reservations),
            "paid_cost_usd": snapshot["consumed"]["cost_usd"],
            "cost_basis": PRICING["cost_basis"], "stop_reason": self._stop_reason,
        }

    def _write_manifest(self) -> None:
        _atomic_json(self.path / "manifest.json", self._manifest_value())


def verify_run(run_dir: str | Path, *, source_root: str | Path | None = None,
               program_lock: ProgramLock | None = None) -> dict:
    """Read-only journal replay and exact manifest reconciliation.

    No source or ledger file is created, repaired, or mutated. Supplying
    ``source_root`` additionally verifies current frozen source files; supplying
    an already held ``program_lock`` rechecks the historical manifests. An audit
    concurrent with a manifest change fails closed and can be rerun after stop.
    An open reservation is reported, never silently closed or made retryable.
    """
    path = Path(run_dir).absolute()
    if path.is_symlink():
        raise AccountingError("run directory must not be a symlink")
    manifest = _read_json(path / "manifest.json")
    events = read_events(path / "journal.jsonl")
    frozen = events[0].get("binding")
    fields = {"schema", "campaign", "source_inventory", "pricing", "historical_runs",
              "historical_inventory_hash", "historical_cost_usd", "campaign_limit_usd",
              "lifetime_limit_usd", "max_attempts", "max_in_flight"}
    if not isinstance(frozen, dict) or set(frozen) != fields:
        raise AccountingError("invalid frozen accounting binding schema")
    if frozen["schema"] != VERSION or frozen["pricing"] != PRICING:
        raise AccountingError("frozen accounting version or pricing drifted")
    campaign = frozen["campaign"]
    if (not isinstance(campaign, dict) or campaign.get("model") != MODEL
            or not isinstance(campaign.get("run_id"), str) or not campaign["run_id"]):
        raise AccountingError("invalid campaign model or run identity")
    # Construct only an in-memory replay view: never invoke the mutating opener.
    journal = ReservationJournal.__new__(ReservationJournal)
    journal.path = path
    journal._mutex = threading.RLock()
    journal._binding = frozen
    journal._events, journal._reservations, journal._settlements = [], {}, {}
    journal._identities = set()
    journal._stop_reason, journal._terminal, journal._poisoned = None, None, False
    journal.campaign_limit = _money(frozen["campaign_limit_usd"])
    journal.lifetime_limit = _money(frozen["lifetime_limit_usd"])
    journal.max_attempts = _integer(frozen["max_attempts"], "max_attempts", 1)
    journal.max_in_flight = _integer(frozen["max_in_flight"], "max_in_flight", 1)
    if (not 0 < journal.campaign_limit <= 100 or not 0 < journal.lifetime_limit <= 250
            or journal.max_attempts > 4000 or journal.max_in_flight > 3):
        raise AccountingError("frozen accounting caps exceed authorization")
    journal._historical = frozen["historical_runs"]
    if (not isinstance(journal._historical, list)
            or _digest(journal._historical) != frozen["historical_inventory_hash"]):
        raise AccountingError("frozen historical inventory hash does not match")
    historical_fields = {"manifest_path", "manifest_sha256", "run_id", "status",
                         "consumed_cost_usd", "reserved_cost_usd"}
    for row in journal._historical:
        if (not isinstance(row, dict) or set(row) != historical_fields
                or not isinstance(row["manifest_sha256"], str)
                or not _HASH.fullmatch(row["manifest_sha256"])
                or _money(row["reserved_cost_usd"]) != 0):
            raise AccountingError("invalid frozen historical inventory row")
    journal.historical_cost = sum((_money(row["consumed_cost_usd"])
                                   for row in journal._historical), Decimal(0))
    if (journal.historical_cost != _money(frozen["historical_cost_usd"])
            or journal.historical_cost > journal.lifetime_limit):
        raise AccountingError("frozen historical spend does not reconcile")
    journal.source_inventory = frozen["source_inventory"]
    if not isinstance(journal.source_inventory, dict) or not journal.source_inventory:
        raise AccountingError("invalid frozen source inventory")
    for name, expected in journal.source_inventory.items():
        if (not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts
                or not isinstance(expected, str) or not _HASH.fullmatch(expected)):
            raise AccountingError("invalid frozen source inventory entry")
    if source_root is not None:
        journal.source_root = Path(source_root).resolve()
        journal._check_sources()
    if program_lock is not None:
        if path.parent.resolve() != program_lock.path.parent.resolve():
            raise AccountingError("run is outside the held shared program lock")
        if historical_inventory(program_lock, excluded_run=path) != journal._historical:
            raise AccountingError("historical inventory drifted since frozen binding")
    for event in events:
        journal._apply(event)
        journal._events.append(event)
    if manifest != _read_json(path / "manifest.json"):
        raise AccountingError("manifest changed during read-only verification")
    if journal._manifest_value() != manifest:
        raise AccountingError("manifest does not exactly reconcile with journal replay")
    snapshot = journal.snapshot()
    return {"schema": VERSION, "run_id": campaign["run_id"], "journal_valid": True,
            "manifest_consistent": True, "event_count": len(events),
            "source_inventory_verified": source_root is not None,
            "historical_inventory_rechecked_under_lock": program_lock is not None,
            "requires_restart_stop": snapshot["in_flight"] > 0,
            "stopped": journal.stopped, "ledger": snapshot}
