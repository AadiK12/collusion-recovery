"""Inspect-only verifier for the consumed Stage 2/v1.0 evidence.

The former Gate 2 readiness generator is intentionally retired.  Stage 2 is
historical v1.0 capability evidence, while the executable successor is a fresh
v1.1 release with its own plan, namespace, run identifier, and authorization
boundary.  Re-rendering a Gate 2 proposal from the Stage 2 configuration would
either compare unlike protocol versions or silently relabel v1.1 behavior as
v1.0.

This module therefore reads and verifies persisted Stage 2 evidence only.  It
contains no provider, SDK-client, credential-loader, or dispatch entry point.
The legacy readiness validator remains solely for inspecting an already saved
no-dispatch artifact; it cannot produce one.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from .budget import (
    LONG_CONTEXT_INPUT_MULTIPLIER,
    LONG_CONTEXT_OUTPUT_MULTIPLIER,
    LONG_CONTEXT_THRESHOLD_INPUT_TOKENS,
)
from .common import canonical, digest
from .live_config import LiveConfig
from .stage2_release_state import (
    STAGE2_DISPATCH_ATTEMPT_COUNT,
    STAGE2_EVENT_COUNT,
    STAGE2_JOURNAL_TAIL,
    STAGE2_PACKAGE_HASH,
    STAGE2_REPORT_HASH,
    STAGE2_RUN_ID,
    Stage2ReleaseStateError,
    load_stage2_consumed_receipt,
)
from .visibility import ROOT


STAGE2_RUN_DIR = ROOT / "runs/empirical/stage2-2026-10-23"
GATE2_AUTHORIZATION_PATH = STAGE2_RUN_DIR / "gate2_human_authorization.json"
STAGE2_SEMANTIC_PLAN_HASH = (
    "9272c78627d3b47f111b1268aec8190a2bd77aea62951f368cf96d250da7d52e"
)

# Reverified against the official model page during this preparation pass.
# Prices are time-sensitive and must be reverified again when a human approves
# an executable release.
PRICING_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6.1-sol"
PRICING_REVERIFIED_AT = "2026-10-24T14:02:52-07:00"
class Gate2ReadinessError(RuntimeError):
    """Stage 2 evidence or the no-dispatch Gate 2 boundary is invalid."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Gate2ReadinessError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise Gate2ReadinessError(f"{label} must be a JSON object")
    return value


def _verify_self_hash(value: Mapping[str, Any], field: str, expected: str,
                      label: str) -> str:
    body = dict(value)
    actual = body.pop(field, None)
    if actual != expected or digest(body) != actual:
        raise Gate2ReadinessError(f"{label} self-hash or expected identity differs")
    return str(actual)


