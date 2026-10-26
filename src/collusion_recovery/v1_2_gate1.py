"""Fail-closed release controller for fresh protocol-v1.2 Gate 1.

Proposal and authorization inspection are credential-free.  The live path is
restricted to the frozen 20-request calibration, atomically consumes a fresh
external authorization before credential access, and hard-stops before smoke.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
from typing import Any, Mapping
import uuid

from .budget import (
    BudgetLedger,
    DispatchReleaseLimits,
    LONG_CONTEXT_INPUT_MULTIPLIER,
    LONG_CONTEXT_OUTPUT_MULTIPLIER,
    LONG_CONTEXT_THRESHOLD_INPUT_TOKENS,
    pricing_multipliers,
)
from .common import canonical, digest
from .events import file_hash
from .gate1_calibration import verify_gate1_passed_lineage
from .gates import calibration_assessment
from .live_config import LiveConfig
from .orchestration import (
    AuthorizationRequiredError,
    DispatchAuthorizationArtifact,
    DurableRun,
    ExecutionContext,
    OBSERVATION_STATUSES,
    PROVIDER_IDENTITY_STATUSES,
    ResumeError,
    ScheduledRequest,
    StagedExecutor,
    V1_2_AMBIGUOUS_5XX_RETRY_POLICY,
    V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
    V1_2_CALIBRATION_NAMESPACE,
    V1_2_GATE1_CONTROLLER_ID,
    V1_2_GATE1_NAMESPACE,
    V1_2_GATE1_RUN_ID,
    _V1_2_GATE1_STAGED_TEST_CAPABILITY,
    _provider_identity_candidate,
    authorization_binding,
    build_request_plan,
    validate_dispatch_authorization,
)
from .providers.openai import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
    provider_result_matches_raw_response,
)
from .providers.base import ProviderResult, ProviderStatus, ProviderTiming
from .stage2 import (
    STAGE2_SDK_VERSION,
    STAGE2_TIMEOUT_SECONDS,
    STAGE2_ENV_FILE,
    STAGE2_PREFLIGHT_INVENTORY_HASH,
    _load_local_openai_key,
    _stage2_transport_metadata,
)
from .staged_requests import ScientificRequestFactory, protocol_prompt_inventory
from .visibility import ROOT


PROPOSAL_PATH = ROOT / "configs/gate1_v1_2_calibration_proposal.json"
AUTHORIZATION_SCHEMA_PATH = ROOT / "release/GATE1_V1_2_HUMAN_AUTHORIZATION_SCHEMA.json"
PRIOR_GATE1_PROPOSAL_PATH = ROOT / "configs/gate1_calibration_proposal.json"
ACTIVATION_DECISION_PATH = ROOT / "V1_2_ACTIVATION_DECISION.md"
PROTOCOL_PATH = ROOT / "PROTOCOL_V1_2_ADDENDUM.md"
RUN_DIRECTORY = f"runs/empirical/gate1-calibration-v1.2-{V1_2_GATE1_RUN_ID}"
AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS = 15 * 60
AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS = 60
PRICING_MAX_AGE_SECONDS = 24 * 60 * 60
AUTHORIZATION_MAX_BYTES = 64 * 1024
IMMUTABLE_JSON_MAX_BYTES = 8 * 1024 * 1024
AUTHORIZATION_RECEIPT_DIRECTORY = (
    Path(pwd.getpwuid(os.getuid()).pw_dir)
    / ".collusion-recovery-authorization-receipts"
    / "gate1-v1.2"
)
V1_2_GATE1_BINDING_EVENT = "v1_2_gate1_calibration_binding"
V1_2_GATE1_AUTHORIZATION_CLAIM_EVENT = "v1_2_gate1_authorization_claimed"
V1_2_GATE1_EXECUTION_MODE_EVENT = "v1_2_gate1_execution_mode_binding"
EMPIRICAL_EXECUTION_MODE = "empirical_live"
FAKE_EXECUTION_MODE = "fake_transport_test"
EMPIRICAL_EXECUTOR_PROVENANCE = "official_openai_client_from_run_v1_2_gate1"
FAKE_EXECUTOR_PROVENANCE = "explicit_offline_fake_factory"

# The only importable executor capability is deliberately fake-only.  Empirical
# provenance is never selected by a caller-supplied token, provider, transport,
# or executor factory: the public run path constructs the exact official SDK
# objects itself, binds the empirical mode only after validating those objects,
# and immediately executes without returning the controller.
_FAKE_EXECUTOR_CAPABILITY = object()

# Source-bound from the official GPT-6.1 Sol model page immediately before the
# final release package was rendered. Runtime proposal validation never accepts
# a caller-supplied override; the optional builder argument exists only for
# deterministic offline review/tests. An unclaimed release expires 24 hours
# after this verification and must be replaced rather than silently repriced.
V1_2_PRICING_RECORD: Mapping[str, Any] | None = {
    "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
    "reverified_at": "2026-10-25T12:21:28-07:00",
    "standard_rates_usd_per_million": {
        "input": "2.00",
        "cached_input": "0.10",
        "cache_write_input": "2.50",
        "output": "10.00",
    },
}
HUMAN_ACKNOWLEDGEMENT = (
    "I authorize exactly the Gate 1 v1.2 capability-calibration scope and exact "
    "ceilings bound by this proposal. I understand this fresh release hard-stops "
    "before smoke and authorizes no hypothesis-bearing experiment request."
)

_AUTH_FIELDS = {
    "artifact_type",
    "authorization_uuid",
    "authorization_id",
    "authorization_nonce",
    "gate1_run_id",
    "gate1_run_directory",
    "confirmed_by",
    "confirmed_at",
    "proposal_hash",
    "approved_scope",
    "approved_caps",
    "approved_pricing_contract_hash",
    "approved_transport_hash",
    "approved_retry_policy_hash",
    "approved_provider_identity",
    "approved_capability_bridge_hash",
    "approved_implementation_hash",
    "approved_calibration_inventory_hash",
    "approved_gate1_config_hash",
    "approved_gate1_plan_hash",
    "approved_dispatch_authorization_hash",
    "allow_paid_calls",
    "human_acknowledgement",
    "authorization_hash",
}


class V12Gate1ReadinessError(RuntimeError):
    """The v1.2 Gate 1 proposal, authorization, run, or evidence is unsafe."""


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise V12Gate1ReadinessError(
            f"{label} must be a nonempty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V12Gate1ReadinessError(f"{label} is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V12Gate1ReadinessError(f"{label} requires an explicit UTC offset")
    return parsed


def _utc_now_datetime(now: datetime | None = None) -> datetime:
    value = datetime.now(timezone.utc) if now is None else now
    if value.tzinfo is None or value.utcoffset() is None:
        raise V12Gate1ReadinessError(
            "authorization clock requires an explicit UTC offset")
    return value.astimezone(timezone.utc)


def _validate_pricing_window(
        proposal: Mapping[str, Any], *, now: datetime | None = None) -> datetime:
    current = _utc_now_datetime(now)
    pricing = _parse_timestamp(
        proposal["pricing_contract"]["reverified_at"],
        "pricing reverification",
    ).astimezone(timezone.utc)
    if pricing > current + timedelta(seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS):
        raise V12Gate1ReadinessError(
            "pricing reverification timestamp is implausibly far in the future")
    if current - pricing > timedelta(seconds=PRICING_MAX_AGE_SECONDS):
        raise V12Gate1ReadinessError(
            "reviewed pricing is stale; regenerate and reauthorize the proposal")
    return pricing


def _validate_first_claim_window(
        authorization: Mapping[str, Any], proposal: Mapping[str, Any], *,
        now: datetime | None = None) -> None:
    current = _utc_now_datetime(now)
    confirmed = _parse_timestamp(
        authorization.get("confirmed_at"), "confirmed_at").astimezone(timezone.utc)
    pricing = _validate_pricing_window(proposal, now=current)
    if confirmed > current + timedelta(seconds=AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS):
        raise V12Gate1ReadinessError(
            "human authorization timestamp is implausibly far in the future")
    if confirmed < pricing:
        raise V12Gate1ReadinessError(
            "human authorization predates the pricing and proposal reverification")
    if current - confirmed > timedelta(
            seconds=AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS):
        raise V12Gate1ReadinessError(
            "unused human authorization expired before its first claim")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_exclusive_json(path: Path, value: Mapping[str, Any]) -> None:
    """Crash-safely publish canonical JSON without ever replacing a target.

    The complete, fsynced value is first written to an owner-only same-directory
    temporary inode.  A hard link is then the atomic no-clobber publication
    point.  A crash can therefore leave either no target or the complete target,
    never a partially written receipt/report.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (canonical(dict(value)) + "\n").encode("utf-8")
    temporary = path.parent / (
        f".{path.name}.publish-{os.getpid()}-{secrets.token_hex(16)}"
    )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
        0o600,
    )
    closed = False
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while publishing immutable JSON")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_size != len(payload):
            raise OSError("immutable JSON temporary file has the wrong size")
        os.close(descriptor)
        closed = True
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(path.parent)
    finally:
        if not closed:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        else:
            _fsync_directory(path.parent)


def _write_immutable_json(
        path: Path, value: Mapping[str, Any], label: str) -> None:
    try:
        _publish_exclusive_json(Path(path), value)
    except FileExistsError:
        if canonical(_read_immutable_json_once(
                Path(path), label)) != canonical(dict(value)):
            raise V12Gate1ReadinessError(
                f"existing {label} differs from durable evidence")


def _read_immutable_json_once(path: Path, label: str) -> dict[str, Any]:
    """Read an existing published file by descriptor without symlink races."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise V12Gate1ReadinessError(
            f"this platform cannot enforce no-follow reads for {label}"
        )
    try:
        descriptor = os.open(
            path, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError as exc:
        raise V12Gate1ReadinessError(f"{label} is unavailable or invalid") from exc
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_mode & 0o077
                or before.st_size < 2
                or before.st_size > IMMUTABLE_JSON_MAX_BYTES):
            raise V12Gate1ReadinessError(
                f"{label} must be a bounded owner-only regular file"
            )
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (len(raw) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns)):
            # Publishing uses a same-directory hard link.  The winning writer
            # subsequently unlinks its private temporary name, which changes
            # inode ctime/link count without changing the published inode or
            # bytes.  Treat dev/ino/size/mtime plus the exact read length as the
            # stable-content boundary so an identical concurrent loser can
            # safely compare the already-published value.
            raise V12Gate1ReadinessError(
                f"{label} changed while it was being read"
            )
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise V12Gate1ReadinessError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise V12Gate1ReadinessError(f"{label} must be an object")
    return value


def _validate_external_parent_chain(path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    parent = lexical.parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current = current / part
        try:
            details = current.lstat()
        except OSError as exc:
            raise V12Gate1ReadinessError(
                "human authorization parent path is unavailable"
            ) from exc
        if (stat.S_ISLNK(details.st_mode)
                or not stat.S_ISDIR(details.st_mode)
                or details.st_uid not in {0, os.getuid()}
                or details.st_mode & 0o022):
            raise V12Gate1ReadinessError(
                "human authorization parent components must be safe, "
                "non-writable directories owned by root or the current user"
            )
    if parent.lstat().st_uid != os.getuid():
        raise V12Gate1ReadinessError(
            "human authorization immediate parent must be current-user-owned"
        )
    resolved = parent.resolve(strict=True) / lexical.name
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return lexical
    raise V12Gate1ReadinessError(
        "human authorization must be outside the repository"
    )


def _read_external_authorization_once(path: Path) -> dict[str, Any]:
    """Open, validate, and read the external authorization through one fd."""
    lexical = _validate_external_parent_chain(Path(path))
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise V12Gate1ReadinessError(
            "this platform cannot enforce no-follow authorization reads"
        )
    try:
        descriptor = os.open(
            lexical, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError as exc:
        raise V12Gate1ReadinessError(
            "human authorization is unavailable or invalid"
        ) from exc
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_mode & 0o077
                or before.st_nlink != 1):
            raise V12Gate1ReadinessError(
                "human authorization must be an owner-only regular file"
            )
        if before.st_size < 2 or before.st_size > AUTHORIZATION_MAX_BYTES:
            raise V12Gate1ReadinessError(
                "human authorization exceeds the bounded file size"
            )
        chunks: list[bytes] = []
        remaining = AUTHORIZATION_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 16 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        stable = (
            before.st_dev, before.st_ino, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns, before.st_nlink,
        ) == (
            after.st_dev, after.st_ino, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns, after.st_nlink,
        )
        if (not stable or len(raw) != before.st_size
                or len(raw) > AUTHORIZATION_MAX_BYTES):
            raise V12Gate1ReadinessError(
                "human authorization changed while it was being read"
            )
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise V12Gate1ReadinessError(
            "human authorization is unavailable or invalid"
        ) from exc
    if not isinstance(value, dict):
        raise V12Gate1ReadinessError("human authorization must be an object")
    return value


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise V12Gate1ReadinessError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise V12Gate1ReadinessError(f"{label} must be an object")
    return value


def _validated_pricing_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the small source-bound record used for the final pricing freeze."""
    expected = {"source", "reverified_at", "standard_rates_usd_per_million"}
    if not isinstance(value, Mapping) or set(value) != expected:
        raise V12Gate1ReadinessError(
            f"v1.2 pricing record must contain exactly {sorted(expected)}")
    source = value["source"]
    if source != "https://developers.openai.com/api/docs/models/gpt-6.1-sol":
        raise V12Gate1ReadinessError("v1.2 pricing must use the official model page")
    _parse_timestamp(value["reverified_at"], "v1.2 pricing reverification")
    rates = value["standard_rates_usd_per_million"]
    rate_names = {"input", "cached_input", "cache_write_input", "output"}
    if not isinstance(rates, Mapping) or set(rates) != rate_names:
        raise V12Gate1ReadinessError(
            f"v1.2 pricing rates must contain exactly {sorted(rate_names)}")
    normalized: dict[str, str] = {}
    for name in sorted(rate_names):
        raw = rates[name]
        if isinstance(raw, bool) or isinstance(raw, float):
            raise V12Gate1ReadinessError("v1.2 pricing rates must be exact strings")
        try:
            amount = Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise V12Gate1ReadinessError("v1.2 pricing rate is malformed") from exc
        if not amount.is_finite() or amount <= 0:
            raise V12Gate1ReadinessError("v1.2 pricing rates must be positive")
        normalized[name] = str(amount)
    return {
        "source": source,
        "reverified_at": value["reverified_at"],
        "standard_rates_usd_per_million": normalized,
        "long_context_threshold_input_tokens": LONG_CONTEXT_THRESHOLD_INPUT_TOKENS,
        "long_context_input_and_cache_multiplier": str(
            LONG_CONTEXT_INPUT_MULTIPLIER),
        "long_context_output_multiplier": str(LONG_CONTEXT_OUTPUT_MULTIPLIER),
        "classification_basis": (
            "per-attempt conservative input bound for reservations; reported "
            "input usage for completed-attempt reconciliation"
        ),
    }


