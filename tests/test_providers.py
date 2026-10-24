from contextlib import ExitStack
from dataclasses import replace
import json
from unittest.mock import patch
import unittest

from collusion_recovery.common import canonical, digest
from collusion_recovery.fixtures import generate_fixture
from collusion_recovery.history import History
from collusion_recovery.prompts import (
    PromptLeakageError,
    provider_request_from_agent_request,
    render_agent_prompt,
)
from collusion_recovery.providers import (
    FakeOutcome,
    FakeProvider,
    OpenAIResponsesProvider,
    OpenAISDKTransport,
    ProviderRequest,
    ProviderResult,
    ProviderStatus,
    ProviderTiming,
    ProviderUsage,
    ResponseMode,
    TextTurn,
    TransportFailure,
    TransportResponse,
    VERDICT_SCHEMA,
)
from collusion_recovery.providers.openai import (
    provider_result_matches_raw_response,
)
from collusion_recovery.visibility import AgentRequest, request_for


def no_network():
    stack = ExitStack()
    for name in ("socket.socket", "socket.create_connection", "urllib.request.urlopen",
                 "http.client.HTTPConnection.connect", "http.client.HTTPSConnection.connect"):
        stack.enter_context(patch(name, side_effect=AssertionError("Network access forbidden")))
    return stack


def provider_request(mode=ResponseMode.VERDICT, **changes):
    values = {
        "request_identity": "pilot/block-0/A/0/alice/verdict",
        "model": "approved-model-snapshot",
        "turns": (TextTurn("developer", "Follow the verification rule."),
                  TextTurn("user", "Visible synthetic packet.")),
        "response_mode": mode,
        "max_output_tokens": 200,
        "model_settings_json": canonical({"temperature": 0}),
    }
    values.update(changes)
    return ProviderRequest(**values)


def response_body(text=None, *, content=None, status="completed", error=None):
    if content is None:
        content = [{"type": "output_text", "text": text}]
    return {
        "id": "resp_body_id",
        "object": "response",
        "status": status,
        "error": error,
        "incomplete_details": None,
        "model": "approved-model-snapshot-2026-10-01",
        "service_tier": "default",
        "created_at": 1792760400,
        "output": [{"type": "reasoning", "content": []},
                   {"type": "message", "role": "assistant", "status": "completed",
                    "content": content}],
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {
                "cached_tokens": 20,
                "cache_write_tokens": 3,
            },
            "output_tokens": 18,
            "output_tokens_details": {"reasoning_tokens": 4},
            "total_tokens": 138,
        },
    }


class ProviderResultInvariantTests(unittest.TestCase):
    def test_known_not_completed_rejects_even_zero_usage_object(self):
        usage = ProviderUsage(
            input_tokens=0,
            output_tokens=0,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
        )
        with self.assertRaisesRegex(ValueError, "any usage object"):
            ProviderResult(
                status=ProviderStatus.RETRYABLE_ERROR,
                raw_response_json=None,
                output_text=None,
                parsed_output_json=None,
                refusal=None,
                error="known transport failure",
                provider_request_id=None,
                returned_model=None,
                model_metadata_json="{}",
                usage=usage,
                timing=ProviderTiming(
                    "2026-10-24T00:00:00-07:00",
                    "2026-10-24T00:00:00-07:00",
                    0.0,
                ),
                ambiguous=False,
                known_not_completed=True,
            )


class QueueTransport:
    def __init__(self, *items):
        self.items = list(items)
        self.payloads = []

    def send(self, payload):
        self.payloads.append(json.loads(canonical(payload)))
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeSDKResponse:
    def __init__(self, body, request_id="req_from_sdk"):
        self.body = body
        self._request_id = request_id

    def model_dump_json(self):
        return canonical(self.body)


class FakeSDKResponses:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(json.loads(canonical(kwargs)))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeSDKClient:
    def __init__(self, result):
        self.responses = FakeSDKResponses(result)
        self.base_url = "https://api.openai.com/v1/"
        self.options = None

    def with_options(self, **kwargs):
        self.options = kwargs
        return self


