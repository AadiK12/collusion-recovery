import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from collusion_recovery.common import canonical, digest
from collusion_recovery.gate2_passed_lineage import (
    DEFAULT_AUTHORIZATION_PATH,
    DEFAULT_AUTHORIZATION_RECEIPT_PATH,
    EXPECTED_LINEAGE_HASH,
    Gate2PassedLineageError,
    LINEAGE_PATH,
    ROOT,
    verify_gate2_passed_lineage,
)
import collusion_recovery.gate2_passed_lineage as passed_lineage


RUN_DIR = (
    ROOT
    / "runs/empirical/gate2-smoke-v1.1-"
    "6abffb64-f71b-4868-89cb-28b0aff0c7e3"
)


class Gate2PassedLineageTests(unittest.TestCase):
    def _loaded_evidence(self):
        snapshot = json.loads(LINEAGE_PATH.read_text())
        manifest = json.loads((RUN_DIR / "manifest.json").read_text())
        plan = json.loads((RUN_DIR / "plan.json").read_text())
        smoke_report = json.loads((RUN_DIR / "gate2_smoke_report.json").read_text())
        events = passed_lineage._read_journal(RUN_DIR / "journal.jsonl")
        _reservations, results, payloads, wires = (
            passed_lineage._verify_content_addressed_payloads(RUN_DIR, events)
        )
        return snapshot, manifest, plan, smoke_report, events, results, payloads, wires

    def test_complete_sealed_lineage_verifies_without_current_rebuild(self):
        manifest = json.loads((RUN_DIR / "manifest.json").read_text())
        self.assertNotIn(
            "src/collusion_recovery/gate2_passed_lineage.py",
            manifest["implementation_files"],
        )

        with patch(
            "socket.create_connection",
            side_effect=AssertionError("network access forbidden"),
        ):
            verified = verify_gate2_passed_lineage()

        self.assertEqual(verified["lineage_hash"], EXPECTED_LINEAGE_HASH)
        self.assertEqual(verified["status"], "completed_smoke_passed_hard_stop")
        self.assertIs(verified["next_stage_authorized"], False)
        self.assertEqual(verified, json.loads(canonical(verified)))

    def test_rehashed_snapshot_cannot_change_a_fixed_anchor(self):
        snapshot = json.loads(LINEAGE_PATH.read_text())
        snapshot["status"] = "completed_smoke_failed_hard_stop"
        body = dict(snapshot)
        body.pop("lineage_hash")
        snapshot["lineage_hash"] = digest(body)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lineage.json"
            path.write_text(canonical(snapshot) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                Gate2PassedLineageError, "canonical self-hash differs from its seal"
            ):
                verify_gate2_passed_lineage(snapshot_path=path)

    def test_tampered_raw_journal_is_rejected_before_reconstruction(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "run"
            copied.mkdir()
            for name in (
                "manifest.json",
                "plan.json",
                "journal.jsonl",
                "gate2_smoke_report.json",
                "gate2_smoke_decision.json",
            ):
                shutil.copy2(RUN_DIR / name, copied / name)
            with (copied / "journal.jsonl").open("ab") as handle:
                handle.write(b" ")
            with self.assertRaisesRegex(
                Gate2PassedLineageError, "raw journal.jsonl hash differs"
            ):
                verify_gate2_passed_lineage(run_dir=copied)

    def test_missing_content_addressed_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "run"
            shutil.copytree(RUN_DIR, copied)
            missing = next((copied / "payloads").glob("*.json"))
            missing.unlink()
            with self.assertRaisesRegex(
                Gate2PassedLineageError, "request payload is unavailable or invalid"
            ):
                verify_gate2_passed_lineage(run_dir=copied)

    def test_historical_blob_tamper_is_rejected(self):
        original = passed_lineage._git

        def tampered(repo_root, *arguments):
            value = original(repo_root, *arguments)
            if (arguments[:2] == ("cat-file", "blob")
                    and arguments[-1].endswith(":prompts/baseline.txt")):
                return value + b"tampered"
            return value

        with patch.object(passed_lineage, "_git", side_effect=tampered):
            with self.assertRaisesRegex(
                Gate2PassedLineageError,
                "historical implementation blob hash differs",
            ):
                verify_gate2_passed_lineage()

    def test_explicit_missing_or_tampered_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.tar.gz"
            with self.assertRaisesRegex(
                Gate2PassedLineageError, "explicit Gate 2 archive is unavailable"
            ):
                verify_gate2_passed_lineage(archive_path=missing)

            tampered = Path(directory) / "archive.tar.gz"
            tampered.write_bytes(b"not the sealed archive")
            with self.assertRaisesRegex(
                Gate2PassedLineageError, "archive hash or size differs"
            ):
                verify_gate2_passed_lineage(archive_path=tampered)

    def test_external_authorization_preimages_are_required_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing-authorization.json"
            with self.assertRaisesRegex(
                Gate2PassedLineageError,
                "human authorization is unavailable or invalid",
            ):
                verify_gate2_passed_lineage(authorization_path=missing)

            authorization = root / "authorization.json"
            receipt = root / "receipt.json"
            shutil.copy2(DEFAULT_AUTHORIZATION_PATH, authorization)
            shutil.copy2(DEFAULT_AUTHORIZATION_RECEIPT_PATH, receipt)
            authorization.chmod(0o600)
            receipt.chmod(0o600)
            value = json.loads(authorization.read_text())
            value["confirmed_by"] = "different-reviewer"
            body = dict(value)
            body.pop("authorization_hash")
            value["authorization_hash"] = digest(body)
            authorization.write_text(canonical(value) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                Gate2PassedLineageError,
                "human authorization preimage differs",
            ):
                verify_gate2_passed_lineage(
                    authorization_path=authorization,
                    authorization_receipt_path=receipt,
                )

            shutil.copy2(DEFAULT_AUTHORIZATION_PATH, authorization)
            authorization.chmod(0o600)
            receipt_value = json.loads(receipt.read_text())
            receipt_value["run_instance_hash"] = "0" * 64
            receipt_body = dict(receipt_value)
            receipt_body.pop("receipt_hash")
            receipt_value["receipt_hash"] = digest(receipt_body)
            receipt.write_text(
                canonical(receipt_value) + "\n", encoding="utf-8"
            )
            receipt.chmod(0o600)
            with self.assertRaisesRegex(
                Gate2PassedLineageError,
                "authorization receipt preimage differs",
            ):
                verify_gate2_passed_lineage(
                    authorization_path=authorization,
                    authorization_receipt_path=receipt,
                )

    def test_external_authorization_must_be_owner_only(self):
        with tempfile.TemporaryDirectory() as directory:
            authorization = Path(directory) / "authorization.json"
            shutil.copy2(DEFAULT_AUTHORIZATION_PATH, authorization)
            authorization.chmod(0o644)
            with self.assertRaisesRegex(
                Gate2PassedLineageError,
                "current-user-owned owner-only regular file",
            ):
                verify_gate2_passed_lineage(authorization_path=authorization)

    def test_duplicate_key_or_noncanonical_payload_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "run"
            shutil.copytree(RUN_DIR, copied)
            target = next((copied / "payloads").glob("*.json"))
            raw = target.read_text(encoding="utf-8")
            self.assertTrue(raw.startswith("{"))
            target.write_text(
                '{"model":"tampered-first-wins",' + raw[1:],
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                Gate2PassedLineageError,
                "unavailable or invalid|noncanonical or ambiguously encoded",
            ):
                verify_gate2_passed_lineage(run_dir=copied)

    def test_block0_marker_is_reconstructed_from_last_three_results(self):
        (snapshot, _manifest, plan, _report, events, _results,
         payloads, _wires) = self._loaded_evidence()
        by_identity = {}
        for event in copy.deepcopy(events):
            if event.get("kind") == "result":
                by_identity.setdefault(event["identity"], []).append(event)
        targets = {
            request["identity"] for request in plan["requests"]
            if (request["action"] == "verdict"
                and request["metadata"].get("history") == "C"
                and request["metadata"].get("episode") == 7)
        }
        self.assertEqual(len(targets), 2)
        for identity in targets:
            by_identity[identity][-1]["provider_result"]["parsed_output"][
                "verdict"
            ] = "ACCEPT"
        with self.assertRaisesRegex(
            Gate2PassedLineageError,
            "formation marker does not reconstruct",
        ):
            passed_lineage._verify_block0_marker(
                snapshot, plan, by_identity, payloads
            )

    def test_result_provider_identity_and_budget_are_recomputed(self):
        (snapshot, manifest, plan, report, _events, results,
         payloads, wires) = self._loaded_evidence()
        tampered_identity = copy.deepcopy(results)
        tampered_identity[0]["provider_identity"]["returned_model"] = "other"
        with self.assertRaisesRegex(
            Gate2PassedLineageError,
            "status or provider identity differs",
        ):
            passed_lineage._verify_results_and_budget(
                snapshot, manifest, plan, report,
                tampered_identity, payloads, wires,
            )

        tampered_budget = copy.deepcopy(results)
        tampered_budget[0]["budget_accounting"]["cost_usd"] = "0"
        with self.assertRaisesRegex(
            Gate2PassedLineageError,
            "consumed budget does not reconcile",
        ):
            passed_lineage._verify_results_and_budget(
                snapshot, manifest, plan, report,
                tampered_budget, payloads, wires,
            )

    def test_git_subprocess_is_noninteractive_no_fetch_and_env_scrubbed(self):
        completed = SimpleNamespace(returncode=0, stdout=b"blob\n", stderr=b"")
        with patch.dict(
            os.environ,
            {"OPENAI_KEY": "must-not-be-inherited", "GIT_ASKPASS": "/unsafe"},
            clear=False,
        ), patch.object(
            passed_lineage.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(
                passed_lineage._git(ROOT, "cat-file", "-t", "unused"),
                b"blob\n",
            )
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
        self.assertNotIn("OPENAI_KEY", environment)
        self.assertNotIn("GIT_ASKPASS", environment)


if __name__ == "__main__":
    unittest.main()