def _base_values(
        pricing_record: Mapping[str, Any] | None = None,
        ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    prior = _read_json(PRIOR_GATE1_PROPOSAL_PATH, "v1.1 Gate 1 proposal")
    try:
        template = json.loads(canonical(prior["gate1_config_template"]))
        required_identity = json.loads(canonical(prior["required_provider_identity"]))
        pricing = json.loads(canonical(prior["pricing_contract"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise V12Gate1ReadinessError("v1.1 capability contract is malformed") from exc
    template.update({
        "run_id": V1_2_GATE1_RUN_ID,
        "protocol_version": "v1.2",
        "split": V1_2_GATE1_NAMESPACE,
        "seed_namespace": V1_2_GATE1_NAMESPACE,
        "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
        "stage": "calibration",
        "blocks": 5,
    })
    if pricing_record is not None:
        pricing = _validated_pricing_record(pricing_record)
        rates = pricing["standard_rates_usd_per_million"]
        template["pricing"] = {
            "source": pricing["source"],
            "verified_at": pricing["reverified_at"],
            "input_usd_per_million": rates["input"],
            "cached_input_usd_per_million": rates["cached_input"],
            "cache_write_input_usd_per_million": rates["cache_write_input"],
            "output_usd_per_million": rates["output"],
        }
    return template, required_identity, pricing


def _projection_config(
        limits: Mapping[str, Any],
        pricing_record: Mapping[str, Any] | None = None) -> LiveConfig:
    values, _identity, _pricing = _base_values(pricing_record)
    values["limits"] = json.loads(canonical(dict(limits)))
    values["authorization"] = {
        "kind": "paid",
        "authorization_id": "proposal-only-not-a-dispatch-release",
        "approved_at": values["pricing"]["verified_at"],
        "approved_budget_usd": "999999999",
        "allow_paid_calls": True,
    }
    return LiveConfig.from_mapping(values)


def _shape_cost(config: LiveConfig, input_tokens: int, output_tokens: int,
                *, strict: bool) -> Decimal:
    input_rate = (max(
        config.pricing.input_usd_per_million,
        config.pricing.cached_input_usd_per_million,
        config.pricing.cache_write_input_usd_per_million,
    ) if strict else config.pricing.input_usd_per_million)
    input_multiplier, output_multiplier = pricing_multipliers(
        config.pricing, input_tokens)
    return (
        Decimal(input_tokens) * input_rate * input_multiplier
        + Decimal(output_tokens) * config.pricing.output_usd_per_million
        * output_multiplier
    ) / Decimal(1_000_000)


def _inventory(config: LiveConfig) -> dict[str, Any]:
    plan = build_request_plan(config)
    if (plan.planned_counts() != {
            "logical_requests": 20,
            "by_stage": {"calibration": 20},
            "by_phase": {"calibration": 20},
            "checkpoints": 0,
            }):
        raise V12Gate1ReadinessError("v1.2 Gate 1 plan escaped calibration-only scope")
    factory = ScientificRequestFactory(config, plan)
    rows = []
    for spec in plan.requests:
        request, rendered = factory.build(spec, ExecutionContext(None, 0, ()))
        wire = OpenAIResponsesProvider.build_payload(request)
        rows.append({
            "identity": spec.identity,
            "ordinal": spec.ordinal,
            "fixture_index": spec.metadata["fixture_index"],
            "fixture_namespace": spec.metadata["fixture_namespace"],
            "request_hash": digest(request.to_dict()),
            "prompt_hash": rendered.prompt_hash,
            "wire_payload_hash": digest(wire),
            "wire_payload_utf8_bytes": len(canonical(wire).encode("utf-8")),
            "conservative_input_token_bound": request.conservative_input_token_bound,
            "max_output_tokens": request.max_output_tokens,
            "strict_reservation_cost_usd": str(_shape_cost(
                config, request.conservative_input_token_bound,
                request.max_output_tokens, strict=True)),
            "nominal_reservation_cost_usd": str(_shape_cost(
                config, request.conservative_input_token_bound,
                request.max_output_tokens, strict=False)),
        })
    longest = max(rows, key=lambda row: (
        row["conservative_input_token_bound"], row["max_output_tokens"]))
    most_expensive = max(rows, key=lambda row: Decimal(row["strict_reservation_cost_usd"]))
    base_input = sum(row["conservative_input_token_bound"] for row in rows)
    base_output = sum(row["max_output_tokens"] for row in rows)
    strict_cost = sum(Decimal(row["strict_reservation_cost_usd"]) for row in rows)
    nominal_cost = sum(Decimal(row["nominal_reservation_cost_usd"]) for row in rows)
    # Known-not-completed retries release their reservation.  The one permitted
    # ambiguous 5xx consumes a full reservation, so the exact aggregate caps
    # include the largest possible duplicate shape once.
    caps = {
        "max_dispatch_attempts": 61,
        "max_total_input_tokens": base_input + longest["conservative_input_token_bound"],
        "max_total_output_tokens": base_output + longest["max_output_tokens"],
        "max_cost_usd": str((
            strict_cost + Decimal(most_expensive["strict_reservation_cost_usd"])
        ).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)),
        "max_in_flight": 1,
        "max_input_tokens_per_request": longest["conservative_input_token_bound"],
        "max_output_tokens_per_request": max(row["max_output_tokens"] for row in rows),
    }
    return {
        "artifact_type": "gate_1_v1_2_no_dispatch_projection_v1",
        "projection_only": True,
        "dispatch_performed": False,
        "protocol_version": "v1.2",
        "protocol_hash": plan.protocol_hash,
        "implementation_hash": plan.implementation_hash,
        "plan_hash": plan.plan_hash,
        "fixture_inventory_hash": digest(dict(plan.fixture_inventory)),
        "logical_request_count": 20,
        "verdict_request_count": 20,
        "billable_content_shape_count": 20,
        "known_not_completed_retry_slots": 40,
        "ambiguous_5xx_retry_slots": 1,
        "maximum_dispatch_attempts": 61,
        "logical_identity_hash": digest([row["identity"] for row in rows]),
        "inventory_hash": digest(rows),
        "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
        "clean_fixture_count": 10,
        "faulty_fixture_count": 10,
        "conservative_input_token_bound_sum_before_5xx_allowance": base_input,
        "output_token_allowance_sum_before_5xx_allowance": base_output,
        "long_context_projected_shape_count": sum(
            row["conservative_input_token_bound"]
            > LONG_CONTEXT_THRESHOLD_INPUT_TOKENS for row in rows),
        "longest": longest,
        "within_model_context": (
            longest["conservative_input_token_bound"] + longest["max_output_tokens"]
            <= config.model_context_tokens),
        "pricing_projection": {
            "strict_base_shapes_reservation_ceiling_usd": str(
                strict_cost.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)),
            "strict_with_ambiguous_5xx_allowance_usd": caps["max_cost_usd"],
            "nominal_uncached_base_shapes_ceiling_usd": str(
                nominal_cost.quantize(Decimal("0.000001"))),
        },
        "exact_caps": caps,
        "requests": rows,
    }


def build_v1_2_gate1_proposal(
        *, verify_prior_lineage: bool = True,
        pricing_record: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Render the keyless proposal; a source-bound price freeze makes it final.

    ``pricing_record`` is an offline review/test surface.  Runtime validation
    deliberately calls this function without an override and therefore accepts
    only :data:`V1_2_PRICING_RECORD` once it is frozen in source.
    """
    source_pricing = V1_2_PRICING_RECORD if pricing_record is None else pricing_record
    pricing_ready = source_pricing is not None
    base, required_identity, pricing_contract = _base_values(source_pricing)
    if verify_prior_lineage:
        prior_lineage = verify_gate1_passed_lineage()
    else:
        prior = _read_json(PRIOR_GATE1_PROPOSAL_PATH, "v1.1 Gate 1 proposal")
        prior_lineage = {
            "run_id": prior["gate1_protocol"]["run_id"],
            "protocol_version": prior["gate1_protocol"]["protocol_version"],
            "protocol_hash": prior["gate1_protocol"]["protocol_hash"],
            "proposal_hash": prior["proposal_hash"],
            "provider_identity": required_identity,
            "transport_hash": prior["transport_hash"],
            "passed": True,
            "unverified_projection_only": True,
        }
    provisional = {
        "max_requests": 1000,
        "max_total_input_tokens": base["model_context_tokens"] * 1000,
        "max_total_output_tokens": base["limits"]["max_output_tokens_per_request"] * 1000,
        "max_input_tokens_per_request": (
            base["model_context_tokens"] - base["limits"]["max_output_tokens_per_request"]),
        "max_output_tokens_per_request": base["limits"]["max_output_tokens_per_request"],
        "max_in_flight": 1,
    }
    first_config = _projection_config(provisional, source_pricing)
    first_inventory = _inventory(first_config)
    caps = first_inventory["exact_caps"]
    exact_limits = {
        "max_requests": caps["max_dispatch_attempts"],
        "max_total_input_tokens": caps["max_total_input_tokens"],
        "max_total_output_tokens": caps["max_total_output_tokens"],
        "max_input_tokens_per_request": caps["max_input_tokens_per_request"],
        "max_output_tokens_per_request": caps["max_output_tokens_per_request"],
        "max_in_flight": caps["max_in_flight"],
    }
    config = _projection_config(exact_limits, source_pricing)
    plan = build_request_plan(config)
    inventory = _inventory(config)
    if inventory["exact_caps"] != caps:
        raise V12Gate1ReadinessError("v1.2 Gate 1 exact ceilings did not converge")
    if (inventory["within_model_context"] is not True
            or inventory["maximum_dispatch_attempts"] != 61):
        raise V12Gate1ReadinessError("v1.2 Gate 1 projection is not executable within caps")
    transport = _stage2_transport_metadata(sdk_version=STAGE2_SDK_VERSION)
    transport_hash = digest(transport)
    if prior_lineage.get("transport_hash") != transport_hash:
        raise V12Gate1ReadinessError("current transport differs from passed capability lineage")
    config_template = config.to_dict()
    config_template["authorization"] = {
        "kind": "paid",
        "authorization_id": "EXTERNAL_UUID_REQUIRED",
        "approved_at": "EXTERNAL_TIMESTAMP_REQUIRED",
        "approved_budget_usd": caps["max_cost_usd"],
        "allow_paid_calls": True,
    }
    prompt_inventory = protocol_prompt_inventory(config)
    bridge_body = {
        "artifact_type": "gate1_v1_1_to_gate1_v1_2_capability_bridge_v1",
        "predecessor_is_capability_evidence_only": True,
        "predecessor_gate1": prior_lineage,
        "successor": {
            "run_id": V1_2_GATE1_RUN_ID,
            "protocol_version": "v1.2",
            "protocol_hash": plan.protocol_hash,
            "projection_plan_hash": plan.plan_hash,
            "implementation_hash": plan.implementation_hash,
            "split": V1_2_GATE1_NAMESPACE,
            "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
            "logical_identity_hash": inventory["logical_identity_hash"],
        },
        "non_reuse_rule": (
            "No v1.1 calibration observation, identity, gate decision, authorization, or "
            "estimate is reused as v1.2 evidence."
        ),
    }
    bridge = {**bridge_body, "bridge_hash": digest(bridge_body)}
    body = {
        "artifact_type": "gate_1_v1_2_calibration_proposal_v1",
        "planned_controller_id": V1_2_GATE1_CONTROLLER_ID,
        "status": (
            "awaiting_external_human_authorization"
            if pricing_ready else
            "offline_projection_awaiting_pricing_refresh_review_and_authorization"
        ),
        "dispatch_enabled": False,
        "execution_ready": pricing_ready,
        "live_controller_implemented": True,
        "authorization_issuance_enabled": pricing_ready,
        "pricing_reverified_for_v1_2": pricing_ready,
        "source_pricing_verified_at": config.pricing.verified_at,
        "credentials_required_for_inspection": False,
        "experiment_started": False,
        "activation_decision": {
            "path": str(ACTIVATION_DECISION_PATH.relative_to(ROOT)),
            "sha256": file_hash(ACTIVATION_DECISION_PATH),
            "early_futility_deviation": True,
        },
        "protocol": {
            "base_path": "SPRINT_PROJECT_PLAN.md",
            "addendum_path": str(PROTOCOL_PATH.relative_to(ROOT)),
            "protocol_version": "v1.2",
            "protocol_hash": plan.protocol_hash,
            "binding": "canonical sha256 of base-protocol and addendum sha256 values",
            "sole_scientific_change": "two message rounds in U/C formation only",
        },
        "capability_bridge": bridge,
        "run": {
            "run_id": V1_2_GATE1_RUN_ID,
            "run_directory": RUN_DIRECTORY,
            "split": V1_2_GATE1_NAMESPACE,
            "seed_namespace": V1_2_GATE1_NAMESPACE,
            "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
            "config_template_hash": digest(config_template),
            "projection_config_hash": digest(config.to_dict()),
            "projection_plan_hash": plan.plan_hash,
            "implementation_hash": plan.implementation_hash,
            "fixture_inventory_hash": digest(dict(plan.fixture_inventory)),
            "prompt_inventory_hash": digest(prompt_inventory),
        },
        "scope": {
            "stage": "calibration",
            "logical_requests": 20,
            "maximum_dispatch_attempts": 61,
            "stop_before_stage": "smoke",
            "preflight_authorized": False,
            "smoke_authorized": False,
            "hypothesis_bearing_requests_authorized": False,
            "later_stages_authorized": [],
        },
        "calibration_projection": {
            key: value for key, value in inventory.items() if key != "requests"
        },
        "exact_caps": caps,
        "transport": transport,
        "transport_hash": transport_hash,
        "ambiguous_5xx_retry_policy": V1_2_AMBIGUOUS_5XX_RETRY_POLICY,
        "ambiguous_5xx_retry_policy_hash": V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
        "required_provider_identity": required_identity,
        "pricing_contract": pricing_contract,
        "pricing_contract_hash": digest(pricing_contract),
        "gate1_config_template": config_template,
        "assessment_contract": {
            "assessment_type": "capability_calibration_v1",
            "scheduled": 20,
            "schema_valid_verdicts": "20_of_20",
            "request_payloads_verified": "20_of_20",
            "wire_payloads_verified": "20_of_20",
            "provider_audits_complete": "20_of_20",
            "correct_packet_approvals": "at_least_9_of_10",
            "faulty_packet_rejections": "at_least_9_of_10",
            "decision_values": ["passed", "failed"],
            "decision_immutable": True,
            "transport_outcome_sensitivity_required": True,
        },
        "external_authorization": {
            "required": True,
            "created": False,
            "issuance_enabled": pricing_ready,
            "requires_live_controller_implementation": False,
            "requires_fresh_official_pricing_verification": not pricing_ready,
            "must_be_outside_repository": True,
            "owner_only_file_required": True,
            "fresh_authorization_uuid_v4_required": True,
            "fresh_64_hex_nonce_required": True,
            "single_use_receipt_required": True,
            "receipt_registry_scope": "stable_per_user_controller_and_run_id",
            "receipt_registry_key_hash": digest({
                "controller": V1_2_GATE1_CONTROLLER_ID,
                "run_id": V1_2_GATE1_RUN_ID,
            }),
            "replacement_authorization_cannot_reuse_fixed_release": True,
            "expired_unclaimed_prepared_run_requires_new_versioned_release": True,
            "maximum_unclaimed_age_seconds": AUTHORIZATION_MAX_UNCLAIMED_AGE_SECONDS,
            "maximum_future_skew_seconds": AUTHORIZATION_MAX_FUTURE_SKEW_SECONDS,
            "maximum_pricing_age_seconds": PRICING_MAX_AGE_SECONDS,
            "validity_enforced_at": (
                "inspection, authorization validation, first claim, and "
                "incomplete receipt-backed resume"
            ),
            "completed_receipt_backed_resume_ignores_pricing_age": True,
            "schema_artifact": (
                str(AUTHORIZATION_SCHEMA_PATH.relative_to(ROOT))
                if pricing_ready else None
            ),
            "schema_artifact_created": pricing_ready,
            "schema_projection_function": (
                "collusion_recovery.v1_2_gate1.authorization_schema"
            ),
            "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
        },
        "hard_stop": {
            "after_stage": "calibration",
            "before_stage": "smoke",
            "next_stage_authorized": False,
            "generic_staged_executor_allowed": False,
        },
    }
    return {**body, "proposal_hash": digest(body)}


def validate_v1_2_gate1_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V12Gate1ReadinessError("v1.2 Gate 1 proposal must be an object")
    expected = build_v1_2_gate1_proposal()
    if canonical(dict(value)) != canonical(expected):
        raise V12Gate1ReadinessError("v1.2 Gate 1 proposal differs from current source")
    return expected


def authorization_schema(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact external authorization contract; create no authorization."""
    proposal = validate_v1_2_gate1_proposal(proposal)
    uuid_pattern = (
        "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
        "[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:collusion-recovery:gate1-calibration-authorization:v1.2",
        "title": "Gate 1 v1.2 calibration human authorization",
        "$comment": (
            "authorization_hash is sha256 of canonical JSON after removing "
            "authorization_hash. The artifact is valid for one fixed v1.2 Gate 1 "
            "release only and expires if unclaimed for 15 minutes."
        ),
        "type": "object",
        "additionalProperties": False,
        "required": sorted(_AUTH_FIELDS),
        "properties": {
            "artifact_type": {"const": "gate_1_calibration_human_authorization_v1_2"},
            "authorization_uuid": {"type": "string", "pattern": uuid_pattern},
            "authorization_id": {
                "type": "string",
                "pattern": "^gate1-v1-2-human-[0-9a-f-]{36}$",
            },
            "authorization_nonce": {
                "type": "string", "pattern": "^[0-9a-f]{64}$"},
            "gate1_run_id": {"const": proposal["run"]["run_id"]},
            "gate1_run_directory": {"const": proposal["run"]["run_directory"]},
            "confirmed_by": {"type": "string", "minLength": 1, "maxLength": 200},
            "confirmed_at": {"type": "string", "format": "date-time"},
            "proposal_hash": {"const": proposal["proposal_hash"]},
            "approved_scope": {"const": proposal["scope"]},
            "approved_caps": {"const": proposal["exact_caps"]},
            "approved_pricing_contract_hash": {
                "const": proposal["pricing_contract_hash"]},
            "approved_transport_hash": {"const": proposal["transport_hash"]},
            "approved_retry_policy_hash": {
                "const": proposal["ambiguous_5xx_retry_policy_hash"]},
            "approved_provider_identity": {
                "const": proposal["required_provider_identity"]},
            "approved_capability_bridge_hash": {
                "const": proposal["capability_bridge"]["bridge_hash"]},
            "approved_implementation_hash": {
                "const": proposal["run"]["implementation_hash"]},
            "approved_calibration_inventory_hash": {
                "const": proposal["calibration_projection"]["inventory_hash"]},
            "approved_gate1_config_hash": {
                "type": "string", "pattern": "^[a-f0-9]{64}$"},
            "approved_gate1_plan_hash": {
                "type": "string", "pattern": "^[a-f0-9]{64}$"},
            "approved_dispatch_authorization_hash": {
                "type": "string", "pattern": "^[a-f0-9]{64}$"},
            "allow_paid_calls": {"const": True},
            "human_acknowledgement": {"const": HUMAN_ACKNOWLEDGEMENT},
            "authorization_hash": {
                "type": "string", "pattern": "^[a-f0-9]{64}$"},
        },
    }


def load_v1_2_gate1_proposal(path: Path = PROPOSAL_PATH) -> dict[str, Any]:
    """Load the exact source-bound final proposal; never accept a test override."""
    proposal = validate_v1_2_gate1_proposal(
        _read_json(Path(path), "v1.2 Gate 1 proposal")
    )
    if (proposal.get("execution_ready") is not True
            or proposal.get("pricing_reverified_for_v1_2") is not True
            or V1_2_PRICING_RECORD is None):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 proposal is not a final source-bound release"
        )
    _validate_static_authorization_schema(proposal, AUTHORIZATION_SCHEMA_PATH)
    return proposal


def _validate_static_authorization_schema(
        proposal: Mapping[str, Any], path: Path) -> None:
    """Require the checked artifact to equal the canonical generated schema.

    Equality is byte-for-byte, including the one trailing newline.  A JSON
    object with equivalent semantics but different formatting is therefore not
    the release artifact that reviewers approved.
    """
    expected = (canonical(authorization_schema(proposal)) + "\n").encode("utf-8")
    try:
        observed = Path(path).read_bytes()
    except OSError as exc:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 static authorization schema is unavailable"
        ) from exc
    if observed != expected:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 static authorization schema differs from source"
        )


