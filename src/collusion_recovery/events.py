"""Exclusive run directories, append-only events, content-addressed inputs."""

import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time

from .agents import AgentFailure, parse_verdict
from .common import canonical, digest, utc_now
from .config import Config
from .design import arm_manifest
from .visibility import ROOT


class RequestLimitError(RuntimeError):
    pass


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_metadata():
    def command(*args):
        try:
            result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                                    text=True, timeout=5, check=False)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    return {"commit": command("rev-parse", "HEAD"),
            "dirty": bool(command("status", "--porcelain"))}


def source_hashes():
    files = list((ROOT / "src").rglob("*.py")) + list((ROOT / "prompts").glob("*.txt"))
    files += [ROOT / "pyproject.toml", ROOT / "SPRINT_PROJECT_PLAN.md"]
    return {str(p.relative_to(ROOT)): file_hash(p) for p in sorted(files)}


def read_jsonl(path):
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class RunStore:
    def __init__(self, directory, config):
        Config(**config.to_dict())  # Gate before ANY filesystem mutation.
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=False)
        (self.path / "inputs").mkdir()
        self.config = config
        self.request_count = 0
        self.review_ids = set()
        self.manifest = {"run_id": self.path.name, "source": "mock", "provider": "scripted",
                         "warning": "SCRIPTED CONTROLS ONLY. NOT EMPIRICAL MODEL RESULTS.",
                         "status": "running", "started_at": utc_now(), "config": config.to_dict(),
                         "config_hash": digest(config.to_dict()), "arms": arm_manifest(),
                         "counts": config.counts(), "git": git_metadata(),
                         "python_version": platform.python_version(), "dependencies": [],
                         "protocol_hash": file_hash(ROOT / "SPRINT_PROJECT_PLAN.md"),
                         "source_hashes": source_hashes(), "paid_calls": 0, "cost_usd": 0}
        self.write_json("manifest.json", self.manifest)

    def write_json(self, name, value):
        # Called only with internal artifact names, never with model-supplied paths.
        path = self.path / name
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(canonical(value) + "\n", encoding="utf-8")
        temp.replace(path)

    def append(self, name, value):
        with (self.path / name).open("a", encoding="utf-8") as handle:
            handle.write(canonical(value) + "\n")

    def call(self, agent, request, meta, action, attempt=0):
        if self.request_count >= self.config.max_requests:
            raise RequestLimitError("Offline request limit reached; run stopped without paid calls")
        self.request_count += 1
        request_id = f"request-{self.request_count:06d}"
        input_hash = request.input_hash
        input_path = self.path / "inputs" / f"{input_hash}.json"
        if not input_path.exists():
            input_path.write_text(request.json_text + "\n", encoding="utf-8")
        elif digest(json.loads(input_path.read_text())) != input_hash:
            raise ValueError("Input hash collision or corrupted input artifact")
        started = time.monotonic()
        raw, status = None, "ok"
        try:
            raw = getattr(agent, action)(request)
        except AgentFailure as exc:
            status = exc.status
        if status == "ok":
            if action == "message":
                status = "invalid" if not isinstance(raw, str) else "overlength" if len(raw) > 200 else "ok"
            else:
                try:
                    parse_verdict(raw)
                except ValueError:
                    status = "invalid"
        event = {**meta, "request_id": request_id, "source": "mock", "action": action,
                 "attempt": attempt, "input_hash": input_hash, "raw_response": raw,
                 "status": status, "timestamp": utc_now(),
                 "elapsed_seconds": time.monotonic() - started,
                 "provider_request_id": None, "token_usage": None, "cost_usd": 0}
        self.append("requests.jsonl", event)
        return event

    def record_review(self, value):
        if value["review_id"] in self.review_ids:
            raise ValueError("Duplicate review")
        self.review_ids.add(value["review_id"])
        self.append("reviews.jsonl", value)

    def finish(self, status, error_type=None):
        self.manifest.update(status=status, ended_at=utc_now(), request_count=self.request_count,
                             observed_reviews=len(self.review_ids), error_type=error_type)
        names = ["scheduled_reviews.json", "schedule.json", "fixtures.jsonl", "requests.jsonl",
                 "reviews.jsonl", "checkpoints.jsonl", "branches.jsonl"]
        self.manifest["artifact_hashes"] = {n: file_hash(self.path / n) for n in names
                                             if (self.path / n).exists()}
        self.write_json("manifest.json", self.manifest)
