"""Conservative request, token, concurrency, and dollar accounting.

Reservations are made before dispatch.  A request whose completion is
ambiguous consumes its entire reservation; it is never made cheap by missing
usage metadata.  Known, completed attempts are reconciled to returned usage
using the configured ordinary-input, cached-input, cache-write, and output
rates.  Reservations use the most expensive possible input category.
For the frozen gpt-6.1-sol price source, requests above 272,000 input tokens
apply the provider's request-wide input/cache and output multipliers in both
reservation and completed-usage reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .common import canonical, digest
from .live_config import ExecutionLimits, Pricing


MILLION = Decimal(1_000_000)
LONG_CONTEXT_PRICING_SOURCE = (
    "https://developers.openai.com/api/docs/models/gpt-6.1-sol"
)
LONG_CONTEXT_THRESHOLD_INPUT_TOKENS = 272_000
LONG_CONTEXT_INPUT_MULTIPLIER = Decimal("2")
LONG_CONTEXT_OUTPUT_MULTIPLIER = Decimal("1.5")


class BudgetError(RuntimeError):
    pass


class BudgetExhausted(BudgetError):
    """The next dispatch cannot be reserved within every approved ceiling."""


class BudgetStateError(BudgetError):
    """The durable accounting stream is inconsistent or exceeds a reservation."""


def _is_durable_journal_record(value: Mapping[str, Any]) -> bool:
    """Distinguish hash-journal rows from small ledger-only unit-test records."""
    return "sequence" in value or "event_hash" in value


def _validate_durable_result_binding(
        result: Mapping[str, Any], reservation_event: Mapping[str, Any]) -> None:
    """Bind every replayed journal result to its reservation and provider record.

    The hash chain makes a journal internally tamper-evident, but an attacker
    able to rewrite and rehash the whole local chain must not be able to move a
    result to another logical identity, change whether it is retryable, or make
    billed usage disappear while retaining a different nested provider result.
    """
    try:
        reservation = Reservation.from_mapping(reservation_event["reservation"])
    except (KeyError, TypeError) as exc:
        raise BudgetStateError("durable reservation event is malformed") from exc
    for name, expected in (
        ("identity", reservation.identity),
        ("attempt", reservation.attempt),
        ("request_hash", reservation_event.get("request_hash")),
        ("wire_payload_hash", reservation_event.get("wire_payload_hash")),
    ):
        if result.get(name) != expected:
            raise BudgetStateError(
                f"durable result {name} disagrees with its reservation")

    provider_result = result.get("provider_result")
    if not isinstance(provider_result, Mapping):
        raise BudgetStateError("durable result lacks its provider result object")
    if (not isinstance(result.get("status"), str)
            or result.get("status") != provider_result.get("status")):
        raise BudgetStateError(
            "durable result status disagrees with its provider result")
    for name in ("ambiguous", "known_not_completed"):
        top_level = result.get(name)
        nested = provider_result.get(name, False)
        if type(top_level) is not bool or type(nested) is not bool or top_level != nested:
            raise BudgetStateError(
                f"durable result {name} disagrees with its provider result")
    if canonical(result.get("usage")) != canonical(provider_result.get("usage")):
        raise BudgetStateError(
            "durable result usage disagrees with its provider result")

    status = result["status"]
    ambiguous = result["ambiguous"]
    known_not_completed = result["known_not_completed"]
    if ambiguous != (status == "ambiguous_timeout"):
        raise BudgetStateError("durable result ambiguity classification is invalid")
    if known_not_completed and status not in {
            "retryable_error", "authentication_error", "quota_error", "budget_error"}:
        raise BudgetStateError(
            "durable result completion certainty is invalid for its status")
    if known_not_completed and result.get("usage") is not None:
        # ProviderResult itself forbids any usage object when the transport
        # proves that the request was not completed. Enforce that exact
        # invariant during replay instead of accepting malformed, negative,
        # or string-valued counters that merely happen not to be positive.
        raise BudgetStateError(
            "known-not-completed durable result reports consumed usage")


def pricing_multipliers(pricing: Pricing, input_tokens: int) -> tuple[Decimal, Decimal]:
    """Return the request-wide input/output multipliers for a token count.

    The frozen gpt-6.1-sol price source applies the long-context rates to a
    request only when its input exceeds 272,000 tokens.  Binding the rule to
    the exact source prevents it from silently changing prices for fake or
    unrelated models that happen to reuse this generic ledger.
    """
    if type(input_tokens) is not int or input_tokens < 0:
        raise BudgetStateError("input-token usage must be a nonnegative integer")
    if (pricing.source == LONG_CONTEXT_PRICING_SOURCE
            and input_tokens > LONG_CONTEXT_THRESHOLD_INPUT_TOKENS):
        return LONG_CONTEXT_INPUT_MULTIPLIER, LONG_CONTEXT_OUTPUT_MULTIPLIER
    return Decimal("1"), Decimal("1")


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

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
        input_details: dict[str, int] = {}
        for name in ("cached_input_tokens", "cache_write_input_tokens"):
            item = value.get(name, 0)
            if item is None:
                return None
            if type(item) is not int or item < 0:
                raise BudgetStateError(
                    f"provider {name.replace('_', '-')} usage must be a nonnegative integer")
            input_details[name] = item
        cached_input_tokens = input_details["cached_input_tokens"]
        cache_write_input_tokens = input_details["cache_write_input_tokens"]
        if cached_input_tokens + cache_write_input_tokens > input_tokens:
            raise BudgetStateError(
                "cached plus cache-write usage exceeds total input tokens")
        details: dict[str, int | None] = {}
        for name in ("reasoning_tokens", "total_tokens"):
            item = value.get(name)
            if item is not None and (type(item) is not int or item < 0):
                raise BudgetStateError(
                    f"provider {name.replace('_', '-')} usage must be a nonnegative integer or null")
            details[name] = item
        reasoning_tokens = details["reasoning_tokens"]
        total_tokens = details["total_tokens"]
        # The Responses API's output_tokens counter is the total billed output,
        # including hidden reasoning tokens.  Never add reasoning_tokens again;
        # instead, reject an internally impossible normalization.
        if reasoning_tokens is not None and reasoning_tokens > output_tokens:
            raise BudgetStateError(
                "provider output-token usage must include all reasoning tokens")
        if total_tokens is not None and total_tokens != input_tokens + output_tokens:
            raise BudgetStateError(
                "provider total-token usage disagrees with input plus output")
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_write_input_tokens=cache_write_input_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
        )

    def to_dict(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_input_tokens": self.cache_write_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass(frozen=True)
class DispatchReleaseLimits:
    """A stage-scoped dispatch overlay, separate from the immutable pilot config.

    The full-pilot :class:`ExecutionLimits` remain unchanged.  Passing this
    value to ``BudgetLedger`` only narrows the currently authorized Stage 2
    window, so a later stage requires a fresh, explicit release rather than a
    config rewrite.
    """

    max_dispatch_attempts: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_cost_usd: Decimal
    max_in_flight: int
    max_input_tokens_per_request: int
    max_output_tokens_per_request: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DispatchReleaseLimits":
        expected = {
            "max_dispatch_attempts",
            "max_total_input_tokens",
            "max_total_output_tokens",
            "max_cost_usd",
            "max_in_flight",
            "max_input_tokens_per_request",
            "max_output_tokens_per_request",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise BudgetStateError(
                f"dispatch release limits must contain exactly {sorted(expected)}")
        integers: dict[str, int] = {}
        for name in expected - {"max_cost_usd"}:
            item = value[name]
            if type(item) is not int or item < 1:
                raise BudgetStateError(f"dispatch release {name} must be a positive integer")
            integers[name] = item
        try:
            cost = Decimal(str(value["max_cost_usd"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise BudgetStateError("dispatch release max_cost_usd must be a decimal") from exc
        if not cost.is_finite() or cost <= 0:
            raise BudgetStateError("dispatch release max_cost_usd must be positive and finite")
        if integers["max_in_flight"] > integers["max_dispatch_attempts"]:
            raise BudgetStateError(
                "dispatch release max_in_flight exceeds its dispatch-attempt cap")
        if integers["max_input_tokens_per_request"] > integers["max_total_input_tokens"]:
            raise BudgetStateError(
                "dispatch release per-request input cap exceeds its total input cap")
        if integers["max_output_tokens_per_request"] > integers["max_total_output_tokens"]:
            raise BudgetStateError(
                "dispatch release per-request output cap exceeds its total output cap")
        return cls(
            max_dispatch_attempts=integers["max_dispatch_attempts"],
            max_total_input_tokens=integers["max_total_input_tokens"],
            max_total_output_tokens=integers["max_total_output_tokens"],
            max_cost_usd=cost,
            max_in_flight=integers["max_in_flight"],
            max_input_tokens_per_request=integers["max_input_tokens_per_request"],
            max_output_tokens_per_request=integers["max_output_tokens_per_request"],
        )

    def to_dict(self) -> dict[str, int | str]:
        return {
            "max_dispatch_attempts": self.max_dispatch_attempts,
            "max_total_input_tokens": self.max_total_input_tokens,
            "max_total_output_tokens": self.max_total_output_tokens,
            "max_cost_usd": str(self.max_cost_usd),
            "max_in_flight": self.max_in_flight,
            "max_input_tokens_per_request": self.max_input_tokens_per_request,
            "max_output_tokens_per_request": self.max_output_tokens_per_request,
        }


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
                 approved_budget_usd: Decimal,
                 release_limits: DispatchReleaseLimits | None = None):
        if not isinstance(approved_budget_usd, Decimal):
            try:
                approved_budget_usd = Decimal(str(approved_budget_usd))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise BudgetStateError("approved budget must be a decimal") from exc
        if not approved_budget_usd.is_finite() or approved_budget_usd < 0:
            raise BudgetStateError("approved budget cannot be negative")
        if release_limits is not None and not isinstance(release_limits, DispatchReleaseLimits):
            raise BudgetStateError("release limits must be DispatchReleaseLimits or null")
        self.limits = limits
        self.pricing = pricing
        self.approved_budget_usd = approved_budget_usd
        self.release_limits = release_limits
        self.max_requests = min(
            limits.max_requests,
            (release_limits.max_dispatch_attempts
             if release_limits is not None else limits.max_requests),
        )
        self.max_total_input_tokens = min(
            limits.max_total_input_tokens,
            (release_limits.max_total_input_tokens
             if release_limits is not None else limits.max_total_input_tokens),
        )
        self.max_total_output_tokens = min(
            limits.max_total_output_tokens,
            (release_limits.max_total_output_tokens
             if release_limits is not None else limits.max_total_output_tokens),
        )
        self.max_in_flight = min(
            limits.max_in_flight,
            (release_limits.max_in_flight
             if release_limits is not None else limits.max_in_flight),
        )
        self.max_cost_usd = min(
            approved_budget_usd,
            (release_limits.max_cost_usd
             if release_limits is not None else approved_budget_usd),
        )
        self.max_input_tokens_per_request = min(
            limits.max_input_tokens_per_request,
            (release_limits.max_input_tokens_per_request
             if release_limits is not None else limits.max_input_tokens_per_request),
        )
        self.max_output_tokens_per_request = min(
            limits.max_output_tokens_per_request,
            (release_limits.max_output_tokens_per_request
             if release_limits is not None else limits.max_output_tokens_per_request),
        )
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = Decimal(0)
        self.active: dict[str, Reservation] = {}
        self.seen: set[str] = set()

    def price(self, input_tokens: int, output_tokens: int, *,
              cached_input_tokens: int = 0,
              cache_write_input_tokens: int = 0) -> Decimal:
        if (type(output_tokens) is not int or output_tokens < 0
                or type(cached_input_tokens) is not int or cached_input_tokens < 0
                or type(cache_write_input_tokens) is not int
                or cache_write_input_tokens < 0):
            raise BudgetStateError("provider token usage must be nonnegative integers")
        input_multiplier, output_multiplier = pricing_multipliers(
            self.pricing, input_tokens)
        ordinary = input_tokens - cached_input_tokens - cache_write_input_tokens
        if ordinary < 0:
            raise BudgetStateError(
                "cached plus cache-write usage exceeds total input tokens")
        return (
            Decimal(ordinary) * self.pricing.input_usd_per_million * input_multiplier
            + Decimal(cached_input_tokens)
            * self.pricing.cached_input_usd_per_million * input_multiplier
            + Decimal(cache_write_input_tokens)
            * self.pricing.cache_write_input_usd_per_million * input_multiplier
            + Decimal(output_tokens) * self.pricing.output_usd_per_million
            * output_multiplier
        ) / MILLION

    def reservation_price(self, input_tokens: int, output_tokens: int) -> Decimal:
        if type(output_tokens) is not int or output_tokens < 0:
            raise BudgetStateError("output-token usage must be a nonnegative integer")
        input_multiplier, output_multiplier = pricing_multipliers(
            self.pricing, input_tokens)
        maximum_input_rate = max(
            self.pricing.input_usd_per_million,
            self.pricing.cached_input_usd_per_million,
            self.pricing.cache_write_input_usd_per_million,
        )
        return (
            Decimal(input_tokens) * maximum_input_rate * input_multiplier
            + Decimal(output_tokens) * self.pricing.output_usd_per_million
            * output_multiplier
        ) / MILLION

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
                or input_tokens > self.max_input_tokens_per_request):
            raise BudgetStateError("input-token reservation exceeds the per-request cap")
        if (type(output_tokens) is not int or output_tokens < 1
                or output_tokens > self.max_output_tokens_per_request):
            raise BudgetStateError("output-token reservation exceeds the per-request cap")
        active_input, active_output, active_cost = self._active_totals()
        cost = self.reservation_price(input_tokens, output_tokens)
        checks = (
            (self.requests + len(self.active) + 1 <= self.max_requests, "request"),
            (len(self.active) + 1 <= self.max_in_flight, "in-flight"),
            (self.input_tokens + active_input + input_tokens <= self.max_total_input_tokens,
             "input-token"),
            (self.output_tokens + active_output + output_tokens <= self.max_total_output_tokens,
             "output-token"),
            (self.cost_usd + active_cost + cost <= self.max_cost_usd, "dollar"),
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
        if (reservation.input_tokens > self.max_input_tokens_per_request
                or reservation.output_tokens > self.max_output_tokens_per_request
                or reservation.cost_usd != self.reservation_price(
                    reservation.input_tokens, reservation.output_tokens)):
            raise BudgetStateError("durable reservation exceeds or disagrees with its release")
        active_input, active_output, active_cost = self._active_totals()
        if (self.requests + len(self.active) + 1 > self.max_requests
                or len(self.active) + 1 > self.max_in_flight
                or self.input_tokens + active_input + reservation.input_tokens
                > self.max_total_input_tokens
                or self.output_tokens + active_output + reservation.output_tokens
                > self.max_total_output_tokens
                or self.cost_usd + active_cost + reservation.cost_usd > self.max_cost_usd):
            raise BudgetStateError("durable reservation exceeds its released ceiling")
        self.active[reservation.reservation_id] = reservation

    def preview_reconciliation(
            self, reservation_id: str, usage: Mapping[str, Any] | None,
            *, ambiguous: bool = False,
            known_not_completed: bool = False) -> dict[str, Any]:
        """Return the exact durable accounting record without mutating state.

        Callers can append this record with the provider result before applying
        ``reconcile``.  Journal replay then independently recomputes and verifies
        it, preserving append-before-mutate crash semantics.
        """
        try:
            reservation = self.active[reservation_id]
        except KeyError as exc:
            raise BudgetStateError("result has no unique active reservation") from exc
        if reservation_id in self.seen:
            raise BudgetStateError("reservation was already reconciled")
        if ambiguous and known_not_completed:
            raise BudgetStateError("an attempt cannot be both ambiguous and known not completed")
        if known_not_completed and usage is not None:
            raise BudgetStateError(
                "known-not-completed accounting requires null provider usage"
            )
        normalized = None if ambiguous or known_not_completed else Usage.from_mapping(usage)
        conservative = ambiguous or (normalized is None and not known_not_completed)
        used_input = 0 if known_not_completed else reservation.input_tokens if conservative else normalized.input_tokens
        used_output = 0 if known_not_completed else reservation.output_tokens if conservative else normalized.output_tokens
        used_cost = (Decimal(0) if known_not_completed
                     else reservation.cost_usd if conservative
                     else self.price(
                         used_input,
                         used_output,
                         cached_input_tokens=normalized.cached_input_tokens,
                         cache_write_input_tokens=normalized.cache_write_input_tokens,
                     ))
        breached = (used_input > reservation.input_tokens
                    or used_output > reservation.output_tokens
                    or self.requests + 1 > self.max_requests
                    or self.input_tokens + used_input > self.max_total_input_tokens
                    or self.output_tokens + used_output > self.max_total_output_tokens
                    or self.cost_usd + used_cost > self.max_cost_usd)
        result = {"reservation_id": reservation_id,
                  "identity": reservation.identity,
                  "attempt": reservation.attempt,
                  "accounting": ("known_not_completed" if known_not_completed
                                  else "full_reservation" if conservative else "reported_usage"),
                  "ambiguous": ambiguous,
                  "known_not_completed": known_not_completed,
                  "reservation": {
                      "input_tokens": reservation.input_tokens,
                      "output_tokens": reservation.output_tokens,
                      "cost_usd": str(reservation.cost_usd),
                  },
                  "reported_usage": None if normalized is None else normalized.to_dict(),
                  # Retain the original flat counters for backwards-compatible
                  # report consumers while adding the detailed nested record.
                  "input_tokens": used_input, "output_tokens": used_output,
                  "cost_usd": str(used_cost),
                  "reservation_breached": breached}
        return result

    def reconcile(self, reservation_id: str, usage: Mapping[str, Any] | None,
                  *, ambiguous: bool = False, known_not_completed: bool = False,
                  expected_accounting: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = self.preview_reconciliation(
            reservation_id, usage, ambiguous=ambiguous,
            known_not_completed=known_not_completed)
        if expected_accounting is not None:
            if (not isinstance(expected_accounting, Mapping)
                    or canonical(dict(expected_accounting)) != canonical(result)):
                raise BudgetStateError(
                    "durable attempt accounting disagrees with the reservation and provider usage")
        reservation = self.active.pop(reservation_id)
        self.requests += 1
        self.input_tokens += result["input_tokens"]
        self.output_tokens += result["output_tokens"]
        self.cost_usd += Decimal(result["cost_usd"])
        self.seen.add(reservation.reservation_id)
        if result["reservation_breached"]:
            raise BudgetStateError("reported usage exceeded its reservation or an approved ceiling")
        return result

    @classmethod
    def replay(cls, limits: ExecutionLimits, pricing: Pricing, approved_budget_usd: Decimal,
               records: Iterable[Mapping[str, Any]],
               release_limits: DispatchReleaseLimits | None = None) -> "BudgetLedger":
        ledger = cls(limits, pricing, approved_budget_usd, release_limits)
        reservation_events: dict[str, Mapping[str, Any]] = {}
        for record in records:
            kind = record.get("kind")
            if kind == "reservation":
                reservation = Reservation.from_mapping(record["reservation"])
                ledger.restore_reservation(reservation)
                reservation_events[reservation.reservation_id] = record
            elif kind == "result":
                reservation_id = str(record.get("reservation_id"))
                reservation_event = reservation_events.get(reservation_id)
                if (reservation_event is not None
                        and (_is_durable_journal_record(record)
                             or _is_durable_journal_record(reservation_event))):
                    _validate_durable_result_binding(record, reservation_event)
                ledger.reconcile(reservation_id, record.get("usage"),
                                 ambiguous=bool(record.get("ambiguous")),
                                 known_not_completed=bool(record.get("known_not_completed")),
                                 expected_accounting=record.get("budget_accounting"))
        return ledger

    def snapshot(self) -> dict[str, Any]:
        reserved_input, reserved_output, reserved_cost = self._active_totals()
        return {
            "pricing_policy": {
                "source": self.pricing.source,
                "long_context_threshold_input_tokens": (
                    LONG_CONTEXT_THRESHOLD_INPUT_TOKENS
                ),
                "long_context_input_and_cache_multiplier": str(
                    LONG_CONTEXT_INPUT_MULTIPLIER
                ),
                "long_context_output_multiplier": str(
                    LONG_CONTEXT_OUTPUT_MULTIPLIER
                ),
                "applies_to_this_pricing_source": (
                    self.pricing.source == LONG_CONTEXT_PRICING_SOURCE
                ),
            },
            "approved": {"requests": self.max_requests,
                         "input_tokens": self.max_total_input_tokens,
                         "output_tokens": self.max_total_output_tokens,
                         "cost_usd": str(self.max_cost_usd),
                         "max_in_flight": self.max_in_flight,
                         "input_tokens_per_request": self.max_input_tokens_per_request,
                         "output_tokens_per_request": self.max_output_tokens_per_request},
            "release": (None if self.release_limits is None
                        else self.release_limits.to_dict()),
            "consumed": {"requests": self.requests, "input_tokens": self.input_tokens,
                         "output_tokens": self.output_tokens, "cost_usd": str(self.cost_usd)},
            "reserved": {"requests": len(self.active), "input_tokens": reserved_input,
                         "output_tokens": reserved_output, "cost_usd": str(reserved_cost)},
            "remaining_after_reservations": {
                "requests": self.max_requests - self.requests - len(self.active),
                "input_tokens": self.max_total_input_tokens - self.input_tokens - reserved_input,
                "output_tokens": self.max_total_output_tokens - self.output_tokens - reserved_output,
                "cost_usd": str(self.max_cost_usd - self.cost_usd - reserved_cost),
            },
        }