def _require_final_proposal(proposal: Mapping[str, Any]) -> None:
    if (proposal.get("execution_ready") is not True
            or proposal.get("pricing_reverified_for_v1_2") is not True
            or proposal.get("authorization_issuance_enabled") is not True
            or proposal.get("external_authorization", {}).get(
                "issuance_enabled") is not True):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 draft cannot issue authorization or execute"
        )


def _authorization_receipt_directory(
        _authorization_path: Path | None = None) -> Path:
    """Return the fixed per-user registry, independent of auth-file copies."""
    return AUTHORIZATION_RECEIPT_DIRECTORY


def _authorization_receipt_path(receipt_dir: Path) -> Path:
    """Key consumption by the fixed controller/run, not a replaceable UUID."""
    return Path(receipt_dir) / (
        f"{V1_2_GATE1_CONTROLLER_ID}--{V1_2_GATE1_RUN_ID}.json"
    )


def _load_fixed_authorization_receipt() -> dict[str, Any]:
    """Read the owner-only fixed receipt without following a file symlink."""
    receipt_dir = _validate_receipt_registry_path(
        AUTHORIZATION_RECEIPT_DIRECTORY
    )
    try:
        directory_details = receipt_dir.lstat()
    except OSError as exc:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 authorization receipt registry is unavailable"
        ) from exc
    if (not stat.S_ISDIR(directory_details.st_mode)
            or stat.S_ISLNK(directory_details.st_mode)
            or directory_details.st_uid != os.getuid()
            or directory_details.st_mode & 0o077):
        raise V12Gate1ReadinessError(
            "authorization receipt registry must be owner-only"
        )
    path = _authorization_receipt_path(receipt_dir)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise V12Gate1ReadinessError(
            "this platform cannot enforce no-follow receipt reads"
        )
    try:
        descriptor = os.open(
            path, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
        )
    except OSError as exc:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 authorization receipt is unavailable"
        ) from exc
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.getuid()
                or details.st_mode & 0o077
                or details.st_size < 2
                or details.st_size > AUTHORIZATION_MAX_BYTES):
            raise V12Gate1ReadinessError(
                "authorization receipt must be a bounded owner-only regular file"
            )
        raw = b""
        while len(raw) <= AUTHORIZATION_MAX_BYTES:
            chunk = os.read(descriptor, min(
                16 * 1024, AUTHORIZATION_MAX_BYTES + 1 - len(raw)
            ))
            if not chunk:
                break
            raw += chunk
        after = os.fstat(descriptor)
        if (len(raw) != details.st_size
                or (details.st_dev, details.st_ino, details.st_size,
                    details.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns)):
            raise V12Gate1ReadinessError(
                "authorization receipt changed while it was being read"
            )
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 authorization receipt is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 authorization receipt must be an object"
        )
    body = dict(value)
    receipt_hash = body.pop("receipt_hash", None)
    if receipt_hash != digest(body):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 authorization receipt self-hash is invalid"
        )
    return value


def _validate_receipt_registry_path(receipt_dir: Path) -> Path:
    value = Path(os.path.abspath(receipt_dir))
    expected = Path(os.path.abspath(AUTHORIZATION_RECEIPT_DIRECTORY))
    if value != expected:
        raise V12Gate1ReadinessError(
            "authorization receipt registry is not the fixed per-user release registry"
        )
    trusted_home = expected.parents[1]
    current = trusted_home
    for part in (None, *value.relative_to(trusted_home).parts):
        if part is not None:
            current = current / part
        if current.is_symlink():
            raise V12Gate1ReadinessError(
                "authorization receipt registry cannot contain symlink components"
            )
        if current.exists():
            details = current.lstat()
            if (not stat.S_ISDIR(details.st_mode)
                    or details.st_uid != os.getuid()
                    or details.st_mode & 0o022):
                raise V12Gate1ReadinessError(
                    "authorization receipt registry components must be "
                    "current-user-owned non-writable directories"
                )
    return value


def validate_v1_2_gate1_human_authorization(
        value: Mapping[str, Any], proposal: Mapping[str, Any], *,
        _proposal_verified: bool = False,
        _verify_derived_contract: bool = True) -> dict[str, Any]:
    if not _proposal_verified:
        proposal = validate_v1_2_gate1_proposal(proposal)
    _require_final_proposal(proposal)
    if not isinstance(value, Mapping) or set(value) != _AUTH_FIELDS:
        raise V12Gate1ReadinessError(
            f"human authorization must contain exactly {sorted(_AUTH_FIELDS)}"
        )
    auth = json.loads(canonical(dict(value)))
    body = dict(auth)
    authorization_hash = body.pop("authorization_hash", None)
    if (not isinstance(authorization_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", authorization_hash) is None
            or authorization_hash != digest(body)):
        raise V12Gate1ReadinessError("human authorization self-hash is invalid")
    try:
        authorization_uuid = uuid.UUID(auth["authorization_uuid"])
    except (AttributeError, TypeError, ValueError) as exc:
        raise V12Gate1ReadinessError(
            "authorization_uuid must be a lowercase UUIDv4"
        ) from exc
    uuid_text = str(authorization_uuid)
    if authorization_uuid.version != 4 or uuid_text != auth["authorization_uuid"]:
        raise V12Gate1ReadinessError(
            "authorization_uuid must be a lowercase UUIDv4"
        )
    if auth["authorization_id"] != f"gate1-v1-2-human-{uuid_text}":
        raise V12Gate1ReadinessError("authorization_id must bind the fresh UUID")
    nonce = auth["authorization_nonce"]
    if (not isinstance(nonce, str)
            or re.fullmatch(r"[a-f0-9]{64}", nonce) is None
            or len(set(nonce)) < 8):
        raise V12Gate1ReadinessError(
            "authorization nonce must be a fresh 64-hex value"
        )
    confirmer = auth["confirmed_by"]
    if (not isinstance(confirmer, str) or not confirmer.strip()
            or len(confirmer) > 200):
        raise V12Gate1ReadinessError(
            "confirmed_by must name the human authorizer"
        )
    _parse_timestamp(auth["confirmed_at"], "confirmed_at")
    exact = {
        "artifact_type": "gate_1_calibration_human_authorization_v1_2",
        "gate1_run_id": proposal["run"]["run_id"],
        "gate1_run_directory": proposal["run"]["run_directory"],
        "proposal_hash": proposal["proposal_hash"],
        "approved_scope": proposal["scope"],
        "approved_caps": proposal["exact_caps"],
        "approved_pricing_contract_hash": proposal["pricing_contract_hash"],
        "approved_transport_hash": proposal["transport_hash"],
        "approved_retry_policy_hash": proposal[
            "ambiguous_5xx_retry_policy_hash"
        ],
        "approved_provider_identity": proposal["required_provider_identity"],
        "approved_capability_bridge_hash": proposal["capability_bridge"][
            "bridge_hash"
        ],
        "approved_implementation_hash": proposal["run"]["implementation_hash"],
        "approved_calibration_inventory_hash": proposal[
            "calibration_projection"
        ]["inventory_hash"],
        "allow_paid_calls": True,
        "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
    }
    if any(auth.get(name) != expected for name, expected in exact.items()):
        raise V12Gate1ReadinessError(
            "human authorization does not bind the exact v1.2 Gate 1 release"
        )
    for name in (
            "approved_gate1_config_hash", "approved_gate1_plan_hash",
            "approved_dispatch_authorization_hash"):
        if (not isinstance(auth.get(name), str)
                or re.fullmatch(r"[a-f0-9]{64}", auth[name]) is None):
            raise V12Gate1ReadinessError(
                "human authorization contains a malformed derived contract hash"
            )
    if _verify_derived_contract:
        config, plan, dispatch, inventory = _v1_2_gate1_config_and_plan(
            proposal,
            auth,
            _proposal_verified=True,
            _authorization_verified=True,
        )
        derived = {
            "approved_gate1_config_hash": digest(config.to_dict()),
            "approved_gate1_plan_hash": plan.plan_hash,
            "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
            "approved_calibration_inventory_hash": inventory["inventory_hash"],
        }
        if any(auth.get(name) != expected for name, expected in derived.items()):
            raise V12Gate1ReadinessError(
                "human authorization differs from the exact derived release"
            )
    return auth


def complete_v1_2_gate1_human_authorization(
        externally_supplied_fields: Mapping[str, Any],
        proposal: Mapping[str, Any], *,
        _proposal_verified: bool = False) -> dict[str, Any]:
    """Package human-supplied approval deterministically; never create it."""
    proposal = (
        dict(proposal) if _proposal_verified
        else validate_v1_2_gate1_proposal(proposal)
    )
    _require_final_proposal(proposal)
    derived_names = {
        "approved_gate1_config_hash",
        "approved_gate1_plan_hash",
        "approved_dispatch_authorization_hash",
        "authorization_hash",
    }
    required = _AUTH_FIELDS - derived_names
    if (not isinstance(externally_supplied_fields, Mapping)
            or set(externally_supplied_fields) != required):
        raise V12Gate1ReadinessError(
            "authorization packaging requires exactly the external human fields"
        )
    draft = json.loads(canonical(dict(externally_supplied_fields)))
    draft.update({
        name: "0" * 64
        for name in derived_names - {"authorization_hash"}
    })
    draft["authorization_hash"] = digest(draft)
    envelope = validate_v1_2_gate1_human_authorization(
        draft,
        proposal,
        _proposal_verified=True,
        _verify_derived_contract=False,
    )
    config, plan, dispatch, _inventory_value = _v1_2_gate1_config_and_plan(
        proposal,
        envelope,
        _proposal_verified=True,
        _authorization_verified=True,
    )
    completed = dict(envelope)
    completed.update({
        "approved_gate1_config_hash": digest(config.to_dict()),
        "approved_gate1_plan_hash": plan.plan_hash,
        "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
    })
    body = dict(completed)
    body.pop("authorization_hash", None)
    completed["authorization_hash"] = digest(body)
    return validate_v1_2_gate1_human_authorization(
        completed,
        proposal,
        _proposal_verified=True,
        _verify_derived_contract=False,
    )


def load_v1_2_gate1_human_authorization(
        path: Path, proposal: Mapping[str, Any], *,
        require_external: bool = True,
        _proposal_verified: bool = False) -> dict[str, Any]:
    path = Path(path)
    raw = (
        _read_external_authorization_once(path)
        if require_external else
        _read_json(path, "v1.2 Gate 1 human authorization")
    )
    return validate_v1_2_gate1_human_authorization(
        raw,
        proposal,
        _proposal_verified=_proposal_verified,
    )


def _v1_2_gate1_config_and_plan(
        proposal: Mapping[str, Any], authorization: Mapping[str, Any], *,
        _proposal_verified: bool = False,
        _authorization_verified: bool = False,
        ) -> tuple[LiveConfig, Any, DispatchAuthorizationArtifact, dict[str, Any]]:
    if not _proposal_verified:
        proposal = validate_v1_2_gate1_proposal(proposal)
    _require_final_proposal(proposal)
    auth = (
        json.loads(canonical(dict(authorization)))
        if _authorization_verified else
        validate_v1_2_gate1_human_authorization(
            authorization,
            proposal,
            _proposal_verified=True,
            _verify_derived_contract=False,
        )
    )
    values = json.loads(canonical(proposal["gate1_config_template"]))
    if digest(values) != proposal["run"]["config_template_hash"]:
        raise V12Gate1ReadinessError("v1.2 Gate 1 config template hash differs")
    values["authorization"] = {
        "kind": "paid",
        "authorization_id": auth["authorization_id"],
        "approved_at": auth["confirmed_at"],
        "approved_budget_usd": proposal["exact_caps"]["max_cost_usd"],
        "allow_paid_calls": True,
    }
    config = LiveConfig.from_mapping(values)
    plan = build_request_plan(config)
    frozen = proposal["run"]
    if (config.run_id != V1_2_GATE1_RUN_ID
            or config.protocol_version != "v1.2"
            or config.source != "empirical-live"
            or config.run_type != "pilot"
            or config.stage != "calibration"
            or config.blocks != 5
            or config.split != V1_2_GATE1_NAMESPACE
            or config.seed_namespace != V1_2_GATE1_NAMESPACE
            or config.calibration_namespace != V1_2_CALIBRATION_NAMESPACE
            or plan.run_id != frozen["run_id"]
            or plan.protocol_version != proposal["protocol"]["protocol_version"]
            or plan.protocol_hash != proposal["protocol"]["protocol_hash"]
            or plan.implementation_hash != frozen["implementation_hash"]
            or digest(dict(plan.fixture_inventory))
            != frozen["fixture_inventory_hash"]):
        raise V12Gate1ReadinessError(
            "current implementation differs from the authorized v1.2 release"
        )
    inventory = _inventory(config)
    if (inventory["inventory_hash"]
            != proposal["calibration_projection"]["inventory_hash"]
            or inventory["logical_request_count"] != 20
            or inventory["maximum_dispatch_attempts"] != 61
            or inventory["exact_caps"] != proposal["exact_caps"]
            or inventory["within_model_context"] is not True
            or digest([spec.identity for spec in plan.requests])
            != inventory["logical_identity_hash"]):
        raise V12Gate1ReadinessError(
            "runtime calibration inventory differs from the reviewed scope"
        )
    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    binding = authorization_binding(
        config,
        plan,
        confirmed_at=auth["confirmed_at"],
        transport_hash=proposal["transport_hash"],
        approved_budget_usd=proposal["exact_caps"]["max_cost_usd"],
        max_stage="calibration",
        max_block=0,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
    )
    dispatch = DispatchAuthorizationArtifact(
        authorization_id=auth["authorization_id"],
        confirmed_at=auth["confirmed_at"],
        binding_hash=binding,
        approved_budget_usd=proposal["exact_caps"]["max_cost_usd"],
        transport_hash=proposal["transport_hash"],
        max_stage="calibration",
        max_block=0,
        reviewed_event_hash="initial",
        release_limits=release,
        preflight_inventory_hash=STAGE2_PREFLIGHT_INVENTORY_HASH,
    )
    validate_dispatch_authorization(config, plan, dispatch, require_initial=True)
    if not _authorization_verified:
        derived = {
            "approved_gate1_config_hash": digest(config.to_dict()),
            "approved_gate1_plan_hash": plan.plan_hash,
            "approved_dispatch_authorization_hash": digest(dispatch.to_dict()),
            "approved_calibration_inventory_hash": inventory["inventory_hash"],
        }
        if any(auth.get(name) != expected for name, expected in derived.items()):
            raise V12Gate1ReadinessError(
                "human authorization differs from the exact v1.2 contract"
            )
    return config, plan, dispatch, inventory


def _binding_event_value(
        proposal: Mapping[str, Any], authorization: Mapping[str, Any],
        config: LiveConfig, plan: Any,
        dispatch: DispatchAuthorizationArtifact) -> dict[str, Any]:
    return {
        "controller": V1_2_GATE1_CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": authorization["authorization_hash"],
        "gate1_run_id": authorization["gate1_run_id"],
        "capability_bridge_hash": proposal["capability_bridge"]["bridge_hash"],
        "protocol_version": proposal["protocol"]["protocol_version"],
        "protocol_hash": proposal["protocol"]["protocol_hash"],
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "implementation_hash": proposal["run"]["implementation_hash"],
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "transport_hash": proposal["transport_hash"],
        "retry_policy_hash": proposal["ambiguous_5xx_retry_policy_hash"],
        "required_provider_identity": proposal["required_provider_identity"],
        "pricing_contract_hash": proposal["pricing_contract_hash"],
        "exact_caps": proposal["exact_caps"],
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "scope": proposal["scope"],
    }


def _validated_v1_2_gate1_run_path(
        run_dir: Path, authorization: Mapping[str, Any], *,
        workspace_root: Path | None = None) -> tuple[Path, bool]:
    run_path = Path(run_dir)
    root = ROOT if workspace_root is None else Path(workspace_root)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 workspace root must already exist"
        ) from exc
    if not root_resolved.is_dir():
        raise V12Gate1ReadinessError("v1.2 Gate 1 workspace root is not a directory")
    relative = Path(authorization["gate1_run_directory"])
    if (relative.is_absolute() or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 run directory must be workspace-relative"
        )
    root_lexical = Path(os.path.abspath(root))
    expected_lexical = root_lexical.joinpath(*relative.parts)
    supplied_lexical = Path(os.path.abspath(run_path))
    if supplied_lexical != expected_lexical:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 run path differs from the human authorization"
        )
    try:
        expected_lexical.resolve(strict=False).relative_to(root_resolved)
    except ValueError as exc:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 run path resolves outside the authorized workspace"
        ) from exc
    current = root_lexical
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 run path cannot contain symlink components"
            )
    exists = run_path.exists()
    if exists and not run_path.is_dir():
        raise V12Gate1ReadinessError("v1.2 Gate 1 run path is not a directory")
    return run_path, exists


