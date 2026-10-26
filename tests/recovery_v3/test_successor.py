"""Offline successor checks; synthetic HTTP, temporary ledger, no real lock/key."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import Mock, patch

import httpx
import pytest

from experiments.induction_v3 import accounting, liability_accounting, pacing
from experiments.induction_v3.transport import DispatchStopped
from experiments.recovery_v3 import runner, successor as s
from tests.recovery_v3 import test_runner_integration as integration

FixedOfflineEstimator = integration.FixedOfflineEstimator


BEFORE = datetime(2026, 10, 26, 11, 30, tzinfo=timezone.utc)
AFTER = datetime(2026, 10, 26, 11, 47, tzinfo=timezone.utc)


def test_cutoff_blocks_final_http_without_calling_delegate():
    delegate = Mock()
    with patch.object(s, "now", return_value=AFTER):
        with pytest.raises(DispatchStopped, match="deadline_no_new_dispatch"):
            s.DeadlineHTTPClient(delegate).post("unused", json={})
    delegate.post.assert_not_called()


def test_deadline_check_before_and_after_history_validation_keeps_inflight_alive():
    journal = object.__new__(s.DeadlineJournal)
    journal.stop = Mock()
    with patch.object(s, "now", side_effect=[BEFORE, AFTER]), \
            patch.object(s.LiabilityReservationJournal, "assert_dispatchable") as parent:
        with pytest.raises(DispatchStopped):
            journal.assert_dispatchable()
    parent.assert_called_once()
    journal.stop.assert_not_called()


def test_capacity_rechecks_cutoff_after_parent_check():
    journal = object.__new__(s.DeadlineJournal)
    with patch.object(s, "now", return_value=AFTER), \
            patch.object(s.LiabilityReservationJournal, "_check_capacity") as parent:
        with pytest.raises(DispatchStopped):
            journal._check_capacity(object())
    parent.assert_called_once()


def test_limits_reuse_envelope_and_cap_each_successor_at_eighty():
    value = {"campaign_limit_usd": "24.30813133", "max_attempts": 514,
             "lifetime_limit_usd": "250", "max_in_flight": 3,
             "prior_recovery_attempts": 86, "prior_recovery_exposure_usd": "0.69186867"}
    with patch.object(runner, "remaining_recovery_allocation", return_value=value):
        assert s.limits_for([]) == {**value, "max_attempts": 80}
    with patch.object(runner, "remaining_recovery_allocation", return_value={**value, "max_attempts": 79}):
        with pytest.raises(ValueError, match="insufficient"):
            s.limits_for([])


def test_fixed_priority_and_no_recollection():
    with patch.object(s, "attempted_sources", return_value={runner.SOURCE_IDS[0]}):
        s.check_selection(s.PRIORITY[0], [])
        with pytest.raises(ValueError, match="priority"):
            s.check_selection(s.PRIORITY[1], [])
    with patch.object(s, "attempted_sources", return_value={s.PRIORITY[0]}):
        with pytest.raises(ValueError, match="already attempted"):
            s.check_selection(s.PRIORITY[0], [])
        s.check_selection(s.PRIORITY[1], [])


def test_cutoff_rejected_before_key_or_real_lock():
    with patch.object(s, "validate", return_value=({}, {}, {})), \
            patch.object(s, "now", return_value=AFTER), \
            patch.object(s, "ProgramLock", side_effect=AssertionError("real lock forbidden")), \
            patch("experiments.induction_v3.transport.load_authorized_key", side_effect=AssertionError("key forbidden")):
        with pytest.raises(DispatchStopped):
            s.run("offline")


def test_capability_reuse_rejects_unknown_or_wrong_cells():
    cells = [{"cell_id": str(i), "expected_verdict": "reject"} for i in range(6)]
    release = {"run_directory": "unused", "release_hash": "x", "capability_schedule": cells}
    bad = {"passed": True, "planned": 6, "results": [
        {"cell_id": "unknown-" + str(i), "valid": True, "verdict": None, "expected": None} for i in range(6)]}
    with patch.object(runner, "validate", return_value=(release, {}, {})), \
            patch.object(runner, "read_json", return_value=bad):
        with pytest.raises(ValueError, match="capability"):
            s.capability_lineage()


def test_observed_rate_feasibility_rejects_late_new_wave():
    release = runner.read_json(runner.DEFAULT_RELEASE)
    lineage = {"run_directory": release["run_directory"]}
    with patch.object(s, "now", return_value=BEFORE):
        estimate = s.feasibility(lineage)
    assert 670 < estimate["estimated_wave_seconds_including_cooldown"] < 680
    with patch.object(s, "now", return_value=AFTER):
        with pytest.raises(ValueError, match="insufficient deadline"):
            s.feasibility(lineage)


class SuccessorIntegrationTests(unittest.TestCase):
    def setUp(self):
        integration.RunnerAccountingIntegrationTests.setUp(self)
        old = self.endpoint_data[runner.SOURCE_IDS[0]]
        self.endpoint_data = {s.PRIORITY[0]: old}
        self.release.update(source_run="runs/empirical/unused-offline-source",
                            endpoint_id=s.PRIORITY[0], capability_schedule=[], capability_lineage={},
                            available_sources=[s.PRIORITY[0]], wave_source_assignment=[s.PRIORITY[0]],
                            wave_planned_calls=80)
        self.release["limits"]["max_attempts"] = 80
        self.release["endpoints"] = [{"endpoint_id": identity,
            "status": "completed" if identity == s.PRIORITY[0] else "pending"} for identity in runner.SOURCE_IDS]
        self.release["initial_request_hashes"] = runner.initial_request_hashes(
            self.release, self.bundle, self.endpoint_data)

    def run_offline(self, *, crossing=False):
        actual = pacing.PacedOfficialTransport
        by_id = {cell["cell_id"]: cell for cell in self.release["schedule"]}
        calls = []
        def factory(journal, run_dir, key, *, pacer, http_client):
            def handler(request):
                context = dict(transport.local.metadata)
                calls.append(context)
                cell = by_id[context["cell_id"]]
                return httpx.Response(200, json={"id": "offline-" + str(len(calls)),
                    "model": accounting.MODEL, "status": "completed", "output": [{
                        "type": "function_call", "id": "item-" + str(len(calls)),
                        "call_id": "call-" + str(len(calls)), "name": "submit_verdict",
                        "arguments": json.dumps({"verdict": cell["expected_verdict"], "reasoning": "Offline."})}],
                    "usage": {"input_tokens": 100, "output_tokens": 20,
                              "input_tokens_details": {"cached_tokens": 0},
                              "output_tokens_details": {"reasoning_tokens": 10}}})
            client = httpx.Client(transport=httpx.MockTransport(handler))
            self.addCleanup(client.close)
            class CrossedHTTP(s.DeadlineHTTPClient):
                def post(inner, *args, **kwargs):
                    with patch.object(s, "now", return_value=AFTER):
                        return super().post(*args, **kwargs)
            wrapped = (CrossedHTTP if crossing else s.DeadlineHTTPClient)(client)
            transport = actual(journal, run_dir, key, pacer=pacer, http_client=wrapped)
            return transport
        with patch.object(s, "ROOT", self.root), \
                patch.object(s, "LOCK", self.empirical / accounting.LOCK_NAME), \
                patch.object(s, "validate", return_value=(self.release, self.bundle, self.endpoint_data)), \
                patch.object(s, "feasibility", return_value={}), \
                patch.object(s, "now", return_value=BEFORE), \
                patch.object(runner, "load_encoding", return_value=None), \
                patch.object(pacing.KnownReplayTokenEstimator, "from_archives", return_value=FixedOfflineEstimator()), \
                patch.object(pacing, "PacedOfficialTransport", side_effect=factory), \
                patch("experiments.induction_v3.transport.load_authorized_key", return_value="offline-key-not-from-file"):
            result = s.run("offline")
        return result, calls

    def test_eighty_calls_no_capability_and_full_missing_denominators(self):
        result, calls = self.run_offline()
        self.assertEqual(len(calls), 80)
        self.assertTrue(all(c["phase"] in {"immediate", "sequential"} for c in calls))
        self.assertEqual(len({c["cell_id"] for c in calls}), 80)
        self.assertTrue(result["wave_complete"])
        self.assertEqual(result["ledger"]["physical_attempts"], 80)
        self.assertEqual(Decimal(result["ledger"]["consumed"]["cost_usd"]), Decimal("0.000049") * 80)
        self.assertEqual(result["ledger"]["reserved"]["cost_usd"], "0")
        self.assertEqual(result["ledger"]["historical_reserved_cost_usd"], "0.6402")
        comparison = runner.read_json(self.run / "comparison.json")
        self.assertEqual(comparison["coverage"]["planned_n"], 480)
        self.assertEqual(comparison["coverage"]["valid_n"], 80)
        self.assertEqual(comparison["coverage"]["unknown_n"], 400)
        self.assertTrue(liability_accounting.verify_run(self.run, source_root=self.root)["manifest_consistent"])

    def test_clock_crossing_after_reservation_never_posts_and_retains_unknown(self):
        result, calls = self.run_offline(crossing=True)
        self.assertEqual(calls, [])
        self.assertFalse(result["wave_complete"])
        self.assertEqual(result["research_responses"], 0)
        attempts = result["ledger"]["physical_attempts"]
        self.assertGreaterEqual(attempts, 1)
        self.assertLessEqual(attempts, 3)
        self.assertEqual(Decimal(result["ledger"]["reserved"]["cost_usd"]), Decimal("0.6402") * attempts)
        self.assertEqual(result["ledger"]["consumed"]["cost_usd"], "0")
        self.assertEqual(result["ledger"]["historical_reserved_cost_usd"], "0.6402")
        self.assertTrue(liability_accounting.verify_run(self.run, source_root=self.root)["manifest_consistent"])
