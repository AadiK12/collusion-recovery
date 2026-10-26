"""Small deterministic artifact helpers; no credentials or provider imports."""
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor/salt_v3"
REPORTS = ROOT / "reports/induction-v3/execution"
RELEASE = ROOT / "release/induction-v3"
PIN = "5afb2c595aa3eff6b9b829a032f4f117b36ee54d"
MODEL = "gpt-5.6-luna"

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def status(stage, **fields):
    path = REPORTS / "STATUS.json"
    value = json.loads(path.read_text()) if path.exists() else {}
    value.update(status=stage, updated_at=utc_now(), **fields)
    atomic_json(path, value)
