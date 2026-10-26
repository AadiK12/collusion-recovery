"""Mocked-audit report tests; never read empirical run data or invoke a provider."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from collusion_recovery.common import canonical, digest
from experiments import exploratory_collusion_v2 as study
from experiments import exploratory_collusion_v2_report as report
from tests.test_exploratory_collusion_v2_assessment import synthetic


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "mock-run"
        self.run_dir.mkdir()
        self.output = self.root / "new-report"
        self.config, trace = synthetic(positive_dyads=1)
        self.paths, self.hashes = [], []
        for index in range(2):
            value = {"reviewer_id": f"synthetic-reviewer-{index+1}", "dyads": [
                {"dyad": row["dyad"], "annotation": deepcopy(row["annotations"][index])}
                for row in trace["dyads"]]}
            path = self.root / f"review-{index}.json"
            report.live.shared._atomic_json(path, value, exclusive=True)
            self.paths.append(path)
            self.hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        for row in trace["dyads"]:
            row["annotations"] = []
        trace["source"] = "empirical_unverified"  # Mocked provenance only, never an actual run.
        self.audit = {"status": report.live.TERMINAL, "integrity_verified": True,
            "accounted_attempts": 192, "dispatch_reservations": 192, "logical_requests_completed": 192,
            "open_reservations": 0, "ledger": {"consumed": {"requests": 192},
                "reserved": {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": "0"}},
            "journal_tail": "synthetic-tail", "trace": trace,
            "induction_established": False, "repairs_authorized": False}
        body = {"artifact_type": report.live.VERSION, "scope": report.SCOPE,
                "run_id": "synthetic-report-test", "development_namespace": self.config.seed_namespace,
                "development_plan": study.build_plan(self.config)}
        self.release = {**body, "release_hash": digest(body)}
        self.release_path = self.root / "release.json"
        report.live.shared._atomic_json(self.release_path, self.release, exclusive=True)
        self.auditor = mock.patch.object(report.live, "audit_run", return_value=self.audit).start()
        self.addCleanup(mock.patch.stopall)

    def export(self):
        return report.export_report(self.release_path, self.run_dir, self.paths, self.output, self.hashes)

    def update_annotation(self, index, transform):
        value = json.loads(self.paths[index].read_text())
        transform(value)
        report.live.shared._atomic_json(self.paths[index], value)
        self.hashes[index] = hashlib.sha256(self.paths[index].read_bytes()).hexdigest()

    def test_export_preserves_audited_trace_and_assessor_authority_fields(self):
        untouched = deepcopy(self.audit)
        raw_inputs = [path.read_bytes() for path in self.paths]
        result = self.export()
        self.assertTrue(result["behavioral_feasibility_gate"])
        self.assertFalse(result["repairs_allowed"])
        self.assertEqual(self.audit, untouched)
        self.assertEqual([path.read_bytes() for path in self.paths], raw_inputs)
        saved = json.loads((self.output / "report.json").read_text())
        self.assertTrue(saved["provenance_audit"]["integrity_verified"])
        self.assertNotIn("trace", saved["provenance_audit"])
        self.assertFalse(saved["annotation_procedure_independently_verified"])
        for field in ("provenance_verified", "empirical_formation_established", "causal_peer_influence_established", "repairs_allowed"):
            self.assertFalse(saved["operational_assessment"][field])
        for index, source in enumerate(raw_inputs, 1):
            frozen = json.loads((self.output / f"frozen_annotation_{index}.json").read_text())
            self.assertEqual(frozen["source_utf8"].encode(), source)
            self.assertEqual(frozen["source_sha256"], hashlib.sha256(source).hexdigest())
            self.assertEqual(frozen["annotation"], json.loads(source))
        for filename, expected in saved["copied_artifact_sha256"].items():
            self.assertEqual(hashlib.sha256((self.output / filename).read_bytes()).hexdigest(), expected)
        self.assertEqual(saved["report_hash"], digest({k: v for k, v in saved.items() if k != "report_hash"}))
        self.assertEqual(self.auditor.call_count, 1)
        self.assertEqual(digest(self.auditor.call_args.args[0]), digest(self.release))
        self.assertEqual(self.auditor.call_args.args[1], self.run_dir.resolve())

    def test_exact_terminal_and_192_usable_attempts_are_mandatory(self):
        for key, value in (("status", "running"), ("integrity_verified", False),
                           ("accounted_attempts", 191), ("dispatch_reservations", 193),
                           ("logical_requests_completed", 191), ("open_reservations", 1)):
            with self.subTest(key=key):
                changed = deepcopy(self.audit)
                changed[key] = value
                self.auditor.return_value = changed
                with self.assertRaises(report.ReportExportError):
                    self.export()
                self.assertFalse(self.output.exists())

    def test_reserved_money_and_mock_source_are_rejected(self):
        for mode in ("reserved", "mock", "count"):
            changed = deepcopy(self.audit)
            if mode == "reserved":
                changed["ledger"]["reserved"]["cost_usd"] = "0.01"
            elif mode == "mock":
                changed["trace"]["source"] = "mock"
            else:
                changed["ledger"]["consumed"]["requests"] = 191
            self.auditor.return_value = changed
            with self.assertRaises(report.ReportExportError):
                self.export()
        self.assertFalse(self.output.exists())

    def test_frozen_hash_mismatch_fails_before_run_data_is_read(self):
        self.hashes[0] = "0" * 64
        with self.assertRaises(report.ReportExportError):
            self.export()
        self.auditor.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_reviewer_id_mismatch_duplicate_or_missing_dyad_rejected(self):
        mutations = [lambda v: v["dyads"].pop(),
                     lambda v: v["dyads"][1].update(dyad=0),
                     lambda v: v["dyads"][0]["annotation"].update(reviewer_id="mismatch"),
                     lambda v: v["dyads"][0]["annotation"].update(outcome_blinded=False)]
        original = self.paths[0].read_bytes()
        for mutation in mutations:
            value = json.loads(original)
            mutation(value)
            report.live.shared._atomic_json(self.paths[0], value)
            self.hashes[0] = hashlib.sha256(self.paths[0].read_bytes()).hexdigest()
            with self.assertRaises(report.ReportExportError):
                self.export()
        self.auditor.assert_not_called()

    def test_same_reviewer_cannot_masquerade_as_two_files(self):
        def duplicate(value):
            value["reviewer_id"] = "synthetic-reviewer-1"
            for row in value["dyads"]:
                row["annotation"]["reviewer_id"] = value["reviewer_id"]
        self.update_annotation(1, duplicate)
        with self.assertRaises(report.ReportExportError):
            self.export()

    def test_existing_output_or_inside_run_cannot_be_written(self):
        self.output.mkdir()
        with self.assertRaises(report.ReportExportError):
            self.export()
        self.output = self.run_dir / "report"
        with self.assertRaises(report.ReportExportError):
            self.export()
        self.assertFalse(self.output.exists())
        self.auditor.assert_not_called()

    def test_reviewer_change_during_audit_fails_before_writes(self):
        def changed(*args):
            self.update_annotation(0, lambda value: value.update(reviewer_id="changed"))
            return self.audit
        self.auditor.side_effect = changed
        with self.assertRaises(report.ReportExportError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_already_annotated_raw_trace_cannot_be_overwritten(self):
        self.audit["trace"]["dyads"][0]["annotations"] = [{"prior": True}]
        with self.assertRaises(report.ReportExportError):
            self.export()
        self.assertFalse(self.output.exists())

    def test_negative_reviews_export_honest_failed_operational_gate(self):
        def negative(value):
            for row in value["dyads"]:
                row["annotation"].update(reciprocal_agreement=False, proposal=None, uptake=None,
                    agreement_covers_late_window=False, no_unresolved_retraction=False)
        for index in range(2):
            self.update_annotation(index, negative)
        result = self.export()
        self.assertFalse(result["behavioral_feasibility_gate"])
        saved = json.loads((self.output / "report.json").read_text())
        self.assertTrue(saved["operational_assessment"]["complete_valid_trace"])
        self.assertEqual(saved["operational_assessment"]["qualifying_dyads"], 0)


if __name__ == "__main__":
    unittest.main()
