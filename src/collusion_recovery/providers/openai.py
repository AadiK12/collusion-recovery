"""OpenAI Responses payload/response adapter with injected I/O only.

This module intentionally contains no SDK import, HTTP client, credential read,
or default transport.  A later, explicitly authorized preflight must provide a
transport implementing ``send``.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
import time
from urllib.parse import urlsplit

from ..agents import parse_verdict
from ..common import canonical, contains_secret_like_string, utc_now
from .base import (
    ProviderRequest,
    ProviderResult,
    ProviderStatus,
    ProviderTiming,
    ProviderUsage,
    ResponseMode,
    ResponsesTransport,
    TransportFailure,
    TransportResponse,
    VERDICT_SCHEMA,
)


_ALLOWED_MODEL_SETTINGS = {"reasoning", "service_tier", "temperature", "top_p"}
_ERROR_RETRYABLE = {"rate_limit_error", "server_error", "overloaded",
                    "temporarily_unavailable", "connection_error"}


def openai_sdk_transport_metadata(*, timeout_seconds: float,
                                  sdk_version: str) -> dict[str, object]:
    """Return the secret-free transport identity used for authorization binding."""
    if (isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 0 < timeout_seconds <= 600):
        raise ValueError("An explicit timeout_seconds between 0 and 600 is required")
    if (not isinstance(sdk_version, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", sdk_version) is None
            or contains_secret_like_string(sdk_version)):
        raise ValueError("An explicit safe official OpenAI SDK version is required")
    return {
        "transport": "official-openai-python-sdk-injected",
        "base_url": "https://api.openai.com/v1",
        "sdk_package": "openai",
        "sdk_version": sdk_version,
        "sdk_max_retries": 0,
        "timeout_seconds": float(timeout_seconds),
    }


def _redact(text):
    if text is None:
        return None
    value = str(text)
    value = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[REDACTED_API_KEY]", value)
    value = re.sub(
        r"(?i)((?:api[_-]?key|access[_-]?token|authorization|client[_-]?secret)\s*[=:]\s*)"
        r"[^\s,;]+",
        r"\1[REDACTED]",
        value,
    )
    return value


def _secretish_key(key):
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key).strip())
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (normalized in {"api_key", "authorization", "access_token", "auth_token",
                           "bearer_token", "client_secret", "credential", "credentials",
                           "refresh_token", "secret"}
            or normalized.endswith(("_api_key", "_credential", "_credentials", "_secret")))


def _redact_value(value):
    if isinstance(value, dict):
        return {
            _redact(key): "[REDACTED]" if _secretish_key(key) else _redact_value(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(child) for child in value]
    return _redact(value) if isinstance(value, str) else value


def _int_or_none(value, name):
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"Malformed provider usage field: {name}")
    return value


def _usage(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Malformed provider usage object")
    input_details = value.get("input_tokens_details") or {}
    output_details = value.get("output_tokens_details") or {}
    if not isinstance(input_details, dict) or not isinstance(output_details, dict):
        raise ValueError("Malformed provider token details")
    input_tokens = _int_or_none(value.get("input_tokens"), "input_tokens")
    cached_tokens = _int_or_none(input_details.get("cached_tokens"), "cached_tokens")
    cache_write_tokens = _int_or_none(
        input_details.get("cache_write_tokens"), "cache_write_tokens")
    output_tokens = _int_or_none(value.get("output_tokens"), "output_tokens")
    reasoning_tokens = _int_or_none(
        output_details.get("reasoning_tokens"), "reasoning_tokens")
    total_tokens = _int_or_none(value.get("total_tokens"), "total_tokens")
    if (cached_tokens is not None and cache_write_tokens is not None
            and input_tokens is not None
            and cached_tokens + cache_write_tokens > input_tokens):
        raise ValueError(
            "Malformed provider usage field: cached plus cache-write tokens exceed input_tokens")
    if (reasoning_tokens is not None and output_tokens is not None
            and reasoning_tokens > output_tokens):
        raise ValueError("Malformed provider usage field: reasoning_tokens exceeds output_tokens")
    if (total_tokens is not None and input_tokens is not None and output_tokens is not None
            and total_tokens != input_tokens + output_tokens):
        raise ValueError("Malformed provider usage field: total_tokens disagrees with counters")
    return ProviderUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_tokens,
        cache_write_input_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        raw_json=canonical(value),
    )


def _classify_api_error(error, *, has_usage: bool):
    if not isinstance(error, dict):
        return ProviderStatus.FATAL_ERROR, _redact(error), False
    marker = " ".join(str(error.get(key, "")).lower() for key in ("type", "code"))
    message = _redact(error.get("message") or marker or "Provider error")
    if any(part in marker for part in ("authentication", "invalid_api_key", "unauthorized")):
        return ProviderStatus.AUTHENTICATION_ERROR, message, not has_usage
    if any(part in marker for part in ("insufficient_quota", "quota_exceeded")):
        return ProviderStatus.QUOTA_ERROR, message, not has_usage
    if "budget" in marker:
        return ProviderStatus.BUDGET_ERROR, message, not has_usage
    if any(part in marker for part in _ERROR_RETRYABLE):
        return ProviderStatus.RETRYABLE_ERROR, message, not has_usage and "server_error" not in marker
    return ProviderStatus.FATAL_ERROR, message, False


def _model_metadata(body):
    keys = ("model", "service_tier", "created_at", "system_fingerprint")
    return {key: body[key] for key in keys if key in body}


def _safe_evidence_token(value):
    """Return an allowlisted diagnostic token, never arbitrary header text."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}", value) is None
            or contains_secret_like_string(value)):
        return None
    return value


