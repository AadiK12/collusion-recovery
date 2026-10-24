from contextlib import ExitStack
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from collusion_recovery.budget import DispatchReleaseLimits
from collusion_recovery.common import canonical, digest
from collusion_recovery.gates import _empirical_audit_complete
from collusion_recovery.live_config import LiveConfig, load_live_config
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    DispatchAuthorizationArtifact,
    DurableRun,
    authorization_binding,
    build_request_plan,
    validate_dispatch_authorization,
)
from collusion_recovery.stage2 import (
    STAGE2_CONFIG,
    STAGE2_CONFIRMED_AT,
    STAGE2_PREFLIGHT_INVENTORY_HASH,
    STAGE2_RUN_DIR,
    Stage2Error,
    _authorization,
    _load_local_openai_key,
    _usage_summary,
    run_stage2,
    stage2_offline_package,
)
from collusion_recovery.stage2_release_state import (
    STAGE2_DISPATCH_ATTEMPT_COUNT,
    STAGE2_PACKAGE_HASH,
    STAGE2_REPORT_HASH,
    load_stage2_consumed_receipt,
)


def _forbid_network():
    stack = ExitStack()
    for name in (
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
    ):
        stack.enter_context(
            patch(name, side_effect=AssertionError("network access forbidden in Stage 2 tests"))
        )
    return stack


class Stage2OfflineContractTests(unittest.TestCase):
    def test_consumed_v1_package_cannot_be_regenerated_or_relabelled(self):
        config = load_live_config(STAGE2_CONFIG)
        with patch("collusion_recovery.stage2.build_request_plan") as planner, \
                patch("collusion_recovery.stage2.OpenAIResponsesProvider.build_payload") \
                as payload_builder:
            with self.assertRaisesRegex(
                    Stage2Error, "cannot be regenerated or relabeled"):
                stage2_offline_package(config)
        planner.assert_not_called()
        payload_builder.assert_not_called()

    def test_empirical_plan_starts_with_two_preflight_then_twenty_calibration(self):
        config = load_live_config(STAGE2_CONFIG)
        plan = build_request_plan(config)

        self.assertEqual(
            [(request.identity, request.action) for request in plan.requests[:2]],
            [("preflight/v1/message", "message"),
             ("preflight/v1/verdict", "verdict")],
        )
        self.assertTrue(all(request.stage == request.phase == "preflight"
                            for request in plan.requests[:2]))
        self.assertEqual(len([request for request in plan.requests
                              if request.stage == "preflight"]), 2)
        self.assertEqual([request.metadata["fixture_index"]
                          for request in plan.requests[2:22]], list(range(20)))
        self.assertTrue(all(request.stage == request.phase == "calibration"
                            and request.action == "verdict"
                            for request in plan.requests[2:22]))
        self.assertEqual(len([request for request in plan.requests
                              if request.stage == "calibration"]), 20)
        self.assertEqual(plan.requests[22].stage, "smoke")