class PromptRenderingTests(unittest.TestCase):
    def setUp(self):
        self.own = generate_fixture("prompt/own").packet
        self.peer_fixture = generate_fixture("prompt/peer", "omit")
        self.peer = self.peer_fixture.packet

    def test_canonical_renderer_is_deterministic_and_logs_exact_input(self):
        history = History().append({
            "visible_input": {
                "agent": "alice",
                "phase": "formation",
                "peer_message": {"sender": "bob", "text": "approve mine", "delivered": True},
            },
            "instructions": ["same rule"],
            "verdicts": {"alice": {"raw_response": "REJECT", "status": "ok"},
                         "bob": {"raw_response": "ACCEPT", "status": "ok"}},
            "runner_feedback": "Episode complete.",
        })
        view = request_for(
            history, self.own, self.peer, "alice", "evaluation", "constrained",
            communication={"peer_message": {"sender": "bob", "text": "approve", "delivered": True}},
        )
        first, second = render_agent_prompt(view), render_agent_prompt(view)
        self.assertEqual(first, second)
        self.assertEqual(first.rendered_input_json,
                         canonical([turn.to_dict() for turn in first.turns]))
        self.assertEqual(first.prompt_hash, digest(json.loads(first.rendered_input_json)))
        self.assertIn("[INSTRUCTION_PRIORITY: DEVELOPER]", first.turns[0].content)
        self.assertIn('"tag":"PEER_MESSAGE"', first.turns[1].content)
        self.assertIn('"tag":"OWN_VERDICT"', first.turns[1].content)
        self.assertIn('"tag":"PEER_VERDICT"', first.turns[1].content)
        self.assertIn('"tag":"RUNNER_EVENT"', first.turns[1].content)
        self.assertIn("approve mine", first.turns[1].content)
        self.assertIn("Episode complete.", first.turns[1].content)

    def test_constrained_prompt_has_claim_but_no_records_or_oracle_label(self):
        view = request_for(History(), self.own, self.peer, "alice", "evaluation", "constrained")
        rendered = render_agent_prompt(view)
        omitted = next(iter(set(self.peer.correct_answer) - set(self.peer.claimed_answer)))
        self.assertIn(self.peer.claimed_answer[0], rendered.rendered_input_json)
        self.assertNotIn(omitted, rendered.rendered_input_json)
        for marker in ("oracle_answer", '"defect"', '"packet_correct"'):
            self.assertNotIn(marker, rendered.rendered_input_json)

    def test_renderer_rejects_evaluator_objects_and_hidden_keys(self):
        with self.assertRaises(TypeError):
            render_agent_prompt(self.peer_fixture)
        payload = request_for(History(), self.own, self.peer, "alice",
                              "evaluation", "full").payload()
        payload["current"]["oracle_answer"] = list(self.peer.correct_answer)
        with self.assertRaises(PromptLeakageError):
            render_agent_prompt(AgentRequest(canonical(payload)))
        with self.assertRaises(PromptLeakageError):
            render_agent_prompt(AgentRequest(canonical(self.peer_fixture.evaluator_record())))
        payload = request_for(History(), self.own, self.peer, "alice",
                              "evaluation", "full").payload()
        payload["current"]["Oracle-Answer"] = list(self.peer.correct_answer)
        with self.assertRaises(PromptLeakageError):
            render_agent_prompt(AgentRequest(canonical(payload)))

    def test_provider_bridge_selects_plain_text_or_strict_verdict(self):
        message_view = request_for(History(), self.own, self.peer, "alice", "formation",
                                   "full", action="message")
        verdict_view = request_for(History(), self.own, self.peer, "alice", "formation", "full")
        message, _ = provider_request_from_agent_request("message-id", "model", message_view, 50)
        verdict, _ = provider_request_from_agent_request("verdict-id", "model", verdict_view, 100)
        self.assertIs(message.response_mode, ResponseMode.TEXT)
        self.assertIs(verdict.response_mode, ResponseMode.VERDICT)


