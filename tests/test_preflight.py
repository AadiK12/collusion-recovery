import json
import unittest

from collusion_recovery.common import canonical, digest
from collusion_recovery.preflight import (
    MESSAGE_OVERLENGTH_FEEDBACK,
    PREFLIGHT_MESSAGE_IDENTITY,
    PREFLIGHT_VERDICT_IDENTITY,
    PreflightError,
    PreflightRequestFactory,
    preflight_assessment,
    preflight_inventory,
)
from collusion_recovery.providers.base import (
    ProviderResult,
    ProviderStatus,
    ProviderTiming,
    ProviderUsage,
    ResponseMode,
)


SETTINGS = {
    "reasoning": {"effort": "low", "mode": "standard"},
    "service_tier": "default",
}


def factory():
    return PreflightRequestFactory("gpt-6.1-sol", 4096, SETTINGS)


def provider_result(action, *, verdict="REJECT", status=ProviderStatus.OK,
                    complete_audit=True, known_not_completed=False):
    if action == "message":
        text = "Please verify the complete records independently."
        parsed = text
    else:
        parsed = {
            "verdict": verdict,
            "evidence_record_ids": ["RABCDEFGHIJ"],
            "brief_reason": "The claimed answer omits a qualifying source record.",
        }
        text = canonical(parsed)
    raw_usage = {
        "input_tokens": 100,
        "input_tokens_details": {
            "cached_tokens": 0,
            "cache_write_tokens": 0,
        },
        "output_tokens": 10,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 110,
    }
    raw_response = {
        "id": f"resp_{action}",
        "object": "response",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "model": "gpt-6.1-sol",
        "service_tier": "default",
        "output": [{
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text}],
        }],
        "usage": raw_usage,
    }
    return ProviderResult(
        status=status,
        raw_response_json=(canonical(raw_response) if complete_audit else None),
        output_text=text if status is ProviderStatus.OK else None,
        parsed_output_json=(canonical(parsed) if status is ProviderStatus.OK else None),
        refusal=None,
        error=None if status is ProviderStatus.OK else "known non-completion",
        provider_request_id=f"req_{action}" if complete_audit else None,
        returned_model="gpt-6.1-sol" if complete_audit else None,
        model_metadata_json=(canonical({"model": "gpt-6.1-sol", "service_tier": "default"})
                             if complete_audit else "{}"),
        usage=(ProviderUsage(input_tokens=100, cached_input_tokens=0,
                             cache_write_input_tokens=0,
                             output_tokens=10, reasoning_tokens=0, total_tokens=110,
                             raw_json=canonical(raw_usage))
               if complete_audit else None),
        timing=ProviderTiming(
            "2026-10-23T23:00:00+00:00", "2026-10-23T23:00:01+00:00", 1.0),
        known_not_completed=known_not_completed,
    )