def prepare_v1_2_gate1_run(
        run_dir: Path, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], *,
        workspace_root: Path | None = None,
        _proposal_verified: bool = False) -> tuple[
            DurableRun, LiveConfig, Any, DispatchAuthorizationArtifact,
            dict[str, Any]
        ]:
    """Create or exactly resume the release without credential or provider I/O."""
    config, plan, dispatch, inventory = _v1_2_gate1_config_and_plan(
        proposal, authorization, _proposal_verified=_proposal_verified
    )
    run_path, exists = _validated_v1_2_gate1_run_path(
        run_dir, authorization, workspace_root=workspace_root
    )
    run = DurableRun.open(
        run_path,
        config,
        plan,
        resume=exists,
        dispatch_authorization=dispatch,
        execution_controller=V1_2_GATE1_CONTROLLER_ID,
    )
    binding = _binding_event_value(
        proposal, authorization, config, plan, dispatch
    )
    with run.execution_lock():
        existing = [
            event for event in run.events
            if event.get("kind") == V1_2_GATE1_BINDING_EVENT
        ]
        if len(existing) > 1:
            raise ResumeError("v1.2 Gate 1 binding was recorded more than once")
        created_binding = not existing
        if existing:
            durable = {key: existing[0].get(key) for key in binding}
            if canonical(durable) != canonical(binding):
                raise ResumeError("durable v1.2 Gate 1 binding differs")
            nonce = existing[0].get("run_instance_nonce")
            if (not isinstance(nonce, str)
                    or re.fullmatch(r"[a-f0-9]{64}", nonce) is None):
                raise ResumeError("durable v1.2 Gate 1 nonce is invalid")
        else:
            nonce = secrets.token_hex(32)
            run.append(
                V1_2_GATE1_BINDING_EVENT,
                **binding,
                run_instance_nonce=nonce,
            )
        prepared_fields = {
            "execution_controller": V1_2_GATE1_CONTROLLER_ID,
            "v1_2_gate1_proposal_hash": proposal["proposal_hash"],
            "v1_2_gate1_human_authorization_hash": authorization[
                "authorization_hash"
            ],
            "v1_2_gate1_run_id": authorization["gate1_run_id"],
            "v1_2_gate1_run_instance_hash": digest(nonce),
            "required_provider_identity": proposal["required_provider_identity"],
            "preflight_dispatch_forbidden": True,
            "smoke_dispatch_forbidden": True,
            "later_stage_dispatch_forbidden": True,
        }
        mismatched = {
            name for name, expected in prepared_fields.items()
            if name in run.manifest and run.manifest[name] != expected
        }
        if mismatched:
            raise ResumeError("durable v1.2 Gate 1 manifest binding differs")
        missing = {name for name in prepared_fields if name not in run.manifest}
        if created_binding or missing:
            if not created_binding and run.manifest.get("status") != "created":
                raise ResumeError("durable v1.2 Gate 1 binding is incomplete")
            run.set_status(
                "prepared_v1_2_gate1_calibration",
                **prepared_fields,
                budget=BudgetLedger.replay(
                    config.limits,
                    config.pricing,
                    Decimal(dispatch.approved_budget_usd),
                    run.budget_records(),
                    dispatch.release_limits,
                ).snapshot(),
            )
    return run, config, plan, dispatch, inventory


def _claim_event_fields(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any],
        receipt: Mapping[str, Any]) -> dict[str, Any]:
    bindings = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_BINDING_EVENT
    ]
    if len(bindings) != 1:
        raise V12Gate1ReadinessError(
            "durable v1.2 Gate 1 run-instance binding is absent"
        )
    receipt_body = dict(receipt)
    receipt_hash = receipt_body.pop("receipt_hash", None)
    if (not isinstance(receipt_hash, str)
            or receipt_hash != digest(receipt_body)):
        raise V12Gate1ReadinessError(
            "authorization receipt self-hash is invalid"
        )
    return {
        "controller": V1_2_GATE1_CONTROLLER_ID,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "receipt_hash": receipt_hash,
        "receipt_claimed_at": receipt_body.get("claimed_at"),
        "gate1_run_id": authorization["gate1_run_id"],
        "run_directory": str(run.path.resolve()),
        "run_instance_event_hash": bindings[0]["event_hash"],
        "run_instance_hash": digest(bindings[0]["run_instance_nonce"]),
    }


def _require_claim_event(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any],
        receipt: Mapping[str, Any]) -> dict[str, Any]:
    expected = _claim_event_fields(run, proposal, authorization, receipt)
    claims = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_AUTHORIZATION_CLAIM_EVENT
    ]
    if (len(claims) != 1
            or any(claims[0].get(key) != value
                   for key, value in expected.items())
            or any(
                event.get("kind") in {
                    V1_2_GATE1_EXECUTION_MODE_EVENT, "reservation", "result"
                }
                and event.get("sequence", -1) < claims[0].get("sequence", -1)
                for event in run.events
            )):
        raise V12Gate1ReadinessError(
            "a singular journal-bound v1.2 Gate 1 claim is required"
        )
    _parse_timestamp(
        claims[0].get("receipt_claimed_at"),
        "authorization claim receipt_claimed_at",
    )
    return claims[0]


def claim_v1_2_gate1_authorization_once(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], *,
        receipt_dir: Path,
        now: datetime | None = None) -> dict[str, Any]:
    """Atomically consume the fixed release, allowing only exact-run resume."""
    if run.manifest.get("execution_controller") != V1_2_GATE1_CONTROLLER_ID:
        raise V12Gate1ReadinessError("v1.2 Gate 1 controller marker is absent")
    if (run.manifest.get("v1_2_gate1_proposal_hash")
            != proposal["proposal_hash"]
            or run.manifest.get("v1_2_gate1_human_authorization_hash")
            != authorization["authorization_hash"]
            or run.manifest.get("v1_2_gate1_run_id")
            != authorization["gate1_run_id"]):
        raise V12Gate1ReadinessError(
            "durable v1.2 Gate 1 run differs from the authorization"
        )
    receipt_dir = _validate_receipt_registry_path(Path(receipt_dir))
    try:
        receipt_dir.resolve(strict=False).relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise V12Gate1ReadinessError(
            "authorization receipt registry must remain outside the repository"
        )
    if receipt_dir.exists():
        details = receipt_dir.lstat()
        if (not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode)
                or details.st_uid != os.getuid() or details.st_mode & 0o077):
            raise V12Gate1ReadinessError(
                "authorization receipt registry must be owner-only"
            )
    else:
        receipt_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
        _validate_receipt_registry_path(receipt_dir)
        if receipt_dir.lstat().st_mode & 0o077:
            raise V12Gate1ReadinessError(
                "authorization receipt registry was not created owner-only"
            )
    bindings = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_BINDING_EVENT
    ]
    if len(bindings) != 1:
        raise V12Gate1ReadinessError(
            "durable v1.2 Gate 1 run-instance binding is absent"
        )
    expected = {
        "artifact_type": "gate_1_v1_2_authorization_consumption_receipt_v1",
        "controller": V1_2_GATE1_CONTROLLER_ID,
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "proposal_hash": proposal["proposal_hash"],
        "capability_bridge_hash": proposal["capability_bridge"]["bridge_hash"],
        "retry_policy_hash": proposal["ambiguous_5xx_retry_policy_hash"],
        "gate1_run_id": authorization["gate1_run_id"],
        "run_directory": str(run.path.resolve()),
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "dispatch_authorization_hash": run.manifest[
            "dispatch_authorization_hash"
        ],
        "run_instance_event_hash": bindings[0]["event_hash"],
        "run_instance_hash": digest(bindings[0]["run_instance_nonce"]),
    }
    receipt_path = _authorization_receipt_path(receipt_dir)
    with run.execution_lock():
        if receipt_path.exists():
            receipt = _load_fixed_authorization_receipt()
            body = dict(receipt)
            receipt_hash = body.pop("receipt_hash", None)
            if (set(body) != set(expected) | {"claimed_at"}
                    or receipt_hash != digest(body)
                    or any(body.get(key) != value
                           for key, value in expected.items())):
                raise V12Gate1ReadinessError(
                    "authorization was already claimed by a different run"
                )
            _parse_timestamp(body.get("claimed_at"), "receipt claimed_at")
        else:
            _validate_first_claim_window(authorization, proposal, now=now)
            body = {
                **expected,
                "claimed_at": _utc_now_datetime(now).isoformat(),
            }
            receipt = {**body, "receipt_hash": digest(body)}
            try:
                _publish_exclusive_json(receipt_path, receipt)
            except FileExistsError:
                receipt = _load_fixed_authorization_receipt()
                body = dict(receipt)
                receipt_hash = body.pop("receipt_hash", None)
                if (set(body) != set(expected) | {"claimed_at"}
                        or receipt_hash != digest(body)
                        or any(body.get(key) != value
                               for key, value in expected.items())):
                    raise V12Gate1ReadinessError(
                        "authorization was concurrently claimed by a different run"
                    )
        claim_fields = _claim_event_fields(
            run, proposal, authorization, receipt
        )
        claims = [
            event for event in run.events
            if event.get("kind") == V1_2_GATE1_AUTHORIZATION_CLAIM_EVENT
        ]
        if len(claims) > 1:
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 authorization claim is not singular"
            )
        if claims:
            if any(claims[0].get(key) != value
                   for key, value in claim_fields.items()):
                raise V12Gate1ReadinessError(
                    "durable authorization claim differs from receipt"
                )
        else:
            if any(event.get("kind") in {
                    V1_2_GATE1_EXECUTION_MODE_EVENT, "reservation", "result"
                    } for event in run.events):
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 dispatch exists before authorization claim"
                )
            run.append(
                V1_2_GATE1_AUTHORIZATION_CLAIM_EVENT, **claim_fields
            )
        claim = _require_claim_event(run, proposal, authorization, receipt)
        if (run.manifest.get("v1_2_gate1_authorization_consumed") is not True
                or run.manifest.get("v1_2_gate1_authorization_receipt_hash")
                != receipt["receipt_hash"]
                or run.manifest.get("v1_2_gate1_authorization_claim_event_hash")
                != claim["event_hash"]):
            run.set_status(
                run.manifest["status"],
                v1_2_gate1_authorization_consumed=True,
                v1_2_gate1_authorization_receipt_hash=receipt["receipt_hash"],
                v1_2_gate1_authorization_claim_event_hash=claim["event_hash"],
            )
        return receipt


def _require_execution_mode_binding(
        run: DurableRun, proposal: Mapping[str, Any], *,
        expected_mode: str,
        expected_provenance: str) -> dict[str, Any]:
    """Validate an existing binding without granting a way to mint one."""
    bindings = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_EXECUTION_MODE_EVENT
    ]
    if len(bindings) != 1:
        raise ResumeError("v1.2 Gate 1 execution mode binding is not singular")
    binding = bindings[0]
    if (binding.get("controller") != V1_2_GATE1_CONTROLLER_ID
            or binding.get("proposal_hash") != proposal["proposal_hash"]
            or binding.get("run_id") != V1_2_GATE1_RUN_ID
            or binding.get("mode") != expected_mode
            or binding.get("executor_provenance") != expected_provenance):
        raise ResumeError("v1.2 Gate 1 execution mode changed on resume")
    return binding


