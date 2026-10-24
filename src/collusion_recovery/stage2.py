"""Historical verifier for the consumed Gate 0 + Gate 1 empirical release.

The one-shot release has completed and is sealed by a checked-in consumed
receipt.  The public controller now performs only offline inspection or
verification of the historical ignored run; it cannot create, resume, or
dispatch Stage 2.  In particular, it does not regenerate an authorization
package under the historical v1 name after the protocol or implementation has
changed.  The paid authorization validator independently rejects this run id
on every DurableRun path.
"""

from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Mapping

from .budget import BudgetLedger, DispatchReleaseLimits
from .common import canonical, digest
from .gates import calibration_assessment
from .live_config import LiveConfig, load_live_config
from .orchestration import (
    DispatchAuthorizationArtifact,
    DurableRun,
    ExecutionContext,
    StagedExecutor,
    build_request_plan,
)
from .preflight import (
    PreflightRequestFactory,
    durable_preflight_assessment,
    preflight_inventory,
)
from .providers.openai import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
    openai_sdk_transport_metadata,
)
from .staged_requests import ScientificRequestFactory
from .stage2_release_state import (
    STAGE2_CONSUMED_RECEIPT,
    Stage2ReleaseStateError,
    load_stage2_consumed_receipt,
)
from .visibility import ROOT


STAGE2_CONFIRMED_AT = "2026-10-23T15:45:09-07:00"
STAGE2_SDK_VERSION = "3.37.0"
STAGE2_TIMEOUT_SECONDS = 60.0
STAGE2_INPUT_USD_PER_MILLION = Decimal("2")
STAGE2_CACHED_INPUT_USD_PER_MILLION = Decimal("0.10")
STAGE2_CACHE_WRITE_INPUT_USD_PER_MILLION = Decimal("2.50")
STAGE2_OUTPUT_USD_PER_MILLION = Decimal("10")
STAGE2_CONFIG = ROOT / "configs/empirical_stage2.toml"
STAGE2_RUN_DIR = ROOT / "runs/empirical/stage2-2026-10-23"
STAGE2_ENV_FILE = ROOT / ".env.local"
STAGE2_PREFLIGHT_INVENTORY_HASH = (
    "b5241d90d652fca0598717517953a46a168103e0a71d42ff28a2752fa6c534d4"
)
STAGE2_CALIBRATION_LONGEST_PROMPT_HASH = (
    "f4440b3545e209b2c909cd337737c2e66c398ce554c1a78bccf1dc7e79228401"
)
STAGE2_CALIBRATION_INVENTORY_HASH = (
    "ccfb1718f0961c79e9f70e8dd3806706e15fd72a77ef4536d15f463a3006d9bb"
)
STAGE2_RELEASE = {
    "max_dispatch_attempts": 67,
    "max_total_input_tokens": 211_932,
    "max_total_output_tokens": 94_208,
    "max_cost_usd": "1.78",
    "max_in_flight": 1,
    "max_input_tokens_per_request": 9_353,
    "max_output_tokens_per_request": 4_096,
}
class Stage2Error(RuntimeError):
    """The approved Stage 2 contract is missing, changed, or cannot proceed."""


class _CalibrationContextRun:
    @staticmethod
    def result_events(_identity: str) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def checkpoint_events() -> dict[str, Any]:
        return {}

    manifest: dict[str, Any] = {}


def _stage2_release() -> DispatchReleaseLimits:
    return DispatchReleaseLimits.from_mapping(STAGE2_RELEASE)


def _stage2_transport_metadata(*, sdk_version: str) -> dict[str, Any]:
    """Bind the SDK wrapper plus the exact environment-independent HTTP client."""
    return {
        **openai_sdk_transport_metadata(
            timeout_seconds=STAGE2_TIMEOUT_SECONDS,
            sdk_version=sdk_version,
        ),
        "ambient_openai_environment": "rejected",
        "http_client": "openai.DefaultHttpxClient",
        "http_client_trust_env": False,
        "http_client_follow_redirects": False,
    }


