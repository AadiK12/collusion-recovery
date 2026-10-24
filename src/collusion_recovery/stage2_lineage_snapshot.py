"""Git-attested portability snapshot of verified historical Stage 2 lineage.

This sanitized, self-hashed snapshot makes the checked Gate 1 proposal
inspectable on a fresh clone.  It is not a replacement for the ignored raw
Stage 2 run: proposal creation, authorization validation, and execution must
still reopen and cross-check the complete local Stage 2 evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .common import canonical, digest
from .gate2_readiness import STAGE2_RUN_DIR, verify_stage2_lineage
from .stage2_release_state import (
    STAGE2_DISPATCH_ATTEMPT_COUNT,
    STAGE2_EVENT_COUNT,
    STAGE2_JOURNAL_TAIL,
    STAGE2_PACKAGE_HASH,
    STAGE2_REPORT_HASH,
    STAGE2_RUN_ID,
    load_stage2_consumed_receipt,
)
from .visibility import ROOT


STAGE2_LINEAGE_SNAPSHOT_PATH = ROOT / "configs/stage2_historical_lineage.json"
EXPECTED_STAGE2_LINEAGE_SNAPSHOT_HASH = (
    "1e2c15b34f08e717a13ed7f4a5b4f88d7b9c8d0b9b6749903d0aae751d776656"
)
_FIELDS = {
    "artifact_type",
    "artifact_version",
    "attestation_scope",
    "raw_evidence_required_for_release",
    "run_id",
    "config_hash",
    "plan_hash",
    "semantic_plan_hash",
    "implementation_hash",
    "protocol_version",
    "protocol_hash",
    "authorization_package_hash",
    "report_hash",
    "journal_tail",
    "journal_event_count",
    "dispatch_attempt_count",
    "dispatch_authorization_hash",
    "transport_hash",
    "provider_identity",
    "verified_gates_hash",
    "calibration_identity_hash",
    "historical_config",
    "consumed_release_receipt_hash",
    "snapshot_hash",
}


class Stage2LineageSnapshotError(RuntimeError):
    pass


def build_stage2_lineage_snapshot(
        run_dir: Path = STAGE2_RUN_DIR) -> dict[str, Any]:
    """Build from complete ignored raw evidence; never from tracked anchors alone."""
    lineage = verify_stage2_lineage(Path(run_dir))
    consumed = load_stage2_consumed_receipt()
    body = {
        "artifact_type": "stage2_v1_0_historical_lineage_portability_snapshot",
        "artifact_version": 1,
        "attestation_scope": (
            "Git-attested sanitized snapshot cross-checked against complete local "
            "Stage 2 evidence at creation; not independent raw proof"
        ),
        "raw_evidence_required_for_release": True,
        "run_id": lineage["run_id"],
        "config_hash": lineage["config_hash"],
        "plan_hash": lineage["plan_hash"],
        "semantic_plan_hash": lineage["semantic_plan_hash"],
        "implementation_hash": lineage["implementation_hash"],
        "protocol_version": lineage["config"].protocol_version,
        "protocol_hash": lineage["plan"]["protocol_hash"],
        "authorization_package_hash": lineage["authorization_package_hash"],
        "report_hash": lineage["report_hash"],
        "journal_tail": lineage["journal_tail"],
        "journal_event_count": lineage["journal_event_count"],
        "dispatch_attempt_count": STAGE2_DISPATCH_ATTEMPT_COUNT,
        "dispatch_authorization_hash": lineage["dispatch_authorization_hash"],
        "transport_hash": lineage["transport_hash"],
        "provider_identity": lineage["provider_identity"],
        "verified_gates_hash": digest(lineage["gates"]),
        "calibration_identity_hash": digest([
            row["identity"] for row in lineage["plan"]["requests"]
            if row["stage"] == "calibration"
        ]),
        "historical_config": lineage["config"].to_dict(),
        "consumed_release_receipt_hash": consumed["receipt_hash"],
    }
    return {**body, "snapshot_hash": digest(body)}


def validate_stage2_lineage_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise Stage2LineageSnapshotError(
            f"Stage 2 lineage snapshot must contain exactly {sorted(_FIELDS)}"
        )
    snapshot = json.loads(canonical(dict(value)))
    body = dict(snapshot)
    snapshot_hash = body.pop("snapshot_hash", None)
    consumed = load_stage2_consumed_receipt()
    if (snapshot_hash != EXPECTED_STAGE2_LINEAGE_SNAPSHOT_HASH
            or snapshot_hash != digest(body)
            or snapshot.get("artifact_type")
            != "stage2_v1_0_historical_lineage_portability_snapshot"
            or snapshot.get("artifact_version") != 1
            or snapshot.get("raw_evidence_required_for_release") is not True
            or snapshot.get("run_id") != STAGE2_RUN_ID
            or snapshot.get("authorization_package_hash") != STAGE2_PACKAGE_HASH
            or snapshot.get("report_hash") != STAGE2_REPORT_HASH
            or snapshot.get("journal_tail") != STAGE2_JOURNAL_TAIL
            or snapshot.get("journal_event_count") != STAGE2_EVENT_COUNT
            or snapshot.get("dispatch_attempt_count")
            != STAGE2_DISPATCH_ATTEMPT_COUNT
            or snapshot.get("consumed_release_receipt_hash")
            != consumed["receipt_hash"]):
        raise Stage2LineageSnapshotError(
            "Stage 2 lineage snapshot self-hash or tracked anchors differ"
        )
    config = snapshot.get("historical_config")
    if (not isinstance(config, Mapping)
            or config.get("run_id") != STAGE2_RUN_ID
            or config.get("protocol_version") != "v1.0"):
        raise Stage2LineageSnapshotError(
            "Stage 2 lineage snapshot historical config is malformed"
        )
    return snapshot


def load_stage2_lineage_snapshot(
        path: Path = STAGE2_LINEAGE_SNAPSHOT_PATH) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Stage2LineageSnapshotError(
            "tracked Stage 2 lineage snapshot is unavailable or invalid"
        ) from exc
    return validate_stage2_lineage_snapshot(value)


def verify_stage2_lineage_snapshot_against_raw(
        snapshot: Mapping[str, Any],
        run_dir: Path = STAGE2_RUN_DIR) -> dict[str, Any]:
    expected = build_stage2_lineage_snapshot(Path(run_dir))
    actual = validate_stage2_lineage_snapshot(snapshot)
    if canonical(actual) != canonical(expected):
        raise Stage2LineageSnapshotError(
            "tracked Stage 2 snapshot differs from complete local raw evidence"
        )
    return actual
