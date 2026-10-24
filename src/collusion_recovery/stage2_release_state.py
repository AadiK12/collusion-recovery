"""Tracked, fail-closed state for the completed one-shot Stage 2 release.

The empirical run directory is intentionally ignored by Git, so its presence
cannot be the only replay barrier.  This module binds the checked-in consumed
receipt to the exact completed authorization and makes that state available to
both the Stage 2 command and the generic paid-authorization validator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .common import canonical, digest
from .visibility import ROOT


STAGE2_RUN_ID = "empirical-pilot-stage2-v1"
STAGE2_AUTHORIZATION_ID = "stage2-release-2026-10-23-v1"
STAGE2_CONSUMED_RECEIPT = ROOT / "configs/stage2_consumed_release.json"
STAGE2_PACKAGE_HASH = (
    "14971740751c8285597692c7f3c954c4ab10146af00a0bbc86e389f6cf4dfdff"
)
STAGE2_REPORT_HASH = (
    "2c46fa0dccb7c7d51a06a7eb602c7243f342f1e66209cddc25abfb22e640a834"
)
STAGE2_JOURNAL_TAIL = (
    "2b2e4f521a3712ce9868b38673225186dba66e402f10bb6e71bedaef630a7bdd"
)
STAGE2_EVENT_COUNT = 47
STAGE2_DISPATCH_ATTEMPT_COUNT = 22


class Stage2ReleaseStateError(RuntimeError):
    """The durable consumed-release state is absent, malformed, or changed."""


def _expected_receipt_body() -> dict[str, Any]:
    return {
        "artifact_type": "stage_2_consumed_release_receipt_v1",
        "authorization_id": STAGE2_AUTHORIZATION_ID,
        "authorization_package_hash": STAGE2_PACKAGE_HASH,
        "consumed": True,
        "dispatch_attempt_count": STAGE2_DISPATCH_ATTEMPT_COUNT,
        "journal_event_count": STAGE2_EVENT_COUNT,
        "journal_tail": STAGE2_JOURNAL_TAIL,
        "report_hash": STAGE2_REPORT_HASH,
        "run_id": STAGE2_RUN_ID,
    }


def load_stage2_consumed_receipt(
        path: Path = STAGE2_CONSUMED_RECEIPT) -> dict[str, Any]:
    """Return the exact self-hashed receipt or fail closed.

    This reads no credentials and has no provider or network dependency.
    """
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise Stage2ReleaseStateError(
            "the tracked Stage 2 consumed-release receipt is unavailable"
        ) from exc
    if not isinstance(value, Mapping):
        raise Stage2ReleaseStateError(
            "the tracked Stage 2 consumed-release receipt is malformed"
        )
    body = dict(value)
    receipt_hash = body.pop("receipt_hash", None)
    expected = _expected_receipt_body()
    if (canonical(body) != canonical(expected)
            or not isinstance(receipt_hash, str)
            or receipt_hash != digest(body)):
        raise Stage2ReleaseStateError(
            "the tracked Stage 2 consumed-release receipt differs"
        )
    return {**body, "receipt_hash": receipt_hash}


def is_stage2_release(config: Any, artifact: Any = None) -> bool:
    """Identify any attempted reuse of the consumed Stage 2 authorization.

    The authorization identifier, rather than the run directory or a mutable
    replacement run id, is the durable capability that was consumed.  Match it
    on either side of validation so copying the tracked Stage 2 config and only
    changing ``run_id`` cannot turn the historical approval into a new release.
    A successor must use a fresh authorization id, a fresh explicitly versioned
    run identity, and its dedicated execution-controller marker.
    """
    config_authorization = getattr(
        getattr(config, "authorization", None), "authorization_id", None)
    artifact_authorization = getattr(artifact, "authorization_id", None)
    return (config_authorization == STAGE2_AUTHORIZATION_ID
            or artifact_authorization == STAGE2_AUTHORIZATION_ID)