def _allowlisted_header(headers, name):
    """Read one case-insensitive header without serializing the header mapping."""
    if headers is None:
        return False, None
    if isinstance(headers, Mapping):
        for key, value in headers.items():
            if str(key).lower() == name:
                return True, value
        return False, None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return False, None
    try:
        value = getter(name)
    except Exception:
        return False, None
    return value is not None, value


def _retry_delay(value, *, milliseconds=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if re.fullmatch(r"(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,6})?", text) is None:
        return None
    seconds = float(text) / (1000 if milliseconds else 1)
    if not math.isfinite(seconds) or not 0 <= seconds <= 86_400:
        return None
    return seconds


def _normalized_retry_after(headers):
    seconds_present, seconds_raw = _allowlisted_header(headers, "retry-after")
    millis_present, millis_raw = _allowlisted_header(headers, "retry-after-ms")
    seconds = _retry_delay(seconds_raw) if seconds_present else None
    millis = (_retry_delay(millis_raw, milliseconds=True)
              if millis_present else None)
    if seconds is not None and millis is not None:
        return True, max(seconds, millis), "both-max"
    if seconds is not None:
        return True, seconds, "retry-after"
    if millis is not None:
        return True, millis, "retry-after-ms"
    return seconds_present or millis_present, None, None


def _sdk_transport_evidence(exc, *, status_code, body, request_id):
    """Build the closed evidence record used by prospective runner policies.

    This adapter does not decide whether a scientific protocol may retry.  It
    records the narrow facts a separately versioned runner can require while
    legacy runners continue to see the historical ``ambiguous_timeout`` status.
    """
    response = getattr(exc, "response", None)
    response_received = response is not None
    headers = getattr(response, "headers", None) if response_received else None
    _, x_request_id_raw = _allowlisted_header(headers, "x-request-id")
    x_request_id = _safe_evidence_token(x_request_id_raw)
    retry_present, retry_seconds, retry_source = _normalized_retry_after(headers)
    safe_request_id = _safe_evidence_token(request_id)
    safe_status = (status_code if type(status_code) is int and 100 <= status_code <= 599
                   else None)
    response_status_raw = (
        getattr(response, "status_code", None) if response_received else None)
    response_status = (
        response_status_raw
        if type(response_status_raw) is int and 100 <= response_status_raw <= 599
        else None
    )
    response_status_consistent = (
        safe_status is not None
        and response_status is not None
        and response_status == safe_status
    )
    detail = body.get("error", body) if isinstance(body, Mapping) else None
    error_type = (_safe_evidence_token(detail.get("type"))
                  if isinstance(detail, Mapping) else None)
    error_code = (_safe_evidence_token(detail.get("code"))
                  if isinstance(detail, Mapping) else None)
    output_absent = ("output" not in body) if response_received and isinstance(body, Mapping) else None
    usage_absent = ("usage" not in body) if response_received and isinstance(body, Mapping) else None
    request_id_consistent = (
        None if safe_request_id is None or x_request_id is None
        else safe_request_id == x_request_id
    )
    retry_eligible = bool(
        safe_status in {500, 502, 503, 504}
        and safe_request_id is not None
        and x_request_id is not None
        and request_id_consistent is True
        and response_received
        and response_status_consistent
        and output_absent is True
        and usage_absent is True
    )
    return {
        "schema_version": "v1",
        "failure_class": ("sdk_http_error" if safe_status is not None
                          else "sdk_transport_error"),
        "http_status": safe_status,
        "response_http_status": response_status,
        "exception_type": _safe_evidence_token(type(exc).__name__),
        "provider_request_id": safe_request_id,
        "x_request_id": x_request_id,
        "request_id_consistent": request_id_consistent,
        "error_type": error_type,
        "error_code": error_code,
        "response_received": response_received,
        "output_absent": output_absent,
        "usage_absent": usage_absent,
        "retry_after_present": retry_present,
        "retry_after_seconds": retry_seconds,
        "retry_after_source": retry_source,
        "retry_eligible": retry_eligible,
    }


def provider_result_matches_raw_response(
        result: Mapping, *, response_mode: ResponseMode | str | None = None,
        message_max_codepoints: int = 200,
        message_retry_history_max_utf8_bytes: int = 2048) -> bool:
    """Verify stored success derivatives against the persisted raw response.

    This is an independent audit path for gates and release evidence.  It
    deliberately reconstructs the single assistant ``output_text``, model
    metadata, and normalized usage instead of trusting adapter-populated
    derivative fields.
    """
    if (not isinstance(result, Mapping)
            or result.get("status") not in {
                "ok", "refusal", "invalid_response", "overlength"
            }):
        return False
    raw = result.get("raw_response")
    if not isinstance(raw, Mapping):
        return False
    body = dict(raw)
    if (body.get("error") is not None
            or not isinstance(body.get("id"), str) or not body["id"].strip()):
        return False
    model_matches = (
        result.get("returned_model") == body.get("model")
        and canonical(result.get("model_metadata"))
        == canonical(_model_metadata(body))
    )
    if not model_matches:
        return False
    try:
        usage = _usage(body.get("usage"))
    except ValueError as exc:
        # The adapter classifies malformed usage before interpreting response
        # status or output.  Preserve that exact fail-closed branch as a valid
        # observed invalid_response while still binding every derivative.
        return (
            result.get("status") == "invalid_response"
            and result.get("usage") is None
            and result.get("output_text") is None
            and result.get("parsed_output") is None
            and result.get("refusal") is None
            and result.get("error") == str(exc)
        )
    normalized_usage = None if usage is None else usage.to_dict()
    if canonical(result.get("usage")) != canonical(normalized_usage):
        return False

    expected_status = None
    expected_output = None
    expected_parsed = None
    expected_refusal = None
    expected_error = None
    if body.get("status") != "completed":
        expected_status = "invalid_response"
        expected_error = (
            "Response was not completed: "
            f"{_redact(canonical(body.get('incomplete_details')))}"
        )
    else:
        outputs = body.get("output")
        if not isinstance(outputs, list):
            expected_status = "invalid_response"
            expected_error = "Response output was not a list"
        else:
            texts, refusals, unexpected = [], [], []
            for item in outputs:
                if not isinstance(item, dict):
                    unexpected.append("non-object")
                    continue
                if item.get("type") == "reasoning":
                    continue
                if item.get("type") != "message":
                    unexpected.append(str(item.get("type")))
                    continue
                if item.get("role") != "assistant":
                    unexpected.append(f"message-role:{item.get('role')}")
                    continue
                if item.get("status") not in {None, "completed"}:
                    unexpected.append(f"message-status:{item.get('status')}")
                    continue
                if not isinstance(item.get("content"), list):
                    unexpected.append("message-content")
                    continue
                for content in item["content"]:
                    if not isinstance(content, dict):
                        unexpected.append("non-object-content")
                    elif (content.get("type") == "output_text"
                          and isinstance(content.get("text"), str)):
                        texts.append(content["text"])
                    elif (content.get("type") == "refusal"
                          and isinstance(content.get("refusal"), str)):
                        refusals.append(content["refusal"])
                    else:
                        unexpected.append(str(content.get("type")))
            if refusals:
                expected_status = "refusal"
                expected_refusal = "\n".join(refusals)
            elif unexpected or len(texts) != 1:
                expected_status = "invalid_response"
                expected_error = (
                    f"Unexpected output item(s): {unexpected}"
                    if unexpected else
                    f"Expected one output_text item, received {len(texts)}"
                )
            else:
                expected_output = texts[0]
                mode = (response_mode.value
                        if isinstance(response_mode, ResponseMode) else response_mode)
                if mode == ResponseMode.TEXT.value:
                    if (len(expected_output.encode("utf-8"))
                            > message_retry_history_max_utf8_bytes):
                        return False
                    if len(expected_output) > message_max_codepoints:
                        expected_status = "overlength"
                        expected_error = "Message exceeded the code-point limit"
                    else:
                        expected_status = "ok"
                        expected_parsed = expected_output
                elif mode == ResponseMode.VERDICT.value:
                    try:
                        verdict = parse_verdict(expected_output)
                    except ValueError as exc:
                        expected_status = "invalid_response"
                        expected_error = str(exc)
                    else:
                        expected_status = "ok"
                        expected_parsed = {
                            "verdict": verdict.verdict,
                            "evidence_record_ids": list(verdict.evidence_record_ids),
                            "brief_reason": verdict.brief_reason,
                        }
                elif result.get("status") == "ok":
                    # Callers that omit the request mode separately validate the
                    # action-specific parsed derivative.
                    expected_status = "ok"
                    expected_parsed = result.get("parsed_output")
                else:
                    # A lone output_text is invalid only relative to a structured
                    # request, so the saved request mode is required to prove it.
                    return False

    return (
        result.get("status") == expected_status
        and result.get("output_text") == expected_output
        and canonical(result.get("parsed_output")) == canonical(expected_parsed)
        and result.get("refusal") == expected_refusal
        and result.get("error") == expected_error
    )


def _sdk_failure(exc):
    """Convert a duck-typed official SDK exception without importing the SDK."""
    name = type(exc).__name__
    status_code = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        body = None
    request_id = _safe_evidence_token(getattr(exc, "request_id", None))
    marker_parts = [name]
    detail = body.get("error", body) if body else {}
    if isinstance(detail, Mapping):
        marker_parts.extend(str(detail.get(key, "")) for key in ("type", "code"))
        provider_message = detail.get("message")
    else:
        provider_message = None
    marker = " ".join(marker_parts).lower()
    if status_code in {401, 403} or any(
            part in marker for part in ("authentication", "permission", "invalid_api_key")):
        status, known_not_completed = ProviderStatus.AUTHENTICATION_ERROR, True
    elif any(part in marker for part in ("insufficient_quota", "quota_exceeded")):
        status, known_not_completed = ProviderStatus.QUOTA_ERROR, True
    elif "timeout" in marker or "connection" in marker:
        # Once the SDK owns dispatch, a missing response cannot prove the server
        # did not complete the request.  Preserve the conservative no-retry state.
        status, known_not_completed = ProviderStatus.AMBIGUOUS_TIMEOUT, False
    elif status_code == 429:
        status, known_not_completed = ProviderStatus.RETRYABLE_ERROR, True
    elif isinstance(status_code, int) and status_code >= 500:
        status, known_not_completed = ProviderStatus.AMBIGUOUS_TIMEOUT, False
    else:
        status, known_not_completed = ProviderStatus.FATAL_ERROR, False
    safe = f"OpenAI SDK {name}"
    if isinstance(status_code, int):
        safe += f" (HTTP {status_code})"
    if provider_message:
        safe += f": {_redact(provider_message)}"
    transport_evidence = _sdk_transport_evidence(
        exc, status_code=status_code, body=body, request_id=request_id)
    return TransportFailure(status, safe, request_id=request_id, raw_response=body,
                            known_not_completed=known_not_completed,
                            transport_evidence=transport_evidence)


class OpenAISDKTransport:
    """Injected wrapper for a preconfigured official OpenAI Python client.

    Construction never imports the SDK, reads an environment variable, or creates
    credentials.  The caller alone owns client setup and explicit authorization.
    """

    def __init__(self, client, *, timeout_seconds: float, sdk_version: str,
                 ambient_openai_environment: str | None = None,
                 http_client_trust_env: bool | None = None,
                 http_client_follow_redirects: bool | None = None):
        metadata = openai_sdk_transport_metadata(
            timeout_seconds=timeout_seconds, sdk_version=sdk_version)
        policy = (
            ambient_openai_environment,
            http_client_trust_env,
            http_client_follow_redirects,
        )
        if any(value is not None for value in policy):
            if policy != ("rejected", False, False):
                raise ValueError(
                    "The bound HTTP policy must reject ambient OpenAI settings, "
                    "environment trust, and redirects")
            metadata.update({
                "ambient_openai_environment": "rejected",
                "http_client": "openai.DefaultHttpxClient",
                "http_client_trust_env": False,
                "http_client_follow_redirects": False,
            })
        base_url = str(getattr(client, "base_url", ""))
        parsed = urlsplit(base_url)
        if (base_url.rstrip("/") != "https://api.openai.com/v1"
                or parsed.scheme != "https" or parsed.netloc != "api.openai.com"
                or parsed.path.rstrip("/") != "/v1"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or contains_secret_like_string(base_url)):
            raise ValueError(
                "The injected client must use exactly https://api.openai.com/v1 without credentials")
        with_options = getattr(client, "with_options", None)
        if not callable(with_options):
            raise ValueError("The injected SDK client must support explicit per-request options")
        # The official SDK retries selected failures by default.  Disable that
        # hidden layer so the durable executor owns every retry identity.
        scoped_client = with_options(max_retries=0, timeout=float(timeout_seconds))
        create = getattr(getattr(scoped_client, "responses", None), "create", None)
        if not callable(create):
            raise ValueError("A preconfigured client with responses.create is required")
        self._create = create
        self.audit_metadata = metadata

    def send(self, payload: Mapping) -> TransportResponse:
        if not isinstance(payload, Mapping):
            raise TypeError("Responses payload must be a mapping")
        # Freeze caller-owned nested values before handing them to an injected
        # client, which may retain or mutate keyword arguments.
        kwargs = json.loads(canonical(dict(payload)))
        try:
            response = self._create(**kwargs)
        except TransportFailure:
            raise
        except Exception as exc:
            raise _sdk_failure(exc) from exc
        request_id = (getattr(response, "_request_id", None)
                      or getattr(response, "request_id", None))
        if isinstance(response, Mapping):
            body_json = canonical(dict(response))
        else:
            dump_json = getattr(response, "model_dump_json", None)
            dump = getattr(response, "model_dump", None)
            if callable(dump_json):
                body_json = dump_json()
            elif callable(dump):
                try:
                    body = dump(mode="json")
                except TypeError:
                    body = dump()
                body_json = canonical(body)
            else:
                raise TransportFailure(
                    ProviderStatus.FATAL_ERROR,
                    "OpenAI SDK returned an unsupported response object",
                    request_id=request_id,
                )
        if not isinstance(body_json, str):
            raise TransportFailure(
                ProviderStatus.FATAL_ERROR,
                "OpenAI SDK response serialization did not return text",
                request_id=request_id,
            )
        try:
            return TransportResponse(body_json, request_id=request_id)
        except (TypeError, ValueError) as exc:
            raise TransportFailure(
                ProviderStatus.FATAL_ERROR,
                "OpenAI SDK returned malformed response JSON",
                request_id=request_id,
            ) from exc


class OpenAIResponsesProvider:
    """Translate the provider-neutral contract to one Responses API call."""

    def __init__(self, transport: ResponsesTransport):
        if transport is None or not callable(getattr(transport, "send", None)):
            raise ValueError("An explicit Responses transport is required")
        self.transport = transport

    @staticmethod
    def build_payload(request: ProviderRequest):
        if not isinstance(request, ProviderRequest):
            raise TypeError("OpenAI adapter requires ProviderRequest")
        settings = request.model_settings
        if request.max_output_tokens < 16:
            raise ValueError("OpenAI max_output_tokens must be at least 16")
        unsupported = set(settings) - _ALLOWED_MODEL_SETTINGS
        if unsupported:
            raise ValueError(f"Unsupported OpenAI model setting(s): {sorted(unsupported)}")
        text_format = ({"type": "text"} if request.response_mode is ResponseMode.TEXT else {
            "type": "json_schema",
            "name": "verification_verdict",
            "strict": True,
            "schema": json.loads(canonical(VERDICT_SCHEMA)),
        })
        # Fixed safety fields are written after the allowlisted settings.  Even if
        # validation changes later, settings cannot override these invariants.
        return {
            **json.loads(canonical(settings)),
            "model": request.model,
            "input": [turn.to_dict() for turn in request.turns],
            "store": False,
            "tools": [],
            "tool_choice": "none",
            "truncation": "disabled",
            "max_output_tokens": request.max_output_tokens,
            "text": {"format": text_format},
        }

    def _failure_result(self, status, message, timing, request_id=None,
                        raw_response_json=None, known_not_completed=False,
                        transport_evidence_json="{}"):
        return ProviderResult(
            status=status,
            raw_response_json=raw_response_json,
            output_text=None,
            parsed_output_json=None,
            refusal=None,
            error=_redact(message),
            provider_request_id=request_id,
            returned_model=None,
            model_metadata_json="{}",
            usage=None,
            timing=timing,
            ambiguous=status is ProviderStatus.AMBIGUOUS_TIMEOUT,
            known_not_completed=known_not_completed,
            transport_evidence_json=transport_evidence_json,
        )

    def complete(self, request: ProviderRequest):
        payload = self.build_payload(request)
        started_at, started = utc_now(), time.monotonic()
        try:
            response = self.transport.send(payload)
        except TransportFailure as exc:
            timing = ProviderTiming(started_at, utc_now(), time.monotonic() - started)
            raw = (None if exc.raw_response_json is None else
                   canonical(_redact_value(json.loads(exc.raw_response_json))))
            return self._failure_result(
                exc.status, exc.safe_message, timing, exc.request_id,
                raw_response_json=raw,
                known_not_completed=exc.known_not_completed,
                transport_evidence_json=exc.transport_evidence_json,
            )
        except Exception as exc:  # transport contract violation, never guessed retryable
            timing = ProviderTiming(started_at, utc_now(), time.monotonic() - started)
            return self._failure_result(ProviderStatus.FATAL_ERROR,
                                        f"Unclassified transport failure: {type(exc).__name__}", timing)
        timing = ProviderTiming(started_at, utc_now(), time.monotonic() - started)
        if not isinstance(response, TransportResponse):
            return self._failure_result(ProviderStatus.FATAL_ERROR,
                                        "Transport returned an unsupported response type", timing)
        body = response.body
        # Successful bodies are byte-for-byte canonicalized from the transport
        # response. Error bodies are defensively redacted because some gateways
        # echo credential fragments in diagnostics.
        raw_json = (response.body_json if body.get("error") is None
                    else canonical(_redact_value(body)))
        provider_id = response.request_id or body.get("id")
        returned_model = body.get("model") if isinstance(body.get("model"), str) else None
        metadata_json = canonical(_model_metadata(body))
        try:
            usage = _usage(body.get("usage"))
        except ValueError as exc:
            return ProviderResult(
                status=ProviderStatus.INVALID_RESPONSE,
                raw_response_json=raw_json,
                output_text=None,
                parsed_output_json=None,
                refusal=None,
                error=str(exc),
                provider_request_id=provider_id,
                returned_model=returned_model,
                model_metadata_json=metadata_json,
                usage=None,
                timing=timing,
            )
        if body.get("error") is not None:
            status, error, known_not_completed = _classify_api_error(
                body["error"], has_usage=usage is not None)
            return ProviderResult(
                status, raw_json, None, None, None, error, provider_id,
                returned_model, metadata_json, usage, timing,
                ambiguous=False, known_not_completed=known_not_completed,
            )
        if body.get("status") != "completed":
            details = body.get("incomplete_details")
            return ProviderResult(
                ProviderStatus.INVALID_RESPONSE, raw_json, None, None, None,
                f"Response was not completed: {_redact(canonical(details))}", provider_id,
                returned_model, metadata_json, usage, timing,
            )
        outputs = body.get("output")
        if not isinstance(outputs, list):
            return ProviderResult(
                ProviderStatus.INVALID_RESPONSE, raw_json, None, None, None,
                "Response output was not a list", provider_id, returned_model,
                metadata_json, usage, timing,
            )
        texts, refusals, unexpected = [], [], []
        for item in outputs:
            if not isinstance(item, dict):
                unexpected.append("non-object")
                continue
            if item.get("type") == "reasoning":
                # Opaque reasoning is captured in raw response but never replayed.
                continue
            if item.get("type") != "message":
                unexpected.append(str(item.get("type")))
                continue
            if item.get("role") != "assistant":
                unexpected.append(f"message-role:{item.get('role')}")
                continue
            if item.get("status") not in {None, "completed"}:
                unexpected.append(f"message-status:{item.get('status')}")
                continue
            if not isinstance(item.get("content"), list):
                unexpected.append("message-content")
                continue
            for content in item["content"]:
                if not isinstance(content, dict):
                    unexpected.append("non-object-content")
                elif content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    texts.append(content["text"])
                elif content.get("type") == "refusal" and isinstance(content.get("refusal"), str):
                    refusals.append(content["refusal"])
                else:
                    unexpected.append(str(content.get("type")))
        if refusals:
            return ProviderResult(
                ProviderStatus.REFUSAL, raw_json, None, None, "\n".join(refusals), None,
                provider_id, returned_model, metadata_json, usage, timing,
            )
        if unexpected or len(texts) != 1:
            reason = (f"Unexpected output item(s): {unexpected}" if unexpected
                      else f"Expected one output_text item, received {len(texts)}")
            return ProviderResult(
                ProviderStatus.INVALID_RESPONSE, raw_json, None, None, None, reason,
                provider_id, returned_model, metadata_json, usage, timing,
            )
        output_text = texts[0]
        if request.response_mode is ResponseMode.TEXT:
            if len(output_text.encode("utf-8")) > request.message_retry_history_max_utf8_bytes:
                return ProviderResult(
                    ProviderStatus.FATAL_ERROR, raw_json, output_text, None, None,
                    "Message exceeded the retry-history byte ceiling", provider_id,
                    returned_model, metadata_json, usage, timing,
                )
            if len(output_text) > request.message_max_codepoints:
                return ProviderResult(
                    ProviderStatus.OVERLENGTH, raw_json, output_text, None, None,
                    "Message exceeded the code-point limit", provider_id, returned_model,
                    metadata_json, usage, timing,
                )
            parsed = output_text
        else:
            try:
                verdict = parse_verdict(output_text)
            except ValueError as exc:
                return ProviderResult(
                    ProviderStatus.INVALID_RESPONSE, raw_json, output_text, None, None,
                    str(exc), provider_id, returned_model, metadata_json, usage, timing,
                )
            parsed = {
                "verdict": verdict.verdict,
                "evidence_record_ids": list(verdict.evidence_record_ids),
                "brief_reason": verdict.brief_reason,
            }
        return ProviderResult(
            ProviderStatus.OK, raw_json, output_text, canonical(parsed), None, None,
            provider_id, returned_model, metadata_json, usage, timing,
        )
