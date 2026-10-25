"""Fail-closed configuration for fake-live and future paid execution.

The offline :mod:`collusion_recovery.config` module intentionally remains a
closed, scripted-only path.  This module is a separate contract for staged
execution.  It never reads credentials and all values returned by ``to_dict``
are safe to persist in a run manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping
import tomllib
from urllib.parse import urlsplit

from .common import contains_secret_like_string


class LiveConfigError(ValueError):
    """Raised before a provider can be initialized or a run directory created."""


STAGE_ORDER = ("preflight", "calibration", "smoke", "formation", "remaining_arms", "probes")
RUN_TYPES = {"pilot", "confirmation"}
SOURCES = {"fake-live", "empirical-live"}
SUPPORTED_PROTOCOL_VERSIONS = {"v1.0", "v1.1", "v1.2"}
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
_SECRET_KEYS = {
    "api_key", "apikey", "authorization", "authorization_header", "bearer",
    "access_token", "credential", "credentials", "openai_api_key", "password",
    "secret", "token", "token_value",
}


def _decimal(value: Any, name: str, *, positive: bool = False,
             nonnegative: bool = False) -> Decimal:
    # Money and prices must be written as strings (or exact integers).  TOML
    # floats are binary approximations and are rejected at this boundary.
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int, Decimal)):
        raise LiveConfigError(f"{name} must be an exact decimal string or integer")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise LiveConfigError(f"{name} is not a valid decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        qualifier = "positive" if positive else "nonnegative"
        raise LiveConfigError(f"{name} must be a finite {qualifier} decimal")
    return result


def _timestamp(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveConfigError(f"{name} must be a nonempty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveConfigError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LiveConfigError(f"{name} must include an explicit UTC offset")
    return value


def _positive_int(value: Any, name: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        suffix = f" no greater than {maximum}" if maximum is not None else ""
        raise LiveConfigError(f"{name} must be a positive integer{suffix}")
    return value


def _nonnegative_int(value: Any, name: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        suffix = f" no greater than {maximum}" if maximum is not None else ""
        raise LiveConfigError(f"{name} must be a nonnegative integer{suffix}")
    return value


def _safe_json_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise LiveConfigError(f"{name} must be a nonempty mapping")

    def inspect(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise LiveConfigError(f"{path} contains a non-string key")
                normalized = key.lower().replace("-", "_")
                if (normalized in _SECRET_KEYS or normalized.endswith("_api_key")
                        or normalized.endswith("_key") or normalized.endswith("_secret")
                        or normalized.endswith("_password") or normalized.endswith("_credential")
                        or normalized.endswith("_access_token")):
                    raise LiveConfigError(f"{path} must not contain credentials or secrets")
                inspect(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                inspect(child, f"{path}[{index}]")
        elif item is None or isinstance(item, (str, int, float, bool)):
            if isinstance(item, float) and (item != item or abs(item) == float("inf")):
                raise LiveConfigError(f"{path} contains a non-finite number")
        else:
            raise LiveConfigError(f"{path} is not JSON serializable")

    inspect(value, name)
    if contains_secret_like_string(value):
        raise LiveConfigError(f"{name} must not contain credential-shaped values")
    # Round-trip to detach from caller-owned mutable dictionaries.
    copied = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    return MappingProxyType(copied)


@dataclass(frozen=True)
class Pricing:
    source: str
    verified_at: str
    input_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    cache_write_input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "Pricing":
        expected = {
            "source", "verified_at", "input_usd_per_million",
            "cached_input_usd_per_million", "cache_write_input_usd_per_million",
            "output_usd_per_million",
        }
        if not isinstance(values, Mapping) or set(values) != expected:
            raise LiveConfigError(f"pricing must contain exactly {sorted(expected)}")
        source = values["source"]
        if not isinstance(source, str) or not source.strip():
            raise LiveConfigError("pricing.source must be nonempty")
        return cls(
            source=source,
            verified_at=_timestamp(values["verified_at"], "pricing.verified_at"),
            input_usd_per_million=_decimal(
                values["input_usd_per_million"], "pricing.input_usd_per_million", nonnegative=True),
            cached_input_usd_per_million=_decimal(
                values["cached_input_usd_per_million"],
                "pricing.cached_input_usd_per_million", nonnegative=True),
            cache_write_input_usd_per_million=_decimal(
                values["cache_write_input_usd_per_million"],
                "pricing.cache_write_input_usd_per_million", nonnegative=True),
            output_usd_per_million=_decimal(
                values["output_usd_per_million"], "pricing.output_usd_per_million", nonnegative=True),
        )

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "verified_at": self.verified_at,
                "input_usd_per_million": str(self.input_usd_per_million),
                "cached_input_usd_per_million": str(
                    self.cached_input_usd_per_million),
                "cache_write_input_usd_per_million": str(
                    self.cache_write_input_usd_per_million),
                "output_usd_per_million": str(self.output_usd_per_million)}


@dataclass(frozen=True)
class Authorization:
    kind: str
    authorization_id: str
    approved_at: str
    approved_budget_usd: Decimal
    allow_paid_calls: bool

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "Authorization":
        expected = {"kind", "authorization_id", "approved_at", "approved_budget_usd", "allow_paid_calls"}
        if not isinstance(values, Mapping) or set(values) != expected:
            raise LiveConfigError(f"authorization must contain exactly {sorted(expected)}")
        kind = values["kind"]
        identifier = values["authorization_id"]
        allow = values["allow_paid_calls"]
        if kind not in {"fake-only", "paid"}:
            raise LiveConfigError("authorization.kind must be fake-only or paid")
        if not isinstance(identifier, str) or not _SAFE_NAME.fullmatch(identifier):
            raise LiveConfigError("authorization.authorization_id is missing or malformed")
        if type(allow) is not bool:
            raise LiveConfigError("authorization.allow_paid_calls must be a boolean")
        return cls(kind, identifier, _timestamp(values["approved_at"], "authorization.approved_at"),
                   _decimal(values["approved_budget_usd"], "authorization.approved_budget_usd",
                            nonnegative=True), allow)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "authorization_id": self.authorization_id,
                "approved_at": self.approved_at,
                "approved_budget_usd": str(self.approved_budget_usd),
                "allow_paid_calls": self.allow_paid_calls}


@dataclass(frozen=True)
class ExecutionLimits:
    max_requests: int
    max_total_input_tokens: int
    max_total_output_tokens: int
    max_input_tokens_per_request: int
    max_output_tokens_per_request: int
    max_in_flight: int

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ExecutionLimits":
        expected = {"max_requests", "max_total_input_tokens", "max_total_output_tokens",
                    "max_input_tokens_per_request", "max_output_tokens_per_request", "max_in_flight"}
        if not isinstance(values, Mapping) or set(values) != expected:
            raise LiveConfigError(f"limits must contain exactly {sorted(expected)}")
        result = cls(**{key: _positive_int(values[key], f"limits.{key}") for key in expected})
        if result.max_input_tokens_per_request > result.max_total_input_tokens:
            raise LiveConfigError("per-request input cap exceeds the total input-token cap")
        if result.max_output_tokens_per_request > result.max_total_output_tokens:
            raise LiveConfigError("per-request output cap exceeds the total output-token cap")
        if result.max_in_flight > result.max_requests:
            raise LiveConfigError("max_in_flight exceeds the request cap")
        return result

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in (
            "max_requests", "max_total_input_tokens", "max_total_output_tokens",
            "max_input_tokens_per_request", "max_output_tokens_per_request", "max_in_flight")}


@dataclass(frozen=True)
class RetryPolicy:
    transport_retries: int
    message_overlength_retries: int
    initial_backoff_seconds: Decimal
    maximum_backoff_seconds: Decimal

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RetryPolicy":
        expected = {"transport_retries", "message_overlength_retries",
                    "initial_backoff_seconds", "maximum_backoff_seconds"}
        if not isinstance(values, Mapping) or set(values) != expected:
            raise LiveConfigError(f"retry_policy must contain exactly {sorted(expected)}")
        result = cls(
            transport_retries=_nonnegative_int(values["transport_retries"],
                                                "retry_policy.transport_retries", maximum=2),
            message_overlength_retries=_nonnegative_int(values["message_overlength_retries"],
                                                         "retry_policy.message_overlength_retries", maximum=1),
            initial_backoff_seconds=_decimal(values["initial_backoff_seconds"],
                                             "retry_policy.initial_backoff_seconds", nonnegative=True),
            maximum_backoff_seconds=_decimal(values["maximum_backoff_seconds"],
                                             "retry_policy.maximum_backoff_seconds", nonnegative=True),
        )
        if result.maximum_backoff_seconds < result.initial_backoff_seconds:
            raise LiveConfigError("maximum retry backoff is smaller than the initial backoff")
        if result.message_overlength_retries != 1:
            raise LiveConfigError(
                "protocol v1.x requires exactly one explicit message-overlength retry"
            )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"transport_retries": self.transport_retries,
                "message_overlength_retries": self.message_overlength_retries,
                "initial_backoff_seconds": str(self.initial_backoff_seconds),
                "maximum_backoff_seconds": str(self.maximum_backoff_seconds)}


@dataclass(frozen=True)
class LiveConfig:
    run_id: str
    protocol_version: str
    source: str
    provider: str
    model: str
    model_context_tokens: int
    model_settings: Mapping[str, Any]
    pricing: Pricing
    authorization: Authorization
    limits: ExecutionLimits
    retry_policy: RetryPolicy
    run_type: str
    split: str
    stage: str
    seed_namespace: str
    calibration_namespace: str
    blocks: int

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "LiveConfig":
        expected = {"run_id", "protocol_version", "source", "provider", "model",
                    "model_context_tokens", "model_settings",
                    "pricing", "authorization", "limits", "retry_policy", "run_type", "split",
                    "stage", "seed_namespace", "calibration_namespace", "blocks"}
        if not isinstance(values, Mapping) or set(values) != expected:
            missing = sorted(expected - set(values)) if isinstance(values, Mapping) else sorted(expected)
            extra = sorted(set(values) - expected) if isinstance(values, Mapping) else []
            raise LiveConfigError(f"live configuration fields mismatch; missing={missing}, unknown={extra}")
        if contains_secret_like_string(values):
            raise LiveConfigError("live configuration must not contain credential-shaped values")
        result = cls(
            run_id=values["run_id"], protocol_version=values["protocol_version"],
            source=values["source"], provider=values["provider"], model=values["model"],
            model_context_tokens=_positive_int(values["model_context_tokens"], "model_context_tokens"),
            model_settings=_safe_json_mapping(values["model_settings"], "model_settings"),
            pricing=Pricing.from_mapping(values["pricing"]),
            authorization=Authorization.from_mapping(values["authorization"]),
            limits=ExecutionLimits.from_mapping(values["limits"]),
            retry_policy=RetryPolicy.from_mapping(values["retry_policy"]),
            run_type=values["run_type"], split=values["split"], stage=values["stage"],
            seed_namespace=values["seed_namespace"],
            calibration_namespace=values["calibration_namespace"],
            blocks=_positive_int(values["blocks"], "blocks", maximum=100),
        )
        result._validate()
        return result

    def _validate(self) -> None:
        for name in ("run_id", "split", "seed_namespace", "calibration_namespace"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value) or ".." in value.split("/"):
                raise LiveConfigError(f"{name} is missing or malformed")
        if self.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise LiveConfigError("unsupported protocol version")
        if self.source not in SOURCES:
            raise LiveConfigError("source must be fake-live or empirical-live")
        if self.run_type not in RUN_TYPES:
            raise LiveConfigError("run_type must be pilot or confirmation")
        run_prefix = {"pilot": "pilot/", "confirmation": "confirm/"}[self.run_type]
        # Historical v1.0 namespaces remain byte-for-byte loadable.  v1.1 and
        # the separately versioned v1.2 fallback make their protocol boundary
        # explicit so neither can reuse another version's split or fixtures.
        required_prefix = (
            run_prefix if self.protocol_version == "v1.0"
            else f"{run_prefix}{self.protocol_version}/"
            if self.protocol_version == "v1.1"
            else f"fallback-two-round/v1.2/{run_prefix}"
        )
        if not self.split.startswith(required_prefix):
            raise LiveConfigError(
                f"{self.run_type} output requires a separate {required_prefix} split namespace")
        if not self.seed_namespace.startswith(required_prefix):
            raise LiveConfigError(
                f"{self.run_type} fixtures require a separate {required_prefix} seed namespace")
        if self.protocol_version == "v1.1":
            if self.calibration_namespace != "calibration/v1.1":
                raise LiveConfigError(
                    "protocol v1.1 requires calibration_namespace=calibration/v1.1"
                )
        elif self.protocol_version == "v1.2":
            if self.calibration_namespace != "fallback-two-round/v1.2/calibration":
                raise LiveConfigError(
                    "protocol v1.2 requires calibration_namespace="
                    "fallback-two-round/v1.2/calibration"
                )
        elif not self.calibration_namespace.startswith("calibration/"):
            raise LiveConfigError("calibration_namespace must use a calibration/ namespace")
        if self.stage not in STAGE_ORDER:
            raise LiveConfigError(f"stage must be one of {STAGE_ORDER}")
        if self.run_type == "confirmation" and self.stage in {"preflight", "calibration", "smoke"}:
            raise LiveConfigError("confirmation stage must be formation, remaining_arms, or probes")
        if not isinstance(self.model, str) or not self.model.strip():
            raise LiveConfigError("model must be explicitly selected")
        if self.limits.max_output_tokens_per_request >= self.model_context_tokens:
            raise LiveConfigError("output-token cap must be smaller than the selected model context")
        if self.source == "empirical-live":
            if self.provider != "openai" or self.authorization.kind != "paid":
                raise LiveConfigError("empirical-live source requires the openai provider and paid authorization")
            if self.authorization.allow_paid_calls is not True:
                raise LiveConfigError("paid calls are not explicitly authorized")
            if self.authorization.approved_budget_usd <= 0:
                raise LiveConfigError("empirical-live execution requires a positive approved budget")
            service_tier = self.model_settings.get("service_tier")
            if not isinstance(service_tier, str) or not service_tier.strip():
                raise LiveConfigError(
                    "empirical-live execution requires an explicit model_settings.service_tier"
                )
            if not self.pricing.source.startswith("https://"):
                raise LiveConfigError("empirical-live pricing must name an HTTPS source")
            pricing_url = urlsplit(self.pricing.source)
            if (pricing_url.username or pricing_url.password or pricing_url.query
                    or pricing_url.fragment or not pricing_url.hostname):
                raise LiveConfigError("pricing source must be a public HTTPS URL without credentials or query data")
            if (self.pricing.input_usd_per_million <= 0
                    or self.pricing.cached_input_usd_per_million <= 0
                    or self.pricing.cache_write_input_usd_per_million <= 0
                    or self.pricing.output_usd_per_million <= 0):
                raise LiveConfigError("empirical-live pricing rates must be strictly positive")
        else:
            if self.provider != "fake" or self.authorization.kind != "fake-only":
                raise LiveConfigError("fake-live source requires the fake provider and fake-only authorization")
            if self.authorization.allow_paid_calls is not False:
                raise LiveConfigError("fake-live execution cannot authorize paid calls")
            if self.authorization.approved_budget_usd != 0:
                raise LiveConfigError("fake-live approved_budget_usd must be exactly zero")
            if (self.pricing.input_usd_per_million != 0
                    or self.pricing.cached_input_usd_per_million != 0
                    or self.pricing.cache_write_input_usd_per_million != 0
                    or self.pricing.output_usd_per_million != 0):
                raise LiveConfigError("fake-live pricing rates must be zero")

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "protocol_version": self.protocol_version,
                "source": self.source, "provider": self.provider, "model": self.model,
                "model_context_tokens": self.model_context_tokens,
                "model_settings": json.loads(json.dumps(dict(self.model_settings), sort_keys=True)),
                "pricing": self.pricing.to_dict(), "authorization": self.authorization.to_dict(),
                "limits": self.limits.to_dict(), "retry_policy": self.retry_policy.to_dict(),
                "run_type": self.run_type, "split": self.split, "stage": self.stage,
                "seed_namespace": self.seed_namespace,
                "calibration_namespace": self.calibration_namespace,
                "blocks": self.blocks}

    @property
    def warning(self) -> str | None:
        return ("FAKE-LIVE PROVIDER OUTPUT. NOT EMPIRICAL MODEL RESULTS."
                if self.source == "fake-live" else None)

    def permits_stage(self, requested: str) -> bool:
        if requested not in STAGE_ORDER:
            return False
        return STAGE_ORDER.index(requested) <= STAGE_ORDER.index(self.stage)


def load_live_config(path: str | Path) -> LiveConfig:
    """Load and fully validate a staged config without reading credentials."""
    try:
        with Path(path).open("rb") as handle:
            values = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise LiveConfigError(f"invalid TOML: {exc}") from exc
    return LiveConfig.from_mapping(values)
