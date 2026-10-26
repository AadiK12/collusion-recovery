"""Trusted entrypoint; receives task-only JSON, never mounts a host directory."""
import base64
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import sys

ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/work",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    "TARGET_CODE_PATH": "/work/target.py",
}
os.environ.clear()
os.environ.update(ENV)


def safe_value(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return f"<binary {len(value)} bytes>"
    return value


def rows(cursor, fetched):
    columns = [item[0] for item in cursor.description or []]
    output = []
    for row in fetched:
        record = {}
        for name, value in zip(columns, row):
            key, suffix = name, 2
            while key in record:
                key = f"{name}__{suffix}"
                suffix += 1
            record[key] = safe_value(value)
        output.append(record)
    return output


def quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def leading_keyword(sql):
    remaining = sql.lstrip()
    while True:
        if remaining.startswith("--"):
            _, newline, remaining = remaining.partition("\n")
            if not newline:
                return ""
        elif remaining.startswith("/*"):
            _, close, remaining = remaining.partition("*/")
            if not close:
                return ""
        else:
            break
        remaining = remaining.lstrip()
    match = re.match(r"[A-Za-z]+", remaining)
    return match.group(0).lower() if match else ""


def readonly_authorizer(action, first, second, db, trigger):
    if action == sqlite3.SQLITE_FUNCTION and str(second).lower() in {
        "load_extension", "readfile", "writefile",
    }:
        return sqlite3.SQLITE_DENY
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    if hasattr(sqlite3, "SQLITE_RECURSIVE"):
        allowed.add(sqlite3.SQLITE_RECURSIVE)
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def database(payload):
    Path("/work/task.sqlite").write_bytes(base64.b64decode(payload["fixture"], validate=True))
    connection = sqlite3.connect("file:/work/task.sqlite?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.enable_load_extension(False)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2_000_000)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100_000)
    if payload["mode"] == "sql":
        sql = payload["sql"].strip()
        if leading_keyword(sql) not in {"select", "with"}:
            raise PermissionError("query_database only allows SELECT or WITH queries")
        connection.set_authorizer(readonly_authorizer)
        cursor = connection.execute(sql)
        limit = max(1, min(int(payload.get("max_rows") or 100), 500))
        fetched = cursor.fetchmany(limit + 1)
        result = {"success": True, "columns": [x[0] for x in cursor.description or []],
                  "truncated": len(fetched) > limit, "rows": rows(cursor, fetched[:limit])}
    else:
        names = [str(row["name"]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        schema, counts, samples, foreign_keys, domains = {}, {}, {}, {}, {}
        for name in names:
            quoted = quote_identifier(name)
            columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            schema[name] = [{"name": str(c["name"]), "type": str(c["type"]),
                             "notnull": bool(c["notnull"]), "primary_key": bool(c["pk"])}
                            for c in columns]
            counts[name] = int(connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])
            foreign_keys[name] = [{"from": str(r["from"]), "to_table": str(r["table"]), "to": str(r["to"])}
                                  for r in connection.execute(f"PRAGMA foreign_key_list({quoted})")]
            cursor = connection.execute(f"SELECT * FROM {quoted} LIMIT 1")
            samples[name] = rows(cursor, cursor.fetchall())
            values = {}
            for c in columns:
                if "TEXT" not in str(c["type"]).upper():
                    continue
                column = str(c["name"])
                distinct = connection.execute(f"SELECT DISTINCT {quote_identifier(column)} FROM {quoted} ORDER BY 1 LIMIT 13").fetchall()
                if 1 < len(distinct) <= 12:
                    values[column] = [safe_value(r[0]) for r in distinct]
            if values:
                domains[name] = values
        result = {"success": True, "tables": names, "schema": schema, "foreign_keys": foreign_keys,
                  "table_counts": counts, "value_domains": domains, "sample_rows": samples}
    connection.close()
    return result


def canary(payload):
    results = {
        "non_root": os.getuid() == os.geteuid() == 65534,
        "allowlisted_environment": dict(os.environ) == ENV,
        "host_paths_absent": all(not Path(path).exists() for path in payload["forbidden_paths"]),
        "docker_socket_absent": not Path("/var/run/docker.sock").exists(),
        "no_active_nonloopback_interface": all(
            name == "lo" or not (int(Path(f"/sys/class/net/{name}/flags").read_text().strip(), 16) & 1)
            for _, name in socket.if_nameindex()
        ),
        "no_ipv4_routes": len(Path("/proc/net/route").read_text().splitlines()) == 1,
    }
    status = Path("/proc/self/status").read_text()
    results["no_effective_capabilities"] = bool(re.search(r"^CapEff:\s+0+$", status, re.M))
    results["no_new_privileges"] = bool(re.search(r"^NoNewPrivs:\s+1$", status, re.M))
    try:
        Path("/opt/isolation-write-canary").write_text("must fail")
        results["readonly_root"] = False
    except OSError:
        results["readonly_root"] = True
    for name, address in [("external_network_blocked", ("1.1.1.1", 443)),
                          ("docker_gateway_blocked", ("192.168.65.1", 80))]:
        sock = socket.socket()
        sock.settimeout(0.5)
        try:
            sock.connect(address)
            results[name] = False
        except OSError:
            results[name] = True
        finally:
            sock.close()
    Path("/work/fixture-canary").write_text("task-write-ok")
    results["task_scratch_writable"] = Path("/work/fixture-canary").read_text() == "task-write-ok"
    results["success"] = all(results.values())
    return results


def main():
    payload = json.load(sys.stdin)
    if payload["mode"] == "canary":
        print(json.dumps(canary(payload), sort_keys=True))
    elif payload["mode"] == "code":
        filename = payload["test_filename"]
        if not re.fullmatch(r"test_[A-Za-z0-9_]+\.py", filename):
            raise ValueError("invalid task test filename")
        Path("/work/target.py").write_text(payload["code"], encoding="utf-8")
        for name, content in payload.get("test_files", {}).items():
            if not re.fullmatch(r"test_[A-Za-z0-9_]+\.py", name) or not isinstance(content, str):
                raise ValueError("invalid supporting task test file")
            Path("/work", name).write_text(content, encoding="utf-8")
        Path("/work", filename).write_text(payload["tests"], encoding="utf-8")
        command = [sys.executable, "-m", "pytest", f"/work/{filename}",
                   "-vv", "--no-header", "-p", "no:cacheprovider"]
        os.execve(sys.executable, command, ENV)
    elif payload["mode"] in {"sql", "inspect_database"}:
        try:
            result = database(payload)
        except Exception as exc:
            result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, ensure_ascii=False))
    else:
        raise ValueError("unsupported task mode")


if __name__ == "__main__":
    main()
