"""Stable serialization and deterministic seed derivation."""

import hashlib
import json
from datetime import datetime, timezone


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def seed_for(namespace):
    return int(hashlib.sha256(namespace.encode("utf-8")).hexdigest(), 16)


def utc_now():
    return datetime.now(timezone.utc).isoformat()