class PreflightRequestTests(unittest.TestCase):
    def test_inventory_matches_the_exact_authorized_payloads(self):
        first = preflight_inventory(factory())
        second = preflight_inventory(factory())
        self.assertEqual(first, second)
        self.assertTrue(first["nonexperimental"])
        self.assertFalse(first["dispatch_performed"])
        self.assertEqual(first["logical_request_count"], 2)
        self.assertEqual(first["rendered_attempt_shape_count"], 3)
        self.assertEqual(first["initial_input_token_bound"], 18106)
        self.assertEqual(first["maximum_with_overlength_retry_input_token_bound"], 27170)
        self.assertEqual(first["initial_output_token_allowance"], 8192)
        self.assertEqual(first["maximum_with_overlength_retry_output_token_allowance"], 12288)
        self.assertEqual(first["inventory_hash"], digest({
            key: value for key, value in first.items() if key != "inventory_hash"
        }))

        records = {(row["action"], row["projected_shape"]): row
                   for row in first["requests"]}
        message = records[("message", "initial_attempt")]
        retry = records[("message", "overlength_retry")]
        verdict = records[("verdict", "initial_attempt")]
        self.assertEqual(message["provider_request"]["conservative_input_token_bound"], 8897)
        self.assertEqual(message["rendered_prompt"]["prompt_hash"],
                         "1fbac15d8d646508e8f208c129c1d69f4f344a68e9bb307325c6c706c3a57001")
        self.assertEqual(message["openai_responses_payload_utf8_bytes"], 7971)
        self.assertEqual(message["openai_responses_payload_hash"],
                         "217004e5a45f99a43602b5b6708aec9e3b7a25f2f53ee82d62a6e00b67b62cb8")
        self.assertEqual(retry["provider_request"]["conservative_input_token_bound"], 9064)
        self.assertEqual(retry["rendered_prompt"]["prompt_hash"],
                         "6336b898e12802ceff29cd7d77c7d917c5959c38aca3e4046a6baaffae75ad17")
        self.assertEqual(verdict["provider_request"]["conservative_input_token_bound"], 9209)
        self.assertEqual(verdict["rendered_prompt"]["prompt_hash"],
                         "d64be9ef0a9e41d9ff5c83597a9625f54660ef38f7a0ab2d2d0153b20125c8ed")
        self.assertEqual(verdict["openai_responses_payload_utf8_bytes"], 8359)
        self.assertEqual(verdict["openai_responses_payload_hash"],
                         "0ecd90a73825f735a0a9ca0ef70b69ce36765f3b7f0cea00e03f848f44d1058f")

        payload = verdict["openai_responses_payload"]
        self.assertFalse(payload["store"])
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["tool_choice"], "none")
        self.assertEqual(payload["truncation"], "disabled")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertNotIn("previous_response_id", payload)
        self.assertNotIn('"defect"', canonical(payload))
        fixture_rows = list(first["fixture_inventory"].values())
        self.assertEqual(len({row["namespace"] for row in fixture_rows}), 4)
        self.assertEqual(len({row["packet_hash"] for row in fixture_rows}), 4)
        self.assertTrue(first["fixture_inventory"]["message/peer"]["expected_correct"])
        self.assertFalse(first["fixture_inventory"]["verdict/peer"]["expected_correct"])

    def test_message_retry_uses_only_the_exact_runner_feedback_shape(self):
        request_factory = factory()
        initial, _ = request_factory.build("message")
        retry, rendered = request_factory.build(
            "message", attempt=1, prior_results=({"status": "overlength"},))
        transport_retry, transport_rendered = request_factory.build(
            "message", attempt=1,
            prior_results=({"status": "retryable_error", "known_not_completed": True},),
        )
        self.assertIs(initial.response_mode, ResponseMode.TEXT)
        self.assertEqual(initial.request_identity, retry.request_identity)
        self.assertNotEqual(initial.input_hash, retry.input_hash)
        self.assertIn(MESSAGE_OVERLENGTH_FEEDBACK, rendered.rendered_input_json)
        self.assertEqual(initial.input_hash, transport_retry.input_hash)
        self.assertNotIn(MESSAGE_OVERLENGTH_FEEDBACK, transport_rendered.rendered_input_json)
        with self.assertRaisesRegex(PreflightError, "only one"):
            request_factory.build(
                "message", attempt=2,
                prior_results=({"status": "overlength"}, {"status": "overlength"}),
            )
        with self.assertRaisesRegex(PreflightError, "only to messages"):
            request_factory.build(
                "verdict", attempt=1, prior_results=({"status": "overlength"},))

    def test_factory_allows_at_most_two_known_not_completed_transport_retries(self):
        request_factory = factory()
        retryable = {"status": "retryable_error", "known_not_completed": True}
        request, _ = request_factory.build(
            "verdict", attempt=2, prior_results=(retryable, retryable))
        self.assertEqual(request.attempt, 2)
        with self.assertRaisesRegex(PreflightError, "at most two"):
            request_factory.build(
                "message", attempt=3,
                prior_results=(retryable, retryable, retryable))
        with self.assertRaisesRegex(PreflightError, "explicitly known not completed"):
            request_factory.build(
                "verdict", attempt=1,
                prior_results=({"status": "retryable_error",
                                "known_not_completed": False},))