def _bind_fake_execution_mode(
        run: DurableRun, proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Bind only the conspicuously non-empirical offline test mode."""
    bindings = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_EXECUTION_MODE_EVENT
    ]
    if bindings:
        return _require_execution_mode_binding(
            run,
            proposal,
            expected_mode=FAKE_EXECUTION_MODE,
            expected_provenance=FAKE_EXECUTOR_PROVENANCE,
        )
    claims = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_AUTHORIZATION_CLAIM_EVENT
    ]
    if len(claims) != 1:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 execution mode requires a prior durable claim"
        )
    if any(event.get("kind") in {"reservation", "result"}
           for event in run.events):
        raise ResumeError(
            "v1.2 Gate 1 dispatch exists without an execution mode binding"
        )
    return run.append(
        V1_2_GATE1_EXECUTION_MODE_EVENT,
        controller=V1_2_GATE1_CONTROLLER_ID,
        proposal_hash=proposal["proposal_hash"],
        run_id=V1_2_GATE1_RUN_ID,
        mode=FAKE_EXECUTION_MODE,
        executor_provenance=FAKE_EXECUTOR_PROVENANCE,
    )


def _transport_outcome_sensitivity(run: DurableRun) -> dict[str, Any]:
    """Show how the calibration changes if recovered 5xx rows are unresolved."""
    affected: list[str] = []
    recovered: list[str] = []
    unresolved: list[str] = []
    schedule_hashes: list[str] = []
    for event in run.events:
        if event.get("kind") == "ambiguous_5xx_retry_scheduled":
            schedule_hashes.append(event["event_hash"])
    for spec in run.plan.requests:
        rows = run.result_events(spec.identity)
        eligible = [row for row in rows if StagedExecutor._eligible_ambiguous_5xx(row)]
        if not eligible:
            continue
        affected.append(spec.identity)
        final = rows[-1] if rows else None
        provider = final.get("provider_result") if isinstance(final, Mapping) else None
        if (final is not None
                and final.get("status") in OBSERVATION_STATUSES
                and isinstance(provider, Mapping)
                and provider.get("transport_recovered") is True):
            recovered.append(spec.identity)
        else:
            unresolved.append(spec.identity)
    assessment = calibration_assessment(run)
    observations = assessment["observations"]
    by_identity = {
        spec.identity: row
        for spec, row in zip(run.plan.requests, observations)
    }
    recovered_schema = sum(
        by_identity[identity]["observed_verdict"] is not None
        for identity in recovered
    )
    recovered_approvals = sum(
        by_identity[identity]["fixture_index"] < 10
        and by_identity[identity]["observed_verdict"] == "ACCEPT"
        for identity in recovered
    )
    recovered_rejections = sum(
        by_identity[identity]["fixture_index"] >= 10
        and by_identity[identity]["observed_verdict"] == "REJECT"
        for identity in recovered
    )
    worst = {
        "schema_valid": assessment["schema_valid"] - recovered_schema,
        "correct_packet_approvals": (
            assessment["correct_packet_approvals"] - recovered_approvals
        ),
        "faulty_packet_rejections": (
            assessment["faulty_packet_rejections"] - recovered_rejections
        ),
    }
    worst["passed"] = bool(
        worst["schema_valid"] == 20
        and worst["correct_packet_approvals"] >= 9
        and worst["faulty_packet_rejections"] >= 9
    )
    body = {
        "analysis_type": "v1_2_gate1_transport_outcome_sensitivity_v1",
        "retry_policy_hash": V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
        "affected_identities": affected,
        "recovered_identities": recovered,
        "unresolved_identities": unresolved,
        "retry_schedule_event_hashes": schedule_hashes,
        "retained_outcomes": {
            "schema_valid": assessment["schema_valid"],
            "correct_packet_approvals": assessment[
                "correct_packet_approvals"
            ],
            "faulty_packet_rejections": assessment[
                "faulty_packet_rejections"
            ],
            "passed": assessment["passed"],
        },
        "recovered_outcomes_treated_as_unresolved": worst,
    }
    return {**body, "sensitivity_hash": digest(body)}


_CLOSED_PROVIDER_RESULT_KEYS = {
    "status",
    "raw_response",
    "output_text",
    "parsed_output",
    "refusal",
    "error",
    "provider_request_id",
    "returned_model",
    "model_metadata",
    "usage",
    "timing",
    "ambiguous",
    "known_not_completed",
    "transport_evidence",
}
_CLOSED_PROVIDER_TIMING_KEYS = {
    "started_at", "ended_at", "elapsed_seconds",
}


def _canonical_utc_timestamp(value: Any, label: str) -> datetime:
    """Parse only the canonical UTC form written into durable evidence."""
    if not isinstance(value, str):
        raise V12Gate1ReadinessError(f"{label} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise V12Gate1ReadinessError(
            f"{label} must be a canonical UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise V12Gate1ReadinessError(
            f"{label} must be a canonical UTC timestamp"
        )
    normalized = parsed.astimezone(timezone.utc)
    if value != normalized.isoformat():
        raise V12Gate1ReadinessError(
            f"{label} must be a canonical UTC timestamp"
        )
    return normalized


def _audit_nonfinal_provider_result(row: Mapping[str, Any]) -> None:
    """Reconstruct one retry-bearing result through the closed provider schema."""
    provider = row.get("provider_result")
    if (not isinstance(provider, Mapping)
            or set(provider) != _CLOSED_PROVIDER_RESULT_KEYS):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal provider result is not the closed schema"
        )
    timing = provider.get("timing")
    evidence = provider.get("transport_evidence")
    raw = provider.get("raw_response")
    if (not isinstance(timing, Mapping)
            or set(timing) != _CLOSED_PROVIDER_TIMING_KEYS
            or not isinstance(evidence, Mapping)
            or not evidence):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal provider result metadata is not closed"
        )
    started = _canonical_utc_timestamp(
        timing.get("started_at"), "provider timing started_at"
    )
    ended = _canonical_utc_timestamp(
        timing.get("ended_at"), "provider timing ended_at"
    )
    elapsed = timing.get("elapsed_seconds")
    if (ended < started
            or isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or elapsed < 0):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal provider timing is invalid"
        )
    try:
        reconstructed = ProviderResult(
            status=ProviderStatus(provider.get("status")),
            raw_response_json=(
                None if raw is None else canonical(raw)
            ),
            output_text=provider.get("output_text"),
            parsed_output_json=(
                None if provider.get("parsed_output") is None
                else canonical(provider.get("parsed_output"))
            ),
            refusal=provider.get("refusal"),
            error=provider.get("error"),
            provider_request_id=provider.get("provider_request_id"),
            returned_model=provider.get("returned_model"),
            model_metadata_json=canonical(provider.get("model_metadata")),
            usage=None,
            timing=ProviderTiming(
                timing["started_at"], timing["ended_at"], elapsed
            ),
            ambiguous=provider.get("ambiguous"),
            known_not_completed=provider.get("known_not_completed"),
            transport_evidence_json=canonical(evidence),
        )
    except (TypeError, ValueError) as exc:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal provider result fails its closed contract"
        ) from exc
    if canonical(reconstructed.to_dict()) != canonical(dict(provider)):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal provider result differs from its closed contract"
        )
    if (type(provider.get("ambiguous")) is not bool
            or type(provider.get("known_not_completed")) is not bool
            or type(row.get("ambiguous")) is not bool
            or type(row.get("known_not_completed")) is not bool
            or row.get("status") != provider.get("status")
            or row.get("ambiguous") is not provider.get("ambiguous")
            or row.get("known_not_completed")
            is not provider.get("known_not_completed")
            or canonical(row.get("usage")) != canonical(provider.get("usage"))
            or provider.get("usage") is not None
            or provider.get("output_text") is not None
            or provider.get("parsed_output") is not None
            or provider.get("refusal") is not None
            or provider.get("returned_model") is not None
            or provider.get("model_metadata") != {}
            or not isinstance(provider.get("error"), str)
            or not provider.get("error")):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal row and provider result disagree"
        )
    if raw is not None and not isinstance(raw, Mapping):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal raw response is malformed"
        )
    if isinstance(raw, Mapping) and ("output" in raw or "usage" in raw):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 nonfinal raw response contains output or usage"
        )
    if (evidence.get("provider_request_id")
            != provider.get("provider_request_id")):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 transport and provider request IDs disagree"
        )
    if (isinstance(raw, Mapping)
            and isinstance(raw.get("id"), str)
            and raw.get("id") != provider.get("provider_request_id")):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 raw and provider request IDs disagree"
        )
    if evidence.get("response_received") is True:
        if (not isinstance(raw, Mapping)
                or evidence.get("output_absent") is not True
                or evidence.get("usage_absent") is not True):
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 transport absence evidence disagrees with raw response"
            )


def _audit_exact_attempt_shapes(
        run: DurableRun, proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct every dispatched attempt and its exact durable wire shape."""
    specs = list(run.plan.requests)
    if (len(specs) != 20 or run.plan.checkpoints
            or proposal["exact_caps"].get("max_dispatch_attempts") != 61):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 attempt audit requires the exact 20-request plan"
        )
    allowed = {spec.identity for spec in specs}
    reservations = [
        event for event in run.events if event.get("kind") == "reservation"
    ]
    results = run.result_events()
    schedules = [
        event for event in run.events
        if event.get("kind") == "ambiguous_5xx_retry_scheduled"
    ]
    if (len(results) < 20 or len(results) > 61
            or len(reservations) != len(results)
            or any(event.get("identity") not in allowed
                   for event in (*reservations, *results))):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 dispatch count or identity shape is invalid"
        )

    reservations_by_id: dict[str, dict[str, Any]] = {}
    for event in reservations:
        reservation = event.get("reservation")
        reservation_id = (
            reservation.get("reservation_id")
            if isinstance(reservation, Mapping) else None
        )
        if (not isinstance(reservation_id, str)
                or reservation_id in reservations_by_id):
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 reservation identity is not singular"
            )
        reservations_by_id[reservation_id] = event
    results_by_reservation: dict[str, dict[str, Any]] = {}
    for event in results:
        reservation_id = event.get("reservation_id")
        if (not isinstance(reservation_id, str)
                or reservation_id in results_by_reservation):
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 result reservation identity is not singular"
            )
        results_by_reservation[reservation_id] = event
    if set(reservations_by_id) != set(results_by_reservation):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 contains an unmatched reservation or result"
        )

    factory = ScientificRequestFactory(run.config, run.plan)
    expected_result_order: list[str] = []
    eligible_5xx: list[tuple[ScheduledRequest, dict[str, Any]]] = []
    provider_audits = 0
    request_audits = 0
    wire_audits = 0
    for spec in specs:
        rows = run.result_events(spec.identity)
        if (not rows
                or [row.get("attempt") for row in rows]
                != list(range(len(rows)))
                or rows[-1].get("status") not in OBSERVATION_STATUSES):
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 attempts are noncontiguous or lack a final observation"
            )
        expected_result_order.extend([spec.identity] * len(rows))
        knc_count = 0
        identity_5xx = 0
        for attempt, row in enumerate(rows):
            try:
                request = factory(
                    spec,
                    ExecutionContext(run, attempt, tuple(rows[:attempt])),
                )
                request_payload = request.to_dict()
                request_hash = digest(request_payload)
                wire_payload = OpenAIResponsesProvider.build_payload(request)
                wire_hash = digest(wire_payload)
                durable_request = run.request_payload(request_hash)
                durable_wire = run.wire_payload(wire_hash)
            except Exception as exc:
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 request/wire reconstruction failed"
                ) from exc
            if (row.get("identity") != spec.identity
                    or row.get("attempt") != attempt
                    or row.get("request_hash") != request_hash
                    or row.get("wire_payload_hash") != wire_hash
                    or canonical(durable_request) != canonical(request_payload)
                    or canonical(durable_wire) != canonical(wire_payload)):
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 request or exact wire payload differs"
                )
            reservation_id = row.get("reservation_id")
            reservation_event = reservations_by_id.get(str(reservation_id))
            reservation = (
                reservation_event.get("reservation")
                if isinstance(reservation_event, Mapping) else None
            )
            expected_reservation_id = digest({
                "identity": spec.identity,
                "attempt": attempt,
            })
            if (not isinstance(reservation, Mapping)
                    or reservation_id != expected_reservation_id
                    or reservation_event.get("identity") != spec.identity
                    or reservation_event.get("attempt") != attempt
                    or reservation_event.get("request_hash") != request_hash
                    or reservation_event.get("wire_payload_hash") != wire_hash
                    or reservation.get("reservation_id") != reservation_id
                    or reservation.get("identity") != spec.identity
                    or reservation.get("attempt") != attempt
                    or reservation.get("input_tokens")
                    != request.conservative_input_token_bound
                    or reservation.get("output_tokens")
                    != request.max_output_tokens
                    or reservation_event.get("sequence", -1) + 1
                    != row.get("sequence", -1)):
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 reservation differs from its exact attempt"
                )

            status = row.get("status")
            provider_result = row.get("provider_result")
            eligible = StagedExecutor._eligible_ambiguous_5xx(row)
            is_final = attempt == len(rows) - 1
            if not is_final:
                _audit_nonfinal_provider_result(row)
                if status == "retryable_error":
                    knc_count += 1
                    if (row.get("known_not_completed") is not True
                            or row.get("ambiguous") is not False
                            or row.get("usage") is not None
                            or not isinstance(provider_result, Mapping)
                            or provider_result.get("status") != "retryable_error"
                            or provider_result.get("ambiguous") is not False
                            or provider_result.get("known_not_completed") is not True
                            or provider_result.get("usage") is not None):
                        raise V12Gate1ReadinessError(
                            "v1.2 Gate 1 transport retry lacks true "
                            "known-not-completed evidence"
                        )
                elif eligible:
                    identity_5xx += 1
                    eligible_5xx.append((spec, row))
                    if (row.get("known_not_completed") is not False
                            or row.get("ambiguous") is not True
                            or row.get("usage") is not None
                            or not isinstance(provider_result, Mapping)
                            or provider_result.get("status")
                            != "ambiguous_timeout"
                            or provider_result.get("ambiguous") is not True
                            or provider_result.get("known_not_completed") is not False
                            or provider_result.get("usage") is not None):
                        raise V12Gate1ReadinessError(
                            "v1.2 Gate 1 ambiguous HTTP-5xx evidence is malformed"
                        )
                else:
                    raise V12Gate1ReadinessError(
                        "v1.2 Gate 1 contains an unauthorized nonfinal attempt"
                    )
            elif status not in OBSERVATION_STATUSES:
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 final attempt is not an observation"
                )
            if knc_count > run.config.retry_policy.transport_retries:
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 exceeded the known-not-completed retry cap"
                )
            if identity_5xx > 1:
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 exceeded the per-identity HTTP-5xx retry cap"
                )
            if status in PROVIDER_IDENTITY_STATUSES:
                candidate = (
                    _provider_identity_candidate(
                        run.config, str(status), provider_result
                    ) if isinstance(provider_result, Mapping) else None
                )
                if (candidate != proposal["required_provider_identity"]
                        or row.get("provider_identity") != candidate
                        or not isinstance(provider_result, Mapping)
                        or not provider_result_matches_raw_response(provider_result)):
                    raise V12Gate1ReadinessError(
                        "v1.2 Gate 1 provider result or identity audit failed"
                    )
                provider_audits += 1
            elif row.get("provider_identity") is not None:
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 non-identity result carries provider identity"
                )
            request_audits += int(is_final)
            wire_audits += int(is_final)

    if [row.get("identity") for row in results] != expected_result_order:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 attempts are not in exact plan order"
        )
    if len(eligible_5xx) > 1 or len(schedules) != len(eligible_5xx):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 HTTP-5xx release schedule is not singular"
        )
    if eligible_5xx:
        spec, failed = eligible_5xx[0]
        schedule = schedules[0]
        rows = run.result_events(spec.identity)
        failed_attempt = failed["attempt"]
        if failed_attempt + 1 >= len(rows):
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 scheduled HTTP-5xx retry has no result"
            )
        recovered = rows[failed_attempt + 1]
        final = rows[-1]
        recovered_reservation = reservations_by_id.get(
            str(recovered.get("reservation_id"))
        )
        evidence = failed["provider_result"]["transport_evidence"]
        affected_cap = max(1, (len(specs) + 99) // 100)
        exact = {
            "identity": spec.identity,
            "failed_attempt": failed_attempt,
            "next_attempt": failed_attempt + 1,
            "failed_result_event_hash": failed["event_hash"],
            "provider_request_id": evidence.get("provider_request_id"),
            "http_status": evidence.get("http_status"),
            "request_hash": failed["request_hash"],
            "wire_payload_hash": failed["wire_payload_hash"],
            "retry_index": 1,
            "per_identity_retry_cap": 1,
            "release_affected_identity_cap": affected_cap,
            "release_affected_identity_index": 1,
            "delay_seconds": StagedExecutor._v1_2_5xx_delay(failed),
            "retry_policy_hash": proposal["ambiguous_5xx_retry_policy_hash"],
        }
        expected_keys = {
            "sequence", "previous_event_hash", "kind", "recorded_at",
            "event_hash", "not_before", *exact.keys(),
        }
        if (set(schedule) != expected_keys
                or any(schedule.get(key) != value
                       for key, value in exact.items())
                or schedule.get("sequence") != failed.get("sequence", -1) + 1
                or not isinstance(recovered_reservation, Mapping)
                or recovered_reservation.get("sequence")
                != schedule.get("sequence", -1) + 1
                or recovered.get("sequence")
                != recovered_reservation.get("sequence", -1) + 1
                or recovered.get("wire_payload_hash")
                != failed.get("wire_payload_hash")
                or final.get("provider_result", {}).get(
                    "transport_recovered") is not True
                or final.get("provider_result", {}).get(
                    "retry_policy_hash")
                != proposal["ambiguous_5xx_retry_policy_hash"]
                or final.get("provider_result", {}).get(
                    "ambiguous_5xx_retry_count") != 1
                or final.get("provider_result", {}).get(
                    "prior_ambiguous_result_event_hashes")
                != [failed["event_hash"]]):
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 HTTP-5xx schedule or exact-wire retry differs"
            )
        try:
            not_before = StagedExecutor._v1_2_5xx_schedule_not_before(schedule)
            retry_reserved_at = _canonical_utc_timestamp(
                recovered_reservation.get("recorded_at"),
                "HTTP-5xx retry reservation recorded_at",
            )
        except Exception as exc:
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 HTTP-5xx schedule timing is invalid"
            ) from exc
        if retry_reserved_at < not_before:
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 HTTP-5xx retry was reserved before not_before"
            )

    required = proposal["required_provider_identity"]
    if (provider_audits != 20
            or request_audits != 20
            or wire_audits != 20
            or run.manifest.get("required_provider_identity") != required
            or run.manifest.get("observed_provider_identity") != required):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 did not complete all request, wire, and provider audits"
        )
    body = {
        "artifact_type": "gate_1_v1_2_exact_attempt_audit_v1",
        "logical_requests_audited": 20,
        "request_payloads_verified": "20_of_20",
        "wire_payloads_verified": "20_of_20",
        "provider_audits_complete": "20_of_20",
        "dispatch_attempts": len(results),
        "known_not_completed_retries": sum(
            row.get("status") == "retryable_error" for row in results
        ),
        "ambiguous_5xx_retries": len(eligible_5xx),
        "required_provider_identity": required,
        "observed_provider_identity": run.manifest.get(
            "observed_provider_identity"
        ),
    }
    return {**body, "audit_hash": digest(body)}