def _stage2_pricing(config: LiveConfig) -> dict[str, str]:
    """Persist the complete rate table needed to reproduce exact list cost."""
    return {
        "source": config.pricing.source,
        "verified_at": config.pricing.verified_at,
        "input_usd_per_million": str(STAGE2_INPUT_USD_PER_MILLION),
        "cached_input_usd_per_million": str(
            STAGE2_CACHED_INPUT_USD_PER_MILLION),
        "cache_write_input_usd_per_million": str(
            STAGE2_CACHE_WRITE_INPUT_USD_PER_MILLION),
        "output_usd_per_million": str(STAGE2_OUTPUT_USD_PER_MILLION),
        "formula": (
            "(ordinary_input*input_rate + cached_input*cached_input_rate + "
            "cache_write_input*cache_write_input_rate + "
            "output_including_reasoning*output_rate)/1000000"
        ),
    }


def _assert_exact_config(config: LiveConfig) -> None:
    expected = {
        "run_id": "empirical-pilot-stage2-v1",
        "protocol_version": "v1.0",
        "source": "empirical-live",
        "provider": "openai",
        "model": "gpt-6.1-sol",
        "model_context_tokens": 1_050_000,
        "run_type": "pilot",
        "split": "pilot/v1",
        "stage": "calibration",
        "seed_namespace": "pilot/v1",
        "calibration_namespace": "calibration/v1",
        "blocks": 5,
    }
    if any(getattr(config, name) != value for name, value in expected.items()):
        raise Stage2Error("configuration differs from the approved Stage 2 identity or scope")
    if dict(config.model_settings) != {
            "reasoning": {"effort": "low", "mode": "standard"},
            "service_tier": "default"}:
        raise Stage2Error("model settings differ from the approved Stage 2 settings")
    if (config.pricing.source
            != "https://developers.openai.com/api/docs/models/gpt-6.1-sol"
            or config.pricing.verified_at != STAGE2_CONFIRMED_AT
            or config.pricing.input_usd_per_million != STAGE2_INPUT_USD_PER_MILLION
            or (config.pricing.cached_input_usd_per_million
                != STAGE2_CACHED_INPUT_USD_PER_MILLION)
            or (config.pricing.cache_write_input_usd_per_million
                != STAGE2_CACHE_WRITE_INPUT_USD_PER_MILLION)
            or config.pricing.output_usd_per_million != STAGE2_OUTPUT_USD_PER_MILLION):
        raise Stage2Error("pricing differs from the approved Stage 2 pricing")
    if (config.authorization.authorization_id != "stage2-release-2026-10-23-v1"
            or config.authorization.approved_at != STAGE2_CONFIRMED_AT
            or config.authorization.approved_budget_usd != Decimal("1.78")
            or config.authorization.allow_paid_calls is not True):
        raise Stage2Error("paid authorization differs from the approved Stage 2 release")
    limits = config.limits.to_dict()
    expected_limits = {
        "max_requests": 67,
        "max_total_input_tokens": 211_932,
        "max_total_output_tokens": 94_208,
        "max_input_tokens_per_request": 9_353,
        "max_output_tokens_per_request": 4_096,
        "max_in_flight": 1,
    }
    if limits != expected_limits:
        raise Stage2Error("configured execution ceilings differ from the approved release")
    if config.retry_policy.to_dict() != {
            "transport_retries": 2,
            "message_overlength_retries": 1,
            "initial_backoff_seconds": "1",
            "maximum_backoff_seconds": "8"}:
        raise Stage2Error("retry policy differs from the approved Stage 2 policy")


def stage2_offline_package(config: LiveConfig) -> dict[str, Any]:
    """Refuse to regenerate the consumed historical authorization package.

    The persisted package is v1.0 evidence bound to its original protocol and
    implementation hashes.  Rebuilding the same artifact type with the current
    v1.1 protocol document or current implementation would silently relabel new
    bytes as historical evidence.  Auditors must instead use
    :func:`gate2_readiness.verify_stage2_lineage`, which reads and verifies the
    immutable persisted package against the tracked consumed-release receipt.
    """
    _assert_exact_config(config)
    try:
        load_stage2_consumed_receipt(STAGE2_CONSUMED_RECEIPT)
    except Stage2ReleaseStateError as exc:
        raise Stage2Error(
            "the tracked Stage 2 consumed-release receipt is invalid; the "
            "historical package cannot be regenerated"
        ) from exc
    raise Stage2Error(
        "the consumed Stage 2 v1.0 authorization package cannot be regenerated "
        "or relabeled by the current implementation; verify the persisted "
        "historical evidence instead"
    )


