"""Deterministic, nonexperimental Gate 0 request shapes and assessment.

This module is deliberately inert: it generates the two approved Responses API
payloads and validates already-returned results, but it never reads credentials,
constructs a client, or dispatches a request.  The fixture namespaces are
disjoint from calibration and experimental namespaces, and evaluator labels do
not cross the prompt-rendering boundary.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from .agents import parse_durable_verdict
from .common import canonical, digest
from .fixtures import generate_fixture
from .history import History
from .prompts import RenderedPrompt, provider_request_from_agent_request
from .providers.base import ProviderRequest, ProviderResult, ResponseMode
from .providers.openai import (
    OpenAIResponsesProvider,
    provider_result_matches_raw_response,
)
from .visibility import request_for


PREFLIGHT_NAMESPACE = "preflight/v1"
PREFLIGHT_MESSAGE_IDENTITY = f"{PREFLIGHT_NAMESPACE}/message"
PREFLIGHT_VERDICT_IDENTITY = f"{PREFLIGHT_NAMESPACE}/verdict"
MESSAGE_OVERLENGTH_FEEDBACK = (
    "Message exceeded 200 code points and was not delivered. Retry once."
)
_ACTIONS = ("message", "verdict")
_MAX_ATTEMPTS = {"message": 4, "verdict": 3}


class PreflightError(ValueError):
    """The requested Gate 0 shape or result set violates the frozen contract."""


def _status(value: ProviderResult | Mapping[str, Any]) -> str | None:
    if isinstance(value, ProviderResult):
        return value.status.value
    status = value.get("status") if isinstance(value, Mapping) else None
    return status.value if hasattr(status, "value") else status if isinstance(status, str) else None


def _known_not_completed(value: ProviderResult | Mapping[str, Any]) -> bool:
    if isinstance(value, ProviderResult):
        return value.known_not_completed is True
    if not isinstance(value, Mapping):
        return False
    if value.get("known_not_completed") is True:
        return True
    nested = value.get("provider_result")
    return isinstance(nested, Mapping) and nested.get("known_not_completed") is True


class PreflightRequestFactory:
    """Build the exact two logical requests approved for Gate 0.

    ``prior_results`` is used only to select the one permitted content-revision
    shape after an overlength message.  Transport retries keep the same visible
    content.  Attempt ordinals remain separate from logical request identities.
    """

    def __init__(self, model: str, max_output_tokens: int,
                 model_settings: Mapping[str, Any] | None = None):
        if not isinstance(model, str) or not model.strip():
            raise PreflightError("preflight model must be nonempty")
        if type(max_output_tokens) is not int or max_output_tokens < 16:
            raise PreflightError(
                "preflight max_output_tokens must satisfy the OpenAI minimum of 16")
        if model_settings is not None and not isinstance(model_settings, Mapping):
            raise PreflightError("preflight model settings must be a mapping")
        self.model = model
        self.max_output_tokens = max_output_tokens
        # Detach nested caller-owned values and make later builds type-exact.
        self._model_settings = json.loads(canonical(dict(model_settings or {})))

    @property
    def model_settings(self) -> dict[str, Any]:
        return json.loads(canonical(self._model_settings))

    @staticmethod
    def _identity(action: str) -> str:
        return (PREFLIGHT_MESSAGE_IDENTITY if action == "message"
                else PREFLIGHT_VERDICT_IDENTITY)

    @staticmethod
    def _fixtures(action: str):
        own_namespace = f"{PREFLIGHT_NAMESPACE}/{action}/own"
        peer_namespace = f"{PREFLIGHT_NAMESPACE}/{action}/peer"
        own = generate_fixture(own_namespace)
        peer = generate_fixture(peer_namespace, "omit" if action == "verdict" else None)
        return own, peer

    def build(self, action: str, *, attempt: int = 0,
              prior_results: Sequence[ProviderResult | Mapping[str, Any]] = ()) -> tuple[
                  ProviderRequest, RenderedPrompt]:
        if action not in _ACTIONS:
            raise PreflightError(f"preflight action must be one of {_ACTIONS}")
        if type(attempt) is not int or attempt < 0:
            raise PreflightError("preflight attempt must be a nonnegative integer")
        if isinstance(prior_results, (str, bytes)) or not isinstance(prior_results, Sequence):
            raise PreflightError("preflight prior_results must be a sequence")
        if attempt != len(prior_results):
            raise PreflightError("preflight attempt must equal the prior-result count")
        statuses = [_status(row) for row in prior_results]
        if any(status is None for status in statuses):
            raise PreflightError("preflight prior result lacks a status")
        if "ok" in statuses:
            raise PreflightError("a completed preflight request cannot be rebuilt")
        overlength_count = sum(status == "overlength" for status in statuses)
        retryable_count = sum(status == "retryable_error" for status in statuses)
        if any(status not in {"overlength", "retryable_error"} for status in statuses):
            raise PreflightError(
                "only overlength or retryable transport results permit another attempt")
        if any(status == "retryable_error" and not _known_not_completed(row)
               for status, row in zip(statuses, prior_results)):
            raise PreflightError(
                "a retryable transport attempt must be explicitly known not completed")
        if retryable_count > 2:
            raise PreflightError(
                "at most two known-not-completed retryable transport attempts are permitted")
        if overlength_count > 1:
            raise PreflightError("only one message-overlength retry is permitted")
        if overlength_count and action != "message":
            raise PreflightError("overlength retry feedback applies only to messages")
        if len(prior_results) >= _MAX_ATTEMPTS[action]:
            raise PreflightError("preflight attempt ceiling exceeded")

        own, peer = self._fixtures(action)
        view = request_for(
            History(), own.packet, peer.packet, "alice", "calibration", "full",
            action=action,
            message_feedback=(MESSAGE_OVERLENGTH_FEEDBACK if overlength_count else None),
        )
        return provider_request_from_agent_request(
            self._identity(action), self.model, view, self.max_output_tokens,
            self.model_settings, attempt=attempt,
        )

    def fixture_inventory(self) -> dict[str, Any]:
        inventory: dict[str, Any] = {}
        packet_hashes: set[str] = set()
        task_ids: set[str] = set()
        for action in _ACTIONS:
            own, peer = self._fixtures(action)
            entries = (("own", own), ("peer", peer))
            for role, fixture in entries:
                if (fixture.packet.packet_hash in packet_hashes
                        or fixture.packet.task_id in task_ids):
                    raise PreflightError("preflight fixture namespaces are not disjoint")
                packet_hashes.add(fixture.packet.packet_hash)
                task_ids.add(fixture.packet.task_id)
                inventory[f"{action}/{role}"] = {
                    "namespace": fixture.namespace,
                    "packet_hash": fixture.packet.packet_hash,
                    "task_id": fixture.packet.task_id,
                    "expected_correct": fixture.packet.is_correct,
                }
        return inventory


def _inventory_record(action: str, shape: str, request: ProviderRequest,
                      rendered: RenderedPrompt) -> dict[str, Any]:
    payload = OpenAIResponsesProvider.build_payload(request)
    payload_json = canonical(payload)
    return {
        "logical_request_identity": request.request_identity,
        "action": action,
        "projected_shape": shape,
        "expected_result": ("plain_text_at_most_200_code_points"
                            if action == "message" else "REJECT"),
        "provider_request": request.to_dict(),
        "provider_request_hash": digest(request.to_dict()),
        "rendered_prompt": rendered.to_dict(),
        "openai_responses_payload": payload,
        "openai_responses_payload_hash": digest(payload),
        "openai_responses_payload_utf8_bytes": len(payload_json.encode("utf-8")),
    }


def preflight_inventory(factory: PreflightRequestFactory) -> dict[str, Any]:
    """Render every permitted Gate 0 content shape without dispatching it."""
    if not isinstance(factory, PreflightRequestFactory):
        raise TypeError("preflight inventory requires PreflightRequestFactory")
    message, message_rendered = factory.build("message")
    retry, retry_rendered = factory.build(
        "message", attempt=1, prior_results=({"status": "overlength"},))
    verdict, verdict_rendered = factory.build("verdict")
    records = [
        _inventory_record("message", "initial_attempt", message, message_rendered),
        _inventory_record("message", "overlength_retry", retry, retry_rendered),
        _inventory_record("verdict", "initial_attempt", verdict, verdict_rendered),
    ]
    initial = [records[0], records[2]]
    longest = max(records, key=lambda row: row["provider_request"][
        "conservative_input_token_bound"])
    report = {
        "artifact_type": "gate_0_transport_schema_preflight_inventory_v1",
        "namespace": PREFLIGHT_NAMESPACE,
        "nonexperimental": True,
        "dispatch_performed": False,
        "logical_request_count": 2,
        "rendered_attempt_shape_count": len(records),
        "model": factory.model,
        "model_settings": factory.model_settings,
        "max_output_tokens_per_attempt": factory.max_output_tokens,
        "fixture_inventory": factory.fixture_inventory(),
        "initial_input_token_bound": sum(
            row["provider_request"]["conservative_input_token_bound"] for row in initial),
        "initial_output_token_allowance": 2 * factory.max_output_tokens,
        "maximum_with_overlength_retry_input_token_bound": sum(
            row["provider_request"]["conservative_input_token_bound"] for row in records),
        "maximum_with_overlength_retry_output_token_allowance": (
            len(records) * factory.max_output_tokens),
        "longest_shape": {
            "logical_request_identity": longest["logical_request_identity"],
            "projected_shape": longest["projected_shape"],
            "conservative_input_token_bound": longest["provider_request"][
                "conservative_input_token_bound"],
            "openai_responses_payload_hash": longest["openai_responses_payload_hash"],
        },
        "requests": records,
    }
    return {**report, "inventory_hash": digest(report)}


def _result_dict(value: ProviderResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, ProviderResult):
        return value.to_dict()
    if not isinstance(value, Mapping):
        raise PreflightError("preflight results must be ProviderResult objects or mappings")
    nested = value.get("provider_result", value)
    if isinstance(nested, ProviderResult):
        result = nested.to_dict()
    elif isinstance(nested, Mapping):
        result = json.loads(canonical(dict(nested)))
    else:
        raise PreflightError("preflight provider_result must be an object")
    if "status" not in result and isinstance(value.get("status"), str):
        result["status"] = value["status"]
    return result


def _audit_complete(result: Mapping[str, Any]) -> bool:
    usage = result.get("usage")
    timing = result.get("timing")
    metadata = result.get("model_metadata")
    return (
        provider_result_matches_raw_response(result)
        and isinstance(result.get("provider_request_id"), str)
        and bool(result["provider_request_id"].strip())
        and isinstance(result.get("returned_model"), str)
        and bool(result["returned_model"].strip())
        and isinstance(metadata, Mapping)
        and metadata.get("model") == result.get("returned_model")
        and isinstance(metadata.get("service_tier"), str)
        and bool(metadata["service_tier"].strip())
        and isinstance(usage, Mapping)
        and all(type(usage.get(name)) is int and usage[name] >= 0
                for name in (
                    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                    "output_tokens", "reasoning_tokens", "total_tokens",
                ))
        and (usage["cached_input_tokens"] + usage["cache_write_input_tokens"]
             <= usage["input_tokens"])
        and usage["reasoning_tokens"] <= usage["output_tokens"]
        and usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
        and isinstance(timing, Mapping)
        and isinstance(timing.get("started_at"), str)
        and isinstance(timing.get("ended_at"), str)
        and isinstance(timing.get("elapsed_seconds"), (int, float))
        and not isinstance(timing.get("elapsed_seconds"), bool)
        and timing["elapsed_seconds"] >= 0
    )


def _valid_message(result: Mapping[str, Any]) -> bool:
    text = result.get("output_text")
    return (result.get("status") == "ok" and isinstance(text, str)
            and len(text) <= 200 and result.get("parsed_output") == text)


def _valid_reject(result: Mapping[str, Any]) -> bool:
    if result.get("status") != "ok":
        return False
    try:
        verdict = parse_durable_verdict(result)
    except ValueError:
        return False
    return verdict.verdict == "REJECT"


def preflight_assessment(results: Mapping[
        str, Sequence[ProviderResult | Mapping[str, Any]]]) -> dict[str, Any]:
    """Validate Gate 0 outcomes and the required provider audit evidence.

    A pass requires exactly the two frozen logical identities, a successful
    bounded text message, a schema-valid ``REJECT`` for the faulty omit packet,
    no more than one overlength content retry, and complete request/model/usage/
    timing evidence on both terminal responses.  Known-not-completed transport
    attempts may precede a terminal response but do not relax these conditions.
    """
    if not isinstance(results, Mapping) or set(results) != {
            PREFLIGHT_MESSAGE_IDENTITY, PREFLIGHT_VERDICT_IDENTITY}:
        raise PreflightError("preflight results must contain exactly the two frozen identities")
    observations = []
    passed = True
    for action, identity in (("message", PREFLIGHT_MESSAGE_IDENTITY),
                             ("verdict", PREFLIGHT_VERDICT_IDENTITY)):
        values = results[identity]
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
            raise PreflightError(f"{identity} results must be a nonempty sequence")
        rows = [_result_dict(value) for value in values]
        statuses = [row.get("status") for row in rows]
        overlength_count = sum(status == "overlength" for status in statuses)
        retryable_count = sum(status == "retryable_error" for status in statuses[:-1])
        retry_shape_valid = (
            len(rows) <= _MAX_ATTEMPTS[action]
            and overlength_count <= (1 if action == "message" else 0)
            and retryable_count <= 2
            and (overlength_count == 0 or statuses[-1] != "overlength")
        )
        for row in rows[:-1]:
            status = row.get("status")
            if status == "overlength" and action == "message":
                continue
            if not (status == "retryable_error" and row.get("known_not_completed") is True):
                retry_shape_valid = False
        terminal = rows[-1]
        outcome_valid = (_valid_message(terminal) if action == "message"
                         else _valid_reject(terminal))
        audit_complete = _audit_complete(terminal)
        identity_passed = retry_shape_valid and outcome_valid and audit_complete
        passed = passed and identity_passed
        observations.append({
            "logical_request_identity": identity,
            "action": action,
            "attempt_count": len(rows),
            "status_sequence": statuses,
            "overlength_count": overlength_count,
            "known_not_completed_retryable_count": retryable_count,
            "retry_shape_valid": retry_shape_valid,
            "outcome_valid": outcome_valid,
            "provider_audit_complete": audit_complete,
            "terminal_result_hash": digest(terminal),
            "passed": identity_passed,
        })
    report = {
        "assessment_type": "gate_0_transport_schema_preflight_v1",
        "namespace": PREFLIGHT_NAMESPACE,
        "expected_logical_requests": 2,
        "observed_logical_requests": len(observations),
        "pass_conditions": {
            "message": "one bounded plain-text completion",
            "verdict": "one schema-valid REJECT of the faulty omit packet",
            "message_overlength_retries": "at_most_one",
            "known_not_completed_transport_retries": "at_most_two_per_logical_request",
            "provider_audit": (
                "raw response, request ID, model metadata, complete usage details, and timing"),
        },
        "observations": observations,
        "passed": passed,
    }
    return {**report, "assessment_hash": digest(report)}


def durable_preflight_assessment(run: Any) -> dict[str, Any]:
    """Recompute Gate 0 from the exact saved requests, wire payloads, and results."""
    config = getattr(run, "config", None)
    plan = getattr(run, "plan", None)
    if (config is None or plan is None or config.source != "empirical-live"
            or config.run_type != "pilot"):
        raise PreflightError("durable Gate 0 requires an empirical pilot run")
    specs = [spec for spec in plan.requests if spec.stage == "preflight"]
    if ([(spec.identity, spec.action) for spec in specs] != [
            (PREFLIGHT_MESSAGE_IDENTITY, "message"),
            (PREFLIGHT_VERDICT_IDENTITY, "verdict")]):
        raise PreflightError("durable plan does not contain the exact Gate 0 identities")
    factory = PreflightRequestFactory(
        config.model,
        config.limits.max_output_tokens_per_request,
        dict(config.model_settings),
    )
    inventory = preflight_inventory(factory)
    rows_by_identity = {
        identity: run.result_events(identity)
        for identity in (PREFLIGHT_MESSAGE_IDENTITY, PREFLIGHT_VERDICT_IDENTITY)
    }
    base = preflight_assessment(rows_by_identity)
    request_checks = []
    all_requests_verified = True
    all_wires_verified = True
    for spec in specs:
        rows = rows_by_identity[spec.identity]
        for attempt, row in enumerate(rows):
            request, _rendered = factory.build(
                spec.action, attempt=attempt, prior_results=rows[:attempt])
            expected_request = request.to_dict()
            expected_request_hash = digest(expected_request)
            expected_wire = OpenAIResponsesProvider.build_payload(request)
            expected_wire_hash = digest(expected_wire)
            request_hash = row.get("request_hash")
            wire_hash = row.get("wire_payload_hash")
            try:
                saved_request = run.request_payload(request_hash)
            except (OSError, TypeError, ValueError):
                saved_request = None
            try:
                saved_wire = run.wire_payload(wire_hash)
            except (OSError, TypeError, ValueError):
                saved_wire = None
            request_verified = (
                row.get("attempt") == attempt
                and request_hash == expected_request_hash
                and saved_request == expected_request
            )
            wire_verified = (
                wire_hash == expected_wire_hash
                and saved_wire == expected_wire
            )
            all_requests_verified = all_requests_verified and request_verified
            all_wires_verified = all_wires_verified and wire_verified
            request_checks.append({
                "logical_request_identity": spec.identity,
                "attempt": attempt,
                "request_hash": request_hash,
                "expected_request_hash": expected_request_hash,
                "request_payload_verified": request_verified,
                "wire_payload_hash": wire_hash,
                "expected_wire_payload_hash": expected_wire_hash,
                "wire_payload_verified": wire_verified,
            })
    manifest = run.manifest
    authorized_inventory = manifest.get("authorized_preflight_inventory_hash")
    inventory_verified = authorized_inventory == inventory["inventory_hash"]
    identity_bound = isinstance(manifest.get("observed_provider_identity"), Mapping)
    report = {
        **{key: value for key, value in base.items() if key != "assessment_hash"},
        "base_assessment_hash": base["assessment_hash"],
        "inventory_hash": inventory["inventory_hash"],
        "authorized_inventory_hash": authorized_inventory,
        "authorization_inventory_verified": inventory_verified,
        "request_payloads_verified": all_requests_verified,
        "wire_payloads_verified": all_wires_verified,
        "provider_identity_bound": identity_bound,
        "attempt_artifacts": request_checks,
    }
    report["passed"] = bool(
        base["passed"] and inventory_verified and all_requests_verified
        and all_wires_verified and identity_bound
    )
    return {**report, "assessment_hash": digest(report)}