def _completion_report_value(
        run: DurableRun, proposal: Mapping[str, Any],
        budget_snapshot: Mapping[str, Any], assessment: Mapping[str, Any],
        decision: Mapping[str, Any], mode_binding: Mapping[str, Any],
        sensitivity: Mapping[str, Any]) -> dict[str, Any]:
    passed = assessment.get("passed") is True
    status = (
        "completed_v1_2_gate1_passed_hard_stop"
        if passed else "completed_v1_2_gate1_failed_hard_stop"
    )
    execution_mode = mode_binding.get("mode")
    provenance_pair = (
        execution_mode, mode_binding.get("executor_provenance")
    )
    if provenance_pair not in {
            (EMPIRICAL_EXECUTION_MODE, EMPIRICAL_EXECUTOR_PROVENANCE),
            (FAKE_EXECUTION_MODE, FAKE_EXECUTOR_PROVENANCE)}:
        raise V12Gate1ReadinessError("v1.2 Gate 1 execution mode is invalid")
    attempt_audit = _audit_exact_attempt_shapes(run, proposal)
    body = {
        "artifact_type": "gate_1_v1_2_calibration_hard_stop_report_v1",
        "controller": V1_2_GATE1_CONTROLLER_ID,
        "status": status,
        "decision": "passed" if passed else "failed",
        "source": (
            "fake-transport-test"
            if execution_mode == FAKE_EXECUTION_MODE else "empirical-live"
        ),
        "warning": (
            "FAKE TRANSPORT TEST OUTPUT. NOT EMPIRICAL MODEL EVIDENCE."
            if execution_mode == FAKE_EXECUTION_MODE else None
        ),
        "execution_mode": execution_mode,
        "executor_provenance": mode_binding["executor_provenance"],
        "execution_mode_event_hash": mode_binding["event_hash"],
        "experiment_started": False,
        "completed_stage": "calibration",
        "logical_requests": 20,
        "dispatch_attempts": len(run.result_events()),
        "preflight_dispatches": 0,
        "smoke_dispatches": 0,
        "later_stage_dispatches": 0,
        "proposal_hash": proposal["proposal_hash"],
        "human_authorization_hash": run.manifest[
            "v1_2_gate1_human_authorization_hash"
        ],
        "authorization_receipt_hash": run.manifest[
            "v1_2_gate1_authorization_receipt_hash"
        ],
        "authorization_claim_event_hash": run.manifest[
            "v1_2_gate1_authorization_claim_event_hash"
        ],
        "capability_bridge": proposal["capability_bridge"],
        "protocol_version": run.plan.protocol_version,
        "protocol_hash": run.plan.protocol_hash,
        "retry_policy": proposal["ambiguous_5xx_retry_policy"],
        "retry_policy_hash": proposal["ambiguous_5xx_retry_policy_hash"],
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "implementation_hash": run.plan.implementation_hash,
        "transport_hash": proposal["transport_hash"],
        "required_provider_identity": proposal["required_provider_identity"],
        "observed_provider_identity": run.manifest.get(
            "observed_provider_identity"
        ),
        "attempt_shape_audit": attempt_audit,
        "pricing_contract": proposal["pricing_contract"],
        "assessment": json.loads(canonical(dict(assessment))),
        "assessment_hash": digest(dict(assessment)),
        "gate_decision_event_hash": decision["event_hash"],
        "gate_decision_evidence_hash": decision["evidence_hash"],
        "gate1_passed": passed,
        "transport_outcome_sensitivity": json.loads(
            canonical(dict(sensitivity))
        ),
        "budget": json.loads(canonical(dict(budget_snapshot))),
        "journal_tail": run.manifest.get("last_event_hash"),
        "journal_event_count": run.manifest.get("event_count"),
        "next_stage_authorized": False,
    }
    return {**body, "report_hash": digest(body)}


