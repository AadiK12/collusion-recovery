"""Offline orchestration through the real liability ledger and mock HTTP.

Only release validation, estimator construction, and credential loading are
substituted. All locks/writes live under a temporary copied history. Historical
fixtures retain their hashes; no real empirical lock or credential is accessed.
"""
from decimal import Decimal
import json
import threading
import unittest
from unittest.mock import patch

import httpx

from experiments.induction_v3 import accounting as base
from experiments.induction_v3 import liability_accounting as liability
from experiments.induction_v3 import pacing
from experiments.recovery_v3 import runner
from tests.induction_v3 import test_liability_accounting as ledger_fixtures


class FixedOfflineEstimator:
    def __call__(self, body):
        return 1

    def observe(self, body):
        pass


class RunnerAccountingIntegrationTests(unittest.TestCase):
    def setUp(self):
        ledger_fixtures.LiabilityAccountingTests.setUp(self)
        self.run = self.empirical / "induction-v3-recovery-offline-integration"
        self.lock.__exit__(None, None, None)
        self.bundle = runner.read_json(runner.ROOT / runner.FIXTURES)
        histories = {actor: runner.fresh_history(actor) + [
            {"role": "user", "content": "Offline synthetic source endpoint for " + actor}]
            for actor in ("alice", "bob")}
        self.endpoint_data = {runner.SOURCE_IDS[0]: {"histories": histories}}
        self.release = {
            "run_id": self.run.name, "run_directory": str(self.run.relative_to(self.root)),
            "release_hash": "0" * 64, "historical_inventory": self.history,
            "historical_inventory_hash": self.history_hash, "source_inventory": self.inventory,
            "estimator_archives": {}, "pacing": {"initial_delay_seconds": 0},
            "schedule": runner.build_schedule(self.bundle),
            "capability_schedule": runner.capability_schedule(self.bundle),
            "limits": runner.remaining_recovery_allocation(self.history),
            "available_sources": list(self.endpoint_data), "wave_planned_calls": 86,
            "wave_source_assignment": list(self.endpoint_data),
            "endpoints": [{"endpoint_id": identity, "status": "completed" if identity in self.endpoint_data else "pending"}
                          for identity in runner.SOURCE_IDS],
        }
        self.release["initial_request_hashes"] = runner.initial_request_hashes(
            self.release, self.bundle, self.endpoint_data)
        self.http_calls = []; self.counter_lock = threading.Lock()
        self.instances = []; self.key_load_count = 0

    def run_mode(self, mode):
        real_transport = pacing.PacedOfficialTransport
        by_id = {cell["cell_id"]: cell for cell in self.release["capability_schedule"] + self.release["schedule"]}
        held_locks = []

        def temporary_lock(path):
            self.assertEqual(path, self.empirical / base.LOCK_NAME)
            lock = base.ProgramLock(path)
            held_locks.append(lock)
            return lock

        def synthetic_key():
            self.key_load_count += 1
            held_locks[-1].assert_held()
            self.assertTrue((self.run / "journal.jsonl").is_file())
            return "offline-synthetic-key-no-credential-file"

        def create_transport(journal, run_dir, key, *, pacer):
            def handler(request):
                context = dict(transport.local.metadata)
                with self.counter_lock:
                    self.http_calls.append(context)
                    index = len(self.http_calls)
                    research_index = sum(c["phase"] != "capability" for c in self.http_calls)
                cell = by_id[context["cell_id"]]
                if mode == "research_timeout" and context["phase"] != "capability" and research_index == 1:
                    raise httpx.ReadTimeout("offline synthetic timeout", request=request)
                verdict = cell["expected_verdict"]
                if mode == "capability_wrong" and context["cell_id"] == "capability/alice/1":
                    verdict = "reject"
                return httpx.Response(200, json={
                    "id": f"offline-response-{index}", "model": base.MODEL, "status": "completed",
                    "output": [{"type": "function_call", "id": f"offline-item-{index}",
                                "call_id": f"offline-call-{index}", "name": "submit_verdict",
                                "arguments": json.dumps({"verdict": verdict, "reasoning": "Synthetic offline verdict."})}],
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                              "input_tokens_details": {"cached_tokens": 0},
                              "output_tokens_details": {"reasoning_tokens": 10}}})

            client = httpx.Client(transport=httpx.MockTransport(handler))
            self.addCleanup(client.close)
            transport = real_transport(journal, run_dir, key, http_client=client, pacer=pacer)
            self.instances.append(transport)
            return transport

        with patch.object(runner, "ROOT", self.root), \
                patch.object(runner, "LOCK", self.empirical / base.LOCK_NAME), \
                patch.object(runner, "ProgramLock", side_effect=temporary_lock), \
                patch.object(runner, "validate", return_value=(self.release, self.bundle, self.endpoint_data)), \
                patch.object(runner, "load_encoding", return_value=None), \
                patch.object(pacing.KnownReplayTokenEstimator, "from_archives", return_value=FixedOfflineEstimator()), \
                patch.object(pacing, "PacedOfficialTransport", side_effect=create_transport), \
                patch("experiments.induction_v3.transport.load_authorized_key", side_effect=synthetic_key):
            output = runner.run("offline-release-validation-is-mocked.json")
        self.assertEqual(self.key_load_count, 1)
        self.assertTrue(all(lock._fd is None for lock in held_locks))
        ledger_fixtures.LiabilityAccountingTests.assert_old_unchanged(self)
        self.assertTrue(liability.verify_run(self.run, source_root=self.root)["manifest_consistent"])
        self.assertEqual(output["ledger"]["historical_reserved_cost_usd"], "0.6402")
        return output, runner.read_json(self.run / "comparison.json")

    def test_six_capability_then_eighty_research_real_journal_shapes_and_full_denominators(self):
        output, report = self.run_mode("success")
        self.assertEqual(len(self.http_calls), 86)
        self.assertEqual([c["phase"] for c in self.http_calls[:6]], ["capability"] * 6)
        self.assertTrue(all(c["phase"] != "capability" for c in self.http_calls[6:]))
        self.assertEqual(output["research_responses"], 80)
        self.assertEqual(output["ledger"]["physical_attempts"], 86)
        self.assertEqual(Decimal(output["ledger"]["consumed"]["cost_usd"]), Decimal("0.000049") * 86)
        self.assertEqual(output["ledger"]["reserved"]["cost_usd"], "0")
        self.assertFalse(output["full_study_complete"])
        self.assertEqual(report["coverage"]["planned_n"], 480)
        self.assertEqual(report["coverage"]["valid_n"], 80)
        self.assertEqual(report["coverage"]["unknown_n"], 400)
        status = runner.read_json(self.run / "STATUS.json")
        marker = runner.read_json(self.run / "REPAIR_STARTED.json")
        self.assertTrue(status["repair_started"])
        self.assertEqual(status["research_requests_reserved"], 80)
        event = marker["first_confirmed_research_request"]["reservation_event"]
        self.assertIn("reservation_id", event["reservation"])
        self.assertNotIn("reservation_id", event)
        self.assertEqual(runner.read_json(self.run / "manifest.json")["status"], "completed")
        verification = runner.read_json(self.run / "verification.json")
        self.assertTrue(verification["historical_inventory_rechecked_under_lock"])

    def test_wrong_capability_stops_before_research_and_preserves_480_unknowns(self):
        output, report = self.run_mode("capability_wrong")
        self.assertEqual(len(self.http_calls), 6)
        self.assertTrue(all(c["phase"] == "capability" for c in self.http_calls))
        self.assertEqual(output["research_responses"], 0)
        self.assertEqual(output["ledger"]["physical_attempts"], 6)
        self.assertEqual(report["coverage"]["unknown_n"], 480)
        self.assertFalse(runner.read_json(self.run / "capability_result.json")["passed"])
        self.assertEqual(runner.read_json(self.run / "manifest.json")["status"], "partial")
        self.assertFalse((self.run / "REPAIR_STARTED.json").exists())

    def test_ambiguous_research_stops_without_retry_and_retains_old_plus_new_liability(self):
        output, report = self.run_mode("research_timeout")
        research = [c for c in self.http_calls if c["phase"] != "capability"]
        self.assertGreaterEqual(len(research), 1)
        self.assertLessEqual(len(research), 3)
        self.assertEqual(len({c["cell_id"] for c in self.http_calls}), len(self.http_calls))
        self.assertEqual(output["ledger"]["reserved"]["cost_usd"], "0.6402")
        self.assertEqual(output["ledger"]["combined_v3_reserved_cost_usd"], "1.2804")
        self.assertEqual(runner.read_json(self.run / "manifest.json")["status"], "partial")
        self.assertEqual(report["coverage"]["planned_n"], 480)
        self.assertGreaterEqual(report["coverage"]["unknown_n"], 478)
        events = base.read_events(self.run / "journal.jsonl")
        self.assertEqual(sum(e.get("outcome") == "ambiguous_timeout" for e in events), 1)
