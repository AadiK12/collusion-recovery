import copy
from dataclasses import FrozenInstanceError
from decimal import Decimal
import unittest

from collusion_recovery.budget import (
    BudgetExhausted,
    BudgetLedger,
    BudgetStateError,
    DispatchReleaseLimits,
)
from collusion_recovery.live_config import ExecutionLimits, Pricing


def global_limits():
    return ExecutionLimits.from_mapping({
        "max_requests": 2_000,
        "max_total_input_tokens": 2_000_000,
        "max_total_output_tokens": 500_000,
        "max_input_tokens_per_request": 500_000,
        "max_output_tokens_per_request": 100_000,
        "max_in_flight": 8,
    })


def list_pricing():
    return Pricing.from_mapping({
        "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        "verified_at": "2026-10-23T15:45:09-07:00",
        "input_usd_per_million": "2",
        "cached_input_usd_per_million": "0.10",
        "cache_write_input_usd_per_million": "2.50",
        "output_usd_per_million": "10",
    })


def stage2_release():
    return DispatchReleaseLimits.from_mapping({
        "max_dispatch_attempts": 67,
        "max_total_input_tokens": 211_932,
        "max_total_output_tokens": 94_208,
        "max_cost_usd": "1.78",
        "max_in_flight": 1,
        "max_input_tokens_per_request": 9_353,
        "max_output_tokens_per_request": 4_096,
    })