def _git_ignored_and_untracked(path: Path) -> bool:
    try:
        target = str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return False
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", target],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", target],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    return ignored and not tracked


def _load_local_openai_key(path: Path) -> str:
    """Read only OPENAI_KEY from one owner-only, ignored local file."""
    path = Path(path)
    if path.resolve(strict=False) != STAGE2_ENV_FILE.resolve(strict=False):
        raise Stage2Error("Stage 2 accepts credentials only from the repository .env.local")
    try:
        info = path.lstat()
    except OSError as exc:
        raise Stage2Error("the ignored .env.local credential file is unavailable") from exc
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise Stage2Error(".env.local must be an owner-only regular file, not a symlink")
    if not _git_ignored_and_untracked(path):
        raise Stage2Error(".env.local must be ignored by Git and must not be tracked")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise Stage2Error(".env.local could not be read as UTF-8") from exc
    found: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != "OPENAI_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        found.append(value)
    if len(found) != 1 or not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,}", found[0]):
        raise Stage2Error(".env.local must contain exactly one valid OPENAI_KEY assignment")
    return found[0]


def _authorization(config: LiveConfig, plan: Any, transport_hash: str,
                   package: Mapping[str, Any]) -> DispatchAuthorizationArtifact:
    """Refuse to mint the already-consumed historical authorization."""
    del config, plan, transport_hash, package
    raise Stage2Error(
        "the one-shot Stage 2 authorization is consumed and cannot be recreated"
    )


