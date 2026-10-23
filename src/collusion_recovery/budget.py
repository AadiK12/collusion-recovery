"""Conservative request, token, concurrency, and dollar accounting.

Reservations are made before dispatch.  A request whose completion is
ambiguous consumes its entire reservation; it is never made cheap by missing
usage metadata.  Known, completed attempts are reconciled to returned usage,
using the full input rate even when a provider reports cached tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .common import digest
from .live_config import ExecutionLimits, Pricing


MILLION = Decimal(1_000_000)


class BudgetError(RuntimeError):
    pass


class BudgetExhausted(BudgetError):
    """The next dispatch cannot be reserved within every approved ceiling."""


class BudgetStateError(BudgetError):
    """The durable accounting stream is inconsistent or exceeds a reservation."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "Usage | None":
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise BudgetStateError("provider usage must be a mapping or null")
        # Provider adapters expose these normalized keys.  Extra metadata such
        # as cached/reasoning details may be retained in the raw result record.
        if "input_tokens" not in value or "output_tokens" not in value:
            return None
        input_tokens, output_tokens = value["input_tokens"], value["output_tokens"]
        # Some provider failures include a usage object whose normalized
        # counters are null.  That is missing accounting, not a trustworthy
        # zero; the caller will conservatively consume the full reservation.
        if input_tokens is None or output_tokens is None:
            return None
        if (type(input_tokens) is not int or input_tokens < 0
                or type(output_tokens) is not int or output_tokens < 0):
            raise BudgetStateError("provider token usage must be nonnegative integers")
        return cls(input_tokens, output_tokens)

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    identity: str
    attempt: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {"reservation_id": self.reservation_id, "identity": self.identity,
                "attempt": self.attempt, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cost_usd": str(self.cost_usd)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Reservation":
        try:
            result = cls(str(value["reservation_id"]), str(value["identity"]),
                         int(value["attempt"]), int(value["input_tokens"]),
                         int(value["output_tokens"]), Decimal(str(value["cost_usd"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise BudgetStateError("malformed reservation record") from exc
        expected = digest({"identity": result.identity, "attempt": result.attempt})
        if (result.reservation_id != expected or result.attempt < 0
                or result.input_tokens < 1 or result.output_tokens < 1
                or result.cost_usd < 0):
            raise BudgetStateError("invalid reservation record")
        return result


class BudgetLedger:
    """Mutable in-memory view reconstructed from an append-only run journal."""

    def __init__(self, limits: ExecutionLimits, pricing: Pricing,
                 approved_budget_usd: Decimal):
        if approved_budget_usd < 0:
            raise BudgetStateError("approved budget cannot be negative")
        self.limits = limits
        self.pricing = pricing
        self.approved_budget_usd = approved_budget_usd
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = Decimal(0)
        self.active: dict[str, Reservation] = {}
        self.seen: set[str] = set()

    def price(self, input_tokens: int, output_tokens: int) -> Decimal:
        return ((Decimal(input_tokens) * self.pricing.input_usd_per_million
                 + Decimal(output_tokens) * self.pricing.output_usd_per_million) / MILLION)

    def _active_totals(self) -> tuple[int, int, Decimal]:
        return (sum(r.input_tokens for r in self.active.values()),
                sum(r.output_tokens for r in self.active.values()),
                sum((r.cost_usd for r in self.active.values()), Decimal(0)))

    def reserve(self, identity: str, attempt: int, *, input_tokens: int | None = None,
                output_tokens: int | None = None) -> Reservation:
        if not isinstance(identity, str) or not identity or type(attempt) is not int or attempt < 0:
            raise BudgetStateError("reservation identity/attempt is malformed")
        reservation_id = digest({"identity": identity, "attempt": attempt})
        if reservation_id in self.seen or reservation_id in self.active:
            raise BudgetStateError("attempt already has a durable reservation")
        input_tokens = self.limits.max_input_tokens_per_request if input_tokens is None else input_tokens
        output_tokens = self.limits.max_output_tokens_per_request if output_tokens is None else output_tokens
        if (type(input_tokens) is not int or input_tokens < 1
                or input_tokens > self.limits.max_input_tokens_per_request):
            raise BudgetStateError("input-token reservation exceeds the per-request cap")
        if (type(output_tokens) is not int or output_tokens < 1
                or output_tokens > self.limits.max_output_tokens_per_request):
            raise BudgetStateError("output-token reservation exceeds the per-request cap")
        active_input, active_output, active_cost = self._active_totals()
        cost = self.price(input_tokens, output_tokens)
        checks = (
            (self.requests + len(self.active) + 1 <= self.limits.max_requests, "request"),
            (len(self.active) + 1 <= self.limits.max_in_flight, "in-flight"),
            (self.input_tokens + active_input + input_tokens <= self.limits.max_total_input_tokens,
             "input-token"),
            (self.output_tokens + active_output + output_tokens <= self.limits.max_total_output_tokens,
             "output-token"),
            (self.cost_usd + active_cost + cost <= self.approved_budget_usd, "dollar"),
        )
        for allowed, name in checks:
            if not allowed:
                raise BudgetExhausted(f"{name} ceiling would be exceeded before dispatch")
        reservation = Reservation(reservation_id, identity, attempt, input_tokens, output_tokens, cost)
        self.active[reservation_id] = reservation
        return reservation

    def restore_reservation(self, reservation: Reservation) -> None:
        """Replay one already-authorized durable reservation without re-gating it."""
        if reservation.reservation_id in self.seen or reservation.reservation_id in self.active:
            raise BudgetStateError("duplicate reservation in durable journal")
        self.active[reservation.reservation_id] = reservation

    def reconcile(self, reservation_id: str, usage: Mapping[str, Any] | None,
                  *, ambiguous: bool = False, known_not_completed: bool = False) -> dict[str, Any]:
        try:
            reservation = self.active.pop(reservation_id)
        except KeyError as exc:
            raise BudgetStateError("result has no unique active reservation") from exc
        if reservation_id in self.seen:
            raise BudgetStateError("reservation was already reconciled")
        if ambiguous and known_not_completed:
            raise BudgetStateError("an attempt cannot be both ambiguous and known not completed")
        normalized = None if ambiguous or known_not_completed else Usage.from_mapping(usage)
        conservative = ambiguous or (normalized is None and not known_not_completed)
        used_input = 0 if known_not_completed else reservation.input_tokens if conservative else normalized.input_tokens
        used_output = 0 if known_not_completed else reservation.output_tokens if conservative else normalized.output_tokens
        used_cost = Decimal(0) if known_not_completed else reservation.cost_usd if conservative else self.price(used_input, used_output)
        self.requests += 1
        self.input_tokens += used_input
        self.output_tokens += used_output
        self.cost_usd += used_cost
        self.seen.add(reservation_id)
        breached = (used_input > reservation.input_tokens
                    or used_output > reservation.output_tokens
                    or self.input_tokens > self.limits.max_total_input_tokens
                    or self.output_tokens > self.limits.max_total_output_tokens
                    or self.cost_usd > self.approved_budget_usd)
        result = {"reservation_id": reservation_id,
                  "accounting": ("known_not_completed" if known_not_completed
                                  else "full_reservation" if conservative else "reported_usage"),
                  "ambiguous": ambiguous, "input_tokens": used_input,
                  "output_tokens": used_output, "cost_usd": str(used_cost),
                  "reservation_breached": breached}
        if breached:
            raise BudgetStateError("reported usage exceeded its reservation or an approved ceiling")
        return result

    @classmethod
    def replay(cls, limits: ExecutionLimits, pricing: Pricing, approved_budget_usd: Decimal,
               records: Iterable[Mapping[str, Any]]) -> "BudgetLedger":
        ledger = cls(limits, pricing, approved_budget_usd)
        for record in records:
            kind = record.get("kind")
            if kind == "reservation":
                ledger.restore_reservation(Reservation.from_mapping(record["reservation"]))
            elif kind == "result":
                ledger.reconcile(str(record.get("reservation_id")), record.get("usage"),
                                 ambiguous=bool(record.get("ambiguous")),
                                 known_not_completed=bool(record.get("known_not_completed")))
        return ledger

    def snapshot(self) -> dict[str, Any]:
        reserved_input, reserved_output, reserved_cost = self._active_totals()
        return {
            "approved": {"requests": self.limits.max_requests,
                         "input_tokens": self.limits.max_total_input_tokens,
                         "output_tokens": self.limits.max_total_output_tokens,
                         "cost_usd": str(self.approved_budget_usd),
                         "max_in_flight": self.limits.max_in_flight},
            "consumed": {"requests": self.requests, "input_tokens": self.input_tokens,
                         "output_tokens": self.output_tokens, "cost_usd": str(self.cost_usd)},
            "reserved": {"requests": len(self.active), "input_tokens": reserved_input,
                         "output_tokens": reserved_output, "cost_usd": str(reserved_cost)},
            "remaining_after_reservations": {
                "requests": self.limits.max_requests - self.requests - len(self.active),
                "input_tokens": self.limits.max_total_input_tokens - self.input_tokens - reserved_input,
                "output_tokens": self.limits.max_total_output_tokens - self.output_tokens - reserved_output,
                "cost_usd": str(self.approved_budget_usd - self.cost_usd - reserved_cost),
            },
        }
