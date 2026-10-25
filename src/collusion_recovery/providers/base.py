"""Small, serializable provider contract with no SDK or network dependency."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
import math
import re
from typing import Mapping, Protocol, Sequence

from ..common import canonical, contains_secret_like_string, digest


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ACCEPT", "REJECT"]},
        "evidence_record_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": "^R[A-Z0-9]{10}$"},
            "maxItems": 32,
        },
        # Structured Outputs supports ``type`` here but not ``maxLength``.
        # The application parser independently enforces the 500-character cap.
        "brief_reason": {"type": "string"},
    },
    "required": ["verdict", "evidence_record_ids", "brief_reason"],
    "additionalProperties": False,
}
MESSAGE_RETRY_HISTORY_MAX_UTF8_BYTES = 2_048

# A byte can contribute at most one ordinary text token under byte-level tokenizers.
# These explicit framing allowances cover the provider's per-request and per-role
# message markers without depending on a model-specific tokenizer or network call.
_INPUT_TOKEN_FIXED_OVERHEAD = 1024


_TRANSPORT_EVIDENCE_KEYS = {
    "schema_version",
    "failure_class",
    "http_status",
    "response_http_status",
    "exception_type",
    "provider_request_id",
    "x_request_id",
    "request_id_consistent",
    "error_type",
    "error_code",
    "response_received",
    "output_absent",
    "usage_absent",
    "retry_after_present",
    "retry_after_seconds",
    "retry_after_source",
    "retry_eligible",
}
_TRANSPORT_FAILURE_CLASSES = {"sdk_http_error", "sdk_transport_error"}
_RETRY_AFTER_SOURCES = {"retry-after", "retry-after-ms", "both-max"}
_SAFE_EVIDENCE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}")


def _load_transport_evidence(evidence_json):
    """Load the closed, secret-free schema used for transport diagnostics.

    An empty object means that the transport supplied no structured evidence.
    Nonempty objects must contain the complete schema so persisted rows cannot
    silently reinterpret missing fields after a code change.
    """
    try:
        evidence = json.loads(evidence_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("transport_evidence_json must be valid JSON") from exc
    if not isinstance(evidence, dict):
        raise ValueError("transport evidence must encode an object")
    if not evidence:
        return evidence
    if set(evidence) != _TRANSPORT_EVIDENCE_KEYS:
        raise ValueError("transport evidence must use the complete closed schema")
    if evidence["schema_version"] != "v1":
        raise ValueError("unsupported transport evidence schema")
    if evidence["failure_class"] not in _TRANSPORT_FAILURE_CLASSES:
        raise ValueError("invalid transport failure class")

    status = evidence["http_status"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError("transport HTTP status must be an integer from 100 through 599")
    response_status = evidence["response_http_status"]
    if (response_status is not None
            and (type(response_status) is not int or not 100 <= response_status <= 599)):
        raise ValueError(
            "transport response HTTP status must be an integer from 100 through 599")
    for name in ("exception_type", "provider_request_id", "x_request_id",
                 "error_type", "error_code"):
        value = evidence[name]
        if value is not None and (
                not isinstance(value, str)
                or _SAFE_EVIDENCE_TOKEN.fullmatch(value) is None
                or contains_secret_like_string(value)):
            raise ValueError(f"unsafe transport evidence token: {name}")
    for name in ("request_id_consistent", "output_absent", "usage_absent"):
        if evidence[name] is not None and type(evidence[name]) is not bool:
            raise ValueError(f"transport evidence {name} must be boolean or null")
    for name in ("response_received", "retry_after_present", "retry_eligible"):
        if type(evidence[name]) is not bool:
            raise ValueError(f"transport evidence {name} must be boolean")

    provider_id = evidence["provider_request_id"]
    header_id = evidence["x_request_id"]
    consistency = evidence["request_id_consistent"]
    expected_consistency = None if provider_id is None or header_id is None else (
        provider_id == header_id)
    if consistency != expected_consistency:
        raise ValueError("transport request-id consistency field is not derived exactly")
    if not evidence["response_received"] and (
            evidence["output_absent"] is not None
            or evidence["usage_absent"] is not None):
        raise ValueError("response-body absence cannot be asserted without a response")

    seconds = evidence["retry_after_seconds"]
    if seconds is not None and (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or not 0 <= seconds <= 86_400):
        raise ValueError("retry-after seconds must be finite and between 0 and 86400")
    source = evidence["retry_after_source"]
    if source is not None and source not in _RETRY_AFTER_SOURCES:
        raise ValueError("invalid retry-after source")
    if (seconds is None) != (source is None):
        raise ValueError("retry-after seconds and source must be present together")
    if not evidence["retry_after_present"] and seconds is not None:
        raise ValueError("normalized retry-after requires a received retry-after header")

    eligible = evidence["retry_eligible"]
    if eligible and not (
            evidence["failure_class"] == "sdk_http_error"
            and status in {500, 502, 503, 504}
            and response_status == status
            and provider_id is not None
            and header_id is not None
            and consistency is True
            and evidence["response_received"] is True
            and evidence["output_absent"] is True
            and evidence["usage_absent"] is True):
        raise ValueError("retry eligibility is unsupported by the recorded evidence")
    return evidence


class ProviderStatus(str, Enum):
    OK = "ok"
    REFUSAL = "refusal"
    INVALID_RESPONSE = "invalid_response"
    OVERLENGTH = "overlength"
    RETRYABLE_ERROR = "retryable_error"
    AMBIGUOUS_TIMEOUT = "ambiguous_timeout"
    AUTHENTICATION_ERROR = "authentication_error"
    QUOTA_ERROR = "quota_error"
    BUDGET_ERROR = "budget_error"
    FATAL_ERROR = "fatal_error"


class ResponseMode(str, Enum):
    TEXT = "text"
    VERDICT = "verdict"


@dataclass(frozen=True)
class TextTurn:
    role: str
    content: str

    def __post_init__(self):
        if self.role not in {"developer", "system", "user", "assistant"}:
            raise ValueError("Unsupported text-turn role")
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("Text-turn content must be nonempty text")

    def to_dict(self):
        return {"role": self.role, "content": self.content}


_RESERVED_SETTING_KEYS = {
    "conversation",
    "include",
    "input",
    "instructions",
    "max_output_tokens",
    "metadata",
    "model",
    "previous_response_id",
    "store",
    "text",
    "tool_choice",
    "tools",
    "truncation",
}
_SECRET_SETTING_KEYS = {
    "access_token",
    "api_key",
    "auth_token",
    "authorization",
    "bearer_token",
    "client_secret",
    "credential",
    "credentials",
    "refresh_token",
    "secret",
}


def _normalize_setting_key(key):
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key).strip())
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _setting_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield _normalize_setting_key(key)
            yield from _setting_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _setting_keys(child)


def _load_settings(settings_json):
    try:
        settings = json.loads(settings_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("model_settings_json must be valid JSON") from exc
    if not isinstance(settings, dict):
        raise ValueError("Model settings must be a JSON object")
    lowered = set(_setting_keys(settings))
    if lowered & _RESERVED_SETTING_KEYS:
        raise ValueError("Model settings cannot override provider safety fields")
    if (lowered & _SECRET_SETTING_KEYS
            or any(key.endswith(("_api_key", "_credential", "_credentials", "_secret"))
                   for key in lowered)):
        raise ValueError("Credentials do not belong in model settings")
    if contains_secret_like_string(settings):
        raise ValueError("Credential-shaped values do not belong in model settings")
    # Round-trip through canonical JSON so caller-owned nested values are never
    # retained and non-JSON values cannot enter a persisted request.
    return json.loads(canonical(settings))


@dataclass(frozen=True)
class ProviderRequest:
    """One scheduled provider attempt.

    ``request_identity`` is the durable scheduled identity.  ``attempt`` is a
    separate transport/format-attempt number, so retries cannot masquerade as a
    new scientific observation.
    """

    request_identity: str
    model: str
    turns: tuple[TextTurn, ...]
    response_mode: ResponseMode
    max_output_tokens: int
    model_settings_json: str = "{}"
    message_max_codepoints: int = 200
    message_retry_history_max_utf8_bytes: int = MESSAGE_RETRY_HISTORY_MAX_UTF8_BYTES
    attempt: int = 0

    def __post_init__(self):
        if not isinstance(self.request_identity, str) or not self.request_identity.strip():
            raise ValueError("request_identity must be nonempty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be nonempty")
        if not isinstance(self.turns, tuple) or not self.turns:
            raise ValueError("turns must be a nonempty tuple")
        if any(not isinstance(turn, TextTurn) for turn in self.turns):
            raise ValueError("Every input turn must be a TextTurn")
        if not isinstance(self.response_mode, ResponseMode):
            raise ValueError("response_mode must be a ResponseMode")
        for name in ("max_output_tokens", "message_max_codepoints",
                     "message_retry_history_max_utf8_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("attempt must be a nonnegative integer")
        _load_settings(self.model_settings_json)

    @property
    def model_settings(self):
        return _load_settings(self.model_settings_json)

    @property
    def input_hash(self):
        return digest([turn.to_dict() for turn in self.turns])

    @property
    def conservative_input_token_bound(self):
        """Deterministic preflight ceiling, independent of a provider tokenizer.

        Every UTF-8 byte in the complete model-visible input, strict response
        schema, and caller-selected settings is counted as one token, with an
        additional 1,024-token allowance for provider framing.  Byte-level
        tokenizers cannot emit more ordinary text tokens than input bytes.  The
        deliberately large fixed allowance keeps structural/special-token
        overhead conservative without importing a model-specific tokenizer.
        """
        material = {
            "turns": [turn.to_dict() for turn in self.turns],
            "model_settings": self.model_settings,
            "response_format": ({"type": "text"}
                                if self.response_mode is ResponseMode.TEXT
                                else VERDICT_SCHEMA),
        }
        return _INPUT_TOKEN_FIXED_OVERHEAD + len(canonical(material).encode("utf-8"))

    def to_dict(self):
        return {
            "request_identity": self.request_identity,
            "attempt": self.attempt,
            "model": self.model,
            "model_settings": self.model_settings,
            "response_mode": self.response_mode.value,
            "max_output_tokens": self.max_output_tokens,
            "message_max_codepoints": self.message_max_codepoints,
            "message_retry_history_max_utf8_bytes": self.message_retry_history_max_utf8_bytes,
            "turns": [turn.to_dict() for turn in self.turns],
            "input_hash": self.input_hash,
            "conservative_input_token_bound": self.conservative_input_token_bound,
        }


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    raw_json: str = "{}"

    def __post_init__(self):
        for name in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                     "output_tokens", "reasoning_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a nonnegative integer or null")
        if (self.input_tokens is not None
                and self.cached_input_tokens is not None
                and self.cache_write_input_tokens is not None
                and (self.cached_input_tokens + self.cache_write_input_tokens
                     > self.input_tokens)):
            raise ValueError(
                "cached plus cache-write usage cannot exceed total input tokens")
        if (self.output_tokens is not None
                and self.reasoning_tokens is not None
                and self.reasoning_tokens > self.output_tokens):
            raise ValueError(
                "output-token usage must include all reasoning tokens")
        if (self.total_tokens is not None
                and self.input_tokens is not None
                and self.output_tokens is not None
                and self.total_tokens != self.input_tokens + self.output_tokens):
            raise ValueError(
                "total-token usage must equal input plus output tokens")
        raw = json.loads(self.raw_json)
        if not isinstance(raw, dict):
            raise ValueError("Usage raw_json must encode an object")

    def to_dict(self):
        result = asdict(self)
        result["raw"] = json.loads(result.pop("raw_json"))
        return result


@dataclass(frozen=True)
class ProviderTiming:
    started_at: str
    ended_at: str
    elapsed_seconds: float

    def __post_init__(self):
        if not isinstance(self.started_at, str) or not isinstance(self.ended_at, str):
            raise ValueError("Timing timestamps must be strings")
        if (isinstance(self.elapsed_seconds, bool)
                or not isinstance(self.elapsed_seconds, (int, float))
                or self.elapsed_seconds < 0):
            raise ValueError("elapsed_seconds must be nonnegative")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class ProviderResult:
    status: ProviderStatus
    raw_response_json: str | None
    output_text: str | None
    parsed_output_json: str | None
    refusal: str | None
    error: str | None
    provider_request_id: str | None
    returned_model: str | None
    model_metadata_json: str
    usage: ProviderUsage | None
    timing: ProviderTiming
    ambiguous: bool = False
    known_not_completed: bool = False
    transport_evidence_json: str = "{}"

    def __post_init__(self):
        if not isinstance(self.status, ProviderStatus):
            raise ValueError("status must be a ProviderStatus")
        if self.ambiguous != (self.status is ProviderStatus.AMBIGUOUS_TIMEOUT):
            raise ValueError("Only ambiguous timeouts may be marked ambiguous")
        if type(self.known_not_completed) is not bool:
            raise ValueError("known_not_completed must be boolean")
        if (self.known_not_completed
                and self.status not in {ProviderStatus.RETRYABLE_ERROR,
                                        ProviderStatus.AUTHENTICATION_ERROR,
                                        ProviderStatus.QUOTA_ERROR,
                                        ProviderStatus.BUDGET_ERROR}):
            raise ValueError("completion certainty is invalid for this provider status")
        if self.known_not_completed and self.usage is not None:
            raise ValueError(
                "a response with any usage object cannot be known not completed"
            )
        for name in ("raw_response_json", "parsed_output_json", "model_metadata_json"):
            value = getattr(self, name)
            if value is not None:
                json.loads(value)
        if not isinstance(json.loads(self.model_metadata_json), dict):
            raise ValueError("model metadata must encode an object")
        evidence = _load_transport_evidence(self.transport_evidence_json)
        if evidence:
            if evidence["retry_eligible"] and self.status is not ProviderStatus.AMBIGUOUS_TIMEOUT:
                raise ValueError(
                    "eligible ambiguous transport evidence requires ambiguous_timeout status")
            if (evidence["provider_request_id"] is not None
                    and evidence["provider_request_id"] != self.provider_request_id):
                raise ValueError("transport evidence request id disagrees with provider result")
            if evidence["retry_eligible"]:
                if self.usage is not None or self.output_text is not None:
                    raise ValueError("eligible transport evidence cannot accompany output or usage")
                if self.raw_response_json is None:
                    raise ValueError("eligible transport evidence requires the received error body")
                raw = json.loads(self.raw_response_json)
                if not isinstance(raw, dict) or "output" in raw or "usage" in raw:
                    raise ValueError(
                        "eligible transport evidence disagrees with the persisted error body")

    @property
    def raw_response(self):
        return None if self.raw_response_json is None else json.loads(self.raw_response_json)

    @property
    def parsed_output(self):
        return None if self.parsed_output_json is None else json.loads(self.parsed_output_json)

    @property
    def model_metadata(self):
        return json.loads(self.model_metadata_json)

    @property
    def transport_evidence(self):
        return _load_transport_evidence(self.transport_evidence_json)

    def to_dict(self):
        return {
            "status": self.status.value,
            "raw_response": self.raw_response,
            "output_text": self.output_text,
            "parsed_output": self.parsed_output,
            "refusal": self.refusal,
            "error": self.error,
            "provider_request_id": self.provider_request_id,
            "returned_model": self.returned_model,
            "model_metadata": self.model_metadata,
            "usage": None if self.usage is None else self.usage.to_dict(),
            "timing": self.timing.to_dict(),
            "ambiguous": self.ambiguous,
            "known_not_completed": self.known_not_completed,
            "transport_evidence": self.transport_evidence,
        }


@dataclass(frozen=True)
class FakeOutcome:
    status: ProviderStatus = ProviderStatus.OK
    output_text: str | None = None
    refusal: str | None = None
    error: str | None = None
    provider_request_id: str | None = None
    returned_model: str | None = None
    usage: ProviderUsage | None = None
    known_not_completed: bool | None = None

    def __post_init__(self):
        if not isinstance(self.status, ProviderStatus):
            raise ValueError("Fake outcome status must be a ProviderStatus")
        if self.known_not_completed is not None and type(self.known_not_completed) is not bool:
            raise ValueError("Fake completion certainty must be boolean or null")


@dataclass(frozen=True)
class TransportResponse:
    body_json: str
    request_id: str | None = None

    def __post_init__(self):
        if not isinstance(json.loads(self.body_json), dict):
            raise ValueError("Transport response body must encode an object")

    @classmethod
    def from_mapping(cls, body: Mapping, request_id=None):
        return cls(canonical(body), request_id)

    @property
    def body(self):
        return json.loads(self.body_json)


class TransportFailure(Exception):
    """A safely classified transport failure.

    Transports must not put authorization headers or credential values in
    ``safe_message``.  The adapter defensively redacts common secret shapes too.
    """

    ALLOWED = {
        ProviderStatus.RETRYABLE_ERROR,
        ProviderStatus.AMBIGUOUS_TIMEOUT,
        ProviderStatus.AUTHENTICATION_ERROR,
        ProviderStatus.QUOTA_ERROR,
        ProviderStatus.BUDGET_ERROR,
        ProviderStatus.FATAL_ERROR,
    }

    def __init__(self, status: ProviderStatus, safe_message: str,
                 request_id: str | None = None, raw_response: Mapping | None = None,
                 known_not_completed: bool = False,
                 transport_evidence: Mapping | None = None):
        if status not in self.ALLOWED:
            raise ValueError("Invalid transport-failure classification")
        if not isinstance(safe_message, str) or not safe_message:
            raise ValueError("Transport failure requires a safe message")
        self.status = status
        self.safe_message = safe_message
        self.request_id = request_id
        self.raw_response_json = None if raw_response is None else canonical(raw_response)
        if type(known_not_completed) is not bool:
            raise ValueError("Transport completion certainty must be boolean")
        self.known_not_completed = known_not_completed
        frozen_evidence = canonical(dict(transport_evidence or {}))
        evidence = _load_transport_evidence(frozen_evidence)
        if evidence:
            if evidence["retry_eligible"] and status is not ProviderStatus.AMBIGUOUS_TIMEOUT:
                raise ValueError(
                    "eligible ambiguous transport evidence requires ambiguous_timeout status")
            if (evidence["provider_request_id"] is not None
                    and evidence["provider_request_id"] != request_id):
                raise ValueError("transport evidence request id disagrees with transport failure")
            if evidence["retry_eligible"]:
                if raw_response is None or "output" in raw_response or "usage" in raw_response:
                    raise ValueError(
                        "eligible transport evidence requires an output/usage-free error body")
        self.transport_evidence_json = frozen_evidence
        super().__init__(safe_message)

    @property
    def transport_evidence(self):
        return _load_transport_evidence(self.transport_evidence_json)


class Provider(Protocol):
    def complete(self, request: ProviderRequest) -> ProviderResult:
        ...


class ResponsesTransport(Protocol):
    """Injected I/O boundary; implementations own authentication and HTTP."""

    def send(self, payload: Mapping) -> TransportResponse:
        ...


def freeze_turns(turns: Sequence[TextTurn]):
    return tuple(turns)