def _existing_or_record_gate_unlocked(run: DurableRun, gate: str,
                                      evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Verify or append one gate while the caller holds ``execution_lock``."""
    existing = run.gate_events().get(gate)
    if existing is not None:
        if (canonical(existing.get("evidence")) != canonical(dict(evidence))
                or existing.get("passed") is not evidence.get("passed")):
            raise Stage2Error(f"saved {gate} decision differs from its recomputed evidence")
        return existing
    return run._record_gate_decision_unlocked(
        gate,
        passed=bool(evidence["passed"]),
        evidence=evidence,
        decided_by="stage2-controller-v1",
        note="Computed from the exact durable Stage 2 journal; no human override.",
    )


def _assess_and_record_gate(run: DurableRun, gate: str,
                            assessment: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Refresh, recompute, and bind a gate in one process-wide critical section."""
    with run.execution_lock():
        evidence = assessment(run)
        event = _existing_or_record_gate_unlocked(run, gate, evidence)
        return evidence, event


def _usage_summary(run: DurableRun, ledger: BudgetLedger) -> dict[str, Any]:
    attempts = run.result_events()
    input_tokens = output_tokens = cached_tokens = 0
    cache_write_tokens = reasoning_tokens = 0
    complete_usage = True
    incomplete_usage_attempts = []
    request_ids = []
    timings = []
    for event in attempts:
        result = event.get("provider_result")
        usage = result.get("usage") if isinstance(result, Mapping) else None
        if event.get("known_not_completed") is True:
            pass
        elif not isinstance(usage, Mapping) or any(
                type(usage.get(name)) is not int
                for name in (
                    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                    "output_tokens", "reasoning_tokens", "total_tokens",
                )):
            complete_usage = False
            incomplete_usage_attempts.append({
                "identity": event.get("identity"),
                "attempt": event.get("attempt"),
            })
        else:
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
            cached_tokens += usage["cached_input_tokens"]
            cache_write_tokens += usage["cache_write_input_tokens"]
            reasoning_tokens += usage["reasoning_tokens"]
        if isinstance(result, Mapping):
            request_ids.append(result.get("provider_request_id"))
            timings.append(result.get("timing"))
    ordinary = (input_tokens - cached_tokens - cache_write_tokens
                if complete_usage else None)
    list_price = None
    if complete_usage:
        list_price = (
            Decimal(ordinary) * STAGE2_INPUT_USD_PER_MILLION
            + Decimal(cached_tokens) * STAGE2_CACHED_INPUT_USD_PER_MILLION
            + Decimal(cache_write_tokens) * STAGE2_CACHE_WRITE_INPUT_USD_PER_MILLION
            + Decimal(output_tokens) * STAGE2_OUTPUT_USD_PER_MILLION
        ) / Decimal(1_000_000)
    return {
        "attempt_count": len(attempts),
        "complete_provider_usage": complete_usage,
        "incomplete_usage_attempts": incomplete_usage_attempts,
        "input_tokens": input_tokens if complete_usage else None,
        "cached_input_tokens": cached_tokens if complete_usage else None,
        "cache_write_input_tokens": cache_write_tokens if complete_usage else None,
        "ordinary_input_tokens": ordinary,
        "output_tokens_including_reasoning": output_tokens if complete_usage else None,
        "reasoning_tokens": reasoning_tokens if complete_usage else None,
        "total_tokens": input_tokens + output_tokens if complete_usage else None,
        "conservative_accounted_cost_usd": ledger.snapshot()["consumed"]["cost_usd"],
        "provider_list_price_cost_usd": None if list_price is None else str(list_price),
        "provider_request_ids": request_ids,
        "timings": timings,
    }


def _write_immutable_json(path: Path, value: Mapping[str, Any], *, label: str) -> None:
    """Create one fsynced artifact, accepting only byte-identical replays."""
    encoded = canonical(dict(value)) + "\n"
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise Stage2Error(f"the existing {label} cannot be verified") from exc
        if existing != encoded:
            raise Stage2Error(f"the immutable {label} differs on replay")
        return
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            existing = path.read_text(encoding="utf-8")
            if existing != encoded:
                raise Stage2Error(f"the immutable {label} differs on replay")
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bind_authorization_package(run: DurableRun,
                                package: Mapping[str, Any]) -> None:
    """Persist and journal-bind the reviewed payload inventory before dispatch."""
    package_hash = package.get("package_hash")
    if not isinstance(package_hash, str) or len(package_hash) != 64:
        raise Stage2Error("the Stage 2 authorization package hash is malformed")
    artifact_name = "stage2_authorization_package.json"
    with run.execution_lock():
        _write_immutable_json(
            run.path / artifact_name,
            package,
            label="Stage 2 authorization package",
        )
        events = [event for event in run.events
                  if event.get("kind") == "authorization_package"]
        expected = {
            "authorization_package_hash": package_hash,
            "artifact": artifact_name,
        }
        if not events:
            run.append("authorization_package", **expected)
        elif (len(events) != 1
              or any(events[0].get(key) != value for key, value in expected.items())):
            raise Stage2Error("the durable Stage 2 authorization package binding differs")
        manifest_hash = run.manifest.get("authorization_package_hash")
        if manifest_hash not in (None, package_hash):
            raise Stage2Error("the manifest Stage 2 authorization package binding differs")
        if manifest_hash is None:
            run.set_status(
                str(run.manifest.get("status", "created")),
                authorization_package_hash=package_hash,
            )


def _durable_stage_summary(run: DurableRun, stage: str) -> dict[str, Any]:
    """Summarize only hash-chained events so a no-op resume is report-stable."""
    identities = {
        spec.identity for spec in run.plan.requests
        if spec.stage == stage
    }
    events = [event for event in run.result_events()
              if event.get("identity") in identities]
    statuses: dict[str, int] = {}
    for event in events:
        status = str(event.get("status"))
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "stage": stage,
        "planned_logical_requests": len(identities),
        "logical_requests_with_results": len({event["identity"] for event in events}),
        "attempt_result_events": len(events),
        "status_counts": dict(sorted(statuses.items())),
    }


