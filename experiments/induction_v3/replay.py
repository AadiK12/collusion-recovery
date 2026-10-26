"""Offline, fail-closed replay of interrupted V3 episodes; never dispatches.

Historical provider responses are consumed by reservation identity, not body hash.
Only nondeterministic test/SQL payloads are substituted; pinned protocol handlers
still construct raw logs, messages, verdicts, and phase transitions. File writes
are confined to a new temporary scratch directory. No participant code runs.
"""
import argparse
from contextlib import ExitStack, contextmanager
import copy
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
from unittest.mock import patch

from .common import ROOT, VENDOR, MODEL, atomic_json, digest, file_hash
from .schedule import policy_for
from .transport import make_body, decode_response, usage_from_response, encode_input
from .upstream import load


class ReplayMismatch(RuntimeError):
    pass


class ReplayBoundary(RuntimeError):
    """A verified failed request or the first request never physically dispatched."""


def _require(condition, message):
    if not condition:
        raise ReplayMismatch(message)


class ReplayTape:
    def __init__(self, run_dir, dyad, episode_number):
        self.run_dir = Path(run_dir)
        self.dyad = dyad
        self.index = 0
        self.pending = []
        self.current_tool = None
        self.proof = []
        self.boundary = None
        self.inputs = {}
        self.rows = []
        self.tool_outputs = {}
        self.saved_output_items = {}
        for actor, history in dyad["incomplete_histories"].items():
            for message in history:
                if message.get("role") == "tool":
                    call_id = message["tool_call_id"]
                    _require(call_id not in self.tool_outputs, "duplicate historical tool-call ID")
                    self.tool_outputs[call_id] = (actor, message["content"])
                for item in message.get("provider_output_items", []):
                    self.saved_output_items[item["id"]] = item
        journal = self.read("journal.jsonl", lines=True)
        settlements = {e["reservation_id"]: e for e in journal if e["kind"] == "settlement"}
        for event in journal:
            metadata = event.get("metadata", {})
            if (event["kind"] != "reservation" or metadata.get("dyad_id") != dyad["dyad_id"]
                    or metadata.get("episode") != episode_number):
                continue
            row = copy.deepcopy(event["reservation"])
            identity = json.loads(row["identity"])
            _require(identity == {**metadata, "physical_call_index": len(self.rows)}, "noncontiguous replay identity")
            row["request"] = self.read("requests/" + row["request_hash"] + ".json")
            _require(digest(row["request"]) == row["request_hash"], "archived request digest mismatch")
            row["response"] = self.read("responses/" + row["reservation_id"] + ".json")
            row["settlement"] = settlements.get(row["reservation_id"])
            _require(row["settlement"] is not None, "unsettled historical request")
            self.rows.append(row)
        _require(bool(self.rows), "no interrupted episode calls")

    def read(self, relative, lines=False):
        path = self.run_dir / relative
        _require(path.is_file() and not path.is_symlink(), "missing/nonregular replay input: " + relative)
        self.inputs[str(path.relative_to(ROOT))] = file_hash(path)
        text = path.read_text()
        return [json.loads(line) for line in text.splitlines()] if lines else json.loads(text)

    def complete(self, *, model, messages, completion_kwargs, temperature,
                 max_output_tokens, accept_tool_calls, usage_recorder=None):
        _require(not self.pending, "unconsumed tool calls before next provider response")
        body = make_body(model=model, messages=messages, completion_kwargs=completion_kwargs,
                         temperature=temperature, max_output_tokens=max_output_tokens)
        request_hash = digest(body)
        if self.index == len(self.rows):
            self.boundary = {"kind": "never_dispatched", "physical_call_index": self.index,
                             "request_hash": request_hash, "request": body}
            raise ReplayBoundary("first uncached request")
        row = self.rows[self.index]
        _require(request_hash == row["request_hash"] and body == row["request"],
                 "reconstructed request differs at index " + str(self.index))
        response = row["response"]
        if "body" not in response:
            _require(response.get("http_status") == 429 and response.get("error", {}).get("code") == "rate_limit_exceeded",
                     "unsupported failed-response boundary")
            _require(self.index == len(self.rows) - 1, "failure was not last archived request")
            self.boundary = {"kind": "verified_http429", "physical_call_index": self.index,
                             "reservation_id": row["reservation_id"], "request_hash": request_hash,
                             "request": body}
            raise ReplayBoundary("exact failed request boundary")
        raw = response["body"]
        _require(response.get("request_hash") == request_hash, "response bound to another request")
        _require(row["settlement"].get("outcome") == "completed", "cached response was not settled completed")
        _require(raw.get("status") == "completed" and (raw.get("model") == MODEL or str(raw.get("model", "")).startswith(MODEL + "-")),
                 "cached response model/status mismatch")
        message = decode_response(raw)
        _require(accept_tool_calls or not message["tool_calls"], "reflection contained unexecuted tool calls")
        _require(bool(message["content"] or message["tool_calls"]), "empty cached response")
        recorded_items = [item.get("id") in self.saved_output_items for item in raw["output"]]
        _require(all(recorded_items) or not any(recorded_items), "partially ingested provider response")
        if all(recorded_items):
            _require(all(self.saved_output_items[item["id"]] == item for item in raw["output"]), "opaque/provider output changed")
        self.pending = [{"call": call, "cached": self.tool_outputs.get(call["id"])} for call in message["tool_calls"]]
        for tool in self.pending:
            if not all(recorded_items):
                _require(tool["cached"] is None and tool["call"]["function"]["name"] in {"save_final_answer", "submit_verdict"},
                         "late output needs an unavailable nondeterministic tool result")
            else:
                _require(tool["cached"] is not None, "ingested response missing historical tool output")
        self.proof.append({"physical_call_index": self.index, "reservation_id": row["reservation_id"],
                           "request_hash": request_hash, "provider_output_digest": digest(raw["output"]),
                           "previously_ingested": all(recorded_items), "tool_calls": len(self.pending)})
        self.index += 1
        if usage_recorder:
            usage_recorder({**(usage_from_response(raw) or {}), "requested_model": MODEL,
                            "response_model": raw.get("model"), "api_attempt": 1, "response_status": "accepted",
                            "finish_reason": "stop", "provider_usage": raw.get("usage"),
                            "latency_seconds": response["latency_seconds"], "reservation_id": row["reservation_id"]})
        return raw, message, message["content"], message["tool_calls"] if accept_tool_calls else []

    def cached_payload(self):
        _require(self.current_tool is not None and self.current_tool["cached"] is not None,
                 "external tool has no exact recorded payload")
        return json.loads(self.current_tool["cached"][1])


