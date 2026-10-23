"""Deterministic fake-live provider.  It has no transport and cannot use a network."""

from __future__ import annotations

import json
from typing import Mapping, Sequence

from ..agents import parse_verdict
from ..common import canonical, utc_now
from .base import (
    FakeOutcome,
    ProviderRequest,
    ProviderResult,
    ProviderStatus,
    ProviderTiming,
    ProviderUsage,
    ResponseMode,
)


_DEFAULT_USAGE = ProviderUsage(
    input_tokens=1,
    cached_input_tokens=0,
    output_tokens=1,
    reasoning_tokens=0,
    total_tokens=2,
    raw_json=canonical({
        "input_tokens": 1,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 1,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 2,
        "source": "fake-live",
    }),
)


class FakeProvider:
    """Return scripted outcomes and retain exact request snapshots for assertions.

    This provider is test infrastructure.  Its records are always tagged ``fake-live``
    and must never be labeled empirical.
    """

    def __init__(self, script: Mapping[str, Sequence[FakeOutcome]] | None = None):
        self._script = {identity: list(outcomes) for identity, outcomes in (script or {}).items()}
        if any(not isinstance(outcome, FakeOutcome)
               for outcomes in self._script.values() for outcome in outcomes):
            raise ValueError("FakeProvider scripts must contain FakeOutcome values")
        self.calls = []
        self._attempts = set()

    def _outcome(self, request):
        queue = self._script.get(request.request_identity)
        if queue:
            return queue.pop(0)
        output = (
            "I will apply the current verification rule."
            if request.response_mode is ResponseMode.TEXT else
            canonical({"verdict": "REJECT", "evidence_record_ids": [],
                       "brief_reason": "Deterministic fake-live output; not empirical evidence."})
        )
        return FakeOutcome(output_text=output)

    def complete(self, request: ProviderRequest):
        if not isinstance(request, ProviderRequest):
            raise TypeError("FakeProvider requires ProviderRequest")
        attempt_key = (request.request_identity, request.attempt)
        if attempt_key in self._attempts:
            raise ValueError("Duplicate fake-live request identity and attempt")
        self._attempts.add(attempt_key)
        # JSON round-trip prevents later caller mutation and makes this the exact
        # fake-live dispatch record rather than a reference to live objects.
        self.calls.append(json.loads(canonical({
            "source": "fake-live",
            "warning": "FAKE-LIVE TEST OUTPUT. NOT EMPIRICAL MODEL EVIDENCE.",
            **request.to_dict(),
        })))
        outcome = self._outcome(request)
        status, error, parsed = outcome.status, outcome.error, None
        output = outcome.output_text
        if status is ProviderStatus.OK:
            if not isinstance(output, str):
                status, error = ProviderStatus.INVALID_RESPONSE, "Fake response contained no text"
            elif request.response_mode is ResponseMode.TEXT:
                if len(output.encode("utf-8")) > request.message_retry_history_max_utf8_bytes:
                    status, error = (ProviderStatus.FATAL_ERROR,
                                     "Message exceeded the retry-history byte ceiling")
                elif len(output) > request.message_max_codepoints:
                    status, error = ProviderStatus.OVERLENGTH, "Message exceeded the code-point limit"
                else:
                    parsed = output
            else:
                try:
                    verdict = parse_verdict(output)
                    parsed = {
                        "verdict": verdict.verdict,
                        "evidence_record_ids": list(verdict.evidence_record_ids),
                        "brief_reason": verdict.brief_reason,
                    }
                except ValueError as exc:
                    status, error = ProviderStatus.INVALID_RESPONSE, str(exc)
        refusal = outcome.refusal
        if status is ProviderStatus.REFUSAL and not refusal:
            refusal = "Fake refusal"
        model = outcome.returned_model or f"{request.model}-fake-live"
        provider_id = outcome.provider_request_id or f"fake-live/{request.request_identity}/{request.attempt}"
        raw = {
            "object": "fake_live_response",
            "source": "fake-live",
            "warning": "FAKE-LIVE TEST OUTPUT. NOT EMPIRICAL MODEL EVIDENCE.",
            "id": provider_id,
            "model": model,
            "status": status.value,
            "output_text": output,
            "refusal": refusal,
            "error": error,
        }
        now = utc_now()
        known_not_completed = (
            outcome.known_not_completed
            if outcome.known_not_completed is not None else
            status in {ProviderStatus.RETRYABLE_ERROR, ProviderStatus.AUTHENTICATION_ERROR,
                       ProviderStatus.QUOTA_ERROR, ProviderStatus.BUDGET_ERROR}
            and outcome.usage is None
        )
        usage = outcome.usage
        if usage is None and status in {ProviderStatus.OK, ProviderStatus.REFUSAL,
                                        ProviderStatus.INVALID_RESPONSE,
                                        ProviderStatus.OVERLENGTH,
                                        ProviderStatus.FATAL_ERROR}:
            usage = _DEFAULT_USAGE
        return ProviderResult(
            status=status,
            raw_response_json=canonical(raw),
            output_text=output,
            parsed_output_json=None if parsed is None else canonical(parsed),
            refusal=refusal,
            error=error,
            provider_request_id=provider_id,
            returned_model=model,
            model_metadata_json=canonical({"model": model, "source": "fake-live"}),
            usage=usage,
            timing=ProviderTiming(now, now, 0.0),
            ambiguous=status is ProviderStatus.AMBIGUOUS_TIMEOUT,
            known_not_completed=known_not_completed,
        )

    def remaining(self, request_identity):
        return len(self._script.get(request_identity, ()))
