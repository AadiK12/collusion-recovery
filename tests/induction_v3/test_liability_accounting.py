"""Offline liability-ledger tests; all locks and writes use temporary roots.

Frozen historical artifacts are copied verbatim as fixtures. No request is sent,
no credentials are accessed, and the real empirical program lock is untouched.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from experiments.induction_v3 import accounting as base
from experiments.induction_v3 import liability_accounting as liability

ROOT = Path(__file__).resolve().parents[2]


class LiabilityAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.empirical = self.root / "runs" / "empirical"
        self.empirical.mkdir(parents=True)
        self.inventory = {}
        for relative in liability.REQUIRED_SOURCES:
            target = self.root / relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
            self.inventory[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        for source in sorted((ROOT / "runs" / "empirical").iterdir()):
            if source.name.startswith(".") or not source.is_dir(): continue
            # Fix this test fixture to the original ten manifests. Future real
            # successor runs are not part of the preregistered baseline fixture.
            if source.name.startswith("induction-v3-") and source.name not in {
                liability.liability_anchor()["run_id"],
                "induction-v3-b2522673-f58a-4d9e-8be0-ae08b511db53",
                "induction-v3-diagnostic-41575ab5-1c1c-41cb-b2e2-edd6e2dbcd54",
            }: continue
            target = self.empirical / source.name; target.mkdir()
            shutil.copyfile(source / "manifest.json", target / "manifest.json")
            if source.name.startswith("induction-v3-"):
                shutil.copyfile(source / "journal.jsonl", target / "journal.jsonl")
        anchor = liability.liability_anchor()
        self.old = self.empirical / anchor["run_id"]
        for folder, name in (("requests", anchor["request_hash"]), ("responses", anchor["reservation_id"])):
            (self.old / folder).mkdir()
            shutil.copyfile(ROOT / "runs" / "empirical" / anchor["run_id"] / folder / (name + ".json"), self.old / folder / (name + ".json"))
        self.lock = base.ProgramLock(self.empirical / base.LOCK_NAME)
        self.lock.__enter__(); self.addCleanup(self.lock.__exit__, None, None, None)
        self.run = self.empirical / "induction-v3-liability-test"
        self.history = liability.historical_inventory(self.lock)
        self.history_hash = liability.inventory_hash(self.history)
        self.old_bytes = {p: p.read_bytes() for p in self.old.rglob("*") if p.is_file()}

    def journal(self, **kwargs):
        options = {"run_dir": self.run, "program_lock": self.lock,
            "binding": {"run_id": self.run.name, "model": base.MODEL},
            "source_root": self.root, "source_inventory": self.inventory,
            "expected_history_hash": self.history_hash}
        options.update(kwargs)
        return liability.LiabilityReservationJournal(**options)

    def reserve(self, journal, identity="new-physical-identity", **kwargs):
        options = {"input_token_bound": 1000, "max_output_tokens": 100,
            "request_hash": hashlib.sha256(identity.encode()).hexdigest()}
        options.update(kwargs)
        return journal.reserve(identity, **options)

    def settle(self, journal, reservation):
        return journal.settle(reservation.reservation_id, usage={"input_tokens": 100, "output_tokens": 20})

    def reopen(self, **kwargs):
        self.lock.__exit__(None, None, None); self.lock.__enter__()
        return self.journal(**kwargs)

    def assert_old_unchanged(self):
        self.assertEqual({p: p.read_bytes() for p in self.old.rglob("*") if p.is_file()}, self.old_bytes)

    def test_exact_baseline_and_original_constructor_remains_blocked(self):
        totals = liability.historical_totals(self.history)
        self.assertEqual(totals, {"lifetime_consumed_usd": "48.50659936", "lifetime_reserved_usd": "0.6402",
            "v3_consumed_usd": "1.09956276", "v3_reserved_usd": "0.6402", "v3_attempts": 468,
            "remaining_v3_usd": "98.26023724", "remaining_lifetime_usd": "200.85320064", "remaining_v3_attempts": 3532})
        with self.assertRaisesRegex(base.AccountingError, "outstanding reservations"):
            base.historical_inventory(self.lock)
        journal = self.journal()
        self.assertEqual(journal.campaign_limit, Decimal("98.26023724"))
        self.assertEqual(journal.max_attempts, 3532)
        snapshot = journal.snapshot()
        self.assertEqual(snapshot["consumed"]["cost_usd"], "0")
        self.assertEqual(snapshot["reserved"]["cost_usd"], "0")
        self.assertEqual(snapshot["historical_reserved_cost_usd"], "0.6402")
        self.assertEqual(snapshot["lifetime_exposure_usd"], "49.14679936")
        self.assertEqual(snapshot["combined_v3_exposure_usd"], "1.73976276")
        self.assert_old_unchanged()

    def test_interleaving_and_readonly_semantic_replay(self):
        journal = self.journal()
        a, b, c = [self.reserve(journal, name) for name in ("a", "b", "c")]
        self.settle(journal, b); self.settle(journal, a)
        d = self.reserve(journal, "d"); self.settle(journal, d); self.settle(journal, c)
        journal.finish()
        before = {p: p.read_bytes() for p in self.run.iterdir()}
        audit = liability.verify_run(self.run, source_root=self.root, program_lock=self.lock)
        self.assertTrue(audit["manifest_consistent"])
        self.assertTrue(audit["frozen_historical_lineage_verified"])
        self.assertEqual(audit["ledger"]["combined_v3_physical_attempts"], 472)
        self.assertEqual(audit["ledger"]["historical_reserved_cost_usd"], "0.6402")
        self.assertEqual(before, {p: p.read_bytes() for p in self.run.iterdir()})
        self.assert_old_unchanged()

    def test_outstanding_new_reservation_restart_stops_and_retains_both(self):
        journal = self.journal(); self.reserve(journal)
        before = liability.verify_run(self.run, source_root=self.root)
        self.assertTrue(before["requires_restart_stop"])
        reopened = self.reopen()
        self.assertTrue(reopened.stopped)
        self.assertEqual(reopened.snapshot()["reserved"]["cost_usd"], "0.00037")
        self.assertEqual(reopened.snapshot()["combined_v3_reserved_cost_usd"], "0.64057")
        with self.assertRaises(base.DispatchStopped): self.reserve(reopened, "another")

    def test_new_unknown_stops_and_blocks_any_successor(self):
        journal = self.journal(); reservation = self.reserve(journal)
        journal.settle(reservation.reservation_id, outcome="unknown")
        journal.finish("partial")
        self.assertEqual(journal.snapshot()["combined_v3_reserved_cost_usd"], "0.64057")
        with self.assertRaises(base.DispatchStopped): self.reserve(journal, "after-unknown")
        self.lock.__exit__(None, None, None); self.lock.__enter__()
        with self.assertRaisesRegex(base.AccountingError, "sole allowed historical liability"):
            liability.historical_inventory(self.lock)
        self.assert_old_unchanged()

    def test_completed_successor_imports_liability_once_and_counts_prior_new_usage(self):
        journal = self.journal(); self.settle(journal, self.reserve(journal)); journal.finish()
        self.lock.__exit__(None, None, None); self.lock.__enter__()
        history = liability.historical_inventory(self.lock)
        totals = liability.historical_totals(history)
        self.assertEqual(totals["lifetime_reserved_usd"], "0.6402")
        self.assertEqual(totals["v3_reserved_usd"], "0.6402")
        self.assertEqual(totals["v3_attempts"], 469)
        self.assertEqual(totals["v3_consumed_usd"], "1.09961176")
        run2 = self.empirical / "induction-v3-second-liability-test"
        next_journal = self.journal(run_dir=run2, binding={"run_id": run2.name, "model": base.MODEL},
            expected_history_hash=liability.inventory_hash(history))
        self.assertEqual(next_journal.snapshot()["combined_v3_reserved_cost_usd"], "0.6402")
        next_journal.finish()
        self.assertTrue(liability.verify_run(run2, source_root=self.root, program_lock=self.lock)["carried_liability_unchanged"])
        self.assert_old_unchanged()

    def test_local_cap_atomic_competition(self):
        journal = self.journal(campaign_limit_usd="0.00037")
        def attempt(i):
            try: return self.reserve(journal, str(i))
            except base.BudgetExceeded: return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(r is not None for r in outcomes), 1)
        self.assertEqual(journal.snapshot()["physical_attempts"], 1)

    def test_concurrency_bound_three(self):
        journal = self.journal()
        def attempt(i):
            try: return self.reserve(journal, str(i))
            except base.BudgetExceeded: return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(8)))
        self.assertEqual(sum(r is not None for r in outcomes), 3)
        self.assertEqual(journal.snapshot()["in_flight"], 3)

    def test_lifetime_cap_includes_external_reserved(self):
        journal = self.journal(lifetime_limit_usd="49.14679936")
        with self.assertRaises(base.BudgetExceeded): self.reserve(journal)
        self.assertEqual(journal.snapshot()["physical_attempts"], 0)

    def test_cap_overrides_cannot_exceed_global_remaining(self):
        for kw in ({"campaign_limit_usd": "98.26023725"}, {"max_attempts": 3533},
                   {"lifetime_limit_usd": "250.01"}, {"max_in_flight": 4}):
            with self.assertRaises(base.BudgetExceeded): self.journal(**kw)
        self.assertFalse(self.run.exists())

    def test_attempt_cap_counts_proven_noncompletion(self):
        journal = self.journal(max_attempts=1)
        reservation = self.reserve(journal)
        journal.settle(reservation.reservation_id, outcome="known_not_completed", noncompletion_evidence="offline-proof")
        with self.assertRaises(base.BudgetExceeded): self.reserve(journal, "second")
        self.assertEqual(journal.snapshot()["combined_v3_physical_attempts"], 469)

    def test_historical_identity_is_not_automatically_retried(self):
        journal = self.journal()
        old = next(e["reservation"] for e in base.read_events(self.old / "journal.jsonl") if e.get("reservation", {}).get("reservation_id") == liability.liability_anchor()["reservation_id"])
        with self.assertRaisesRegex(base.AccountingError, "historical physical identity"):
            self.reserve(journal, old["identity"])
        self.assertEqual(journal.snapshot()["physical_attempts"], 0)

    def test_history_hash_and_current_history_drift_fail_closed(self):
        with self.assertRaisesRegex(base.AccountingError, "prepared historical inventory drifted"):
            self.journal(expected_history_hash="0" * 64)
        journal = self.journal()
        path = next(p for p in self.empirical.glob("*/manifest.json") if not p.parent.name.startswith("induction-v3-"))
        manifest = json.loads(path.read_text()); ledger = manifest.get("ledger") or manifest.get("budget")
        ledger["consumed"]["cost_usd"] = "0"
        path.write_text(json.dumps(manifest))
        with self.assertRaises(base.AccountingError): self.reserve(journal)
        with self.assertRaises(base.AccountingError): liability.verify_run(self.run)
        self.assertEqual(journal.snapshot()["physical_attempts"], 0)

    def test_exact_429_response_drift_rejected(self):
        journal = self.journal()
        path = self.old / "responses" / (liability.liability_anchor()["reservation_id"] + ".json")
        path.write_text('{"http_status": 429, "error": {"code": "rate_limit_exceeded"}}')
        with self.assertRaisesRegex(base.AccountingError, "sole allowed historical liability"):
            self.reserve(journal)

    def test_source_drift_and_manifest_semantic_mismatch_rejected(self):
        journal = self.journal()
        path = self.root / liability.REQUIRED_SOURCES[1]
        path.write_text(path.read_text() + "\n# drift\n")
        with self.assertRaisesRegex(base.AccountingError, "source drift"):
            self.reserve(journal)
        manifest_path = self.run / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["ledger"]["historical_reserved_cost_usd"] = "0"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(base.AccountingError, "reconcile exactly"):
            liability.verify_run(self.run)

    def test_binding_schema_tamper_rejected_even_with_rehashed_chain(self):
        self.journal()
        events = base.read_events(self.run / "journal.jsonl")
        events[0]["binding"]["historical_totals"]["lifetime_reserved_usd"] = "0"
        events[0]["event_hash"] = base._digest({k: v for k, v in events[0].items() if k != "event_hash"})
        (self.run / "journal.jsonl").write_text(base._canonical(events[0]) + "\n")
        with self.assertRaisesRegex(base.AccountingError, "totals or hash"):
            liability.verify_run(self.run)


if __name__ == "__main__":
    unittest.main()