class ReplayTransport:
    """Thread-local replay gate around an independently accounted live transport.

Install hooks after engine.install_adapters. The delegate owns all new physical
attempts, pacing and accounting. Cached calls never touch its journal. No replay
scope can become live until its previously audited offline boundary matches.
"""
    def __init__(self, delegate, *, resume_run_id):
        _require(isinstance(resume_run_id, str) and bool(resume_run_id), "missing distinct resume run identity")
        self.delegate = delegate
        self.resume_run_id = resume_run_id
        self.local = threading.local()

    def context(self, **metadata):
        _require("resume_run_id" not in metadata or metadata["resume_run_id"] == self.resume_run_id,
                 "resume identity override")
        self.local.metadata = {**metadata, "resume_run_id": self.resume_run_id}
        self.delegate.context(**self.local.metadata)

    @property
    def historical(self):
        return getattr(self.local, "tape", None) is not None and not getattr(self.local, "live", False)

    @contextmanager
    def episode_replay(self, run_dir, dyad, proof, *, histories):
        from .accounting import verify_run
        _require(getattr(self.local, "tape", None) is None, "nested replay context")
        _require(proof.get("status") == "passed" and proof["checkpoint_digest"] == digest(proof["checkpoint"]),
                 "invalid offline proof checkpoint")
        run_dir = Path(run_dir).resolve()
        check = verify_run(run_dir, source_root=ROOT)
        _require(check["stopped"] and not check["requires_restart_stop"], "source journal not at settled terminal")
        _require(proof["source_run"] == str(run_dir.relative_to(ROOT)) and proof["dyad_id"] == dyad["dyad_id"],
                 "proof belongs to another source trajectory")
        for name, sha in proof["input_inventory"].items():
            _require(file_hash(ROOT / name) == sha, "offline proof input drift: " + name)
        source_dyad = json.loads((run_dir / "dyads" / (dyad["dyad_id"] + ".json")).read_text())
        _require(dyad == source_dyad, "caller changed source dyad")
        _require(proof["episode"] == len(dyad["episodes"]) + 1, "proof episode differs")
        expected_initial = {a: dyad["episodes"][-1]["agents"][a]["messages"] for a in ("alice", "bob")}
        _require(histories == expected_initial, "replay must begin at exact last completed snapshots")
        metadata = getattr(self.local, "metadata", {})
        _require(metadata.get("dyad_id") == dyad["dyad_id"] and metadata.get("episode") == proof["episode"]
                 and metadata.get("recipe_id") == dyad["recipe_id"] and metadata.get("campaign_phase") == proof["campaign_phase"],
                 "new transport context is not the proven logical episode")
        self.local.tape = ReplayTape(run_dir, dyad, proof["episode"])
        self.local.proof = proof
        self.local.histories = histories
        self.local.live = False
        self.local.state = None
        self.local.live_logical_index = None
        try:
            yield self.local.tape
            _require(self.local.live and not self.local.tape.pending, "replay ended before validated live boundary")
        finally:
            self.local.tape = None
            self.local.proof = None
            self.local.histories = None
            self.local.state = None
            self.local.live = False

    def _validate_boundary(self):
        tape, proof = self.local.tape, self.local.proof
        boundary = {k: v for k, v in tape.boundary.items() if k != "request"}
        _require(boundary == proof["boundary"] and tape.proof == proof["consumed_responses"],
                 "live boundary differs from offline proof")
        _require(not tape.pending and self.local.state is not None, "incomplete replay state at live boundary")
        current = copy.deepcopy(self.local.state)
        expected = proof["checkpoint"]["state"]
        mapping = {}
        for actor in ("alice", "bob"):
            path = current["agents"][actor].get("task_tmp_dir")
            if path:
                original = expected["agents"][actor]["task_tmp_dir"]
                mapping[path] = original
                mapping[str(Path(path).resolve())] = original

        def normalize(value):
            if isinstance(value, dict): return {k: normalize(v) for k, v in value.items()}
            if isinstance(value, list): return [normalize(v) for v in value]
            if isinstance(value, str):
                for path, original in mapping.items():
                    if value == path or value.startswith(path + "/"): return original + value[len(path):]
            return value

        _require(normalize(current) == expected, "reconstructed protocol state differs from offline proof")
        _require(self.local.histories == proof["checkpoint"]["histories"], "reconstructed histories differ from offline proof")
        _require(tape.boundary["request"] == proof["checkpoint"]["pending_request"], "pending request differs from proof")
        # No historical identity is reused. The delegate starts a new physical
        # sequence carrying resume_run_id plus explicit source logical index.
        self.local.live_logical_index = boundary["physical_call_index"]
        self.delegate.context(**self.local.metadata,
                              source_boundary_request_hash=boundary["request_hash"],
                              source_logical_call_index=boundary["physical_call_index"],
                              source_failed_reservation_id=boundary.get("reservation_id"))
        self.local.live = True

    def complete(self, **kwargs):
        if self.historical:
            try:
                return self.local.tape.complete(**kwargs)
            except ReplayBoundary:
                self._validate_boundary()
        return self.delegate.complete(**kwargs)