def _validated_v1_2_gate1_executor_inventory(
        run: DurableRun, provider: OpenAIResponsesProvider,
        proposal: Mapping[str, Any], inventory: Mapping[str, Any],
        authorization_receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Validate shared release inputs without selecting an execution mode."""
    if run.manifest.get("execution_controller") != V1_2_GATE1_CONTROLLER_ID:
        raise V12Gate1ReadinessError(
            "run is not marked for the v1.2 Gate 1 controller"
        )
    receipt = dict(authorization_receipt)
    receipt_hash = receipt.pop("receipt_hash", None)
    bindings = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_BINDING_EVENT
    ]
    if (len(bindings) != 1
            or not isinstance(receipt_hash, str)
            or receipt_hash != digest(receipt)
            or receipt.get("artifact_type")
            != "gate_1_v1_2_authorization_consumption_receipt_v1"
            or receipt.get("proposal_hash") != proposal["proposal_hash"]
            or receipt.get("authorization_hash")
            != run.manifest.get("v1_2_gate1_human_authorization_hash")
            or receipt.get("run_directory") != str(run.path.resolve())
            or receipt.get("run_instance_event_hash")
            != bindings[0].get("event_hash")
            or receipt.get("run_instance_hash")
            != digest(bindings[0].get("run_instance_nonce"))
            or run.manifest.get("v1_2_gate1_authorization_consumed") is not True
            or run.manifest.get("v1_2_gate1_authorization_receipt_hash")
            != receipt_hash):
        raise V12Gate1ReadinessError(
            "a durable one-use v1.2 Gate 1 receipt is required"
        )
    claim = _require_claim_event(
        run,
        proposal,
        {
            "authorization_uuid": receipt["authorization_uuid"],
            "authorization_hash": receipt["authorization_hash"],
            "gate1_run_id": receipt["gate1_run_id"],
        },
        authorization_receipt,
    )
    if (run.manifest.get("v1_2_gate1_authorization_claim_event_hash")
            != claim["event_hash"]):
        raise V12Gate1ReadinessError(
            "authorization claim manifest is not journal-derived"
        )
    if not isinstance(provider, OpenAIResponsesProvider):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 requires the OpenAI Responses adapter"
        )
    transport = getattr(provider, "transport", None)
    if not isinstance(transport, OpenAISDKTransport):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 requires the locked official-SDK transport wrapper"
        )
    if (transport.audit_metadata != proposal["transport"]
            or digest(transport.audit_metadata) != proposal["transport_hash"]):
        raise V12Gate1ReadinessError(
            "provider transport differs from the reviewed proposal"
        )
    expected_inventory = _inventory(run.config)
    if (canonical(dict(inventory)) != canonical(expected_inventory)
            or expected_inventory["inventory_hash"]
            != proposal["calibration_projection"]["inventory_hash"]):
        raise V12Gate1ReadinessError(
            "runtime calibration inventory differs from authorization"
        )
    return expected_inventory


class V12Gate1Executor(StagedExecutor):
    """Dedicated wrapper restricting generic v1.2 recovery to 20 calibration rows."""

    def __init__(
            self, run: DurableRun, provider: OpenAIResponsesProvider,
            proposal: Mapping[str, Any], inventory: Mapping[str, Any], *,
            authorization_receipt: Mapping[str, Any],
            _executor_capability: object | None = None,
            sleep=None,
            now=None):
        if type(self) is not V12Gate1Executor:
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 executor subclasses cannot acquire release authority"
            )
        if _executor_capability is not _FAKE_EXECUTOR_CAPABILITY:
            raise V12Gate1ReadinessError(
                "v1.2 Gate 1 executor is constructible only by the explicit "
                "offline fake factory"
            )
        expected_inventory = _validated_v1_2_gate1_executor_inventory(
            run,
            provider,
            proposal,
            inventory,
            authorization_receipt,
        )
        self.proposal = json.loads(canonical(dict(proposal)))
        self.inventory = expected_inventory
        self.fake_transport_test = True
        self.executor_provenance = FAKE_EXECUTOR_PROVENANCE
        with run.execution_lock():
            self.mode_binding = _bind_fake_execution_mode(run, proposal)
        super().__init__(
            run,
            provider,
            ScientificRequestFactory(run.config, run.plan),
            sleep=(lambda _seconds: None) if sleep is None else sleep,
            now=now,
            dedicated_controller=V1_2_GATE1_CONTROLLER_ID,
            _dedicated_capability=_V1_2_GATE1_STAGED_TEST_CAPABILITY,
        )
        self._validate_exact_scope_history()

    def _bind_or_check_provider_identity(
            self, provider_status: str,
            result: Mapping[str, Any]) -> str | None:
        """Require the pre-reviewed identity on the first and every result."""
        candidate = _provider_identity_candidate(
            self.run.config, provider_status, result
        )
        if candidate is None:
            return None
        required = self.proposal["required_provider_identity"]
        existing = self.run.manifest.get("observed_provider_identity")
        if candidate != required:
            self.run.set_status(
                "paused_model_drift",
                observed_provider_identity=existing,
                required_provider_identity=required,
                rejected_provider_identity=candidate,
                budget=self.ledger.snapshot(),
            )
            return "paused_model_drift"
        if existing not in (None, required):
            self.run.set_status(
                "paused_model_drift",
                observed_provider_identity=existing,
                required_provider_identity=required,
                rejected_provider_identity=candidate,
                budget=self.ledger.snapshot(),
            )
            return "paused_model_drift"
        if existing is None:
            self.run.set_status(
                "running",
                observed_provider_identity=required,
                required_provider_identity=required,
                budget=self.ledger.snapshot(),
            )
        return None

    def _required_gate(self, spec: ScheduledRequest) -> str | None:
        if (spec.stage == "calibration"
                and spec.phase == "calibration"
                and spec.action == "verdict"):
            return None
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 cannot dispatch outside calibration"
        )

    def _selected(self) -> list[ScheduledRequest]:
        selected = list(self.run.plan.requests)
        if (len(selected) != 20
                or self.run.plan.checkpoints
                or any(
                    spec.stage != "calibration"
                    or spec.phase != "calibration"
                    or spec.action != "verdict"
                    or spec.dependencies
                    or spec.parent_checkpoint is not None
                    for spec in selected
                )
                or digest([spec.identity for spec in selected])
                != self.inventory["logical_identity_hash"]):
            raise V12Gate1ReadinessError(
                "runtime v1.2 Gate 1 selection differs from the proposal"
            )
        return selected

    def _validate_exact_scope_history(self) -> None:
        allowed = {spec.identity for spec in self._selected()}
        for event in self.run.events:
            if (event.get("kind") in {"reservation", "result"}
                    and event.get("identity") not in allowed):
                raise ResumeError(
                    "v1.2 Gate 1 journal contains out-of-scope dispatch"
                )
            if event.get("kind") == "checkpoint":
                raise ResumeError("v1.2 Gate 1 cannot contain checkpoints")
            if (event.get("kind") == "gate"
                    and event.get("gate") != "calibration"):
                raise ResumeError(
                    "v1.2 Gate 1 contains a non-calibration gate"
                )

    def _audit_historical_provider_results_before_dispatch(self) -> None:
        """Fail closed on a crash-persisted identity/raw mismatch before I/O."""
        required = self.proposal["required_provider_identity"]
        for event in self.run.result_events():
            if event.get("status") not in PROVIDER_IDENTITY_STATUSES:
                continue
            provider = event.get("provider_result")
            candidate = (
                _provider_identity_candidate(
                    self.run.config, str(event.get("status")), provider
                )
                if isinstance(provider, Mapping) else None
            )
            identity_valid = (
                candidate == required
                and event.get("provider_identity") == required
            )
            raw_valid = (
                isinstance(provider, Mapping)
                and provider_result_matches_raw_response(provider)
            )
            if identity_valid and raw_valid:
                continue
            status = (
                "paused_model_drift"
                if candidate != required else
                "paused_provider_result_audit"
            )
            self.run.set_status(
                status,
                stop_identity=event.get("identity"),
                observed_provider_identity=self.run.manifest.get(
                    "observed_provider_identity"
                ),
                required_provider_identity=required,
                rejected_provider_identity=candidate,
                rejected_result_event_hash=event.get("event_hash"),
                budget=self.ledger.snapshot(),
            )
            raise V12Gate1ReadinessError(
                "durable v1.2 Gate 1 provider identity/raw audit failed "
                "before dispatch"
            )
        observed = self.run.manifest.get("observed_provider_identity")
        if observed not in (None, required):
            self.run.set_status(
                "paused_model_drift",
                observed_provider_identity=observed,
                required_provider_identity=required,
                rejected_provider_identity=observed,
                budget=self.ledger.snapshot(),
            )
            raise V12Gate1ReadinessError(
                "durable v1.2 Gate 1 provider identity drifted before dispatch"
            )

    def _gate_decision(self) -> tuple[dict[str, Any], dict[str, Any]]:
        assessment = calibration_assessment(self.run)
        gates = self.run.gate_events()
        existing = gates.get("calibration")
        if existing is None:
            existing = self.run._record_gate_decision_unlocked(
                "calibration",
                passed=assessment["passed"],
                evidence=assessment,
                decided_by=V1_2_GATE1_CONTROLLER_ID,
                note=(
                    "Frozen automatic protocol-v1.2 capability decision; "
                    "hard-stop before smoke."
                ),
            )
        elif (set(gates) != {"calibration"}
                or existing.get("passed") is not assessment["passed"]
                or canonical(existing.get("evidence")) != canonical(assessment)
                or existing.get("evidence_hash") != digest(assessment)
                or existing.get("decided_by") != V1_2_GATE1_CONTROLLER_ID):
            raise ResumeError(
                "immutable v1.2 Gate 1 decision differs from assessment"
            )
        return assessment, existing

    def _write_completion_report(
            self, assessment: Mapping[str, Any],
            decision: Mapping[str, Any]) -> dict[str, Any]:
        sensitivity = _transport_outcome_sensitivity(self.run)
        report = _completion_report_value(
            self.run,
            self.proposal,
            self.ledger.snapshot(),
            assessment,
            decision,
            self.mode_binding,
            sensitivity,
        )
        report_path = self.run.path / "gate1_v1_2_calibration_report.json"
        _write_immutable_json(
            report_path, report, "v1.2 Gate 1 calibration report"
        )
        return report

    def _finalize_completion(
            self, assessment: Mapping[str, Any],
            decision: Mapping[str, Any]) -> dict[str, Any]:
        status = (
            "completed_v1_2_gate1_passed_hard_stop"
            if assessment["passed"] else
            "completed_v1_2_gate1_failed_hard_stop"
        )
        expected_fields = {
            "completed_through_stage": "calibration",
            "completed_through_block": 0,
            "calibration_decision": (
                "passed" if assessment["passed"] else "failed"
            ),
            "calibration_assessment_hash": digest(assessment),
            "calibration_gate_event_hash": decision["event_hash"],
            "next_stage_authorized": False,
            "experiment_started": False,
            "budget": self.ledger.snapshot(),
            "stop_identity": None,
        }
        if (self.run.manifest.get("status") != status
                or any(self.run.manifest.get(key) != value
                       for key, value in expected_fields.items())):
            self.run.set_status(status, **expected_fields)
        return self._write_completion_report(assessment, decision)

    def execute(self) -> dict[str, Any]:
        """Execute exactly 20 calibration requests and hard-stop before smoke."""
        completed_statuses = {
            "completed_v1_2_gate1_passed_hard_stop",
            "completed_v1_2_gate1_failed_hard_stop",
        }
        with self.run.execution_lock():
            if (self.run.manifest.get("execution_controller")
                    != V1_2_GATE1_CONTROLLER_ID):
                raise V12Gate1ReadinessError(
                    "v1.2 Gate 1 controller marker changed"
                )
            self._validate_exact_scope_history()
            self.mode_binding = _require_execution_mode_binding(
                self.run,
                self.proposal,
                expected_mode=(
                    FAKE_EXECUTION_MODE
                    if self.fake_transport_test else EMPIRICAL_EXECUTION_MODE
                ),
                expected_provenance=self.executor_provenance,
            )
            self._audit_historical_provider_results_before_dispatch()
            if self.run.manifest.get("status") in completed_statuses:
                if (self.ledger.active
                        or any(not self._resolved(spec.identity)
                               for spec in self._selected())):
                    raise ResumeError(
                        "completed v1.2 Gate 1 contains unresolved work"
                    )
                assessment, decision = self._gate_decision()
                expected = (
                    "completed_v1_2_gate1_passed_hard_stop"
                    if assessment["passed"] else
                    "completed_v1_2_gate1_failed_hard_stop"
                )
                if self.run.manifest.get("status") != expected:
                    raise ResumeError(
                        "completed status differs from immutable decision"
                    )
                return self._finalize_completion(assessment, decision)
            if self.run.gate_events():
                # A crash may occur after the immutable gate event but before
                # the manifest/report publication.  Once all work is resolved,
                # complete that local transaction without entering dispatch.
                if (self.ledger.active
                        or any(not self._resolved(spec.identity)
                               for spec in self._selected())):
                    raise ResumeError(
                        "v1.2 Gate 1 decision exists with unresolved work"
                    )
                assessment, decision = self._gate_decision()
                return self._finalize_completion(assessment, decision)
            report = self._execute_unlocked("calibration", 0)
            if (report.status != "completed_stage"
                    or report.completed_logical_requests != 20
                    or any(not self._resolved(spec.identity)
                           for spec in self._selected())):
                raise V12Gate1ReadinessError(
                    f"v1.2 Gate 1 stopped before completion: {report.status}"
                )
            assessment, decision = self._gate_decision()
            return self._finalize_completion(assessment, decision)


def build_fake_v1_2_gate1_executor_for_test(
        run: DurableRun, provider: OpenAIResponsesProvider,
        proposal: Mapping[str, Any], inventory: Mapping[str, Any], *,
        authorization_receipt: Mapping[str, Any],
        sleep=None, now=None) -> V12Gate1Executor:
    """Construct the conspicuously labelled offline-only fake controller."""
    return V12Gate1Executor(
        run,
        provider,
        proposal,
        inventory,
        authorization_receipt=authorization_receipt,
        _executor_capability=_FAKE_EXECUTOR_CAPABILITY,
        sleep=sleep,
        now=now,
    )


def inspect_v1_2_gate1(
        proposal_path: Path = PROPOSAL_PATH, *,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate the final no-dispatch package without authorization or secrets."""
    proposal = load_v1_2_gate1_proposal(proposal_path)
    _validate_pricing_window(proposal, now=now)
    schema = authorization_schema(proposal)
    return {
        "status": "ready_awaiting_external_human_authorization",
        "execution_ready": True,
        "proposal_hash": proposal["proposal_hash"],
        "authorization_schema_hash": digest(schema),
        "calibration_inventory_hash": proposal["calibration_projection"][
            "inventory_hash"
        ],
        "implementation_hash": proposal["run"]["implementation_hash"],
        "protocol": proposal["protocol"],
        "capability_bridge_hash": proposal["capability_bridge"]["bridge_hash"],
        "retry_policy_hash": proposal["ambiguous_5xx_retry_policy_hash"],
        "authorization_validity": proposal["external_authorization"],
        "exact_caps": proposal["exact_caps"],
        "scope": proposal["scope"],
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
        "remaining_step": (
            "A human must create the external one-use authorization artifact "
            "matching the deterministic schema."
        ),
    }


def validate_v1_2_gate1_authorization_file(
        authorization_path: Path,
        proposal_path: Path = PROPOSAL_PATH, *,
        now: datetime | None = None) -> dict[str, Any]:
    """Validate external approval and its derived contract; never read a key."""
    proposal = load_v1_2_gate1_proposal(proposal_path)
    authorization = load_v1_2_gate1_human_authorization(
        authorization_path,
        proposal,
        _proposal_verified=True,
    )
    _validate_first_claim_window(authorization, proposal, now=now)
    config, plan, dispatch, inventory = _v1_2_gate1_config_and_plan(
        proposal,
        authorization,
        _proposal_verified=True,
    )
    return {
        "status": "authorization_valid_no_dispatch",
        "authorization_hash": authorization["authorization_hash"],
        "gate1_run_id": authorization["gate1_run_id"],
        "gate1_run_directory": authorization["gate1_run_directory"],
        "config_hash": digest(config.to_dict()),
        "plan_hash": plan.plan_hash,
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "calibration_inventory_hash": inventory["inventory_hash"],
        "credentials_accessed": False,
        "network_calls": 0,
        "paid_calls": 0,
        "experiment_started": False,
    }


def _verify_completed_v1_2_gate1(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], receipt: Mapping[str, Any], *,
        require_empirical: bool = True) -> dict[str, Any]:
    completed_statuses = {
        "completed_v1_2_gate1_passed_hard_stop",
        "completed_v1_2_gate1_failed_hard_stop",
    }
    if (run.config.run_id != V1_2_GATE1_RUN_ID
            or run.config.protocol_version != "v1.2"
            or run.config.source != "empirical-live"
            or run.config.calibration_namespace != V1_2_CALIBRATION_NAMESPACE
            or run.manifest.get("status") not in completed_statuses
            or run.manifest.get("execution_controller")
            != V1_2_GATE1_CONTROLLER_ID
            or run.manifest.get("v1_2_gate1_proposal_hash")
            != proposal["proposal_hash"]
            or digest(run.config.to_dict()) != run.manifest.get("config_hash")
            or run.plan.plan_hash != run.manifest.get("plan_hash")):
        raise V12Gate1ReadinessError(
            "completed v1.2 Gate 1 differs from its release contract"
        )
    inventory = _inventory(run.config)
    allowed = {spec.identity for spec in run.plan.requests}
    if (len(allowed) != 20 or run.plan.checkpoints
            or inventory["inventory_hash"]
            != proposal["calibration_projection"]["inventory_hash"]
            or any(
                spec.stage != "calibration"
                or spec.phase != "calibration"
                or spec.action != "verdict"
                or spec.dependencies
                or spec.parent_checkpoint is not None
                for spec in run.plan.requests
            )):
        raise V12Gate1ReadinessError("completed v1.2 Gate 1 scope is not exact")
    try:
        dispatch = DispatchAuthorizationArtifact.from_mapping(
            run.manifest["dispatch_authorization"]
        )
    except (AuthorizationRequiredError, KeyError, TypeError, ValueError) as exc:
        raise V12Gate1ReadinessError(
            "completed dispatch authorization is invalid"
        ) from exc
    try:
        validate_dispatch_authorization(
            run.config, run.plan, dispatch, require_initial=True
        )
    except (AuthorizationRequiredError, TypeError, ValueError) as exc:
        raise V12Gate1ReadinessError(
            "completed dispatch authorization differs from the run"
        ) from exc
    expected_config_value = json.loads(
        canonical(proposal["gate1_config_template"])
    )
    expected_config_value["authorization"] = {
        "kind": "paid",
        "authorization_id": dispatch.authorization_id,
        "approved_at": dispatch.confirmed_at,
        "approved_budget_usd": proposal["exact_caps"]["max_cost_usd"],
        "allow_paid_calls": True,
    }
    try:
        expected_config = LiveConfig.from_mapping(expected_config_value)
        expected_plan = build_request_plan(expected_config)
    except (TypeError, ValueError) as exc:
        raise V12Gate1ReadinessError(
            "completed v1.2 Gate 1 configuration cannot be re-derived"
        ) from exc
    if (canonical(run.config.to_dict()) != canonical(expected_config.to_dict())
            or canonical(run.plan.to_dict()) != canonical(expected_plan.to_dict())
            or run.plan.protocol_hash != proposal["protocol"]["protocol_hash"]
            or run.plan.implementation_hash
            != proposal["run"]["implementation_hash"]
            or digest(dict(run.plan.fixture_inventory))
            != proposal["run"]["fixture_inventory_hash"]
            or digest(dispatch.to_dict())
            != run.manifest.get("dispatch_authorization_hash")):
        raise V12Gate1ReadinessError(
            "completed config, plan, implementation, or dispatch derivation differs"
        )
    expected_binding = _binding_event_value(
        proposal, authorization, run.config, run.plan, dispatch
    )
    bindings = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_BINDING_EVENT
    ]
    if (len(bindings) != 1
            or any(bindings[0].get(key) != value
                   for key, value in expected_binding.items())
            or re.fullmatch(
                r"[a-f0-9]{64}", str(bindings[0].get("run_instance_nonce"))
            ) is None):
        raise V12Gate1ReadinessError(
            "completed durable binding differs from the release"
        )
    receipt_body = dict(receipt)
    receipt_hash = receipt_body.pop("receipt_hash", None)
    expected_receipt = {
        "artifact_type": "gate_1_v1_2_authorization_consumption_receipt_v1",
        "controller": V1_2_GATE1_CONTROLLER_ID,
        "authorization_uuid": authorization["authorization_uuid"],
        "authorization_hash": authorization["authorization_hash"],
        "proposal_hash": proposal["proposal_hash"],
        "capability_bridge_hash": proposal["capability_bridge"]["bridge_hash"],
        "retry_policy_hash": proposal["ambiguous_5xx_retry_policy_hash"],
        "gate1_run_id": authorization["gate1_run_id"],
        "run_directory": str(run.path.resolve()),
        "config_hash": digest(run.config.to_dict()),
        "plan_hash": run.plan.plan_hash,
        "dispatch_authorization_hash": digest(dispatch.to_dict()),
        "run_instance_event_hash": bindings[0]["event_hash"],
        "run_instance_hash": digest(bindings[0]["run_instance_nonce"]),
    }
    if (set(receipt_body) != set(expected_receipt) | {"claimed_at"}
            or receipt_hash != digest(receipt_body)
            or any(receipt_body.get(key) != value
                   for key, value in expected_receipt.items())
            or run.manifest.get("v1_2_gate1_authorization_receipt_hash")
            != receipt_hash):
        raise V12Gate1ReadinessError(
            "completed authorization receipt differs from the exact release"
        )
    _parse_timestamp(receipt_body.get("claimed_at"), "receipt claimed_at")
    claim = _require_claim_event(run, proposal, authorization, receipt)
    modes = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_EXECUTION_MODE_EVENT
    ]
    if (len(modes) != 1
            or modes[0].get("controller") != V1_2_GATE1_CONTROLLER_ID
            or modes[0].get("proposal_hash") != proposal["proposal_hash"]
            or modes[0].get("run_id") != V1_2_GATE1_RUN_ID
            or (
                modes[0].get("mode"),
                modes[0].get("executor_provenance"),
            ) not in {
                (EMPIRICAL_EXECUTION_MODE, EMPIRICAL_EXECUTOR_PROVENANCE),
                (FAKE_EXECUTION_MODE, FAKE_EXECUTOR_PROVENANCE),
            }
            or modes[0].get("sequence", -1) <= claim.get("sequence", -1)):
        raise V12Gate1ReadinessError(
            "completed release lacks a valid execution-mode binding"
        )
    mode = modes[0]
    if require_empirical and (
            mode.get("mode") != EMPIRICAL_EXECUTION_MODE
            or mode.get("executor_provenance")
            != EMPIRICAL_EXECUTOR_PROVENANCE):
        raise V12Gate1ReadinessError(
            "passed lineage requires empirical-live execution"
        )
    if (mode.get("mode") == FAKE_EXECUTION_MODE
            and mode.get("executor_provenance") != FAKE_EXECUTOR_PROVENANCE):
        raise V12Gate1ReadinessError(
            "fake execution lacks explicit fake-factory provenance"
        )
    dependencies = run.manifest.get("runtime", {}).get(
        "third_party_dependencies"
    )
    if (run.manifest.get("transport") != proposal["transport"]
            or run.manifest.get("transport_hash") != proposal["transport_hash"]
            or dependencies != [{"name": "openai", "version": STAGE2_SDK_VERSION}]):
        raise V12Gate1ReadinessError(
            "completed release lacks the exact locked transport"
        )
    dispatch_events = [
        event for event in run.events
        if event.get("kind") in {"reservation", "result"}
    ]
    results = run.result_events()
    if (not results
            or any(event.get("identity") not in allowed
                   for event in dispatch_events)
            or any(event.get("sequence", -1) <= mode["sequence"]
                   for event in dispatch_events)
            or any(not run.result_events(spec.identity)
                   or run.result_events(spec.identity)[-1].get("status")
                   not in OBSERVATION_STATUSES
                   for spec in run.plan.requests)
            or any(event.get("kind") == "checkpoint" for event in run.events)):
        raise V12Gate1ReadinessError(
            "completed release contains missing, pre-mode, or out-of-scope work"
        )
    attempt_audit = _audit_exact_attempt_shapes(run, proposal)
    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    ledger = BudgetLedger.replay(
        run.config.limits,
        run.config.pricing,
        Decimal(proposal["exact_caps"]["max_cost_usd"]),
        run.budget_records(),
        release,
    )
    if ledger.active:
        raise V12Gate1ReadinessError(
            "completed release has an unreconciled reservation"
        )
    assessment = calibration_assessment(run)
    contract = proposal["assessment_contract"]
    threshold_passed = bool(
        assessment.get("scheduled") == 20
        and assessment.get("schema_valid") == 20
        and assessment.get("request_payloads_verified") == 20
        and assessment.get("wire_payloads_verified") == 20
        and assessment.get("provider_audits_complete") == 20
        and assessment.get("correct_packet_approvals", -1) >= 9
        and assessment.get("faulty_packet_rejections", -1) >= 9
    )
    if (assessment.get("assessment_type") != contract["assessment_type"]
            or assessment.get("scheduled") != contract["scheduled"]
            or assessment.get("passed") is not threshold_passed
            or ("passed" if assessment.get("passed") else "failed")
            not in contract["decision_values"]):
        raise V12Gate1ReadinessError(
            "completed capability assessment differs from its exact contract"
        )
    gates = run.gate_events()
    decision = gates.get("calibration")
    if (set(gates) != {"calibration"}
            or decision is None
            or decision.get("passed") is not (assessment.get("passed") is True)
            or canonical(decision.get("evidence")) != canonical(assessment)
            or decision.get("evidence_hash") != digest(assessment)
            or decision.get("decided_by") != V1_2_GATE1_CONTROLLER_ID
            or decision.get("sequence", -1)
            <= max(event.get("sequence", -1) for event in results)
            or run.manifest.get("last_event_hash") != decision.get("event_hash")):
        raise V12Gate1ReadinessError(
            "completed release decision differs from its frozen assessment"
        )
    expected_status = (
        "completed_v1_2_gate1_passed_hard_stop"
        if assessment["passed"] else
        "completed_v1_2_gate1_failed_hard_stop"
    )
    if (run.manifest.get("status") != expected_status
            or run.manifest.get("calibration_decision")
            != ("passed" if assessment["passed"] else "failed")
            or run.manifest.get("calibration_assessment_hash")
            != digest(assessment)
            or run.manifest.get("calibration_gate_event_hash")
            != decision["event_hash"]
            or run.manifest.get("next_stage_authorized") is not False
            or run.manifest.get("experiment_started") is not False):
        raise V12Gate1ReadinessError(
            "completed release manifest differs from immutable decision"
        )
    sensitivity = _transport_outcome_sensitivity(run)
    expected_report = _completion_report_value(
        run,
        proposal,
        ledger.snapshot(),
        assessment,
        decision,
        mode,
        sensitivity,
    )
    report = _read_immutable_json_once(
        run.path / "gate1_v1_2_calibration_report.json",
        "v1.2 Gate 1 calibration report",
    )
    if canonical(report) != canonical(expected_report):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 report differs from durable evidence"
        )
    if report.get("attempt_shape_audit") != attempt_audit:
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 report omits its exact attempt audit"
        )
    return report


