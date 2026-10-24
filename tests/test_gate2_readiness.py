from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from collusion_recovery.budget import DispatchReleaseLimits
from collusion_recovery.common import digest
from collusion_recovery.gate2_readiness import (
    STAGE2_EVENT_COUNT,
    STAGE2_JOURNAL_TAIL,
    STAGE2_PACKAGE_HASH,
    STAGE2_REPORT_HASH,
    STAGE2_RUN_DIR,
    Gate2ReadinessError,
    audit_smoke_inventory,
    build_gate2_readiness,
    validate_readiness_artifact,
    verify_stage2_lineage,
)
from collusion_recovery.live_config import load_live_config
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    DispatchAuthorizationArtifact,
    authorization_binding,
    build_request_plan,
    validate_dispatch_authorization,
)
from collusion_recovery.stage2 import STAGE2_CONFIG


def _forbid_network_and_credentials():
    stack = ExitStack()
    for name in (
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
    ):
        stack.enter_context(patch(name, side_effect=AssertionError(
            "network access forbidden in Gate 2 readiness tests")))
    stack.enter_context(patch(
        "collusion_recovery.stage2._load_local_openai_key",
        side_effect=AssertionError("credential loader must not be called"),
    ))
    return stack


def _persisted_legacy_readiness_fixture():
    body = {
        "artifact_type": "gate_2_no_dispatch_readiness_v2",
        "status": "blocked_no_dispatch",
        "paid_calls": 0,
        "network_calls": 0,
        "experiment_started": False,
        "dispatch_boundary": {"dispatch_enabled": False},
        "human_authorization": {"artifact_present": False},
    }
    return {**body, "readiness_hash": digest(body)}


class Gate2LegacyGeneratorRetirementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_live_config(STAGE2_CONFIG)
        cls.plan = build_request_plan(cls.config)

    def test_smoke_inventory_cannot_be_regenerated_or_relabeled(self):
        with _forbid_network_and_credentials(), self.assertRaisesRegex(
                Gate2ReadinessError, "v1.0 smoke-inventory generator is retired"):
            audit_smoke_inventory(self.config, self.plan.to_dict())

    def test_readiness_cannot_be_regenerated_with_or_without_authorization(self):
        with tempfile.TemporaryDirectory() as temp, _forbid_network_and_credentials():
            absent = Path(temp) / "absent.json"
            present = Path(temp) / "present.json"
            present.write_text("{}\n", encoding="utf-8")
            for authorization_path in (absent, present):
                with self.subTest(path=authorization_path), self.assertRaisesRegex(
                        Gate2ReadinessError, "readiness generator is retired"):
                    build_gate2_readiness(
                        run_dir=Path(temp) / "nonexistent-run",
                        authorization_path=authorization_path,
                    )

    def test_persisted_legacy_readiness_is_inspect_only(self):
        artifact = _persisted_legacy_readiness_fixture()
        validate_readiness_artifact(artifact)
        artifact["dispatch_boundary"]["dispatch_enabled"] = True
        with self.assertRaises(Gate2ReadinessError):
            validate_readiness_artifact(artifact)