def install_replay_hooks(wrapper):
    """Install once after engine adapters; return an uninstall callback.

All historical decisions are thread-local. Future calls use the existing Docker
helpers unchanged. Intended for one shared wrapper across concurrent dyads.
"""
    episode, agents, _ = load()
    from experiments.protocol import code_tests, dispatch
    from experiments.protocol.state import _log_event
    original_pair = episode._prepare_pair
    original_execute = agents.execute_tool_call
    original_tests = code_tests._run_python_tests
    original_inspect = dispatch._inspect_database
    original_query = dispatch._query_database

    def prepare_pair(**kwargs):
        value = original_pair(**kwargs)
        if wrapper.historical:
            wrapper.local.state = value.state
        return value

    def execute(*, state, actor, tool_name, arguments):
        if not wrapper.historical:
            return original_execute(state=state, actor=actor, tool_name=tool_name, arguments=arguments)
        tape = wrapper.local.tape
        _require(bool(tape.pending), "historical tool has no bound provider call")
        tool = tape.pending.pop(0)
        _require(tool["call"]["function"]["name"] == tool_name and json.loads(tool["call"]["function"]["arguments"]) == arguments,
                 "historical tool/arguments drift")
        if tool["cached"]:
            _require(tool["cached"][0] == actor, "historical tool actor drift")
        tape.current_tool = tool
        try:
            result = original_execute(state=state, actor=actor, tool_name=tool_name, arguments=arguments)
            if tool["cached"]:
                _require(dispatch.format_tool_content(result) == tool["cached"][1], "historical tool output drift: " + tool_name)
            return result
        finally:
            tape.current_tool = None

    def tests(state, slot, path):
        return wrapper.local.tape.cached_payload() if wrapper.historical else original_tests(state, slot, path)

    def sql_tool(state, actor, query=None, max_rows=100):
        if not wrapper.historical:
            return original_inspect(state, actor) if query is None else original_query(state, actor, query, max_rows)
        result = wrapper.local.tape.cached_payload()
        name = "inspect_database" if query is None else "query_database"
        _log_event(state, {"actor": actor, "tool": name, "success": bool(result.get("success")),
                          "columns": result.get("columns", []), "truncated": result.get("truncated", False),
                          **({"error": result["error"]} if "error" in result else {})})
        return result

    patches = [(episode, "_prepare_pair", prepare_pair), (agents, "execute_tool_call", execute),
               (code_tests, "_run_python_tests", tests),
               (dispatch, "_inspect_database", lambda state, actor: sql_tool(state, actor)),
               (dispatch, "_query_database", lambda state, actor, sql, max_rows=100: sql_tool(state, actor, sql, max_rows))]
    stack = ExitStack()
    for owner, name, value in patches:
        stack.enter_context(patch.object(owner, name, value))
    return stack.close


