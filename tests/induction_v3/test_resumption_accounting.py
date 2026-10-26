"""Offline integration: new liability ledger plus pre-reservation pacing.

All HTTP is httpx.MockTransport, all keys are synthetic, and every filesystem
write/lock is confined to the copied temporary fixture used by accounting tests.
"""
from decimal import Decimal
import json
import unittest

import httpx

from experiments.induction_v3 import accounting as base
from experiments.induction_v3 import liability_accounting as liability
from experiments.induction_v3.pacing import PacedOfficialTransport, PacingStopped, SlidingWindowPacer
from experiments.induction_v3.transport import DispatchStopped as TransportStopped
from tests.induction_v3 import test_liability_accounting as fixtures


class Clock:
    def __init__(self): self.now = 0; self.hook = None
    def __call__(self): return self.now
    def sleep(self, seconds):
        self.now += seconds
        if self.hook: self.hook()


def reply():
    return {"model": base.MODEL, "status": "completed", "output": [
        {"type": "reasoning", "id": "offline-reasoning", "summary": [], "encrypted_content": "synthetic-opaque-value"},
        {"type": "message", "id": "offline-message", "role": "assistant", "content": [{"type": "output_text", "text": "offline answer"}]}],
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 10}}}


class ResumptionAccountingIntegrationTests(unittest.TestCase):
    setUp = fixtures.LiabilityAccountingTests.setUp
    journal = fixtures.LiabilityAccountingTests.journal
    assert_old_unchanged = fixtures.LiabilityAccountingTests.assert_old_unchanged

    def transport(self, journal, handler, **pacing):
        self.http_calls = 0
        def counted(request):
            self.http_calls += 1
            return handler(request)
        client = httpx.Client(transport=httpx.MockTransport(counted))
        self.addCleanup(client.close)
        self.clock = Clock()
        pacer = SlidingWindowPacer(estimator=pacing.pop("estimator", lambda body: 100),
            initial_delay_seconds=pacing.pop("initial_delay_seconds", 0),
            clock=self.clock, sleep=self.clock.sleep, **pacing)
        value = PacedOfficialTransport(journal, self.run, "offline-only-synthetic-key", http_client=client, pacer=pacer)
        value.context(dyad_id="development-warmup_raw-1", recipe_id="warmup_raw", campaign_phase="main", episode=5,
            resume_run_id=self.run.name, source_failed_reservation_id=liability.liability_anchor()["reservation_id"])
        return value

    def complete(self, transport):
        return transport.complete(model=base.MODEL, messages=[{"role": "user", "content": "offline fixture"}],
            completion_kwargs={"reasoning_effort": "high"}, temperature=1.0, max_output_tokens=64000, accept_tool_calls=True)

    def test_pre_reservation_rejection_creates_no_attempt_or_new_liability(self):
        journal = self.journal()
        transport = self.transport(journal, lambda request: self.fail("unexpected HTTP"), capacity=99)
        with self.assertRaisesRegex(PacingStopped, "queuedbeforephysicalattempt:request_exceeds_window_capacity"):
            self.complete(transport)
        snapshot = journal.snapshot()
        self.assertEqual(snapshot["physical_attempts"], 0)
        self.assertEqual(snapshot["combined_v3_physical_attempts"], 468)
        self.assertEqual(snapshot["combined_v3_reserved_cost_usd"], "0.6402")
        self.assertEqual(self.http_calls, 0)
        self.assert_old_unchanged()

    def test_stop_while_queued_creates_no_attempt(self):
        journal = self.journal()
        transport = self.transport(journal, lambda request: self.fail("unexpected HTTP"), initial_delay_seconds=60)
        self.clock.hook = lambda: journal.stop("offline-stop-while-waiting")
        with self.assertRaisesRegex(PacingStopped, "queuedbeforephysicalattempt:journal_stopped"):
            self.complete(transport)
        self.assertEqual(self.http_calls, 0)
        self.assertEqual(journal.snapshot()["physical_attempts"], 0)
        self.assertEqual(journal.snapshot()["combined_v3_exposure_usd"], "1.73976276")

    def test_successful_new_physical_call_settles_only_its_own_usage(self):
        journal = self.journal()
        transport = self.transport(journal, lambda request: httpx.Response(200, json=reply()))
        self.complete(transport)
        snapshot = journal.snapshot()
        self.assertEqual(self.http_calls, 1)
        self.assertEqual(snapshot["physical_attempts"], 1)
        self.assertEqual(snapshot["consumed"]["cost_usd"], "0.000049")
        self.assertEqual(snapshot["reserved"]["cost_usd"], "0")
        self.assertEqual(snapshot["combined_v3_reserved_cost_usd"], "0.6402")
        self.assertEqual(snapshot["combined_v3_exposure_usd"], "1.73981176")
        event = next(e for e in journal.events if e["kind"] == "reservation")
        self.assertEqual(json.loads(event["reservation"]["identity"])["resume_run_id"], self.run.name)
        self.assertTrue(liability.verify_run(self.run, source_root=self.root)["manifest_consistent"])
        self.assert_old_unchanged()

    def test_new_429_retains_two_distinct_liabilities_and_no_retry(self):
        journal = self.journal()
        transport = self.transport(journal, lambda request: httpx.Response(429, json={"error": {
            "code": "rate_limit_exceeded", "type": "tokens", "message": "offline fixture"}}))
        with self.assertRaisesRegex(TransportStopped, "official_endpoint_rejected_request:rate_limit_exceeded"):
            self.complete(transport)
        with self.assertRaises(base.DispatchStopped): self.complete(transport)
        snapshot = journal.snapshot()
        self.assertEqual(self.http_calls, 1)
        self.assertEqual(snapshot["physical_attempts"], 1)
        self.assertEqual(snapshot["reserved"]["cost_usd"], "0.6402")
        self.assertEqual(snapshot["historical_reserved_cost_usd"], "0.6402")
        self.assertEqual(snapshot["combined_v3_reserved_cost_usd"], "1.2804")
        self.assertEqual(snapshot["consumed"]["cost_usd"], "0")
        self.assertEqual([e["outcome"] for e in journal.events if e["kind"] == "settlement"], ["unknown"])
        self.assertTrue(liability.verify_run(self.run, source_root=self.root)["manifest_consistent"])
        self.assert_old_unchanged()

    def test_new_timeout_is_distinct_from_429_and_retains_reserve(self):
        journal = self.journal()
        def timeout(request): raise httpx.ReadTimeout("offline fixture", request=request)
        transport = self.transport(journal, timeout)
        with self.assertRaisesRegex(TransportStopped, "ambiguous_timeout"):
            self.complete(transport)
        self.assertEqual(self.http_calls, 1)
        self.assertEqual([e["outcome"] for e in journal.events if e["kind"] == "settlement"], ["ambiguous_timeout"])
        self.assertEqual(journal.snapshot()["combined_v3_reserved_cost_usd"], "1.2804")
        self.assertTrue(journal.stopped)

    def test_observer_failure_keeps_completed_usage_and_stops_before_another_call(self):
        class BrokenObserver:
            def __call__(self, body): return 100
            def observe(self, raw): raise ValueError("offline-estimator-failure")
        journal = self.journal()
        transport = self.transport(journal, lambda request: httpx.Response(200, json=reply()), estimator=BrokenObserver())
        with self.assertRaisesRegex(ValueError, "offline-estimator-failure"): self.complete(transport)
        with self.assertRaises(base.DispatchStopped): self.complete(transport)
        self.assertEqual(self.http_calls, 1)
        self.assertEqual(journal.stop_reason, "pacing_replay_observation_failed")
        self.assertEqual(journal.snapshot()["consumed"]["requests"], 1)
        self.assertEqual(Decimal(journal.snapshot()["consumed"]["cost_usd"]), Decimal("0.000049"))
        self.assertEqual(journal.snapshot()["reserved"]["requests"], 0)
        self.assertEqual(journal.snapshot()["combined_v3_reserved_cost_usd"], "0.6402")
        self.assertTrue(liability.verify_run(self.run, source_root=self.root)["manifest_consistent"])


if __name__ == "__main__": unittest.main()