class DispatchReleaseLimitsTests(unittest.TestCase):
    def test_exact_authorized_release_is_strict_and_immutable(self):
        release = stage2_release()
        self.assertEqual(release.to_dict(), {
            "max_dispatch_attempts": 67,
            "max_total_input_tokens": 211_932,
            "max_total_output_tokens": 94_208,
            "max_cost_usd": "1.78",
            "max_in_flight": 1,
            "max_input_tokens_per_request": 9_353,
            "max_output_tokens_per_request": 4_096,
        })
        with self.assertRaises(FrozenInstanceError):
            release.max_dispatch_attempts = 68

        valid = release.to_dict()
        invalid = [
            {key: value for key, value in valid.items() if key != "max_cost_usd"},
            {**valid, "unexpected": 1},
            {**valid, "max_dispatch_attempts": True},
            {**valid, "max_total_input_tokens": 0},
            {**valid, "max_cost_usd": "NaN"},
            {**valid, "max_in_flight": 68},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(BudgetStateError):
                DispatchReleaseLimits.from_mapping(value)

    def test_release_narrows_but_does_not_rewrite_global_limits(self):
        limits = global_limits()
        release = stage2_release()
        ledger = BudgetLedger(limits, list_pricing(), Decimal("20"), release)

        self.assertEqual(limits.max_requests, 2_000)
        self.assertEqual(ledger.snapshot()["approved"], {
            "requests": 67,
            "input_tokens": 211_932,
            "output_tokens": 94_208,
            "cost_usd": "1.78",
            "max_in_flight": 1,
            "input_tokens_per_request": 9_353,
            "output_tokens_per_request": 4_096,
        })
        self.assertEqual(ledger.snapshot()["release"], release.to_dict())

        first = ledger.reserve("first", 0, input_tokens=1, output_tokens=1)
        with self.assertRaisesRegex(BudgetExhausted, "in-flight"):
            ledger.reserve("concurrent", 0, input_tokens=1, output_tokens=1)
        ledger.reconcile(first.reservation_id, None, known_not_completed=True)

        for index in range(1, 67):
            reservation = ledger.reserve(
                f"attempt-{index}", 0, input_tokens=1, output_tokens=1)
            ledger.reconcile(
                reservation.reservation_id, None, known_not_completed=True)
        with self.assertRaisesRegex(BudgetExhausted, "request"):
            ledger.reserve("attempt-68", 0, input_tokens=1, output_tokens=1)

    def test_release_enforces_token_and_dollar_caps(self):
        release = stage2_release()

        input_ledger = BudgetLedger(
            global_limits(), list_pricing(), Decimal("20"), release)
        for index in range(22):
            amount = 9_353 if index < 21 else 15_519
            if amount > 9_353:
                amount = 9_353
            reservation = input_ledger.reserve(
                f"input-{index}", 0, input_tokens=amount, output_tokens=1)
            input_ledger.reconcile(
                reservation.reservation_id,
                {"input_tokens": amount, "output_tokens": 1,
                 "reasoning_tokens": 0, "total_tokens": amount + 1},
            )
        remaining = 211_932 - input_ledger.input_tokens
        reservation = input_ledger.reserve(
            "input-final", 0, input_tokens=remaining, output_tokens=1)
        input_ledger.reconcile(
            reservation.reservation_id,
            {"input_tokens": remaining, "output_tokens": 1,
             "reasoning_tokens": 0, "total_tokens": remaining + 1},
        )
        with self.assertRaisesRegex(BudgetExhausted, "input-token"):
            input_ledger.reserve("one-more-input", 0, input_tokens=1, output_tokens=1)

        output_ledger = BudgetLedger(
            global_limits(), list_pricing(), Decimal("20"), release)
        for index in range(23):
            amount = 4_096
            reservation = output_ledger.reserve(
                f"output-{index}", 0, input_tokens=1, output_tokens=amount)
            output_ledger.reconcile(
                reservation.reservation_id,
                {"input_tokens": 1, "output_tokens": amount,
                 "reasoning_tokens": amount, "total_tokens": amount + 1},
            )
        with self.assertRaisesRegex(BudgetExhausted, "output-token"):
            output_ledger.reserve("one-more-output", 0, input_tokens=1, output_tokens=1)

        expensive = Pricing.from_mapping({
            "source": "test://one-dollar-per-token",
            "verified_at": "2026-10-23T15:45:09-07:00",
            "input_usd_per_million": "1000000",
            "cached_input_usd_per_million": "1000000",
            "cache_write_input_usd_per_million": "1000000",
            "output_usd_per_million": "1000000",
        })
        dollar_ledger = BudgetLedger(
            global_limits(), expensive, Decimal("20"), release)
        with self.assertRaisesRegex(BudgetExhausted, "dollar"):
            dollar_ledger.reserve("two-dollars", 0, input_tokens=1, output_tokens=1)


class LongContextPricingTests(unittest.TestCase):
    def setUp(self):
        self.ledger = BudgetLedger(
            global_limits(), list_pricing(), Decimal("20"))

    def test_threshold_is_strict_and_reservation_uses_long_context_rates(self):
        self.assertEqual(
            self.ledger.reservation_price(272_000, 1_000),
            Decimal("0.690"),
        )
        self.assertEqual(
            self.ledger.reservation_price(272_001, 1_000),
            Decimal("1.375005"),
        )
        reservation = self.ledger.reserve(
            "long-context", 0, input_tokens=300_001, output_tokens=1_000)
        self.assertEqual(reservation.cost_usd, Decimal("1.515005"))

    def test_reported_usage_prices_every_input_category_and_output_multiplier(self):
        self.assertEqual(
            self.ledger.price(
                300_001,
                1_000,
                cached_input_tokens=100_000,
                cache_write_input_tokens=100_000,
            ),
            Decimal("0.935004"),
        )
        self.assertEqual(
            self.ledger.price(272_000, 1_000),
            Decimal("0.554"),
        )
        self.assertEqual(
            self.ledger.price(272_001, 1_000),
            Decimal("1.103004"),
        )

    def test_reconciliation_uses_actual_context_band_and_releases_reservation(self):
        reservation = self.ledger.reserve(
            "actual-long-context", 0,
            input_tokens=300_001, output_tokens=1_000,
        )
        accounting = self.ledger.reconcile(
            reservation.reservation_id,
            {
                "input_tokens": 280_000,
                "output_tokens": 1_000,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "reasoning_tokens": 100,
                "total_tokens": 281_000,
            },
        )
        self.assertEqual(accounting["reservation"]["cost_usd"], "1.515005")
        self.assertEqual(accounting["cost_usd"], "1.135")
        self.assertEqual(
            self.ledger.snapshot()["pricing_policy"],
            {
                "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
                "long_context_threshold_input_tokens": 272_000,
                "long_context_input_and_cache_multiplier": "2",
                "long_context_output_multiplier": "1.5",
                "applies_to_this_pricing_source": True,
            },
        )

    def test_unrelated_pricing_source_does_not_inherit_model_specific_multiplier(self):
        unrelated = Pricing.from_mapping({
            **list_pricing().to_dict(),
            "source": "test://unrelated-model",
        })
        ledger = BudgetLedger(global_limits(), unrelated, Decimal("20"))
        self.assertEqual(
            ledger.price(300_001, 1_000),
            Decimal("0.610002"),
        )


class AttemptAccountingTests(unittest.TestCase):
    def setUp(self):
        self.ledger = BudgetLedger(
            global_limits(), list_pricing(), Decimal("20"), stage2_release())

    def test_preview_is_nonmutating_and_output_already_includes_reasoning(self):
        reservation = self.ledger.reserve(
            "reported", 0, input_tokens=100, output_tokens=50)
        usage = {
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "reasoning_tokens": 15,
            "total_tokens": 30,
        }

        accounting = self.ledger.preview_reconciliation(
            reservation.reservation_id, usage)
        self.assertEqual(self.ledger.snapshot()["consumed"]["requests"], 0)
        self.assertEqual(self.ledger.snapshot()["reserved"]["requests"], 1)
        self.assertEqual(accounting["accounting"], "reported_usage")
        self.assertEqual(accounting["reported_usage"], usage)
        # 10 input * $2/M + 20 total output * $10/M.  The 15 reasoning
        # tokens are a subset of output_tokens and are not charged twice.
        self.assertEqual(accounting["cost_usd"], "0.00022")
        self.assertEqual(accounting["reservation"], {
            "input_tokens": 100,
            "output_tokens": 50,
            # Cache writes are the most expensive possible input category, so
            # the pre-dispatch reservation uses $2.50/M for every input token.
            "cost_usd": "0.00075",
        })

        reconciled = self.ledger.reconcile(
            reservation.reservation_id, usage,
            expected_accounting=accounting)
        self.assertEqual(reconciled, accounting)
        self.assertEqual(self.ledger.snapshot()["consumed"], {
            "requests": 1,
            "input_tokens": 10,
            "output_tokens": 20,
            "cost_usd": "0.00022",
        })

    def test_reasoning_must_be_included_in_normalized_output(self):
        reservation = self.ledger.reserve(
            "bad-usage", 0, input_tokens=100, output_tokens=50)
        with self.assertRaisesRegex(BudgetStateError, "include all reasoning"):
            self.ledger.preview_reconciliation(
                reservation.reservation_id,
                {"input_tokens": 10, "output_tokens": 5,
                 "reasoning_tokens": 6, "total_tokens": 15},
            )

    def test_known_not_completed_is_zero_and_ambiguous_is_full(self):
        retry = self.ledger.reserve(
            "retry", 0, input_tokens=100, output_tokens=50)
        released = self.ledger.reconcile(
            retry.reservation_id, None, known_not_completed=True)
        self.assertEqual(released["accounting"], "known_not_completed")
        self.assertEqual(
            (released["input_tokens"], released["output_tokens"], released["cost_usd"]),
            (0, 0, "0"),
        )

        unknown = self.ledger.reserve(
            "unknown", 0, input_tokens=100, output_tokens=50)
        consumed = self.ledger.reconcile(
            unknown.reservation_id, None, ambiguous=True)
        self.assertEqual(consumed["accounting"], "full_reservation")
        self.assertTrue(consumed["ambiguous"])
        self.assertEqual(
            (consumed["input_tokens"], consumed["output_tokens"], consumed["cost_usd"]),
            (100, 50, "0.00075"),
        )

    def test_reported_usage_prices_ordinary_cached_and_cache_write_input_separately(self):
        reservation = self.ledger.reserve(
            "cache-categories", 0, input_tokens=100, output_tokens=50)
        usage = {
            "input_tokens": 10,
            "output_tokens": 1,
            "cached_input_tokens": 2,
            "cache_write_input_tokens": 3,
            "reasoning_tokens": 1,
            "total_tokens": 11,
        }

        accounting = self.ledger.preview_reconciliation(
            reservation.reservation_id, usage)

        self.assertEqual(accounting["reported_usage"], usage)
        # Five ordinary input tokens at $2/M, two cached at $0.10/M,
        # three cache writes at $2.50/M, and one output token at $10/M.
        self.assertEqual(accounting["cost_usd"], "0.0000277")
        self.assertEqual(accounting["reservation"]["cost_usd"], "0.00075")

    def test_replay_verifies_persisted_attempt_accounting(self):
        reservation = self.ledger.reserve(
            "durable", 0, input_tokens=100, output_tokens=50)
        usage = {
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "reasoning_tokens": 15,
            "total_tokens": 30,
        }
        accounting = self.ledger.preview_reconciliation(
            reservation.reservation_id, usage)
        records = [
            {"kind": "reservation", "reservation": reservation.to_dict()},
            {
                "kind": "result",
                "reservation_id": reservation.reservation_id,
                "usage": usage,
                "ambiguous": False,
                "known_not_completed": False,
                "budget_accounting": accounting,
            },
        ]

        replayed = BudgetLedger.replay(
            global_limits(), list_pricing(), Decimal("20"), records,
            stage2_release())
        self.assertEqual(replayed.snapshot()["consumed"]["cost_usd"], "0.00022")

        tampered = copy.deepcopy(records)
        tampered[1]["budget_accounting"]["cost_usd"] = "0"
        with self.assertRaisesRegex(BudgetStateError, "durable attempt accounting"):
            BudgetLedger.replay(
                global_limits(), list_pricing(), Decimal("20"), tampered,
                stage2_release())

    def test_durable_replay_binds_result_to_reservation_and_provider_record(self):
        reservation = self.ledger.reserve(
            "durable-provider-binding", 0, input_tokens=100, output_tokens=50)
        usage = {
            "input_tokens": 10,
            "output_tokens": 20,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "reasoning_tokens": 15,
            "total_tokens": 30,
        }
        accounting = self.ledger.preview_reconciliation(
            reservation.reservation_id, usage)
        reservation_event = {
            "sequence": 0,
            "event_hash": "a" * 64,
            "kind": "reservation",
            "identity": reservation.identity,
            "attempt": reservation.attempt,
            "request_hash": "b" * 64,
            "wire_payload_hash": "c" * 64,
            "reservation": reservation.to_dict(),
        }
        provider_result = {
            "status": "ok",
            "ambiguous": False,
            "known_not_completed": False,
            "usage": usage,
        }
        result_event = {
            "sequence": 1,
            "event_hash": "d" * 64,
            "kind": "result",
            "identity": reservation.identity,
            "attempt": reservation.attempt,
            "reservation_id": reservation.reservation_id,
            "request_hash": reservation_event["request_hash"],
            "wire_payload_hash": reservation_event["wire_payload_hash"],
            "status": "ok",
            "ambiguous": False,
            "known_not_completed": False,
            "usage": usage,
            "budget_accounting": accounting,
            "provider_result": provider_result,
        }
        records = [reservation_event, result_event]

        replayed = BudgetLedger.replay(
            global_limits(), list_pricing(), Decimal("20"), records,
            stage2_release())
        self.assertEqual(replayed.requests, 1)

        mutations = (
            ("identity", "other", "identity"),
            ("attempt", 1, "attempt"),
            ("request_hash", "e" * 64, "request_hash"),
            ("wire_payload_hash", "f" * 64, "wire_payload_hash"),
            ("status", "refusal", "status"),
            ("ambiguous", "false", "ambiguous"),
        )
        for field, value, error in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(records)
                changed[1][field] = value
                with self.assertRaisesRegex(BudgetStateError, error):
                    BudgetLedger.replay(
                        global_limits(), list_pricing(), Decimal("20"), changed,
                        stage2_release())

        changed = copy.deepcopy(records)
        changed[1]["usage"]["input_tokens"] = 9
        with self.assertRaisesRegex(BudgetStateError, "usage"):
            BudgetLedger.replay(
                global_limits(), list_pricing(), Decimal("20"), changed,
                stage2_release())

        changed = copy.deepcopy(records)
        changed[1].pop("provider_result")
        with self.assertRaisesRegex(BudgetStateError, "provider result object"):
            BudgetLedger.replay(
                global_limits(), list_pricing(), Decimal("20"), changed,
                stage2_release())

    def test_durable_replay_rejects_any_known_not_completed_usage_object(self):
        reservation = self.ledger.reserve(
            "certainty-binding", 0, input_tokens=100, output_tokens=50)
        usage = {"input_tokens": "not-an-integer", "output_tokens": -1}
        accounting = self.ledger.preview_reconciliation(
            reservation.reservation_id, None, known_not_completed=True)
        reservation_event = {
            "sequence": 0,
            "event_hash": "a" * 64,
            "kind": "reservation",
            "identity": reservation.identity,
            "attempt": reservation.attempt,
            "request_hash": "b" * 64,
            "wire_payload_hash": "c" * 64,
            "reservation": reservation.to_dict(),
        }
        result_event = {
            "sequence": 1,
            "event_hash": "d" * 64,
            "kind": "result",
            "identity": reservation.identity,
            "attempt": reservation.attempt,
            "reservation_id": reservation.reservation_id,
            "request_hash": reservation_event["request_hash"],
            "wire_payload_hash": reservation_event["wire_payload_hash"],
            "status": "retryable_error",
            "ambiguous": False,
            "known_not_completed": True,
            "usage": usage,
            "budget_accounting": accounting,
            "provider_result": {
                "status": "retryable_error",
                "ambiguous": False,
                "known_not_completed": True,
                "usage": usage,
            },
        }
        with self.assertRaisesRegex(BudgetStateError, "consumed usage"):
            BudgetLedger.replay(
                global_limits(), list_pricing(), Decimal("20"),
                [reservation_event, result_event], stage2_release())

        with self.assertRaisesRegex(BudgetStateError, "requires null"):
            self.ledger.preview_reconciliation(
                reservation.reservation_id,
                {"input_tokens": 0, "output_tokens": 0},
                known_not_completed=True,
            )


if __name__ == "__main__":
    unittest.main()