class Stage2CredentialTests(unittest.TestCase):
    @staticmethod
    def _dummy_secret(marker):
        # Construct at runtime so the repository never contains a credential-
        # shaped literal, even though the loader exercises the real grammar.
        return "sk" + "-" + marker * 24

    def test_loader_rejects_nonprivate_mode_without_exposing_value(self):
        secret = self._dummy_secret("M")
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / ".env.local"
            env_file.write_text(f"OPENAI_KEY={secret}\n", encoding="utf-8")
            env_file.chmod(0o640)
            with patch("collusion_recovery.stage2.STAGE2_ENV_FILE", env_file), \
                    patch("collusion_recovery.stage2._git_ignored_and_untracked",
                          return_value=True):
                with self.assertRaises(Stage2Error) as caught:
                    _load_local_openai_key(env_file)
        self.assertIn("owner-only regular file", str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))

    def test_loader_rejects_symlink_without_exposing_value(self):
        secret = self._dummy_secret("S")
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "credential-target"
            target.write_text(f"OPENAI_KEY={secret}\n", encoding="utf-8")
            target.chmod(0o600)
            env_file = Path(temp) / ".env.local"
            env_file.symlink_to(target)
            with patch("collusion_recovery.stage2.STAGE2_ENV_FILE", env_file), \
                    patch("collusion_recovery.stage2._git_ignored_and_untracked",
                          return_value=True):
                with self.assertRaises(Stage2Error) as caught:
                    _load_local_openai_key(env_file)
        self.assertIn("not a symlink", str(caught.exception))
        self.assertNotIn(secret, str(caught.exception))

    def test_loader_rejects_duplicate_assignment_without_exposing_values(self):
        first = self._dummy_secret("A")
        second = self._dummy_secret("B")
        with tempfile.TemporaryDirectory() as temp:
            env_file = Path(temp) / ".env.local"
            env_file.write_text(
                f"OPENAI_KEY={first}\nexport OPENAI_KEY='{second}'\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            with patch("collusion_recovery.stage2.STAGE2_ENV_FILE", env_file), \
                    patch("collusion_recovery.stage2._git_ignored_and_untracked",
                          return_value=True):
                with self.assertRaises(Stage2Error) as caught:
                    _load_local_openai_key(env_file)
        error = str(caught.exception)
        self.assertIn("exactly one valid OPENAI_KEY", error)
        self.assertNotIn(first, error)
        self.assertNotIn(second, error)