def replay_episode(run_dir, dyad_id):
    """Return proof/checkpoint at a new-call boundary; no original file is written."""
    run_dir = Path(run_dir).resolve()
    from .accounting import verify_run
    journal_check = verify_run(run_dir, source_root=ROOT)
    _require(journal_check["stopped"] and not journal_check["requires_restart_stop"],
             "source run is not terminal with settled in-flight attempts")
    release_path = run_dir / "release.json"
    release = json.loads(release_path.read_text())
    frozen = dict(release); expected = frozen.pop("release_hash")
    _require(digest(frozen) == expected, "release digest mismatch")
    for name, sha in release["source_inventory"].items():
        _require(file_hash(ROOT / name) == sha, "frozen source drift: " + name)
    dyad_path = run_dir / "dyads" / (dyad_id + ".json")
    dyad = json.loads(dyad_path.read_text())
    _require(dyad["status"] == "stopped" and dyad["episodes"], "requires an interrupted, previously active dyad")
    episode_number = len(dyad["episodes"]) + 1
    tape = ReplayTape(run_dir, dyad, episode_number)
    tape.inputs[str(release_path.relative_to(ROOT))] = file_hash(release_path)
    tape.inputs[str(dyad_path.relative_to(ROOT))] = file_hash(dyad_path)
    tape.inputs[str((run_dir / "manifest.json").relative_to(ROOT))] = file_hash(run_dir / "manifest.json")
    recipe = next(r for r in release["recipes"] if r["recipe_id"] == dyad["recipe_id"])
    schedule = next(s for s in release["schedules"][dyad["phase"]] if s["dyad_index"] == int(dyad_id.rsplit("-", 1)[1]))
    jobs = ([("warmup", pair) for pair in schedule["warmup"]] if recipe["warmup"]["episodes"] else [])
    jobs += [("main", pair) for pair in schedule["main"]]
    campaign_phase, pair = jobs[episode_number - 1]
    histories = {a: copy.deepcopy(dyad["episodes"][-1]["agents"][a]["messages"]) for a in ("alice", "bob")}
    for actor, history in histories.items():
        incomplete = dyad["incomplete_histories"][actor]
        boundary = max(i for i, m in enumerate(incomplete) if m["role"] == "_runner_active_episode_start")
        _require(encode_input(history) == encode_input(incomplete[:boundary]), "completed snapshot is not interrupted history prefix")
    episode, agents, _ = load()
    from experiments.models import AgentConfig, EpisodeRunConfig
    from experiments.protocol import code_tests, dispatch
    from experiments.protocol.state import agent_state, _log_event
    from experiments.memory.messages import snapshot_visible_messages
    config = EpisodeRunConfig(**policy_for(recipe, campaign_phase), temp_run_id=dyad_id,
                              episode_index=episode_number - 1, verbose=False, reflection=True,
                              reward=True, verdict_review=True)
    original_prepare = episode._prepare_agent_task
    original_pair = episode._prepare_pair
    original_execute = agents.execute_tool_call
    state_holder = {}
    scratch_map = {}
    generated_files = {}

    def deny(*args, **kwargs):
        raise ReplayMismatch("offline replay attempted network/process execution")

    with tempfile.TemporaryDirectory(prefix="v3-offline-replay-") as scratch:
        def prepare(**kwargs):
            result = original_prepare(**kwargs)
            slot = agent_state(kwargs["state"], kwargs["agent_id"])
            if slot.get("task_tmp_dir"):
                basename = Path(slot["task_tmp_dir"]).name
                source_path = str(run_dir / "scratch" / basename)
                actual_path = str((Path(scratch) / basename).resolve())
                scratch_map[actual_path] = source_path
                slot["task_tmp_dir"] = actual_path
            return result

        def prepared_pair(**kwargs):
            value = original_pair(**kwargs)
            state_holder["state"] = value.state
            return value

        def execute(*, state, actor, tool_name, arguments):
            _require(bool(tape.pending), "tool execution not bound to provider response")
            tool = tape.pending.pop(0)
            _require(tool["call"]["function"]["name"] == tool_name and json.loads(tool["call"]["function"]["arguments"]) == arguments,
                     "tool call/arguments changed")
            if tool["cached"]:
                _require(tool["cached"][0] == actor, "tool output attributed to wrong actor")
            tape.current_tool = tool
            try:
                result = original_execute(state=state, actor=actor, tool_name=tool_name, arguments=arguments)
                if tool["cached"]:
                    _require(dispatch.format_tool_content(result) == tool["cached"][1], "tool output changed: " + tool_name)
                return result
            finally:
                tape.current_tool = None

        def sql_tool(state, actor, sql=None, max_rows=100):
            name = "inspect_database" if sql is None else "query_database"
            result = tape.cached_payload()
            _log_event(state, {"actor": actor, "tool": name, "success": bool(result.get("success")),
                              "columns": result.get("columns", []), "truncated": result.get("truncated", False),
                              **({"error": result["error"]} if "error" in result else {})})
            return result

        with ExitStack() as stack:
            changes = [(episode, "_prepare_agent_task", prepare), (episode, "_prepare_pair", prepared_pair),
                       (agents, "_complete_with_retries", tape.complete), (agents, "execute_tool_call", execute),
                       (agents, "reasoning_to_replay", lambda model, message: {"provider_output_items": copy.deepcopy(message["provider_output_items"])}),
                       (agents, "_extract_reasoning_trace", lambda message: None),
                       (episode, "_is_context_window_error", lambda error: False),
                       (code_tests, "_run_python_tests", lambda state, slot, path: tape.cached_payload()),
                       (dispatch, "_inspect_database", lambda state, actor: sql_tool(state, actor)),
                       (dispatch, "_query_database", lambda state, actor, sql, max_rows=100: sql_tool(state, actor, sql, max_rows)),
                       (socket, "socket", deny), (socket, "create_connection", deny), (subprocess, "Popen", deny)]
            for owner, name, value in changes:
                stack.enter_context(patch.object(owner, name, value))
            try:
                episode.run_one_episode(pair=tuple(pair), agent_configs={a: AgentConfig(MODEL, "high") for a in histories},
                                        repo_root=VENDOR, config=config, agent_messages=histories)
            except ReplayBoundary:
                pass
            else:
                raise ReplayMismatch("replay unexpectedly completed without an uncached boundary")
        _require(tape.boundary is not None and not tape.pending, "replay ended with incomplete tools")
        for path in Path(scratch).rglob("*"):
            if path.is_file():
                path = path.resolve()
                original = next((v + str(path)[len(k):] for k, v in scratch_map.items() if str(path).startswith(k + "/")), None)
                _require(original is not None, "scratch file cannot be mapped to source identity")
                generated_files[original] = path.read_text()
        # Source file paths are not model-visible. Checkpoint uses the original
        # canonical names plus file contents; replay never touches those files.
        def restore_paths(value):
            if isinstance(value, dict): return {k: restore_paths(v) for k, v in value.items()}
            if isinstance(value, list): return [restore_paths(v) for v in value]
            if isinstance(value, str):
                for current, original in scratch_map.items():
                    if value == current or value.startswith(current + "/"): return original + value[len(current):]
            return value
        checkpoint = {"state": restore_paths(copy.deepcopy(state_holder["state"])),
                      "histories": copy.deepcopy(histories), "scratch_files": generated_files,
                      "pending_request": tape.boundary["request"]}
    for actor, old_history in dyad["incomplete_histories"].items():
        old = snapshot_visible_messages(old_history)
        new = snapshot_visible_messages(histories[actor])
        _require(new[:len(old)] == old, "replay changed already-ingested actor history")
    for name, sha in tape.inputs.items():
        _require(file_hash(ROOT / name) == sha, "replay input changed during verification")
    return {"schema_version": 1, "status": "passed", "source_run": str(run_dir.relative_to(ROOT)),
            "dyad_id": dyad_id, "episode": episode_number, "campaign_phase": campaign_phase,
            "source_release_hash": expected, "provider_calls": 0, "generated_code_executions": 0,
            "matched_request_count": len(tape.proof) + (tape.boundary["kind"] == "verified_http429"),
            "cached_response_count": len(tape.proof), "late_response_count": sum(not p["previously_ingested"] for p in tape.proof),
            "consumed_responses": tape.proof, "boundary": {k: v for k, v in tape.boundary.items() if k != "request"},
            "input_inventory": tape.inputs, "input_inventory_digest": digest(tape.inputs),
            "checkpoint_digest": digest(checkpoint), "checkpoint": checkpoint,
            "path_note": "Temporary scratch paths restored to source names in state; old files never written. Checkpoint is data, not a serialized Python stack. Resume requires deterministic replay to this boundary."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("dyad_id")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    _require(not args.output.exists(), "offline proof output already exists")
    _require(not args.output.resolve().is_relative_to(args.run_dir.resolve()), "proof cannot be written inside source run")
    value = replay_episode(args.run_dir, args.dyad_id)
    atomic_json(args.output, value)
    print(json.dumps({k: value[k] for k in ("status", "dyad_id", "matched_request_count", "cached_response_count", "late_response_count", "boundary", "checkpoint_digest")}, indent=2))


if __name__ == "__main__":
    main()
