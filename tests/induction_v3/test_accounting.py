"""Offline adversarial accounting tests. No provider or credential access."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.induction_v3.accounting import (
    AccountingError, BudgetExceeded, DispatchStopped, LOCK_NAME, MODEL,
    ProgramLock, ReservationJournal, _canonical, _digest, historical_inventory,
    normalized_usage, read_events, reservation_price, usage_price,
    verify_run,
)


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.empirical = self.root / "runs" / "empirical"
        self.empirical.mkdir(parents=True)
        self.source = self.root / "engine.py"
        self.source.write_text("# frozen test engine\n")
        self.inventory = {"engine.py": hashlib.sha256(self.source.read_bytes()).hexdigest()}
        self.historical = self.empirical / "prior"
        self.historical.mkdir()
        self.prior_manifest = {
            "empirical": True, "run_id": "prior", "status": "completed",
            "ledger": {"consumed": {"cost_usd": "47.4070366", "requests": 7,
                                      "input_tokens": 100, "output_tokens": 20},
                       "reserved": {"cost_usd": "0", "requests": 0,
                                    "input_tokens": 0, "output_tokens": 0}},
        }
        self.write_prior()
        self.lock = ProgramLock(self.empirical / LOCK_NAME)
        self.lock.__enter__()
        self.addCleanup(self.lock.__exit__, None, None, None)
        self.run = self.empirical / "new-campaign"

    def write_prior(self):
        (self.historical / "manifest.json").write_text(json.dumps(self.prior_manifest))

    def journal(self, **kw):
        return ReservationJournal(
            self.run, program_lock=self.lock, source_root=self.root,
            binding={"model": MODEL, "run_id": "new-campaign", "schedule_hash": "d" * 64},
            source_inventory=self.inventory, **kw)

    def reserve(self, journal, name="a", **kw):
        args = {"input_token_bound": 1000, "max_output_tokens": 100,
                "request_hash": hashlib.sha256(name.encode()).hexdigest()}
        args.update(kw)
        return journal.reserve(name, **args)

    def settle(self, journal, reservation):
        return journal.settle(reservation.reservation_id,
                              usage={"input_tokens": 100, "output_tokens": 20,
                                     "reasoning_tokens": 10})

    def reopen(self, **kw):
        self.lock.__exit__(None, None, None)
        self.lock.__enter__()
        return self.journal(**kw)

    def rewrite_events(self, events):
        previous = "initial"
        for index, row in enumerate(events):
            row["sequence"], row["previous_event_hash"] = index, previous
            body = {key: value for key, value in row.items() if key != "event_hash"}
            row["event_hash"] = _digest(body)
            previous = row["event_hash"]
        (self.run / "journal.jsonl").write_text("".join(_canonical(row) + "\n" for row in events))

    def test_interleaved_settlements_replay_by_reservation(self):
        journal = self.journal()
        a, b, c = [self.reserve(journal, name) for name in "abc"]
        self.settle(journal, b)
        self.settle(journal, a)
        d = self.reserve(journal, "d")
        self.settle(journal, d)
        self.settle(journal, c)
        original = journal.snapshot()
        reopened = self.reopen()
        self.assertEqual(reopened.snapshot(), original)
        self.assertEqual(original["physical_attempts"], 4)
        self.assertEqual(original["reserved"]["requests"], 0)
        self.assertEqual(Decimal(original["consumed"]["cost_usd"]), Decimal("0.000196"))
        reopened.finish()
        with self.assertRaises(DispatchStopped):
            self.reserve(reopened, "after-completion")

    def test_three_concurrent_reservations_at_most(self):
        journal = self.journal()
        barrier = threading.Barrier(12)
        def reserve(index):
            barrier.wait()
            try:
                return self.reserve(journal, str(index))
            except BudgetExceeded:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(12)))
        self.assertEqual(sum(result is not None for result in results), 3)
        self.assertEqual(journal.snapshot()["in_flight"], 3)
        self.assertEqual(len(read_events(self.run / "journal.jsonl")), 4)

    def test_campaign_cap_is_atomic_under_competition(self):
        journal = self.journal(campaign_limit_usd="0.00037")
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = [pool.submit(self.reserve, journal, str(i)) for i in range(3)]
        successes = 0
        for future in results:
            try:
                future.result()
                successes += 1
            except BudgetExceeded:
                pass
        self.assertEqual(successes, 1)
        self.assertEqual(Decimal(journal.snapshot()["campaign_exposure_usd"]), Decimal("0.00037"))

    def test_lifetime_limit_includes_history(self):
        journal = self.journal(lifetime_limit_usd="47.4074")
        with self.assertRaises(BudgetExceeded):
            self.reserve(journal)
        self.assertEqual(journal.snapshot()["physical_attempts"], 0)

    def test_attempt_cap_counts_proven_uncompleted_requests(self):
        journal = self.journal(max_attempts=1)
        item = self.reserve(journal)
        with self.assertRaisesRegex(AccountingError, "evidence"):
            journal.settle(item.reservation_id, outcome="known_not_completed")
        journal.settle(item.reservation_id, outcome="known_not_completed",
                       noncompletion_evidence="offline_fixture_proves_no_dispatch")
        self.assertEqual(journal.snapshot()["consumed"]["cost_usd"], "0")
        with self.assertRaises(BudgetExceeded):
            self.reserve(journal, "b")

    def test_unknown_timeout_halts_globally_and_keeps_reservation(self):
        journal = self.journal()
        a, b = self.reserve(journal), self.reserve(journal, "b")
        journal.settle(a.reservation_id, outcome="ambiguous_timeout")
        self.settle(journal, b)  # A concurrent response can still be charged.
        with self.assertRaises(DispatchStopped):
            self.reserve(journal, "c")
        original = journal.snapshot()
        self.assertEqual(original["reserved"]["cost_usd"], str(a.cost_usd))
        self.assertEqual(original["reserved"]["requests"], 1)
        self.assertEqual(self.reopen().snapshot(), original)

    def test_unknown_missing_usage_remains_reserved(self):
        journal = self.journal()
        item = self.reserve(journal)
        journal.settle(item.reservation_id, usage=None)
        self.assertTrue(journal.stopped)
        self.assertEqual(journal.stop_reason, "missing_or_invalid_provider_usage")
        self.assertEqual(journal.snapshot()["reserved"]["cost_usd"], str(item.cost_usd))
        with self.assertRaises(AccountingError):
            journal.finish()

    def test_restart_with_open_attempt_is_not_a_retry(self):
        journal = self.journal()
        self.reserve(journal)
        reopened = self.reopen()
        self.assertEqual(reopened.stop_reason, "unsettled_reservations_on_restart")
        self.assertEqual(reopened.snapshot()["physical_attempts"], 1)
        self.assertEqual(reopened.snapshot()["reserved"]["requests"], 1)
        with self.assertRaises(DispatchStopped):
            self.reserve(reopened, "b")

    def test_manifest_lag_rebuilt_from_durable_journal(self):
        journal = self.journal()
        initial_manifest = (self.run / "manifest.json").read_bytes()
        item = self.reserve(journal)
        self.settle(journal, item)
        (self.run / "manifest.json").write_bytes(initial_manifest)
        reopened = self.reopen()
        manifest = json.loads((self.run / "manifest.json").read_text())
        self.assertEqual(manifest["ledger"], reopened.snapshot())
        self.assertEqual(manifest["event_count"], 3)

    def test_duplicate_reservation_identity_and_settlement_rejected(self):
        journal = self.journal()
        item = self.reserve(journal)
        self.settle(journal, item)
        with self.assertRaises(AccountingError):
            self.reserve(journal)
        with self.assertRaises(AccountingError):
            self.settle(journal, item)

    def test_freeze_detects_source_and_history_drift(self):
        journal = self.journal()
        self.source.write_text("# changed\n")
        with self.assertRaisesRegex(AccountingError, "source drift"):
            self.reserve(journal)
        self.source.write_text("# frozen test engine\n")
        self.prior_manifest["status"] = "stopped"
        self.write_prior()
        with self.assertRaisesRegex(AccountingError, "inventory drifted"):
            self.reserve(journal)
        self.assertEqual(journal.snapshot()["physical_attempts"], 0)

    def test_binding_source_schema_and_price_tampering_rejected_on_replay(self):
        for mutation in ("binding", "price", "schema", "settlement_id"):
            with self.subTest(mutation=mutation):
                self.run = self.empirical / ("new-" + mutation)
                journal = self.journal()
                item = self.reserve(journal)
                self.settle(journal, item)
                events = journal.events
                if mutation == "binding":
                    events[0]["binding"]["campaign"]["schedule_hash"] = "e" * 64
                elif mutation == "price":
                    events[1]["reservation"]["cost_usd"] = "0"
                elif mutation == "schema":
                    events[1]["extra"] = True
                else:
                    events[2]["reservation_id"] = "0" * 32
                self.rewrite_events(events)
                self.lock.__exit__(None, None, None)
                self.lock.__enter__()
                with self.assertRaises(AccountingError):
                    self.journal()
                # Keep the next test's historical inventory valid and closed.
                manifest = json.loads((self.run / "manifest.json").read_text())
                manifest["status"] = "stopped"
                (self.run / "manifest.json").write_text(json.dumps(manifest))

    def test_truncated_journal_and_bad_hash_fail_closed(self):
        journal = self.journal()
        item = self.reserve(journal)
        path = self.run / "journal.jsonl"
        raw = path.read_bytes()
        path.write_bytes(raw[:-3])
        with self.assertRaises(AccountingError):
            self.reopen()
        path.write_bytes(raw.replace(item.reservation_id.encode(), b"0" * 32))
        with self.assertRaises(AccountingError):
            self.journal()

    def test_usage_over_reservation_is_charged_and_stopped(self):
        journal = self.journal()
        item = self.reserve(journal)
        journal.settle(item.reservation_id, usage={"input_tokens": 500000, "output_tokens": 300})
        self.assertEqual(journal.stop_reason, "provider_usage_exceeds_reserved_bound")
        self.assertGreater(Decimal(journal.snapshot()["consumed"]["cost_usd"]), item.cost_usd)
        self.assertEqual(journal.snapshot()["reserved"]["requests"], 0)
        self.assertTrue(self.reopen().stopped)

    def test_price_cache_write_reasoning_and_threshold(self):
        self.assertEqual(reservation_price(1000000, 1000000), Decimal("2.3"))
        self.assertEqual(usage_price({"input_tokens": 1000, "output_tokens": 100,
                                     "cached_input_tokens": 100, "cache_write_input_tokens": 200,
                                     "reasoning_tokens": 80}), Decimal("0.000312"))
        self.assertEqual(reservation_price(272000, 100), Decimal("0.06812"))
        self.assertEqual(reservation_price(272001, 100), Decimal("0.1361805"))
        for bad in ({"input_tokens": True, "output_tokens": 0},
                    {"input_tokens": 10, "output_tokens": 3, "reasoning_tokens": 4},
                    {"input_tokens": 10, "output_tokens": 3, "total_tokens": 99},
                    {"input_tokens": 10, "output_tokens": 3, "cached_input_tokens": 11}):
            with self.assertRaises(AccountingError):
                normalized_usage(bad)

    def test_history_reconciliation_rejects_ambiguity_and_schema(self):
        rows = historical_inventory(self.lock)
        self.assertEqual(rows[0]["consumed_cost_usd"], "47.4070366")
        self.prior_manifest["ledger"]["reserved"]["cost_usd"] = "0.10"
        self.write_prior()
        with self.assertRaisesRegex(AccountingError, "outstanding"):
            self.journal()
        self.prior_manifest["ledger"]["reserved"]["cost_usd"] = "0"
        self.prior_manifest["budget"] = {}
        self.write_prior()
        with self.assertRaisesRegex(AccountingError, "conflicting"):
            self.journal()

    def test_missing_cache_write_details_use_conservative_cost_and_provenance(self):
        usage = {"input_tokens": 1000, "cached_input_tokens": 300, "output_tokens": 100}
        normalized = normalized_usage(usage)
        self.assertEqual(normalized["cache_write_input_tokens"], 700)
        self.assertEqual(normalized["cache_write_accounting"], "assumed_all_non_cached_write")
        self.assertEqual(normalized_usage(normalized), normalized)
        self.assertEqual(usage_price(usage), Decimal("0.000301"))
        self.assertEqual(usage_price({**usage, "cache_write_input_tokens": 0}), Decimal("0.000266"))
        journal = self.journal()
        item = self.reserve(journal)
        journal.settle(item.reservation_id, usage=usage)
        self.assertEqual(self.reopen().snapshot()["consumed"]["cost_usd"], "0.000301")

    def test_read_only_verifier_replays_without_mutating_and_reports_open_calls(self):
        journal = self.journal()
        item = self.reserve(journal)
        before = {path: path.read_bytes() for path in self.run.iterdir()}
        report = verify_run(self.run, source_root=self.root, program_lock=self.lock)
        self.assertTrue(report["journal_valid"])
        self.assertTrue(report["requires_restart_stop"])
        self.assertEqual(before, {path: path.read_bytes() for path in self.run.iterdir()})
        self.settle(journal, item)
        journal.finish()
        report = verify_run(self.run, source_root=self.root)
        self.assertEqual(report["ledger"], journal.snapshot())
        manifest = json.loads((self.run / "manifest.json").read_text())
        manifest["paid_cost_usd"] = "0"
        (self.run / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(AccountingError, "exactly reconcile"):
            verify_run(self.run)

    def test_durability_before_dispatch_and_poison_on_manifest_failure(self):
        journal = self.journal()
        with patch("experiments.induction_v3.accounting.os.fsync", wraps=__import__("os").fsync) as fsync:
            self.reserve(journal)
        self.assertGreaterEqual(fsync.call_count, 3)  # Journal, manifest, parent directory.
        with patch("experiments.induction_v3.accounting._atomic_json", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.reserve(journal, "b")
        with self.assertRaises(DispatchStopped):
            self.reserve(journal, "c")
        self.assertTrue(self.reopen().stopped)

    def test_lock_required_unique_and_caps_cannot_expand(self):
        with self.assertRaises(BlockingIOError):
            with ProgramLock(self.empirical / LOCK_NAME):
                pass
        for kw in ({"campaign_limit_usd": "101"}, {"lifetime_limit_usd": "251"},
                   {"max_attempts": 4001}, {"max_in_flight": 4}):
            with self.assertRaises(AccountingError):
                self.journal(**kw)
        journal = self.journal()
        with self.assertRaises(AccountingError):
            self.journal()
        self.lock.__exit__(None, None, None)
        with self.assertRaises(AccountingError):
            self.reserve(journal)
        self.lock.__enter__()
        with self.assertRaisesRegex(AccountingError, "no longer owns"):
            self.reserve(journal)

    def make_transport(self, journal, raw=None, failure=None):
        from experiments.induction_v3.transport import OfficialTransport
        if raw is None:
            raw = {"model": MODEL, "status": "completed",
                   "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                   "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                             "output_tokens_details": {"reasoning_tokens": 10}}}
        calls = []
        def post(*args, **kwargs):
            calls.append((args, kwargs))
            if failure:
                raise failure
            return SimpleNamespace(status_code=200, headers={"x-request-id": "offline-response-id"}, json=lambda: raw)
        # Dependency injection prevents a real HTTP client and keeps this test
        # runnable without installing HTTPX into the repository's main venv.
        with patch.dict("sys.modules", {"httpx": SimpleNamespace()}):
            transport = OfficialTransport(journal, self.run, "offline-test-key-no-authority",
                                          http_client=SimpleNamespace(post=post))
        transport.context(recipe="offline-test", dyad=0)
        return transport, calls

    def complete_transport(self, transport):
        return transport.complete(model=MODEL, messages=[{"role": "user", "content": "offline"}],
                                  completion_kwargs={"reasoning_effort": "high"}, temperature=1.0,
                                  max_output_tokens=64000, accept_tool_calls=False)

    def test_transport_integration_missing_write_detail_charges_conservatively(self):
        journal = self.journal()
        transport, calls = self.make_transport(journal)
        self.assertEqual(self.complete_transport(transport)[2], "ok")
        self.assertEqual(len(calls), 1)
        self.assertEqual(journal.snapshot()["consumed"]["cost_usd"], "0.000049")
        for path in self.run.rglob("*.json*"):
            self.assertNotIn("offline-test-key-no-authority", path.read_text())

    def test_transport_integration_invalid_usage_never_returns_accepted(self):
        journal = self.journal()
        transport, calls = self.make_transport(journal, raw={"model": MODEL, "status": "completed", "usage": None})
        with self.assertRaisesRegex(RuntimeError, "usage_unresolved"):
            self.complete_transport(transport)
        with self.assertRaises(RuntimeError):
            self.complete_transport(transport)
        self.assertEqual(len(calls), 1)
        self.assertEqual(journal.snapshot()["reserved"]["requests"], 1)
        self.assertEqual(journal.snapshot()["consumed"]["cost_usd"], "0")

    def test_transport_integration_malformed_usage_postdispatch_stops(self):
        journal = self.journal()
        raw = {"model": MODEL, "status": "completed",
               "usage": {"input_tokens": 100, "output_tokens": 20,
                         "input_tokens_details": ["invalid-object"]}}
        transport, calls = self.make_transport(journal, raw=raw)
        with self.assertRaises(Exception):
            self.complete_transport(transport)
        self.assertEqual(len(calls), 1)
        self.assertTrue(journal.stopped)
        self.assertEqual(journal.snapshot()["reserved"]["requests"], 1)
        self.assertEqual(journal.snapshot()["in_flight"], 0)

    def test_transport_integration_response_artifact_failure_keeps_charge(self):
        from experiments.induction_v3.common import atomic_json
        journal = self.journal()
        transport, calls = self.make_transport(journal)
        def fail_response(path, value):
            if Path(path).parent.name == "responses":
                raise OSError("offline artifact write fault")
            return atomic_json(path, value)
        with patch("experiments.induction_v3.transport.atomic_json", side_effect=fail_response):
            with self.assertRaises(OSError):
                self.complete_transport(transport)
        self.assertEqual(len(calls), 1)
        self.assertTrue(journal.stopped)
        self.assertEqual(journal.snapshot()["reserved"]["requests"], 1)
        self.assertEqual(journal.snapshot()["in_flight"], 0)


if __name__ == "__main__":
    unittest.main()
