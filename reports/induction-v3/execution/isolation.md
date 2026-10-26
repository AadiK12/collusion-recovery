# Verified task isolation

Status: ready for the execution owner's integration. No paid calls were made by
the isolation worker. Docker Desktop was installed but stopped; launching the
installed app made its local Unix daemon available. No privileged host changes,
existing virtual-environment changes, or repository bind mounts were used.

## Frozen runtime

- Image ID: `sha256:66c348ebea2f48c244fa4727b8a2cc918dafeeb7d6b0b1c0a68f943c500de5b2`.
- Base: Python 3.11.17, pinned image digest in `release/induction-v3/sandbox/Dockerfile`.
- pytest: `9.1.1`, exactly the pinned source requirement.
- Image build context is exclusively `release/induction-v3/sandbox`; its
  `.dockerignore` allows only the Dockerfile and trusted runner.
- Machine evidence and file hashes: `isolation-status.json` in this directory.

## Callable interface

```python
from experiments.induction_v3.isolation import DockerSandbox

sandbox = DockerSandbox(
    "sha256:66c348ebea2f48c244fa4727b8a2cc918dafeeb7d6b0b1c0a68f943c500de5b2"
)
sandbox.verify()  # Required before any generated task code runs.

code_result = sandbox.run_code_tests(
    code, tests, test_filename="test_generated.py",
    test_files={"test_helper.py": earlier_same_actor_test_contents},
    timeout_seconds=30,
)
salt_result = code_result.salt_code_result()
sql_result = sandbox.run_sql_query(sql, fixture_bytes_or_trusted_path, max_rows=100).payload
schema_result = sandbox.inspect_database(fixture_bytes_or_trusted_path).payload
```

`ToolResult` retains actual stdout, stderr, exit code, timeout, truncation, and
elapsed time. pytest test failure remains `success: true` with actual failure
output, as in source `experiments/protocol/code_tests.py`; process failure,
timeout, or excess output returns a tool error. SQL results retain the source
columns, duplicate-column naming, row limit/truncation, byte conversion,
inspection schema, sample rows, foreign keys, and small categorical domains.
The adapter must preserve the upstream filename/path and nonvacuity checks.
Only the selected actor's explicitly written helper files may be transported.

## Enforcement and evidence

Each invocation creates and inspects a fresh container before starting it.
It uses no bind mounts or Docker socket, no network, a read-only runtime,
UID/GID 65534, all capabilities dropped, no-new-privileges, a private PID
namespace, no shared IPC, 64 PIDs, 256 MiB memory with no additional swap,
one CPU, 128 open files, and disabled core dumps. Writable task scratch is
limited to 64 MiB `/work` and 16 MiB `/tmp` tmpfs mounts with noexec/nosuid/nodev.
Task timeout is at most 30 seconds. Captured stdout/stderr each stop at 256,000
bytes; overflow or timeout destroys the container. Client subprocesses receive
an explicit four-variable environment, never the dispatcher's environment.
The trusted entrypoint replaces its environment with a fixed task allowlist.

Canaries verified absent host/repository paths, absent Docker socket, absence of
host secret variables, denied runtime-root writes, UID/capability restrictions,
no active nonloopback network interfaces, no IPv4 routes, failed external and
Docker-gateway connections, and usable isolated task scratch. Docker's empty
network namespace has inactive kernel tunnel interfaces, so the check correctly
tests that none is active rather than requiring the interface list to contain
only `lo`.

22 tests passed in 6.41 seconds, including actual correct/failing pytest output,
hostile test code, timeout cleanup, bounded output, SQL mutation/extension/file
access denial, same-actor helper import, and three concurrent isolated tasks.
No sandbox containers remained after testing.

Reproduce with:

```sh
INDUCTION_SANDBOX_IMAGE=sha256:66c348ebea2f48c244fa4727b8a2cc918dafeeb7d6b0b1c0a68f943c500de5b2 .venv-induction-v3/bin/python -m pytest tests/induction_v3/test_isolation.py -q
```

## Declared execution differences from pinned SALT

The upstream test runner uses repository-root host execution, which is forbidden
for this campaign. The replacement runs pytest in `/work` with only the target
code and explicit same-actor generated tests; `TARGET_CODE_PATH` is
`/work/target.py`. Other repository files and agents' files are inaccessible.
Third-party pytest plugin auto-loading is disabled. Generated filenames are
restricted to `test_[A-Za-z0-9_]+.py`. Host/path-specific transcript details and
Python runtime identity may therefore differ. The 30-second upstream process
limit is retained; memory/PID/CPU/output limits are new security bounds.

SQL is copied into a fresh task-only container and opened read-only. In addition
to upstream SELECT/WITH and query-only restrictions, a SQLite authorizer denies
ATTACH, mutations, PRAGMAs in agent queries, extension loading, and file functions;
trusted-schema is disabled. These are declared safety differences, not silent
changes based on observed experimental outcomes. Canaries establish the tested
container boundary; they are not a claim that Docker has no kernel/runtime bugs.