class Stage2AuthorizationBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_live_config(STAGE2_CONFIG)
        cls.plan = build_request_plan(cls.config)

    def _authorization(self, release, *, max_stage="calibration"):
        initial = json.loads((STAGE2_RUN_DIR / "manifest.json").read_text(
            encoding="utf-8"))["dispatch_authorization"]
        confirmed_at = "2026-10-23T22:00:00-07:00"
        reviewed = STAGE2_JOURNAL_TAIL
        binding = authorization_binding(
            self.config, self.plan,
            confirmed_at=confirmed_at,
            transport_hash=initial["transport_hash"],
            approved_budget_usd="1.78",
            max_stage=max_stage,
            max_block=0,
            reviewed_event_hash=reviewed,
            release_limits=release,
            preflight_inventory_hash=initial["preflight_inventory_hash"],
        )
        return DispatchAuthorizationArtifact(
            authorization_id=self.config.authorization.authorization_id,
            confirmed_at=confirmed_at,
            binding_hash=binding,
            approved_budget_usd="1.78",
            transport_hash=initial["transport_hash"],
            max_stage=max_stage,
            max_block=0,
            reviewed_event_hash=reviewed,
            release_limits=release,
            preflight_inventory_hash=initial["preflight_inventory_hash"],
        )

    @unittest.skipUnless(STAGE2_RUN_DIR.is_dir(), "local Stage 2 evidence is unavailable")
    def test_existing_authorization_validator_rejects_smoke_expansion(self):
        release = DispatchReleaseLimits.from_mapping({
            "max_dispatch_attempts": 67,
            "max_total_input_tokens": 211_932,
            "max_total_output_tokens": 94_208,
            "max_cost_usd": "1.78",
            "max_in_flight": 1,
            "max_input_tokens_per_request": 9_353,
            "max_output_tokens_per_request": 4_096,
        })
        with self.assertRaises(AuthorizationRequiredError):
            validate_dispatch_authorization(
                self.config, self.plan,
                self._authorization(release, max_stage="smoke"),
            )

    @unittest.skipUnless(STAGE2_RUN_DIR.is_dir(), "local Stage 2 evidence is unavailable")
    def test_existing_authorization_validator_rejects_limit_expansion(self):
        release = DispatchReleaseLimits.from_mapping({
            "max_dispatch_attempts": 68,
            "max_total_input_tokens": 211_932,
            "max_total_output_tokens": 94_208,
            "max_cost_usd": "1.78",
            "max_in_flight": 1,
            "max_input_tokens_per_request": 9_353,
            "max_output_tokens_per_request": 4_096,
        })
        with self.assertRaises(AuthorizationRequiredError):
            validate_dispatch_authorization(
                self.config, self.plan, self._authorization(release),
            )


@unittest.skipUnless(STAGE2_RUN_DIR.is_dir(), "local Stage 2 evidence is unavailable")
class Gate2PersistedLineageTests(unittest.TestCase):
    @staticmethod
    def _copy_lineage(destination: Path) -> None:
        for name in (
            "manifest.json",
            "plan.json",
            "journal.jsonl",
            "stage2_authorization_package.json",
            "stage2_report.json",
        ):
            shutil.copy2(STAGE2_RUN_DIR / name, destination / name)

    def test_exact_real_stage2_lineage(self):
        lineage = verify_stage2_lineage()
        self.assertEqual(lineage["authorization_package_hash"], STAGE2_PACKAGE_HASH)
        self.assertEqual(lineage["report_hash"], STAGE2_REPORT_HASH)
        self.assertEqual(lineage["journal_tail"], STAGE2_JOURNAL_TAIL)
        self.assertEqual(lineage["journal_event_count"], STAGE2_EVENT_COUNT)
        self.assertTrue(lineage["gates"]["transport_preflight"]["passed"])
        self.assertTrue(lineage["gates"]["calibration"]["passed"])

    def test_manifest_config_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            self._copy_lineage(destination)
            path = destination / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["config"]["stage"] = "smoke"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(Gate2ReadinessError):
                verify_stage2_lineage(destination)

    def test_manifest_transport_lineage_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            self._copy_lineage(destination)
            path = destination / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["authorized_transport_hash"] = "0" * 64
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(Gate2ReadinessError):
                verify_stage2_lineage(destination)

    def test_report_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            self._copy_lineage(destination)
            path = destination / "stage2_report.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            report["smoke_dispatches"] = 1
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(Gate2ReadinessError):
                verify_stage2_lineage(destination)

    def test_journal_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp)
            self._copy_lineage(destination)
            path = destination / "journal.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            final = json.loads(lines[-1])
            final["note"] = "tampered"
            lines[-1] = json.dumps(final)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaises(Gate2ReadinessError):
                verify_stage2_lineage(destination)

    def test_readiness_claim_tamper_is_rejected(self):
        artifact = _persisted_legacy_readiness_fixture()
        artifact["dispatch_boundary"]["dispatch_enabled"] = True
        with self.assertRaises(Gate2ReadinessError):
            validate_readiness_artifact(artifact)


if __name__ == "__main__":
    unittest.main()