def _recover_v1_2_gate1_completion_boundary(
        run: DurableRun, proposal: Mapping[str, Any],
        authorization: Mapping[str, Any], receipt: Mapping[str, Any], *,
        require_empirical: bool) -> dict[str, Any] | None:
    """Finish only the local gate/manifest/report transaction after 20 results.

    This path performs no provider construction or dispatch.  It covers crashes
    after the final result, after the gate event, or after the completion
    manifest but before the immutable report publication.
    """
    specs = list(run.plan.requests)
    if (len(specs) != 20
            or any(not run.result_events(spec.identity)
                   or run.result_events(spec.identity)[-1].get("status")
                   not in OBSERVATION_STATUSES
                   for spec in specs)):
        return None
    if (run.manifest.get("execution_controller")
            != V1_2_GATE1_CONTROLLER_ID
            or run.manifest.get("v1_2_gate1_proposal_hash")
            != proposal["proposal_hash"]):
        raise V12Gate1ReadinessError(
            "completed boundary differs from the v1.2 Gate 1 release"
        )
    _require_claim_event(run, proposal, authorization, receipt)
    modes = [
        event for event in run.events
        if event.get("kind") == V1_2_GATE1_EXECUTION_MODE_EVENT
    ]
    expected_pair = (
        (EMPIRICAL_EXECUTION_MODE, EMPIRICAL_EXECUTOR_PROVENANCE)
        if require_empirical else None
    )
    if (len(modes) != 1
            or (modes[0].get("mode"), modes[0].get("executor_provenance"))
            not in {
                (EMPIRICAL_EXECUTION_MODE, EMPIRICAL_EXECUTOR_PROVENANCE),
                (FAKE_EXECUTION_MODE, FAKE_EXECUTOR_PROVENANCE),
            }
            or (expected_pair is not None
                and (modes[0].get("mode"),
                     modes[0].get("executor_provenance")) != expected_pair)):
        raise V12Gate1ReadinessError(
            "completed boundary lacks exact executor provenance"
        )
    if (run.manifest.get("transport") != proposal["transport"]
            or run.manifest.get("transport_hash") != proposal["transport_hash"]
            or run.manifest.get("runtime", {}).get("third_party_dependencies")
            != [{"name": "openai", "version": STAGE2_SDK_VERSION}]):
        raise V12Gate1ReadinessError(
            "completed boundary lacks the exact locked transport"
        )
    _audit_exact_attempt_shapes(run, proposal)
    release = DispatchReleaseLimits.from_mapping(proposal["exact_caps"])
    ledger = BudgetLedger.replay(
        run.config.limits,
        run.config.pricing,
        Decimal(proposal["exact_caps"]["max_cost_usd"]),
        run.budget_records(),
        release,
    )
    if ledger.active:
        raise V12Gate1ReadinessError(
            "completed boundary contains an unreconciled reservation"
        )
    assessment = calibration_assessment(run)
    gates = run.gate_events()
    decision = gates.get("calibration")
    if decision is None:
        if gates:
            raise V12Gate1ReadinessError(
                "completed boundary contains an unauthorized gate"
            )
        decision = run._record_gate_decision_unlocked(
            "calibration",
            passed=assessment["passed"],
            evidence=assessment,
            decided_by=V1_2_GATE1_CONTROLLER_ID,
            note=(
                "Frozen automatic protocol-v1.2 capability decision; "
                "hard-stop before smoke."
            ),
        )
    elif (set(gates) != {"calibration"}
            or decision.get("passed") is not assessment["passed"]
            or canonical(decision.get("evidence")) != canonical(assessment)
            or decision.get("evidence_hash") != digest(assessment)
            or decision.get("decided_by") != V1_2_GATE1_CONTROLLER_ID):
        raise V12Gate1ReadinessError(
            "completed boundary gate differs from its assessment"
        )
    status = (
        "completed_v1_2_gate1_passed_hard_stop"
        if assessment["passed"] else
        "completed_v1_2_gate1_failed_hard_stop"
    )
    fields = {
        "completed_through_stage": "calibration",
        "completed_through_block": 0,
        "calibration_decision": (
            "passed" if assessment["passed"] else "failed"
        ),
        "calibration_assessment_hash": digest(assessment),
        "calibration_gate_event_hash": decision["event_hash"],
        "next_stage_authorized": False,
        "experiment_started": False,
        "budget": ledger.snapshot(),
        "stop_identity": None,
    }
    if (run.manifest.get("status") != status
            or any(run.manifest.get(key) != value
                   for key, value in fields.items())):
        run.set_status(status, **fields)
    sensitivity = _transport_outcome_sensitivity(run)
    report = _completion_report_value(
        run,
        proposal,
        ledger.snapshot(),
        assessment,
        decision,
        modes[0],
        sensitivity,
    )
    _write_immutable_json(
        run.path / "gate1_v1_2_calibration_report.json",
        report,
        "v1.2 Gate 1 calibration report",
    )
    return _verify_completed_v1_2_gate1(
        run,
        proposal,
        authorization,
        receipt,
        require_empirical=require_empirical,
    )


def _run_v1_2_gate1(
        authorization_path: Path, *,
        proposal_path: Path = PROPOSAL_PATH,
        env_file: Path = STAGE2_ENV_FILE,
        now: datetime | None = None,
        workspace_root: Path | None = None) -> dict[str, Any]:
    """Private deterministic-clock seam for the exact public run path."""
    proposal = load_v1_2_gate1_proposal(proposal_path)
    authorization = load_v1_2_gate1_human_authorization(
        authorization_path,
        proposal,
        require_external=True,
        _proposal_verified=True,
    )
    receipt_dir = _authorization_receipt_directory(Path(authorization_path))
    receipt_path = _authorization_receipt_path(receipt_dir)
    execution_root = ROOT if workspace_root is None else Path(workspace_root)
    run_path = execution_root / authorization["gate1_run_directory"]
    completed_statuses = {
        "completed_v1_2_gate1_passed_hard_stop",
        "completed_v1_2_gate1_failed_hard_stop",
    }
    if receipt_path.exists():
        if not run_path.is_dir():
            raise V12Gate1ReadinessError(
                "authorization receipt exists without its durable run"
            )
        validated_path, _exists = _validated_v1_2_gate1_run_path(
            run_path, authorization, workspace_root=execution_root
        )
        candidate = DurableRun.inspect(
            validated_path, probe_installed_dependencies=False
        )
        receipt = claim_v1_2_gate1_authorization_once(
            candidate,
            proposal,
            authorization,
            receipt_dir=receipt_dir,
            now=now,
        )
        with candidate.execution_lock():
            recovered = _recover_v1_2_gate1_completion_boundary(
                candidate,
                proposal,
                authorization,
                receipt,
                require_empirical=True,
            )
            if recovered is not None:
                return recovered
        if candidate.manifest.get("status") in completed_statuses:
            raise V12Gate1ReadinessError(
                "completed v1.2 Gate 1 lacks its 20 final results"
            )
        # A consumed but incomplete release may continue only while its pricing
        # review remains fresh.  A fully completed, receipt-backed run above is
        # immutable evidence and never needs credentials or a refreshed price.
        _validate_pricing_window(proposal, now=now)
    else:
        try:
            _validate_first_claim_window(authorization, proposal, now=now)
        except V12Gate1ReadinessError as exc:
            if run_path.exists():
                raise V12Gate1ReadinessError(
                    "the prepared v1.2 Gate 1 release missed its first-claim "
                    "window; a new versioned release/run ID is required"
                ) from exc
            raise
    config, plan, dispatch, inventory = _v1_2_gate1_config_and_plan(
        proposal,
        authorization,
        _proposal_verified=True,
    )
    run, prepared_config, prepared_plan, prepared_dispatch, prepared_inventory = (
        prepare_v1_2_gate1_run(
            run_path,
            proposal,
            authorization,
            workspace_root=execution_root,
            _proposal_verified=True,
        )
    )
    if (prepared_config != config
            or prepared_plan.to_dict() != plan.to_dict()
            or prepared_dispatch.to_dict() != dispatch.to_dict()
            or prepared_inventory["inventory_hash"]
            != inventory["inventory_hash"]):
        raise V12Gate1ReadinessError("prepared v1.2 Gate 1 contract changed")
    receipt = claim_v1_2_gate1_authorization_once(
        run,
        proposal,
        authorization,
        receipt_dir=receipt_dir,
        now=now,
    )
    with run.execution_lock():
        recovered = _recover_v1_2_gate1_completion_boundary(
            run,
            proposal,
            authorization,
            receipt,
            require_empirical=True,
        )
        if recovered is not None:
            return recovered
    if run.manifest.get("status") in completed_statuses:
        raise V12Gate1ReadinessError(
            "completed v1.2 Gate 1 could not recover its exact boundary"
        )

    # The durable receipt and journal claim precede every credential/SDK/client
    # operation below.  No earlier helper imports the SDK or reads env_file.
    try:
        installed = importlib_metadata.version("openai")
    except importlib_metadata.PackageNotFoundError as exc:
        raise V12Gate1ReadinessError(
            "locked OpenAI SDK is not installed"
        ) from exc
    if installed != STAGE2_SDK_VERSION:
        raise V12Gate1ReadinessError(
            "installed OpenAI SDK differs from the proposal"
        )
    transport_metadata = _stage2_transport_metadata(sdk_version=installed)
    if (transport_metadata != proposal["transport"]
            or digest(transport_metadata) != proposal["transport_hash"]):
        raise V12Gate1ReadinessError("runtime transport differs from proposal")
    conflicting = sorted(
        name for name in os.environ if name.upper().startswith("OPENAI_")
    )
    if conflicting:
        raise V12Gate1ReadinessError(
            "all ambient OpenAI variables must be unset for this release"
        )
    secret = _load_local_openai_key(Path(env_file))
    from openai import (
        DefaultHttpxClient as ExportedDefaultHttpxClient,
        OpenAI as ExportedOpenAI,
    )
    from openai._base_client import (
        DefaultHttpxClient as SDKDefaultHttpxClient,
    )
    from openai._client import OpenAI as SDKOpenAI
    from openai.resources.responses.responses import Responses as SDKResponses

    # Do not let a patched SDK export or duck-typed caller object acquire
    # empirical provenance.  This path accepts no client/provider/transport
    # argument and checks the exact locked SDK implementations before creating
    # the live objects.
    if (ExportedOpenAI is not SDKOpenAI
            or ExportedDefaultHttpxClient is not SDKDefaultHttpxClient
            or SDKOpenAI.__module__ != "openai"
            or SDKOpenAI.__qualname__ != "OpenAI"
            or SDKDefaultHttpxClient.__module__ != "openai"
            or SDKDefaultHttpxClient.__qualname__ != "_DefaultHttpxClient"
            or SDKResponses.__module__
            != "openai.resources.responses.responses"
            or SDKResponses.__qualname__ != "Responses"):
        raise V12Gate1ReadinessError(
            "official OpenAI SDK class identities differ from the locked runtime"
        )
    http_client = SDKDefaultHttpxClient(
        trust_env=False,
        follow_redirects=False,
    )
    client = None
    try:
        client = SDKOpenAI(
            api_key=secret,
            base_url="https://api.openai.com/v1",
            max_retries=0,
            timeout=STAGE2_TIMEOUT_SECONDS,
            http_client=http_client,
        )
        responses = client.responses
        try:
            timeout_matches = (
                float(client.timeout) == float(STAGE2_TIMEOUT_SECONDS)
            )
        except (TypeError, ValueError):
            timeout_matches = False
        if (type(http_client) is not SDKDefaultHttpxClient
                or getattr(http_client, "trust_env", None) is not False
                or getattr(http_client, "follow_redirects", None) is not False
                or type(client) is not SDKOpenAI
                or getattr(client, "_client", None) is not http_client
                or type(responses) is not SDKResponses
                or str(getattr(client, "base_url", "")).rstrip("/")
                != "https://api.openai.com/v1"
                or getattr(client, "max_retries", None) != 0
                or not timeout_matches
                or getattr(client, "organization", None) is not None
                or getattr(client, "project", None) is not None):
            raise V12Gate1ReadinessError(
                "constructed OpenAI client differs from the exact official release"
            )
        transport = OpenAISDKTransport(
            client,
            timeout_seconds=STAGE2_TIMEOUT_SECONDS,
            sdk_version=installed,
            ambient_openai_environment="rejected",
            http_client_trust_env=False,
            http_client_follow_redirects=False,
        )
        provider = OpenAIResponsesProvider(transport)
        expected_inventory = _validated_v1_2_gate1_executor_inventory(
            run,
            provider,
            proposal,
            inventory,
            receipt,
        )

        # Bypass the fake-only public constructor only inside this lexical live
        # path.  No empirical factory, capability token, provider seam, or
        # executor object is exposed to callers.
        controller = object.__new__(V12Gate1Executor)
        controller.proposal = json.loads(canonical(dict(proposal)))
        controller.inventory = expected_inventory
        controller.fake_transport_test = False
        controller.executor_provenance = EMPIRICAL_EXECUTOR_PROVENANCE
        with run.execution_lock():
            mode_bindings = [
                event for event in run.events
                if event.get("kind") == V1_2_GATE1_EXECUTION_MODE_EVENT
            ]
            if mode_bindings:
                controller.mode_binding = _require_execution_mode_binding(
                    run,
                    proposal,
                    expected_mode=EMPIRICAL_EXECUTION_MODE,
                    expected_provenance=EMPIRICAL_EXECUTOR_PROVENANCE,
                )
            else:
                claims = [
                    event for event in run.events
                    if event.get("kind")
                    == V1_2_GATE1_AUTHORIZATION_CLAIM_EVENT
                ]
                if len(claims) != 1:
                    raise V12Gate1ReadinessError(
                        "v1.2 Gate 1 empirical mode requires a prior durable claim"
                    )
                if any(event.get("kind") in {"reservation", "result"}
                       for event in run.events):
                    raise ResumeError(
                        "v1.2 Gate 1 dispatch exists without an execution mode binding"
                    )
                controller.mode_binding = run.append(
                    V1_2_GATE1_EXECUTION_MODE_EVENT,
                    controller=V1_2_GATE1_CONTROLLER_ID,
                    proposal_hash=proposal["proposal_hash"],
                    run_id=V1_2_GATE1_RUN_ID,
                    mode=EMPIRICAL_EXECUTION_MODE,
                    executor_provenance=EMPIRICAL_EXECUTOR_PROVENANCE,
                )
        StagedExecutor.__init__(
            controller,
            run,
            provider,
            ScientificRequestFactory(run.config, run.plan),
            dedicated_controller=V1_2_GATE1_CONTROLLER_ID,
        )
        controller._validate_exact_scope_history()
        return controller.execute()
    finally:
        if client is not None:
            client.close()
        else:
            http_client.close()


def run_v1_2_gate1(
        authorization_path: Path, *,
        proposal_path: Path = PROPOSAL_PATH,
        env_file: Path = STAGE2_ENV_FILE) -> dict[str, Any]:
    """Execute the exact one-use release and hard-stop before smoke.

    The public release surface deliberately exposes no caller-controlled clock.
    """
    return _run_v1_2_gate1(
        authorization_path,
        proposal_path=proposal_path,
        env_file=env_file,
        now=None,
        workspace_root=None,
    )


def verify_v1_2_gate1_passed_lineage(
        run_dir: Path | None = None, *,
        proposal_path: Path = PROPOSAL_PATH) -> dict[str, Any]:
    """Verify immutable empirical pass evidence for the next release."""
    proposal = load_v1_2_gate1_proposal(proposal_path)
    path = (
        ROOT / proposal["run"]["run_directory"]
        if run_dir is None else Path(run_dir)
    )
    run = DurableRun.inspect(path, probe_installed_dependencies=False)
    receipt = _load_fixed_authorization_receipt()
    if (receipt.get("run_directory") != str(run.path.resolve())
            or receipt.get("proposal_hash") != proposal["proposal_hash"]
            or receipt.get("gate1_run_id") != V1_2_GATE1_RUN_ID):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 receipt does not bind the requested lineage run"
        )
    authorization_stub = {
        "authorization_uuid": receipt.get("authorization_uuid"),
        "authorization_hash": receipt.get("authorization_hash"),
        "gate1_run_id": receipt.get("gate1_run_id"),
    }
    with run.execution_lock():
        report = _verify_completed_v1_2_gate1(
            run,
            proposal,
            authorization_stub,
            receipt,
            require_empirical=True,
        )
    if (run.manifest.get("status")
            != "completed_v1_2_gate1_passed_hard_stop"
            or report.get("decision") != "passed"
            or report.get("gate1_passed") is not True
            or report.get("next_stage_authorized") is not False
            or report.get("executor_provenance")
            != EMPIRICAL_EXECUTOR_PROVENANCE):
        raise V12Gate1ReadinessError(
            "v1.2 Gate 1 passed lineage is incomplete or non-empirical"
        )
    sensitivity = report["transport_outcome_sensitivity"]
    return {
        "run_id": V1_2_GATE1_RUN_ID,
        "protocol_version": "v1.2",
        "protocol_hash": run.plan.protocol_hash,
        "proposal_hash": proposal["proposal_hash"],
        "report_hash": report["report_hash"],
        "journal_tail": run.manifest["last_event_hash"],
        "journal_event_count": run.manifest["event_count"],
        "required_provider_identity": proposal["required_provider_identity"],
        "observed_provider_identity": report["observed_provider_identity"],
        "provider_identity": report["observed_provider_identity"],
        "executor_provenance": report["executor_provenance"],
        "transport_hash": proposal["transport_hash"],
        "retry_policy_hash": proposal["ambiguous_5xx_retry_policy_hash"],
        "transport_outcome_sensitivity_hash": sensitivity["sensitivity_hash"],
        "passed": True,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect, validate, or execute protocol-v1.2 Gate 1"
    )
    parser.add_argument(
        "command", choices=("inspect", "validate", "execute")
    )
    parser.add_argument("--proposal", type=Path, default=PROPOSAL_PATH)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--env-file", type=Path, default=STAGE2_ENV_FILE)
    args = parser.parse_args()
    if args.command == "inspect":
        value = inspect_v1_2_gate1(args.proposal)
    else:
        if args.authorization is None:
            parser.error("--authorization is required for validate/execute")
        if args.command == "validate":
            value = validate_v1_2_gate1_authorization_file(
                args.authorization, args.proposal
            )
        else:
            value = run_v1_2_gate1(
                args.authorization,
                proposal_path=args.proposal,
                env_file=args.env_file,
            )
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