class Stage2ControllerTests(unittest.TestCase):
    def test_consumed_receipt_is_exact_and_self_hashed(self):
        receipt = load_stage2_consumed_receipt()
        body = dict(receipt)
        receipt_hash = body.pop("receipt_hash")

        self.assertEqual(receipt_hash, digest(body))
        self.assertTrue(receipt["consumed"])
        self.assertEqual(receipt["authorization_package_hash"], STAGE2_PACKAGE_HASH)
        self.assertEqual(receipt["report_hash"], STAGE2_REPORT_HASH)
        self.assertEqual(
            receipt["dispatch_attempt_count"], STAGE2_DISPATCH_ATTEMPT_COUNT)

    def test_consumed_authorization_cannot_be_recreated_or_validated(self):
        config = load_live_config(STAGE2_CONFIG)
        plan = build_request_plan(config)
        with self.assertRaisesRegex(Stage2Error, "consumed"):
            _authorization(config, plan, "0" * 64, {})

        historical_shape = DispatchAuthorizationArtifact(
            authorization_id=config.authorization.authorization_id,
            confirmed_at=STAGE2_CONFIRMED_AT,
            binding_hash="0" * 64,
            approved_budget_usd="1.78",
            transport_hash="0" * 64,
            max_stage="calibration",
            max_block=0,
            reviewed_event_hash="initial",
        )
        with self.assertRaisesRegex(AuthorizationRequiredError, "consumed"):
            validate_dispatch_authorization(config, plan, historical_shape)

    def test_consumed_authorization_cannot_be_replayed_under_a_new_run_id(self):
        original = load_live_config(STAGE2_CONFIG)
        values = original.to_dict()
        values["run_id"] = "renamed-copy-of-consumed-stage2"
        copied = LiveConfig.from_mapping(values)
        copied_plan = build_request_plan(copied)
        confirmed_at = STAGE2_CONFIRMED_AT
        release = DispatchReleaseLimits.from_mapping({
            "max_dispatch_attempts": 1,
            "max_total_input_tokens": 10_000,
            "max_total_output_tokens": 4_096,
            "max_cost_usd": "0.10",
            "max_in_flight": 1,
            "max_input_tokens_per_request": 10_000,
            "max_output_tokens_per_request": 4_096,
        })
        binding = authorization_binding(
            copied,
            copied_plan,
            confirmed_at=confirmed_at,
            transport_hash="0" * 64,
            approved_budget_usd="0.10",
            max_stage="preflight",
            max_block=0,
            reviewed_event_hash="initial",
            release_limits=release,
            preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
        )
        replay = DispatchAuthorizationArtifact(
            authorization_id=copied.authorization.authorization_id,
            confirmed_at=confirmed_at,
            binding_hash=binding,
            approved_budget_usd="0.10",
            transport_hash="0" * 64,
            max_stage="preflight",
            max_block=0,
            reviewed_event_hash="initial",
            release_limits=release,
            preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
        )

        with self.assertRaisesRegex(AuthorizationRequiredError, "consumed"):
            validate_dispatch_authorization(copied, copied_plan, replay)

    def test_consumed_run_id_cannot_be_reused_with_a_fresh_authorization(self):
        original = load_live_config(STAGE2_CONFIG)
        values = original.to_dict()
        values["authorization"]["authorization_id"] = "fresh-versioned-test"
        copied = LiveConfig.from_mapping(values)
        copied_plan = build_request_plan(copied)
        release = DispatchReleaseLimits.from_mapping({
            "max_dispatch_attempts": copied.limits.max_requests,
            "max_total_input_tokens": copied.limits.max_total_input_tokens,
            "max_total_output_tokens": copied.limits.max_total_output_tokens,
            "max_cost_usd": "1.78",
            "max_in_flight": copied.limits.max_in_flight,
            "max_input_tokens_per_request": (
                copied.limits.max_input_tokens_per_request
            ),
            "max_output_tokens_per_request": (
                copied.limits.max_output_tokens_per_request
            ),
        })
        binding = authorization_binding(
            copied,
            copied_plan,
            confirmed_at=STAGE2_CONFIRMED_AT,
            transport_hash="0" * 64,
            approved_budget_usd="1.78",
            max_stage="calibration",
            max_block=0,
            reviewed_event_hash="initial",
            release_limits=release,
            preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
        )
        fresh = DispatchAuthorizationArtifact(
            authorization_id=copied.authorization.authorization_id,
            confirmed_at=STAGE2_CONFIRMED_AT,
            binding_hash=binding,
            approved_budget_usd="1.78",
            transport_hash="0" * 64,
            max_stage="calibration",
            max_block=0,
            reviewed_event_hash="initial",
            release_limits=release,
            preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
        )

        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "must-not-be-created"
            with self.assertRaisesRegex(
                    AuthorizationRequiredError, "cannot be reused"):
                DurableRun.open(
                    destination,
                    copied,
                    copied_plan,
                    dispatch_authorization=fresh,
                    execution_controller="gate2_smoke_successor_v1",
                )
            self.assertFalse(destination.exists())

    def test_fresh_clone_missing_run_fails_before_credentials_client_or_network(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "stage2-run"
            with patch("collusion_recovery.stage2.STAGE2_RUN_DIR", run_dir), \
                    patch("collusion_recovery.stage2._load_local_openai_key") as loader, \
                    patch("openai.OpenAI") as client_factory, \
                    _forbid_network():
                with self.assertRaisesRegex(Stage2Error, "cannot be recreated"):
                    run_stage2(
                        config_path=STAGE2_CONFIG,
                        run_dir=run_dir,
                        env_file=Path(temp) / "must-not-be-read.env",
                    )
            loader.assert_not_called()
            client_factory.assert_not_called()
            self.assertFalse(run_dir.exists())

    def test_inspection_on_fresh_clone_is_no_dispatch_and_non_replayable(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "stage2-run"
            with patch("collusion_recovery.stage2.STAGE2_RUN_DIR", run_dir), \
                    patch("collusion_recovery.stage2._load_local_openai_key") as loader, \
                    patch("openai.OpenAI") as client_factory, \
                    _forbid_network():
                result = run_stage2(
                    config_path=STAGE2_CONFIG,
                    run_dir=run_dir,
                    env_file=Path(temp) / "must-not-be-read.env",
                    inspect_only=True,
                )
            loader.assert_not_called()
            client_factory.assert_not_called()
            self.assertEqual(result["status"], "inspection_complete_no_dispatch")
            self.assertEqual(result["release_state"], "consumed_non_replayable")
            self.assertFalse(result["authorization_replayable"])
            self.assertFalse(result["historical_run_present"])
            self.assertEqual((result["paid_calls"], result["network_calls"]), (0, 0))
            self.assertFalse(run_dir.exists())

    @unittest.skipUnless(STAGE2_RUN_DIR.is_dir(), "local Stage 2 evidence is unavailable")
    def test_completed_run_is_verified_without_credentials_client_or_network(self):
        with patch("collusion_recovery.stage2._load_local_openai_key") as loader, \
                patch("openai.OpenAI") as client_factory, \
                _forbid_network():
            report = run_stage2()
        loader.assert_not_called()
        client_factory.assert_not_called()
        self.assertEqual(report["report_hash"], STAGE2_REPORT_HASH)
        self.assertEqual(report["usage"]["attempt_count"], 22)
        self.assertTrue(report["stopped_before_smoke"])

    def test_live_release_rejects_alternate_run_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            alternate = Path(temp) / "alternate"
            with self.assertRaisesRegex(Stage2Error, "frozen run directory"):
                run_stage2(config_path=STAGE2_CONFIG, run_dir=alternate)
            self.assertFalse(alternate.exists())

    def test_usage_summary_never_invents_missing_cached_or_reasoning_details(self):
        class Run:
            @staticmethod
            def result_events():
                return [{
                    "identity": "preflight/v1/message",
                    "attempt": 0,
                    "known_not_completed": False,
                    "provider_result": {
                        "provider_request_id": "request-test",
                        "timing": {"elapsed_seconds": 1.0},
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                            "cached_input_tokens": None,
                            "cache_write_input_tokens": 0,
                            "reasoning_tokens": None,
                        },
                    },
                }]

        class Ledger:
            @staticmethod
            def snapshot():
                return {"consumed": {"cost_usd": "0.000070"}}

        summary = _usage_summary(Run(), Ledger())
        self.assertFalse(summary["complete_provider_usage"])
        self.assertIsNone(summary["cached_input_tokens"])
        self.assertIsNone(summary["reasoning_tokens"])
        self.assertIsNone(summary["provider_list_price_cost_usd"])

    def test_calibration_audit_requires_exact_cost_usage_details(self):
        verdict = {
            "verdict": "REJECT",
            "evidence_record_ids": [],
            "brief_reason": "Synthetic audit fixture.",
        }
        output_text = canonical(verdict)
        raw_usage = {
            "input_tokens": 10,
            "input_tokens_details": {
                "cached_tokens": 0,
                "cache_write_tokens": 0,
            },
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 2},
            "total_tokens": 15,
        }
        normalized_usage = {
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_tokens": 2,
            "total_tokens": 15,
            "raw": raw_usage,
        }
        run = SimpleNamespace(config=SimpleNamespace(
            source="empirical-live",
            model_settings={"service_tier": "default"},
        ), request_payload=lambda _request_hash: {
            "response_mode": "verdict",
            "message_max_codepoints": 200,
            "message_retry_history_max_utf8_bytes": 2048,
        })
        row = {
            "request_hash": "request-hash-test",
            "status": "ok",
            "ambiguous": False,
            "known_not_completed": False,
            "usage": normalized_usage,
            "provider_result": {
                "status": "ok",
                "raw_response": {
                    "id": "response-test",
                    "object": "response",
                    "status": "completed",
                    "error": None,
                    "incomplete_details": None,
                    "model": "gpt-6.1-sol-test-snapshot",
                    "service_tier": "default",
                    "output": [{
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{
                            "type": "output_text",
                            "text": output_text,
                        }],
                    }],
                    "usage": raw_usage,
                },
                "output_text": output_text,
                "parsed_output": verdict,
                "refusal": None,
                "error": None,
                "provider_request_id": "request-test",
                "returned_model": "gpt-6.1-sol-test-snapshot",
                "model_metadata": {
                    "model": "gpt-6.1-sol-test-snapshot",
                    "service_tier": "default",
                },
                "usage": normalized_usage,
                "timing": {
                    "started_at": "2026-10-23T23:00:00+00:00",
                    "ended_at": "2026-10-23T23:00:01+00:00",
                    "elapsed_seconds": 1.0,
                },
                "ambiguous": False,
                "known_not_completed": False,
            },
            "budget_accounting": {
                "accounting": "reported_usage",
                "reservation_breached": False,
            },
        }
        self.assertTrue(_empirical_audit_complete(run, row))
        row["provider_result"]["usage"]["cached_input_tokens"] = None
        self.assertFalse(_empirical_audit_complete(run, row))


if __name__ == "__main__":
    unittest.main()
