"""Fail-closed Docker isolation for SALT task code and SQLite fixtures.

There are no bind mounts. The only host data crossing the container boundary is
an explicit task payload on stdin. Docker sees a clean client environment and a
local Unix daemon socket; the container never receives that socket. An immutable
image ID and a successful canary check are mandatory before task execution.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any
import uuid

MAX_PAYLOAD_BYTES = 8_000_000
MAX_OUTPUT_BYTES = 256_000
MEMORY_BYTES = 256 * 1024 * 1024
PIDS_LIMIT = 64
TIMEOUT_SECONDS = 30.0
IMAGE_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
TEST_NAME_PATTERN = re.compile(r"test_[A-Za-z0-9_]+\.py\Z")


class IsolationError(RuntimeError):
    """A required isolation property could not be established."""


@dataclass(frozen=True)
class ToolResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False
    truncated: bool = False
    elapsed_seconds: float = 0.0
    payload: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def salt_code_result(self) -> dict[str, Any]:
        """Preserve SALT's distinction between test failures and tool failure."""
        if self.timed_out:
            return {"success": False, "error": "TestRunError: isolated test process exceeded 30-second bound"}
        if self.truncated:
            return {"success": False, "error": "TestRunError: isolated test output exceeded capture limit"}
        # pytest 0-5 are genuine toolchain outcomes, including failed tests.
        if self.returncode not in range(6):
            return {"success": False, "error": f"TestRunError: isolated process terminated (exit {self.returncode})"}
        return {"success": True, "toolchain_output": self.stdout + self.stderr}