class PreflightAssessmentTests(unittest.TestCase):
    def test_pass_requires_bounded_message_reject_and_complete_audit(self):
        report = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [provider_result("message")],
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict")],
        })
        self.assertTrue(report["passed"])
        self.assertTrue(all(row["passed"] for row in report["observations"]))
        self.assertEqual(report["assessment_hash"], digest({
            key: value for key, value in report.items() if key != "assessment_hash"
        }))

    def test_retry_sequence_can_pass_but_wrong_verdict_or_missing_audit_cannot(self):
        retryable = provider_result(
            "message", status=ProviderStatus.RETRYABLE_ERROR,
            complete_audit=False, known_not_completed=True)
        overlength = {
            "status": "overlength",
            "known_not_completed": False,
            "output_text": "x" * 201,
        }
        passed = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [retryable, overlength, provider_result("message")],
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict")],
        })
        self.assertTrue(passed["passed"])

        wrong = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [provider_result("message")],
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict", verdict="ACCEPT")],
        })
        self.assertFalse(wrong["passed"])
        missing = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [provider_result("message", complete_audit=False)],
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict")],
        })
        self.assertFalse(missing["passed"])
        missing_cost_detail = provider_result("message").to_dict()
        missing_cost_detail["usage"]["cached_input_tokens"] = None
        incomplete_usage = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [missing_cost_detail],
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict")],
        })
        self.assertFalse(incomplete_usage["passed"])

    def test_assessment_binds_output_and_derivatives_to_raw_provider_response(self):
        message = provider_result("message").to_dict()
        message["output_text"] = "A forged but internally consistent derivative."
        message["parsed_output"] = message["output_text"]
        report = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [message],
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict")],
        })
        self.assertFalse(report["passed"])
        observation = next(
            row for row in report["observations"] if row["action"] == "message"
        )
        self.assertFalse(observation["provider_audit_complete"])

    def test_malformed_identity_set_and_unbounded_retries_fail_closed(self):
        with self.assertRaisesRegex(PreflightError, "exactly the two"):
            preflight_assessment({PREFLIGHT_MESSAGE_IDENTITY: [provider_result("message")]})
        rows = [{"status": "overlength"}, {"status": "overlength"},
                provider_result("message")]
        report = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: rows,
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict")],
        })
        self.assertFalse(report["passed"])
        message = next(row for row in report["observations"] if row["action"] == "message")
        self.assertFalse(message["retry_shape_valid"])

    def test_assessment_rejects_a_third_known_not_completed_transport_retry(self):
        retryable = provider_result(
            "verdict", status=ProviderStatus.RETRYABLE_ERROR,
            complete_audit=False, known_not_completed=True)
        two = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [provider_result("message")],
            PREFLIGHT_VERDICT_IDENTITY: [retryable, retryable, provider_result("verdict")],
        })
        self.assertTrue(two["passed"])
        verdict = next(row for row in two["observations"] if row["action"] == "verdict")
        self.assertEqual(verdict["known_not_completed_retryable_count"], 2)

        three = preflight_assessment({
            PREFLIGHT_MESSAGE_IDENTITY: [retryable, retryable, retryable,
                                         provider_result("message")],
            PREFLIGHT_VERDICT_IDENTITY: [provider_result("verdict")],
        })
        self.assertFalse(three["passed"])
        message = next(row for row in three["observations"] if row["action"] == "message")
        self.assertEqual(message["known_not_completed_retryable_count"], 3)
        self.assertFalse(message["retry_shape_valid"])


if __name__ == "__main__":
    unittest.main()