def _read_and_verify_journal(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise Gate2ReadinessError("Stage 2 journal is unavailable") from exc
    if not raw or not raw.endswith(b"\n"):
        raise Gate2ReadinessError("Stage 2 journal is empty or has a torn tail")
    rows: list[dict[str, Any]] = []
    previous = None
    for sequence, chunk in enumerate(raw.splitlines()):
        try:
            event = json.loads(chunk.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise Gate2ReadinessError("Stage 2 journal contains invalid JSON") from exc
        if not isinstance(event, dict):
            raise Gate2ReadinessError("Stage 2 journal event is not an object")
        event_hash = event.pop("event_hash", None)
        if (event.get("sequence") != sequence
                or event.get("previous_event_hash") != previous
                or not isinstance(event_hash, str)
                or digest(event) != event_hash):
            raise Gate2ReadinessError("Stage 2 journal hash chain differs")
        event["event_hash"] = event_hash
        rows.append(event)
        previous = event_hash
    return rows


def _semantic_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    result = json.loads(canonical(dict(value)))
    result.pop("implementation_hash", None)
    return result


def verify_stage2_lineage(run_dir: Path = STAGE2_RUN_DIR) -> dict[str, Any]:
    """Verify exact persisted Stage 2 evidence without opening credentials."""
    run_dir = Path(run_dir)
    try:
        consumed_receipt = load_stage2_consumed_receipt()
    except Stage2ReleaseStateError as exc:
        raise Gate2ReadinessError(
            "the tracked Stage 2 consumed-release receipt is invalid"
        ) from exc
    manifest = _read_json(run_dir / "manifest.json", "Stage 2 manifest")
    plan = _read_json(run_dir / "plan.json", "Stage 2 plan")
    package = _read_json(
        run_dir / "stage2_authorization_package.json",
        "Stage 2 authorization package",
    )
    report = _read_json(run_dir / "stage2_report.json", "Stage 2 report")
    package_hash = _verify_self_hash(
        package, "package_hash", STAGE2_PACKAGE_HASH,
        "Stage 2 authorization package",
    )
    report_hash = _verify_self_hash(
        report, "report_hash", STAGE2_REPORT_HASH, "Stage 2 report",
    )
    events = _read_and_verify_journal(run_dir / "journal.jsonl")
    tail = events[-1]["event_hash"]

    try:
        config = LiveConfig.from_mapping(manifest["config"])
    except (KeyError, TypeError, ValueError) as exc:
        raise Gate2ReadinessError("Stage 2 manifest configuration is invalid") from exc
    config_hash = digest(config.to_dict())
    plan_hash = digest(plan)
    semantic_plan_hash = digest(_semantic_plan(plan))
    authorization = manifest.get("dispatch_authorization")
    authorization_hash = (digest(authorization)
                          if isinstance(authorization, Mapping) else None)
    manifest_transport = manifest.get("transport")
    report_transport = report.get("transport")
    transport_hash = (digest(manifest_transport)
                      if isinstance(manifest_transport, Mapping) else None)
    authorization_transport_hash = (
        authorization.get("transport_hash")
        if isinstance(authorization, Mapping) else None
    )
    plan_requests = plan.get("requests")
    if (not isinstance(plan_requests, list)
            or any(not isinstance(row, Mapping) for row in plan_requests)):
        raise Gate2ReadinessError("Stage 2 plan request inventory is malformed")
    smoke_identities = {
        row.get("identity") for row in plan_requests if row.get("stage") == "smoke"
    }
    smoke_events = [
        event for event in events
        if event.get("kind") in {"reservation", "result"}
        and event.get("identity") in smoke_identities
    ]
    gates = report.get("gates")
    pricing = package.get("pricing")
    preflight_gate = (gates.get("transport_preflight")
                      if isinstance(gates, Mapping) else None)
    calibration_gate = (gates.get("calibration")
                        if isinstance(gates, Mapping) else None)
    expected_identity = {
        "requested_model": "gpt-6.1-sol",
        "returned_model": "gpt-6.1-sol",
        "metadata_model": "gpt-6.1-sol",
        "requested_service_tier": "default",
        "returned_service_tier": "default",
        "system_fingerprint": None,
    }
    invariant = (
        manifest.get("run_id") == STAGE2_RUN_ID
        and manifest.get("status") == "completed_stage"
        and manifest.get("config_hash") == config_hash == package.get("config_hash")
        == report.get("config_hash") == plan.get("config_hash")
        and manifest.get("plan_hash") == plan_hash == package.get("plan_hash")
        == report.get("plan_hash")
        and manifest.get("implementation_hash") == plan.get("implementation_hash")
        == package.get("implementation_hash") == report.get("implementation_hash")
        and semantic_plan_hash == STAGE2_SEMANTIC_PLAN_HASH
        and manifest.get("last_event_hash") == report.get("journal_tail")
        == tail == STAGE2_JOURNAL_TAIL
        and manifest.get("event_count") == report.get("journal_event_count")
        == len(events) == STAGE2_EVENT_COUNT
        and manifest.get("authorization_package_hash") == package_hash
        == report.get("authorization_package_hash")
        and manifest.get("dispatch_authorization_hash") == authorization_hash
        and authorization == report.get("authorization")
        and isinstance(manifest_transport, Mapping)
        and isinstance(report_transport, Mapping)
        and canonical(manifest_transport) == canonical(report_transport)
        and manifest.get("authorized_transport_hash")
        == manifest.get("transport_hash")
        == authorization_transport_hash
        == transport_hash
        and manifest.get("observed_provider_identity") == expected_identity
        == report.get("provider_identity")
        and manifest.get("authorized_stage") == "calibration"
        and manifest.get("authorized_max_block") == 0
        and report.get("stopped_before_smoke") is True
        and report.get("smoke_dispatches") == 0
        and not smoke_events
        and isinstance(preflight_gate, Mapping)
        and preflight_gate.get("passed") is True
        and isinstance(calibration_gate, Mapping)
        and calibration_gate.get("passed") is True
        and package.get("stop_before_stage") == "smoke"
        and config.stage == "calibration"
        and config.model == "gpt-6.1-sol"
        and dict(config.model_settings) == {
            "reasoning": {"effort": "low", "mode": "standard"},
            "service_tier": "default",
        }
        and isinstance(pricing, Mapping)
        and pricing.get("source") == PRICING_SOURCE
        and consumed_receipt["authorization_package_hash"] == package_hash
        and consumed_receipt["report_hash"] == report_hash
        and consumed_receipt["journal_tail"] == tail
        and consumed_receipt["journal_event_count"] == len(events)
        and isinstance(report.get("usage"), Mapping)
        and report["usage"].get("attempt_count")
        == consumed_receipt["dispatch_attempt_count"]
        == STAGE2_DISPATCH_ATTEMPT_COUNT
    )
    if not invariant:
        raise Gate2ReadinessError("Stage 2 lineage or stop-before-smoke invariant differs")
    return {
        "run_id": manifest["run_id"],
        "config": config,
        "config_hash": config_hash,
        "plan": plan,
        "plan_hash": plan_hash,
        "semantic_plan_hash": semantic_plan_hash,
        "implementation_hash": plan["implementation_hash"],
        "authorization_package_hash": package_hash,
        "report_hash": report_hash,
        "journal_tail": tail,
        "journal_event_count": len(events),
        "consumed_receipt_hash": consumed_receipt["receipt_hash"],
        "dispatch_authorization_hash": authorization_hash,
        "transport_hash": transport_hash,
        "provider_identity": expected_identity,
        "pricing": package["pricing"],
        "stage2_usage": report.get("usage"),
        "stage2_budget": report.get("budget"),
        "gates": gates,
    }


def audit_smoke_inventory(config: LiveConfig,
                          saved_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse to derive a new smoke inventory from historical v1.0 inputs."""
    del config, saved_plan
    raise Gate2ReadinessError(
        "the legacy Stage 2/v1.0 smoke-inventory generator is retired; build "
        "and verify the fresh versioned Gate 2 successor proposal instead"
    )


def build_gate2_readiness(
        run_dir: Path = STAGE2_RUN_DIR,
        authorization_path: Path = GATE2_AUTHORIZATION_PATH) -> dict[str, Any]:
    """Refuse to generate a legacy readiness artifact after v1.1 exists."""
    del run_dir, authorization_path
    raise Gate2ReadinessError(
        "the legacy Stage 2/v1.0 Gate 2 readiness generator is retired and "
        "cannot regenerate or relabel a proposal; use the fresh v1.1 successor"
    )


def validate_readiness_artifact(value: Mapping[str, Any]) -> None:
    """Inspect an already persisted legacy no-dispatch readiness artifact."""
    if not isinstance(value, Mapping):
        raise Gate2ReadinessError("Gate 2 readiness artifact must be an object")
    body = dict(value)
    readiness_hash = body.pop("readiness_hash", None)
    if (not isinstance(readiness_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", readiness_hash) is None
            or readiness_hash != digest(body)
            or body.get("artifact_type") != "gate_2_no_dispatch_readiness_v2"
            or body.get("status") != "blocked_no_dispatch"
            or body.get("paid_calls") != 0
            or body.get("network_calls") != 0
            or body.get("experiment_started") is not False
            or body.get("dispatch_boundary", {}).get("dispatch_enabled") is not False
            or body.get("human_authorization", {}).get("artifact_present") is not False):
        raise Gate2ReadinessError("Gate 2 readiness artifact was edited or claims dispatch")
