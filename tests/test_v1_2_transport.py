"""Offline invariants for prospective v1.2 transport-recovery evidence."""

from dataclasses import replace
import json
import unittest

from collusion_recovery.common import canonical
from collusion_recovery.providers import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
    ProviderRequest,
    ProviderStatus,
    ResponseMode,
    TextTurn,
)


def provider_request():
    return ProviderRequest(
        request_identity="fallback-two-round/v1.2/test/request",
        model="approved-model-snapshot",
        turns=(TextTurn("developer", "Follow the fixed rule."),
               TextTurn("user", "Synthetic input.")),
        response_mode=ResponseMode.TEXT,
        max_output_tokens=64,
    )


class FakeHTTPResponse:
    def __init__(self, status_code, headers):
        self.status_code = status_code
        self.headers = headers


class InternalServerError(Exception):
    def __init__(self, *, status_code=500, request_id="req_500",
                 headers=None, body=None, response_status=None):
        super().__init__("provider status error")
        self.status_code = status_code
        self.request_id = request_id
        self.body = body if body is not None else {
            "error": {
                "type": "server_error",
                "code": "internal_error",
                "message": "Temporary provider error",
            },
        }
        self.response = FakeHTTPResponse(
            status_code if response_status is None else response_status,
            headers if headers is not None else {
                "x-request-id": request_id,
                "retry-after-ms": "1500",
            },
        )


class FakeSDKResponses:
    def __init__(self, error):
        self.error = error

    def create(self, **_kwargs):
        raise self.error


class FakeSDKClient:
    def __init__(self, error):
        self.responses = FakeSDKResponses(error)
        self.base_url = "https://api.openai.com/v1/"
        self.options = None

    def with_options(self, **kwargs):
        self.options = kwargs
        return self


def complete(error):
    client = FakeSDKClient(error)
    transport = OpenAISDKTransport(
        client, timeout_seconds=30, sdk_version="9.9.9-test")
    result = OpenAIResponsesProvider(transport).complete(provider_request())
    return client, result