def _build_and_write_report(*, run: DurableRun, run_path: Path,
                            config: LiveConfig, plan: Any,
                            package: Mapping[str, Any],
                            transport_metadata: Mapping[str, Any],
                            ) -> dict[str, Any]:
    """Create the terminal report from one refreshed, process-locked snapshot."""
    with run.execution_lock():
        gate_events = run.gate_events()
        preflight_gate = gate_events.get("transport_preflight")
        calibration_gate = gate_events.get("calibration")
        preflight_evidence = (None if preflight_gate is None
                              else preflight_gate["evidence"])
        calibration_evidence = (None if calibration_gate is None
                                else calibration_gate["evidence"])
        current_authorization = DispatchAuthorizationArtifact.from_mapping(
            run.manifest["dispatch_authorization"])
        ledger = BudgetLedger.replay(
            config.limits, config.pricing,
            Decimal(current_authorization.approved_budget_usd),
            run.budget_records(), current_authorization.release_limits,
        )
        body = {
            "artifact_type": "stage_2_execution_report_v1",
            "run_dir": str(run_path.resolve()),
            "config_hash": digest(config.to_dict()),
            "plan_hash": plan.plan_hash,
            "implementation_hash": plan.implementation_hash,
            "model": config.model,
            "model_settings": dict(config.model_settings),
            "pricing": _stage2_pricing(config),
            "authorization": current_authorization.to_dict(),
            "authorization_package_hash": package["package_hash"],
            "authorization_package_artifact": "stage2_authorization_package.json",
            "transport": dict(transport_metadata),
            "provider_identity": run.manifest.get("observed_provider_identity"),
            "preflight_execution": _durable_stage_summary(run, "preflight"),
            "preflight_assessment": preflight_evidence,
            "calibration_execution": _durable_stage_summary(run, "calibration"),
            "calibration_assessment": calibration_evidence,
            "gates": {
                name: {"passed": event["passed"],
                       "evidence_hash": event["evidence_hash"]}
                for name, event in sorted(gate_events.items())
            },
            "budget": ledger.snapshot(),
            "usage": _usage_summary(run, ledger),
            "stopped_before_smoke": True,
            "smoke_dispatches": 0,
            "journal_tail": run.manifest.get("last_event_hash"),
            "journal_event_count": run.manifest.get("event_count"),
        }
        report = {**body, "report_hash": digest(body)}
        _write_immutable_json(
            run_path / "stage2_report.json",
            report,
            label="Stage 2 report",
        )
        return report


def run_stage2(*, config_path: Path = STAGE2_CONFIG,
               run_dir: Path = STAGE2_RUN_DIR,
               env_file: Path = STAGE2_ENV_FILE,
               inspect_only: bool = False) -> dict[str, Any]:
    """Inspect or verify the completed Stage 2 release without dispatch."""
    config = load_live_config(config_path)
    _assert_exact_config(config)
    del env_file  # Kept for CLI/API compatibility; credentials are never opened.
    run_path = Path(run_dir)
    if run_path.resolve(strict=False) != STAGE2_RUN_DIR.resolve(strict=False):
        raise Stage2Error(
            "the consumed Stage 2 release is bound to its frozen run directory")
    try:
        receipt = load_stage2_consumed_receipt(STAGE2_CONSUMED_RECEIPT)
    except Stage2ReleaseStateError as exc:
        raise Stage2Error(
            "the tracked Stage 2 consumed-release receipt is invalid; dispatch "
            "remains disabled"
        ) from exc
    if inspect_only:
        return {
            "status": "inspection_complete_no_dispatch",
            "release_state": "consumed_non_replayable",
            "consumed_receipt": receipt,
            "historical_run_present": run_path.is_dir(),
            "authorization_replayable": False,
            "credential_access": "forbidden_and_not_attempted",
            "paid_calls": 0,
            "network_calls": 0,
        }

    if not run_path.is_dir():
        raise Stage2Error(
            "the one-shot Stage 2 release is already consumed; an absent ignored "
            "run directory cannot be recreated")

    # Import locally to keep the no-dispatch release-state check independent of
    # the larger readiness module.  The verifier reads only persisted JSON.
    from .gate2_readiness import Gate2ReadinessError, verify_stage2_lineage
    try:
        lineage = verify_stage2_lineage(run_path)
    except Gate2ReadinessError as exc:
        raise Stage2Error(
            "the historical Stage 2 evidence is unavailable or differs; dispatch "
            "remains disabled"
        ) from exc
    stage2_usage = lineage.get("stage2_usage")
    if any((
        lineage["authorization_package_hash"]
        != receipt["authorization_package_hash"],
        lineage["report_hash"] != receipt["report_hash"],
        lineage["journal_tail"] != receipt["journal_tail"],
        lineage["journal_event_count"] != receipt["journal_event_count"],
        not isinstance(stage2_usage, Mapping),
        (stage2_usage.get("attempt_count") if isinstance(stage2_usage, Mapping)
         else None)
        != receipt["dispatch_attempt_count"],
    )):
        raise Stage2Error(
            "the historical Stage 2 evidence differs from the consumed receipt"
        )
    try:
        report = json.loads(
            (run_path / "stage2_report.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Stage2Error("the historical Stage 2 report cannot be read") from exc
    if not isinstance(report, dict):
        raise Stage2Error("the historical Stage 2 report is malformed")
    return report
