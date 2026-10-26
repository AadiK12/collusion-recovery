"""Unit checks always run; set INDUCTION_SANDBOX_IMAGE for Docker canary tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3

import pytest

from experiments.induction_v3.isolation import DockerSandbox, IsolationError, ToolResult


@pytest.fixture(scope="module")
def sandbox():
    image = os.environ.get("INDUCTION_SANDBOX_IMAGE")
    if not image:
        pytest.skip("set INDUCTION_SANDBOX_IMAGE to immutable locally built image ID")
    instance = DockerSandbox(image)
    instance.verify()
    return instance


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "task.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT, price REAL)")
        connection.executemany("INSERT INTO items VALUES (?, ?, ?)", [(1, "alpha", 2.5), (2, "beta", 8.0)])
    return path


def test_mutable_image_tag_rejected():
    with pytest.raises(IsolationError, match="immutable"):
        DockerSandbox("induction-v3-sandbox:20261026")


def test_client_environment_does_not_inherit_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_KEY", "synthetic-not-a-provider-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-not-a-provider-key")
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.example:2375")
    env = DockerSandbox._client_env("/tmp/isolated-client")
    assert set(env) == {"PATH", "HOME", "DOCKER_CONFIG", "LANG"}
    assert not any("synthetic" in item for item in env.values())


def test_task_execution_requires_verified_gate():
    instance = object.__new__(DockerSandbox)
    instance._verified = False
    with pytest.raises(IsolationError, match="verify"):
        instance.run_code_tests("", "")


def test_filename_injection_rejected_before_execution():
    instance = object.__new__(DockerSandbox)
    for name in ["/tmp/test_x.py", "../test_x.py", "test_x.py/../../escape", "test_x.py\x00"]:
        with pytest.raises(ValueError):
            instance.run_code_tests("", "", test_filename=name)


def test_fixture_requires_real_sqlite(database, tmp_path):
    with pytest.raises(ValueError):
        DockerSandbox._fixture(b"not a database")
    linked = tmp_path / "alias.sqlite"
    linked.symlink_to(database)
    with pytest.raises(ValueError):
        DockerSandbox._fixture(linked)


def test_pytest_failure_is_real_output_not_tool_failure():
    result = ToolResult("one test FAILED", "", 1)
    assert result.salt_code_result() == {"success": True, "toolchain_output": "one test FAILED"}
    assert ToolResult("", "", 137).salt_code_result()["success"] is False
    assert ToolResult("", "", 0, timed_out=True).salt_code_result()["success"] is False
    assert ToolResult("", "", 0, truncated=True).salt_code_result()["success"] is False


def test_verified_canaries(sandbox):
    assert sandbox.verification["status"] == "verified"
    assert all(sandbox.verification["canaries"].values())


def test_real_tests_target_code_and_failing_test_logs(sandbox):
    tests = '''import importlib.util, os
spec = importlib.util.spec_from_file_location("target", os.environ["TARGET_CODE_PATH"])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
def test_increment():
    assert module.increment(2) == 3
'''
    passed = sandbox.run_code_tests("def increment(x): return x + 1\n", tests)
    assert passed.returncode == 0
    assert "1 passed" in passed.salt_code_result()["toolchain_output"]
    failed = sandbox.run_code_tests("def increment(x): return x + 2\n", tests)
    assert failed.returncode == 1
    assert "1 failed" in failed.salt_code_result()["toolchain_output"]


def test_only_explicit_actor_test_helpers_are_transported(sandbox):
    result = sandbox.run_code_tests("", "from test_helper import value\ndef test_value(): assert value == 7\n",
                                    test_files={"test_helper.py": "value = 7\n"})
    assert result.returncode == 0, result.as_dict()
    with pytest.raises(ValueError):
        sandbox.run_code_tests("", "", test_files={"../../escape.py": "pass"})
    with pytest.raises(ValueError):
        sandbox.run_code_tests("", "", test_files={"target.py": "pass"})


def test_generated_tests_cannot_see_host_env_repo_socket_or_network(sandbox, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_KEY", "synthetic-host-only-secret")
    host_canary = tmp_path / "host-secret-canary"
    host_canary.write_text("synthetic-host-only-secret")
    tests = f'''import os, pathlib, socket, pytest
def test_isolation():
    assert "OPENAI_KEY" not in os.environ
    assert "synthetic-host-only-secret" not in pathlib.Path("/proc/self/environ").read_bytes().decode()
    assert not pathlib.Path({str(host_canary)!r}).exists()
    assert not pathlib.Path({str(Path(__file__).resolve().parents[2])!r}).exists()
    assert not pathlib.Path("/var/run/docker.sock").exists()
    assert os.getuid() == 65534
    with pytest.raises(OSError):
        pathlib.Path("/opt/write-test").write_text("fail")
    connection = socket.socket()
    connection.settimeout(0.2)
    with pytest.raises(OSError):
        connection.connect(("1.1.1.1", 443))
'''
    result = sandbox.run_code_tests("", tests)
    assert result.returncode == 0, result.as_dict()


def test_timeout_kills_container(sandbox):
    result = sandbox.run_code_tests("", "import time\ndef test_timeout(): time.sleep(60)\n", timeout_seconds=0.4)
    assert result.timed_out
    assert not result.salt_code_result()["success"]
    assert result.elapsed_seconds < 10


def test_output_is_bounded(sandbox):
    result = sandbox.run_code_tests("", "def test_overflow():\n    print('X' * 600000)\n    assert False\n")
    assert result.truncated
    assert len(result.stdout.encode()) <= 256000
    assert not result.salt_code_result()["success"]


def test_sql_actual_results_duplicates_and_limits(sandbox, database):
    result = sandbox.run_sql_query("SELECT label, label, price FROM items ORDER BY id", database, max_rows=1)
    assert result.payload == {"success": True, "columns": ["label", "label", "price"], "truncated": True,
                              "rows": [{"label": "alpha", "label__2": "alpha", "price": 2.5}]}


def test_sql_inspection_matches_source_schema(sandbox, database):
    result = sandbox.inspect_database(database).payload
    assert result["success"]
    assert result["tables"] == ["items"]
    assert result["table_counts"] == {"items": 2}
    assert result["value_domains"] == {"items": {"label": ["alpha", "beta"]}}
    assert result["sample_rows"]["items"] == [{"id": 1, "label": "alpha", "price": 2.5}]


@pytest.mark.parametrize("query", [
    "ATTACH DATABASE '/etc/passwd' AS stolen",
    "DELETE FROM items",
    "WITH x AS (SELECT 1) DELETE FROM items",
    "SELECT load_extension('/tmp/evil.so')",
    "SELECT readfile('/etc/passwd')",
    "SELECT * FROM pragma_database_list",
    "SELECT 1; DELETE FROM items",
])
def test_sql_rejects_mutation_host_access_extensions(sandbox, database, query):
    before = database.read_bytes()
    result = sandbox.run_sql_query(query, database)
    assert result.payload["success"] is False, result.as_dict()
    assert database.read_bytes() == before


def test_parallel_tasks_do_not_share_scratch(sandbox):
    def task(index):
        return sandbox.run_code_tests("", f'''from pathlib import Path
def test_private_scratch():
    p = Path("/work/my-private-state")
    assert not p.exists()
    p.write_text({str(index)!r})
    assert p.read_text() == {str(index)!r}
''')
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(task, range(3)))
    assert all(r.returncode == 0 for r in results), [r.as_dict() for r in results]
