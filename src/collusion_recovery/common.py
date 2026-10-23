"""Stable serialization and deterministic seed derivation."""

import hashlib
import json
import re
from datetime import datetime, timezone


_SECRET_VALUE_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"(?i)(?:^|[^A-Za-z0-9])sk-(?:proj-|svcacct-)?[A-Za-z0-9_.-]{3,}",
    r"(?i)(?:^|[^A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})",
    r"(?i)(?:^|[^A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{8,}",
    r"(?:^|[^A-Za-z0-9])AIza[0-9A-Za-z_-]{16,}",
    r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{8,}={0,2}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*\S{6,}",
))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def contains_secret_like_string(value):
    """Return true when nested JSON-like data contains a credential-shaped value."""
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)
    if isinstance(value, dict):
        return any(contains_secret_like_string(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_secret_like_string(child) for child in value)
    return False


def seed_for(namespace):
    return int(hashlib.sha256(namespace.encode("utf-8")).hexdigest(), 16)


def utc_now():
    return datetime.now(timezone.utc).isoformat()