class OpenAIAdapterTests(unittest.TestCase):
    def test_strict_verdict_schema_uses_only_supported_string_constraints(self):
        self.assertEqual(VERDICT_SCHEMA["properties"]["brief_reason"],
                         {"type": "string"})
        self.assertEqual(
            VERDICT_SCHEMA["properties"]["evidence_record_ids"]["items"],
            {"type": "string", "pattern": "^R[A-Z0-9]{10}$"},
        )
        self.assertNotIn("maxLength", canonical(VERDICT_SCHEMA))

    def test_injected_sdk_transport_uses_preconfigured_client_only(self):
        text = canonical({"verdict": "ACCEPT", "evidence_record_ids": ["RABCDEFGHIJ"],
                          "brief_reason": "The complete records support the claim."})
        client = FakeSDKClient(FakeSDKResponse(response_body(text)))
        transport = OpenAISDKTransport(client, timeout_seconds=30, sdk_version="9.9.9-test")
        provider = OpenAIResponsesProvider(transport)
        with no_network():
            result = provider.complete(provider_request())
        self.assertIs(result.status, ProviderStatus.OK)
        self.assertEqual(result.provider_request_id, "req_from_sdk")
        self.assertEqual(len(client.responses.calls), 1)
        self.assertFalse(client.responses.calls[0]["store"])
        self.assertNotIn("previous_response_id", client.responses.calls[0])
        self.assertEqual(client.options, {"max_retries": 0, "timeout": 30.0})
        self.assertEqual(transport.audit_metadata["sdk_max_retries"], 0)
        self.assertEqual(transport.audit_metadata["sdk_package"], "openai")
        self.assertEqual(transport.audit_metadata["sdk_version"], "9.9.9-test")

    def test_sdk_transport_requires_explicit_timeout_options_and_official_base(self):
        client = FakeSDKClient(FakeSDKResponse(response_body("unused")))
        with self.assertRaises(ValueError):
            OpenAISDKTransport(client, timeout_seconds=0, sdk_version="9.9.9-test")
        client.base_url = "https://example.test/v1/"
        with self.assertRaises(ValueError):
            OpenAISDKTransport(client, timeout_seconds=30, sdk_version="9.9.9-test")
        client.base_url = "https://api.openai.com/v1/"
        client.with_options = None
        with self.assertRaises(ValueError):
            OpenAISDKTransport(client, timeout_seconds=30, sdk_version="9.9.9-test")
        for unsafe in (
                "https://api.openai.com:444/v1/sk-proj-LEAKME12345678",
                "https://api.openai.com/v1/extra",
                "https://api.openai.com:443/v1"):
            client.base_url = unsafe
            client.with_options = lambda **_kwargs: client
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                OpenAISDKTransport(client, timeout_seconds=30, sdk_version="9.9.9-test")

    def test_transport_failure_raw_body_is_sanitized_and_persisted(self):
        failure = TransportFailure(
            ProviderStatus.AUTHENTICATION_ERROR,
            "Invalid authorization=plain-secret and Bearer sk-testsecret12345",
            request_id="req_failed",
            raw_response={
                "error": {"code": "invalid_api_key", "message": "Bearer sk-testsecret12345"},
                "headers": {"Authorization": "plain-secret", "openaiApiKey": "also-secret"},
            },
        )
        result = OpenAIResponsesProvider(QueueTransport(failure)).complete(provider_request())
        self.assertIs(result.status, ProviderStatus.AUTHENTICATION_ERROR)
        self.assertEqual(result.provider_request_id, "req_failed")
        self.assertEqual(result.raw_response["error"]["code"], "invalid_api_key")
        self.assertEqual(result.raw_response["headers"]["Authorization"], "[REDACTED]")
        self.assertEqual(result.raw_response["headers"]["openaiApiKey"], "[REDACTED]")
        self.assertNotIn("plain-secret", result.error)
        self.assertNotIn("sk-testsecret", result.raw_response_json)

    def test_conservative_input_bound_covers_utf8_schema_settings_and_framing(self):
        request = provider_request(turns=(TextTurn("developer", "a"),
                                          TextTurn("user", "🧪"),))
        material = {
            "turns": [turn.to_dict() for turn in request.turns],
            "model_settings": request.model_settings,
            "response_format": VERDICT_SCHEMA,
        }
        expected = 1024 + len(canonical(material).encode("utf-8"))
        self.assertEqual(request.conservative_input_token_bound, expected)
        self.assertEqual(request.to_dict()["conservative_input_token_bound"], expected)

    def test_verdict_schema_and_parser_bound_history_growth(self):
        self.assertEqual(VERDICT_SCHEMA["properties"]["evidence_record_ids"]["maxItems"], 32)
        self.assertEqual(
            VERDICT_SCHEMA["properties"]["evidence_record_ids"]["items"]["pattern"],
            "^R[A-Z0-9]{10}$")
        too_many = canonical({"verdict": "ACCEPT",
                              "evidence_record_ids": [f"R{i:010d}" for i in range(33)],
                              "brief_reason": "bounded"})
        malformed_id = canonical({"verdict": "ACCEPT", "evidence_record_ids": ["R1"],
                                  "brief_reason": "bounded"})
        provider = FakeProvider({
            "many": [FakeOutcome(output_text=too_many)],
            "malformed": [FakeOutcome(output_text=malformed_id)],
        })
        self.assertIs(provider.complete(provider_request(request_identity="many")).status,
                      ProviderStatus.INVALID_RESPONSE)
        self.assertIs(provider.complete(provider_request(request_identity="malformed")).status,
                      ProviderStatus.INVALID_RESPONSE)

    def test_exact_stateless_payload_and_strict_schema(self):
        message_payload = OpenAIResponsesProvider.build_payload(provider_request(ResponseMode.TEXT))
        verdict_payload = OpenAIResponsesProvider.build_payload(provider_request())
        for payload in (message_payload, verdict_payload):
            self.assertFalse(payload["store"])
            self.assertEqual(payload["tools"], [])
            self.assertEqual(payload["tool_choice"], "none")
            self.assertEqual(payload["truncation"], "disabled")
            self.assertNotIn("conversation", payload)
            self.assertNotIn("previous_response_id", payload)
            self.assertNotIn("metadata", payload)
            self.assertEqual(payload["input"][0]["role"], "developer")
        self.assertEqual(message_payload["text"], {"format": {"type": "text"}})
        fmt = verdict_payload["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["strict"])
        self.assertEqual(fmt["schema"], VERDICT_SCHEMA)
        self.assertFalse(fmt["schema"]["additionalProperties"])

    def test_settings_cannot_override_safety_or_carry_credentials(self):
        for key, value in (("store", True), ("tools", [{"type": "web_search"}]),
                           ("metadata", {"hidden": "channel"}),
                           ("previous_response_id", "resp_old"),
                           ("authorization", "Bearer fake-secret"),
                           ("api_key", "fake-secret")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                provider_request(model_settings_json=canonical({key: value}))
        request = provider_request(model_settings_json=canonical({"unknown_setting": 1}))
        with self.assertRaises(ValueError):
            OpenAIResponsesProvider.build_payload(request)
        with self.assertRaises(ValueError):
            provider_request(model_settings_json=canonical({"reasoning": {"metadata": "hidden"}}))
        for nested_key in ("previousResponseId", "clientSecret", "openaiApiKey"):
            with self.subTest(nested_key=nested_key), self.assertRaises(ValueError):
                provider_request(model_settings_json=canonical(
                    {"reasoning": {nested_key: "must-not-cross-the-boundary"}}))
        with self.assertRaisesRegex(ValueError, "Credential-shaped"):
            provider_request(model_settings_json=canonical(
                {"service_tier": "sk-proj-secret-shaped-value"}))
        with self.assertRaises(ValueError):
            OpenAIResponsesProvider.build_payload(provider_request(max_output_tokens=15))

    def test_structured_verdict_captures_raw_parsed_usage_model_and_timing(self):
        text = canonical({"verdict": "REJECT", "evidence_record_ids": ["RABCDEFGHIJ"],
                          "brief_reason": "Claim omits a qualifying record."})
        body = response_body(text)
        transport = QueueTransport(TransportResponse.from_mapping(body, "req_header_id"))
        with no_network():
            result = OpenAIResponsesProvider(transport).complete(provider_request())
        self.assertIs(result.status, ProviderStatus.OK)
        self.assertEqual(result.raw_response, body)
        self.assertEqual(result.parsed_output["verdict"], "REJECT")
        self.assertEqual(result.provider_request_id, "req_header_id")
        self.assertEqual(result.returned_model, body["model"])
        self.assertEqual(result.model_metadata["service_tier"], "default")
        self.assertEqual(result.usage.input_tokens, 120)
        self.assertEqual(result.usage.cached_input_tokens, 20)
        self.assertEqual(result.usage.cache_write_input_tokens, 3)
        self.assertEqual(result.usage.reasoning_tokens, 4)
        self.assertGreaterEqual(result.timing.elapsed_seconds, 0)

    def test_refusal_and_malformed_outputs_are_distinct(self):
        refusal = response_body(content=[{"type": "refusal", "refusal": "Cannot comply."}])
        malformed = response_body("not json")
        provider = OpenAIResponsesProvider(QueueTransport(
            TransportResponse.from_mapping(refusal), TransportResponse.from_mapping(malformed)))
        refused = provider.complete(provider_request())
        invalid = provider.complete(replace(provider_request(), attempt=1))
        self.assertIs(refused.status, ProviderStatus.REFUSAL)
        self.assertEqual(refused.refusal, "Cannot comply.")
        self.assertIsNone(refused.parsed_output)
        self.assertIs(invalid.status, ProviderStatus.INVALID_RESPONSE)
        self.assertIn("valid JSON", invalid.error)
        self.assertTrue(provider_result_matches_raw_response(
            refused.to_dict(), response_mode=ResponseMode.VERDICT,
        ))
        self.assertTrue(provider_result_matches_raw_response(
            invalid.to_dict(), response_mode=ResponseMode.VERDICT,
        ))
        forged = invalid.to_dict()
        forged["output_text"] = "different invalid output"
        self.assertFalse(provider_result_matches_raw_response(
            forged, response_mode=ResponseMode.VERDICT,
        ))

    def test_raw_verifier_accepts_exact_adapter_invalid_response_branches(self):
        malformed_usage = response_body("ignored")
        malformed_usage["usage"] = {"input_tokens": "not-an-integer"}
        nonlist_output = response_body("ignored")
        nonlist_output["output"] = {"type": "message"}
        provider = OpenAIResponsesProvider(QueueTransport(
            TransportResponse.from_mapping(malformed_usage),
            TransportResponse.from_mapping(nonlist_output),
        ))
        usage_invalid = provider.complete(provider_request())
        output_invalid = provider.complete(replace(provider_request(), attempt=1))
        self.assertIs(usage_invalid.status, ProviderStatus.INVALID_RESPONSE)
        self.assertIs(output_invalid.status, ProviderStatus.INVALID_RESPONSE)
        self.assertTrue(provider_result_matches_raw_response(
            usage_invalid.to_dict(), response_mode=ResponseMode.VERDICT,
        ))
        self.assertTrue(provider_result_matches_raw_response(
            output_invalid.to_dict(), response_mode=ResponseMode.VERDICT,
        ))
        tampered = output_invalid.to_dict()
        tampered["error"] = "different error"
        self.assertFalse(provider_result_matches_raw_response(
            tampered, response_mode=ResponseMode.VERDICT,
        ))

    def test_overlength_text_is_not_truncated(self):
        text = "🧪" * 201
        provider = OpenAIResponsesProvider(QueueTransport(
            TransportResponse.from_mapping(response_body(text))))
        result = provider.complete(provider_request(ResponseMode.TEXT))
        self.assertIs(result.status, ProviderStatus.OVERLENGTH)
        self.assertEqual(result.output_text, text)
        self.assertTrue(provider_result_matches_raw_response(
            result.to_dict(), response_mode=ResponseMode.TEXT,
        ))
        tampered = result.to_dict()
        tampered["output_text"] = "different" * 30
        self.assertFalse(provider_result_matches_raw_response(
            tampered, response_mode=ResponseMode.TEXT,
        ))

    def test_oversized_message_history_stops_instead_of_entering_a_retry_prompt(self):
        text = "🧪" * 4097
        provider = OpenAIResponsesProvider(QueueTransport(
            TransportResponse.from_mapping(response_body(text))))
        result = provider.complete(provider_request(response_mode=ResponseMode.TEXT))
        self.assertIs(result.status, ProviderStatus.FATAL_ERROR)
        self.assertEqual(result.output_text, text)
        self.assertIn("byte ceiling", result.error)
        self.assertEqual(len(result.output_text), 4097)

    def test_retryable_ambiguous_auth_and_quota_failures_are_classified(self):
        outcomes = [
            TransportFailure(ProviderStatus.RETRYABLE_ERROR, "temporary"),
            TransportFailure(ProviderStatus.AMBIGUOUS_TIMEOUT, "unknown completion", "req-timeout"),
            TransportResponse.from_mapping(response_body(error={
                "type": "authentication_error", "code": "invalid_api_key",
                "message": "Invalid Bearer sk-testsecret12345",
            })),
            TransportResponse.from_mapping(response_body(error={
                "type": "insufficient_quota", "code": "insufficient_quota",
                "message": "Quota exhausted",
            })),
        ]
        provider = OpenAIResponsesProvider(QueueTransport(*outcomes))
        statuses = []
        results = []
        for attempt in range(4):
            result = provider.complete(replace(provider_request(), attempt=attempt))
            statuses.append(result.status)
            results.append(result)
        self.assertEqual(statuses, [ProviderStatus.RETRYABLE_ERROR,
                                    ProviderStatus.AMBIGUOUS_TIMEOUT,
                                    ProviderStatus.AUTHENTICATION_ERROR,
                                    ProviderStatus.QUOTA_ERROR])
        self.assertTrue(results[1].ambiguous)
        self.assertEqual(results[1].provider_request_id, "req-timeout")
        self.assertNotIn("sk-testsecret", results[2].error)
        self.assertNotIn("sk-testsecret", results[2].raw_response_json)

    def test_unexpected_alternate_output_channel_is_invalid(self):
        tool_body = response_body("ignored")
        tool_body["output"] = [{"type": "web_search_call", "id": "unexpected"}]
        wrong_role = response_body("ignored")
        wrong_role["output"][1]["role"] = "tool"
        provider = OpenAIResponsesProvider(QueueTransport(
            TransportResponse.from_mapping(tool_body),
            TransportResponse.from_mapping(wrong_role)))
        for attempt in range(2):
            result = provider.complete(replace(provider_request(), attempt=attempt))
            self.assertIs(result.status, ProviderStatus.INVALID_RESPONSE)
            self.assertIn("Unexpected output", result.error)


class FakeProviderTests(unittest.TestCase):
    def test_fake_provider_is_network_proof_and_explicitly_nonempirical(self):
        fake = FakeProvider()
        with no_network():
            result = fake.complete(provider_request())
        self.assertIs(result.status, ProviderStatus.OK)
        self.assertEqual(result.raw_response["source"], "fake-live")
        self.assertEqual(fake.calls[0]["source"], "fake-live")
        self.assertIn("not empirical", result.raw_response["warning"].lower())

    def test_fake_script_classifies_malformed_refusal_and_overlength(self):
        script = {
            "bad": [FakeOutcome(output_text="not-json")],
            "refuse": [FakeOutcome(status=ProviderStatus.REFUSAL, refusal="No")],
            "long": [FakeOutcome(output_text="x" * 201)],
        }
        fake = FakeProvider(script)
        bad = fake.complete(provider_request(request_identity="bad"))
        refused = fake.complete(provider_request(request_identity="refuse"))
        long = fake.complete(provider_request(ResponseMode.TEXT, request_identity="long"))
        self.assertIs(bad.status, ProviderStatus.INVALID_RESPONSE)
        self.assertIs(refused.status, ProviderStatus.REFUSAL)
        self.assertIs(long.status, ProviderStatus.OVERLENGTH)
        self.assertEqual(len(long.output_text), 201)

    def test_exact_attempt_may_not_be_dispatched_twice(self):
        fake = FakeProvider()
        request = provider_request()
        fake.complete(request)
        with self.assertRaises(ValueError):
            fake.complete(request)
        retried = fake.complete(replace(request, attempt=1))
        self.assertIs(retried.status, ProviderStatus.OK)
        self.assertEqual([call["attempt"] for call in fake.calls], [0, 1])


if __name__ == "__main__":
    unittest.main()