class DockerSandbox:
    def __init__(self, image: str, *, docker_binary: str = "/usr/local/bin/docker"):
        if not IMAGE_ID_PATTERN.fullmatch(image):
            raise IsolationError("sandbox image must be an immutable sha256 image ID")
        binary = Path(docker_binary)
        if not binary.is_absolute() or not binary.is_file():
            raise IsolationError("an absolute installed Docker CLI path is required")
        sockets = [Path.home() / ".docker/run/docker.sock", Path("/var/run/docker.sock")]
        socket_path = next((path for path in sockets if path.is_socket()), None)
        if socket_path is None:
            raise IsolationError("local Docker daemon socket unavailable")
        self.image = image
        self.docker_binary = str(binary)
        self.docker_host = "unix://" + str(socket_path)
        self._verified = False
        self.verification: dict[str, Any] | None = None

    @staticmethod
    def _client_env(client_home: str) -> dict[str, str]:
        return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": client_home,
                "DOCKER_CONFIG": client_home, "LANG": "C.UTF-8"}

    def _command(self, *args: str) -> list[str]:
        return [self.docker_binary, "--host", self.docker_host, *args]

    def _docker(self, args: list[str], client_home: str, *, timeout: float = 15) -> str:
        result = subprocess.run(self._command(*args), env=self._client_env(client_home),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
        if result.returncode:
            # Docker diagnostic text contains no task payload or inherited secrets.
            raise IsolationError("Docker operation failed: " + result.stderr.decode("utf-8", "replace")[:1500])
        return result.stdout.decode("utf-8", "replace")

    def _create_args(self, name: str) -> list[str]:
        return ["create", "--name", name, "--network=none", "--ipc=none", "--read-only",
                "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges=true",
                "--pids-limit=64", "--memory=256m", "--memory-swap=256m", "--cpus=1",
                "--ulimit=nofile=128:128", "--ulimit=core=0:0", "--hostname=induction-task",
                "--workdir=/work", "--stop-timeout=1", "--interactive",
                "--tmpfs=/work:rw,noexec,nosuid,nodev,size=67108864,mode=700,uid=65534,gid=65534",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=700,uid=65534,gid=65534",
                "--env=HOME=/work", "--env=LANG=C.UTF-8", "--env=PYTHONDONTWRITEBYTECODE=1",
                "--env=PYTHONHASHSEED=0", self.image]

    def _validate_container(self, data: dict[str, Any]) -> None:
        host, config = data["HostConfig"], data["Config"]
        checks = {
            "immutable_image": data["Image"] == self.image,
            "no_host_mounts": not data.get("Mounts") and not host.get("Binds") and not host.get("Mounts"),
            "network_none": host.get("NetworkMode") == "none",
            "private_ipc": host.get("IpcMode") == "none",
            "readonly_root": host.get("ReadonlyRootfs") is True,
            "non_root": config.get("User") == "65534:65534",
            "cap_drop": set(host.get("CapDrop") or []) == {"ALL"},
            "no_cap_add": not host.get("CapAdd"),
            "no_new_privileges": "no-new-privileges=true" in (host.get("SecurityOpt") or []),
            "not_privileged": host.get("Privileged") is False,
            "private_pid": not host.get("PidMode"),
            "no_devices": not host.get("Devices") and not host.get("DeviceRequests"),
            "bounded_pids": host.get("PidsLimit") == PIDS_LIMIT,
            "bounded_memory": host.get("Memory") == MEMORY_BYTES and host.get("MemorySwap") == MEMORY_BYTES,
            "bounded_cpu": host.get("NanoCpus") == 1_000_000_000,
            "only_task_tmpfs": set(host.get("Tmpfs") or {}) == {"/work", "/tmp"},
            "fixed_entrypoint": config.get("Entrypoint") == ["/usr/local/bin/python", "-I", "/opt/induction/runner.py"],
        }
        failed = [key for key, value in checks.items() if not value]
        if failed:
            raise IsolationError("container isolation audit failed: " + ", ".join(failed))

    def _execute(self, payload: dict[str, Any], *, timeout_seconds: float, verifying: bool = False) -> ToolResult:
        if not verifying and not self._verified:
            raise IsolationError("sandbox.verify() must pass before task execution")
        if not (0 < timeout_seconds <= TIMEOUT_SECONDS):
            raise ValueError("task timeout must be greater than 0 and at most 30 seconds")
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("task payload exceeds 8 MB limit")
        name = "induction-v3-" + uuid.uuid4().hex
        started = time.monotonic()
        stdout_parts: list[bytes] = []
        stderr_parts: list[bytes] = []
        overflow = threading.Event()
        timed_out = False
        created = False
        with tempfile.TemporaryDirectory(prefix="induction-docker-client-") as client_home:
            try:
                self._docker(self._create_args(name), client_home)
                created = True
                description = json.loads(self._docker(["inspect", name], client_home))[0]
                self._validate_container(description)
                process = subprocess.Popen(self._command("start", "--attach", "--interactive", name),
                                           env=self._client_env(client_home), stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                def read_stream(stream, collected):
                    total = 0
                    while True:
                        chunk = stream.read(8192)
                        if not chunk:
                            break
                        allowed = MAX_OUTPUT_BYTES - total
                        if allowed > 0:
                            collected.append(chunk[:allowed])
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            overflow.set()
                    stream.close()
                readers = [threading.Thread(target=read_stream, args=(process.stdout, stdout_parts), daemon=True),
                           threading.Thread(target=read_stream, args=(process.stderr, stderr_parts), daemon=True)]
                for reader in readers:
                    reader.start()
                # The trusted runner consumes the complete bounded payload before code starts.
                try:
                    process.stdin.write(encoded)
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                deadline = time.monotonic() + timeout_seconds
                while process.poll() is None:
                    if overflow.is_set() or time.monotonic() >= deadline:
                        timed_out = not overflow.is_set()
                        self._docker(["rm", "--force", name], client_home)
                        created = False
                        process.wait(timeout=5)
                        break
                    time.sleep(0.02)
                for reader in readers:
                    reader.join(timeout=5)
                if any(reader.is_alive() for reader in readers):
                    raise IsolationError("Docker output collection failed to terminate")
                stdout = b"".join(stdout_parts).decode("utf-8", "replace")
                stderr = b"".join(stderr_parts).decode("utf-8", "replace")
                parsed = None
                if payload["mode"] != "code":
                    if timed_out or overflow.is_set():
                        parsed = {"success": False, "error": "QueryError: isolated process timeout or output limit"}
                    else:
                        try:
                            parsed = json.loads(stdout)
                        except (json.JSONDecodeError, ValueError):
                            parsed = {"success": False, "error": "TaskRunError: invalid isolated tool output"}
                return ToolResult(stdout, stderr, process.returncode, timed_out, overflow.is_set(),
                                  time.monotonic() - started, parsed)
            finally:
                if created:
                    self._docker(["rm", "--force", name], client_home)

    def verify(self) -> dict[str, Any]:
        """Run trusted security canaries with the exact per-task container policy."""
        self._verified = False
        with tempfile.TemporaryDirectory(prefix="induction-host-canary-") as host_dir:
            canary_path = Path(host_dir) / "host-only-canary.txt"
            canary_path.write_text(uuid.uuid4().hex)
            result = self._execute({"mode": "canary", "forbidden_paths": [
                str(canary_path), str(Path(__file__).resolve().parents[2]),
                str(Path.home() / ".env.local"), str(Path.home() / ".docker"),
            ]}, timeout_seconds=10, verifying=True)
        if result.returncode or result.timed_out or result.truncated or not (result.payload or {}).get("success"):
            raise IsolationError("sandbox canaries failed: " + json.dumps(result.as_dict(), sort_keys=True))
        self.verification = {"status": "verified", "image_id": self.image,
                             "canaries": result.payload, "no_bind_mounts": True,
                             "task_timeout_seconds": TIMEOUT_SECONDS,
                             "output_limit_bytes_per_stream": MAX_OUTPUT_BYTES}
        self._verified = True
        return dict(self.verification)

    def run_code_tests(self, code: str, tests: str, *, test_filename: str = "test_generated.py",
                       test_files: dict[str, str] | None = None,
                       timeout_seconds: float = TIMEOUT_SECONDS) -> ToolResult:
        if not TEST_NAME_PATTERN.fullmatch(test_filename):
            raise ValueError("test filename must match test_[A-Za-z0-9_]+.py")
        if not isinstance(code, str) or not isinstance(tests, str):
            raise TypeError("code and tests must be strings")
        extra = dict(test_files or {})
        if any(not TEST_NAME_PATTERN.fullmatch(name) or not isinstance(content, str)
               for name, content in extra.items()):
            raise ValueError("supporting test files must be task-local test_*.py strings")
        extra[test_filename] = tests
        return self._execute({"mode": "code", "code": code, "tests": tests,
                              "test_filename": test_filename, "test_files": extra},
                             timeout_seconds=timeout_seconds)

    @staticmethod
    def _fixture(fixture: bytes | Path) -> str:
        if isinstance(fixture, Path):
            if fixture.is_symlink() or fixture.stat().st_size > 4_000_000:
                raise ValueError("invalid or oversized task fixture")
            data = fixture.read_bytes()
        elif isinstance(fixture, bytes):
            data = fixture
        else:
            raise TypeError("fixture must be explicit bytes or a trusted task Path")
        if len(data) > 4_000_000 or not data.startswith(b"SQLite format 3\x00"):
            raise ValueError("fixture must be a bounded SQLite database")
        return base64.b64encode(data).decode("ascii")

    def run_sql_query(self, query: str, fixture: bytes | Path, *, max_rows: int = 100,
                      timeout_seconds: float = TIMEOUT_SECONDS) -> ToolResult:
        return self._execute({"mode": "sql", "sql": query, "fixture": self._fixture(fixture),
                              "max_rows": max(1, min(int(max_rows or 100), 500))},
                             timeout_seconds=timeout_seconds)

    def inspect_database(self, fixture: bytes | Path, *, timeout_seconds: float = TIMEOUT_SECONDS) -> ToolResult:
        return self._execute({"mode": "inspect_database", "fixture": self._fixture(fixture)},
                             timeout_seconds=timeout_seconds)