class V12TransportEvidenceTests(unittest.TestCase):
    def test_eligible_500_remains_ambiguous_and_records_only_closed_evidence(self):
        error = InternalServerError(headers={
            "X-Request-ID": "req_500",
            "Retry-After-Ms": "1500",
            "Authorization": "Bearer sk-testsecret123456789",
            "X-Arbitrary-Debug": "must-not-be-persisted",
        })
        client, result = complete(error)

        # Provider classification stays backward-compatible.  A versioned
        # runner may separately inspect the closed evidence record.
        self.assertIs(result.status, ProviderStatus.AMBIGUOUS_TIMEOUT)
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.known_not_completed)
        self.assertEqual(client.options, {"max_retries": 0, "timeout": 30.0})
        self.assertEqual(result.provider_request_id, "req_500")
        self.assertIsNone(result.output_text)
        self.assertIsNone(result.usage)

        evidence = result.transport_evidence
        self.assertEqual(evidence["schema_version"], "v1")
        self.assertEqual(evidence["failure_class"], "sdk_http_error")
        self.assertEqual(evidence["http_status"], 500)
        self.assertEqual(evidence["response_http_status"], 500)
        self.assertEqual(evidence["exception_type"], "InternalServerError")
        self.assertEqual(evidence["provider_request_id"], "req_500")
        self.assertEqual(evidence["x_request_id"], "req_500")
        self.assertTrue(evidence["request_id_consistent"])
        self.assertEqual(evidence["error_type"], "server_error")
        self.assertEqual(evidence["error_code"], "internal_error")
        self.assertTrue(evidence["response_received"])
        self.assertTrue(evidence["output_absent"])
        self.assertTrue(evidence["usage_absent"])
        self.assertTrue(evidence["retry_after_present"])
        self.assertEqual(evidence["retry_after_seconds"], 1.5)
        self.assertEqual(evidence["retry_after_source"], "retry-after-ms")
        self.assertTrue(evidence["retry_eligible"])

        persisted = canonical(result.to_dict())
        self.assertNotIn("Authorization", persisted)
        self.assertNotIn("X-Arbitrary-Debug", persisted)
        self.assertNotIn("must-not-be-persisted", persisted)
        self.assertNotIn("sk-testsecret", persisted)

    def test_retry_after_normalization_never_persists_raw_values(self):
        _, both = complete(InternalServerError(headers={
            "x-request-id": "req_500",
            "retry-after": "2",
            "retry-after-ms": "3500",
        }))
        self.assertEqual(both.transport_evidence["retry_after_seconds"], 3.5)
        self.assertEqual(both.transport_evidence["retry_after_source"], "both-max")

        _, malformed = complete(InternalServerError(headers={
            "x-request-id": "req_500",
            "retry-after": "Bearer sk-testsecret123456789",
        }))
        evidence = malformed.transport_evidence
        self.assertTrue(evidence["retry_after_present"])
        self.assertIsNone(evidence["retry_after_seconds"])
        self.assertIsNone(evidence["retry_after_source"])
        self.assertNotIn("sk-testsecret", canonical(malformed.to_dict()))

    def test_long_retry_after_seconds_and_milliseconds_are_preserved(self):
        cases = {
            "delay-seconds": ({"retry-after": "000172801"}, 172801.0,
                              "retry-after"),
            "milliseconds": ({"retry-after-ms": "172801250"}, 172801.25,
                             "retry-after-ms"),
        }
        for label, (retry_header, expected, source) in cases.items():
            with self.subTest(label=label):
                _, result = complete(InternalServerError(headers={
                    "x-request-id": "req_500",
                    **retry_header,
                }))
                evidence = result.transport_evidence
                self.assertTrue(evidence["retry_eligible"])
                self.assertTrue(evidence["retry_after_present"])
                self.assertEqual(evidence["retry_after_source"], source)
                self.assertEqual(evidence["retry_after_seconds"], expected)
                # The closed persisted evidence schema must accept the same
                # long normalized delay rather than dropping it on round-trip.
                self.assertEqual(
                    result.to_dict()["transport_evidence"]["retry_after_seconds"],
                    evidence["retry_after_seconds"],
                )

    def test_http_date_is_present_but_deliberately_not_normalized(self):
        _, result = complete(InternalServerError(headers={
            "x-request-id": "req_500",
            "retry-after": "Wed, 31 Dec 2099 23:59:59 GMT",
        }))
        evidence = result.transport_evidence
        self.assertTrue(evidence["retry_eligible"])
        self.assertTrue(evidence["retry_after_present"])
        self.assertIsNone(evidence["retry_after_seconds"])
        self.assertIsNone(evidence["retry_after_source"])

    def test_retry_candidate_requires_exact_status_ids_and_absence_evidence(self):
        missing_response_status = InternalServerError()
        del missing_response_status.response.status_code
        string_response_status = InternalServerError()
        string_response_status.response.status_code = "500"
        cases = {
            "unsupported-status": InternalServerError(status_code=501),
            "missing-header-id": InternalServerError(headers={}),
            "mismatched-id": InternalServerError(headers={"x-request-id": "req_other"}),
            "output-key-present": InternalServerError(body={
                "error": {"type": "server_error", "code": "internal_error"},
                "output": [],
            }),
            "usage-key-present": InternalServerError(body={
                "error": {"type": "server_error", "code": "internal_error"},
                "usage": None,
            }),
            "response-status-mismatch": InternalServerError(response_status=502),
            "response-status-missing": missing_response_status,
            "response-status-string": string_response_status,
        }
        for label, error in cases.items():
            with self.subTest(label=label):
                _, result = complete(error)
                self.assertIs(result.status, ProviderStatus.AMBIGUOUS_TIMEOUT)
                self.assertFalse(result.transport_evidence["retry_eligible"])
        self.assertFalse(
            complete(cases["mismatched-id"])[1]
            .transport_evidence["request_id_consistent"]
        )

    def test_closed_schema_and_cross_field_invariants_reject_forgery(self):
        _, result = complete(InternalServerError())
        evidence = result.transport_evidence

        with self.assertRaisesRegex(ValueError, "complete closed schema"):
            replace(
                result,
                transport_evidence_json=canonical({**evidence, "headers": {"secret": "x"}}),
            )

        mismatched = {**evidence, "provider_request_id": "req_different",
                      "request_id_consistent": False, "retry_eligible": False}
        with self.assertRaisesRegex(ValueError, "disagrees with provider result"):
            replace(result, transport_evidence_json=canonical(mismatched))

        unsupported = {**evidence, "http_status": 501, "retry_eligible": True}
        with self.assertRaisesRegex(ValueError, "unsupported by the recorded evidence"):
            replace(result, transport_evidence_json=canonical(unsupported))

        # A persisted eligible row cannot claim output absence falsely.
        with self.assertRaisesRegex(ValueError, "persisted error body"):
            replace(
                result,
                raw_response_json=canonical({**result.raw_response, "output": []}),
                transport_evidence_json=canonical(evidence),
            )

    def test_429_keeps_historical_retryable_classification_without_5xx_eligibility(self):
        _, result = complete(InternalServerError(status_code=429))
        self.assertIs(result.status, ProviderStatus.RETRYABLE_ERROR)
        self.assertTrue(result.known_not_completed)
        self.assertFalse(result.transport_evidence["retry_eligible"])


if __name__ == "__main__":
    unittest.main()
