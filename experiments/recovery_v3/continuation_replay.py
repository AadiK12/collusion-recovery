"""Exact replay for the administrative partial-stop continuation.

Reconstruct only from completed snapshots, immutable request/response artifacts,
and already-observed tool outputs. No cached response is sampled again. The
four unrecorded messaging/verdict acknowledgments are deterministic state
transitions; external code/SQL execution always requires a cached result.
"""
from contextlib import ExitStack, contextmanager
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile
from unittest.mock import patch

from experiments.induction_v3 import replay as original
from experiments.induction_v3.common import ROOT, VENDOR, MODEL, digest, file_hash
from experiments.induction_v3.liability_accounting import verify_run
from experiments.induction_v3.schedule import policy_for
from experiments.induction_v3.transport import make_body, decode_response, usage_from_response, encode_input
from experiments.induction_v3.upstream import load

ReplayMismatch = original.ReplayMismatch
ReplayBoundary = original.ReplayBoundary
_require = original._require
install_replay_hooks = original.install_replay_hooks
SOURCE_RUN = "runs/empirical/induction-v3-resumption-02c8e4c3-7cda-4aac-b058-7b4df6878ff6"
HANDOFF_PROOF = "reports/recovery-v3/execution/administrative-handoff-proof.json"
HANDOFF_FINAL = "reports/recovery-v3/execution/administrative-handoff-final.json"
ALLOWED_DYADS = ("development-warmup_raw-1", "development-warmup_summary-1")
DETERMINISTIC_UNRECORDED_TOOLS = frozenset({"send_message", "submit_verdict"})


def _regular(path):
    path = Path(path).absolute()
    _require(path.is_relative_to(ROOT), "source path outside repository")
    relative = path.relative_to(ROOT)
    _require(".." not in relative.parts, "source path traversal")
    _require(not any(ROOT.joinpath(*relative.parts[:i]).is_symlink()
                     for i in range(1, len(relative.parts) + 1)),
             "symlink in source path")
    _require(path.is_file(), "source file missing")
    return path


def _source(run_dir, dyad_id):
    run_dir = Path(run_dir).absolute()
    _require(run_dir == ROOT / SOURCE_RUN and dyad_id in ALLOWED_DYADS,
             "source or dyad outside assigned continuation")
    check = verify_run(run_dir, source_root=ROOT)
    _require(check["stopped"] and not check["requires_restart_stop"]
             and check["ledger"]["in_flight"] == 0
             and all(Decimal(str(v)) == 0 for v in check["ledger"]["reserved"].values()),
             "source must have no local unresolved reservations")
    manifest = json.loads(_regular(run_dir / "manifest.json").read_text())
    events = [json.loads(line) for line in _regular(run_dir / "journal.jsonl").read_text().splitlines()]
    _require(manifest["status"] == "partial"
             and events[-1]["kind"] == "terminal" and events[-1]["status"] == "partial"
             and events[-2]["kind"] == "stop"
             and events[-2]["reason"] == "user_requested_early_exploratory_recovery_handoff",
             "requires the exact administrative partial stop")
    proof = json.loads(_regular(ROOT / HANDOFF_PROOF).read_text())
    proof_body = dict(proof); proof_hash = proof_body.pop("proof_hash")
    final = json.loads(_regular(ROOT / HANDOFF_FINAL).read_text())
    _require(digest(proof_body) == proof_hash and proof["run_directory"] == SOURCE_RUN
             and final["proof_hash"] == proof_hash and final["run_id"] == run_dir.name
             and final["status"] == "administratively_stopped_partial"
             and final["original_journal_prefix_preserved"] is True,
             "administrative handoff evidence mismatch")
    for relative, sha in proof["artifact_hashes"].items():
        if relative not in {"journal.jsonl", "manifest.json"}:
            _require(file_hash(_regular(run_dir / relative)) == sha,
                     "source artifact changed since quiescent handoff: " + relative)
    original_prefix = b"".join((run_dir / "journal.jsonl").read_bytes().splitlines(keepends=True)[:-2])
    _require(hashlib.sha256(original_prefix).hexdigest() == proof["artifact_hashes"]["journal.jsonl"],
             "administrative journal prefix changed")
    release_path = _regular(run_dir / "release.json")
    release = json.loads(release_path.read_text())
    body = dict(release); expected = body.pop("release_hash")
    _require(digest(body) == expected and release["run_directory"] == SOURCE_RUN,
             "source release identity mismatch")
    for name, sha in release["source_inventory"].items():
        _require(file_hash(_regular(ROOT / name)) == sha, "frozen source drift: " + name)
    dyad = json.loads(_regular(run_dir / "dyads" / (dyad_id + ".json")).read_text())
    _require(dyad["dyad_id"] == dyad_id and dyad["phase"] == "development"
             and dyad["status"] == "running" and bool(dyad["episodes"])
             and not dyad.get("incomplete_histories"),
             "requires preserved administrative-stop dyad")
    return run_dir, release, dyad


class ContinuationReplayTape(original.ReplayTape):
    """Current partial calls, attributed by their exact completed role prefix."""
    def __init__(self, run_dir, dyad, episode_number):
        self.run_dir, self.dyad = Path(run_dir), dyad
        self.index, self.pending, self.current_tool = 0, [], None
        self.proof, self.boundary, self.inputs, self.rows = [], None, {}, []
        self.tool_outputs, self.saved_output_items, self.latest_inputs = {}, {}, {}
        self.initial_histories = {
            actor: copy.deepcopy(dyad["episodes"][-1]["agents"][actor]["messages"])
            for actor in ("alice", "bob")}
        prefix = {a: encode_input(h) for a, h in self.initial_histories.items()}
        journal = self.read("journal.jsonl", lines=True)
        settlements = {e["reservation_id"]: e for e in journal if e["kind"] == "settlement"}
        for event in journal:
            metadata = event.get("metadata", {})
            if (event["kind"] != "reservation" or metadata.get("dyad_id") != dyad["dyad_id"]
                    or metadata.get("episode") != episode_number):
                continue
            row = copy.deepcopy(event["reservation"])
            identity = json.loads(row["identity"])
            _require(identity == {**metadata, "physical_call_index": len(self.rows)},
                     "noncontiguous original physical identity")
            _require(metadata.get("resume_run_id") == self.run_dir.name
                     and metadata.get("recipe_id") == dyad["recipe_id"]
                     and metadata.get("campaign_phase") == "main",
                     "original trajectory identity mismatch")
            row["request"] = self.read("requests/" + row["request_hash"] + ".json")
            _require(digest(row["request"]) == row["request_hash"], "archived request digest mismatch")
            actors = [a for a, p in prefix.items() if row["request"]["input"][:len(p)] == p]
            _require(len(actors) == 1, "archived request has no unique completed role prefix")
            row["actor"] = actor = actors[0]
            self.latest_inputs[actor] = copy.deepcopy(row["request"]["input"])
            for item in row["request"]["input"]:
                if item.get("type") == "function_call_output":
                    identity = item["call_id"]; value = (actor, item["output"])
                    _require(identity not in self.tool_outputs or self.tool_outputs[identity] == value,
                             "inconsistent archived tool output or actor")
                    self.tool_outputs[identity] = value
                if "id" in item:
                    identity = item["id"]
                    _require(identity not in self.saved_output_items or self.saved_output_items[identity] == item,
                             "changed opaque/provider replay item")
                    self.saved_output_items[identity] = copy.deepcopy(item)
            row["response"] = self.read("responses/" + row["reservation_id"] + ".json")
            row["settlement"] = settlements.get(row["reservation_id"])
            _require(row["settlement"] is not None and row["settlement"]["outcome"] == "completed",
                     "partial call lacks completed settlement")
            _require("body" in row["response"], "administrative continuation cannot retry a failed response")
            self.rows.append(row)
        _require(bool(self.rows), "no selected cached partial calls")

    def read(self, relative, lines=False):
        _regular(self.run_dir / relative)
        return super().read(relative, lines=lines)

    def complete(self, *, model, messages, completion_kwargs, temperature,
                 max_output_tokens, accept_tool_calls, usage_recorder=None):
        _require(not self.pending, "unconsumed historical tool calls")
        body = make_body(model=model, messages=messages, completion_kwargs=completion_kwargs,
                         temperature=temperature, max_output_tokens=max_output_tokens)
        request_hash = digest(body)
        if self.index == len(self.rows):
            self.boundary = {"kind": "never_dispatched", "physical_call_index": self.index,
                             "request_hash": request_hash, "request": body}
            raise ReplayBoundary("all selected cached responses consumed")
        row = self.rows[self.index]
        _require(body == row["request"] and request_hash == row["request_hash"],
                 "reconstructed request differs at index " + str(self.index))
        response = row["response"]; raw = response["body"]
        _require(response.get("request_hash") == request_hash, "response request binding mismatch")
        _require(raw.get("status") == "completed" and (raw.get("model") == MODEL
                 or str(raw.get("model", "")).startswith(MODEL + "-")), "cached response identity mismatch")
        message = decode_response(raw)
        _require(accept_tool_calls or not message["tool_calls"], "reflection has orphan tool calls")
        _require(bool(message["content"] or message["tool_calls"]), "empty cached response")
        recorded = [item.get("id") in self.saved_output_items for item in raw["output"]]
        _require(all(recorded) or not any(recorded), "partially retained provider output")
        if all(recorded):
            _require(all(self.saved_output_items[item["id"]] == item for item in raw["output"]),
                     "changed ordered provider output")
        self.pending = [{"call": call, "cached": self.tool_outputs.get(call["id"])}
                        for call in message["tool_calls"]]
        for tool in self.pending:
            if tool["cached"] is None:
                _require(tool["call"]["function"]["name"] in DETERMINISTIC_UNRECORDED_TOOLS,
                         "external tool output unavailable; replay cannot execute or invent it")
            else:
                _require(tool["cached"][0] == row["actor"], "tool result actor mismatch")
        self.proof.append({"physical_call_index": self.index, "reservation_id": row["reservation_id"],
            "original_physical_identity": row["identity"], "actor": row["actor"],
            "request_hash": request_hash, "provider_output_digest": digest(raw["output"]),
            "previously_ingested": all(recorded), "tool_calls": len(self.pending),
            "deterministic_unrecorded_tool_results": sum(t["cached"] is None for t in self.pending)})
        self.index += 1
        if usage_recorder:
            usage_recorder({**(usage_from_response(raw) or {}), "requested_model": MODEL,
                "response_model": raw.get("model"), "api_attempt": 1, "response_status": "accepted",
                "finish_reason": "stop", "provider_usage": raw.get("usage"),
                "latency_seconds": response["latency_seconds"], "reservation_id": row["reservation_id"]})
        return raw, message, message["content"], message["tool_calls"] if accept_tool_calls else []


class ContinuationReplayTransport(original.ReplayTransport):
    @contextmanager
    def episode_replay(self, run_dir, dyad, proof, *, histories):
        _require(getattr(self.local, "tape", None) is None, "nested replay scope")
        run_dir, release, source_dyad = _source(run_dir, dyad["dyad_id"])
        _require(dyad == source_dyad, "caller changed source dyad")
        _require(proof.get("status") == "passed"
                 and proof["checkpoint_digest"] == digest(proof["checkpoint"])
                 and proof["source_run"] == SOURCE_RUN and proof["dyad_id"] == dyad["dyad_id"]
                 and proof["source_release_hash"] == release["release_hash"]
                 and proof["episode"] == len(dyad["episodes"]) + 1,
                 "offline proof identity or checkpoint mismatch")
        for name, sha in proof["input_inventory"].items():
            _require(file_hash(_regular(ROOT / name)) == sha, "offline proof input drift: " + name)
        tape = ContinuationReplayTape(run_dir, dyad, proof["episode"])
        _require(histories == tape.initial_histories, "replay must start at exact completed role snapshots")
        metadata = getattr(self.local, "metadata", {})
        _require(metadata.get("dyad_id") == dyad["dyad_id"]
                 and metadata.get("recipe_id") == dyad["recipe_id"]
                 and metadata.get("campaign_phase") == proof["campaign_phase"]
                 and metadata.get("episode") == proof["episode"], "context differs from replay proof")
        self.local.tape, self.local.proof, self.local.histories = tape, proof, histories
        self.local.live, self.local.state, self.local.live_logical_index = False, None, None
        try:
            yield tape
            _require(self.local.live and not tape.pending, "replay ended before verified new-call boundary")
        finally:
            self.local.tape = self.local.proof = self.local.histories = self.local.state = None
            self.local.live = False


def replay_episode(run_dir, dyad_id):
    """Return a deterministic exact-boundary proof, with no provider/code calls."""
    run_dir, release, dyad = _source(run_dir, dyad_id)
    expected = release["release_hash"]
    episode_number = len(dyad["episodes"]) + 1
    tape = ContinuationReplayTape(run_dir, dyad, episode_number)
    for relative in ("release.json", "manifest.json", "dyads/" + dyad_id + ".json"):
        tape.inputs[str((run_dir / relative).relative_to(ROOT))] = file_hash(run_dir / relative)
    for name in (HANDOFF_PROOF, HANDOFF_FINAL):
        tape.inputs[name] = file_hash(ROOT / name)
    recipe = next(r for r in release["recipes"] if r["recipe_id"] == dyad["recipe_id"])
    schedule = next(s for s in release["schedules"][dyad["phase"]]
                    if s["dyad_index"] == int(dyad_id.rsplit("-", 1)[1]))
    jobs = ([("warmup", pair) for pair in schedule["warmup"]] if recipe["warmup"]["episodes"] else [])
    jobs += [("main", pair) for pair in schedule["main"]]
    campaign_phase, pair = jobs[episode_number - 1]
    histories = copy.deepcopy(tape.initial_histories)
    episode, agents, _ = load()
    from experiments.models import AgentConfig, EpisodeRunConfig
    from experiments.protocol import code_tests, dispatch
    from experiments.protocol.state import agent_state, _log_event
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
    for actor, old_input in tape.latest_inputs.items():
        new_input = encode_input(histories[actor])
        _require(new_input[:len(old_input)] == old_input, "replay changed an archived actor input prefix")
    for name, sha in tape.inputs.items():
        _require(file_hash(ROOT / name) == sha, "replay input changed during verification")
    return {"schema_version": 1, "status": "passed", "source_run": str(run_dir.relative_to(ROOT)),
            "dyad_id": dyad_id, "episode": episode_number, "campaign_phase": campaign_phase,
            "source_release_hash": expected, "provider_calls": 0, "generated_code_executions": 0,
            "matched_request_count": len(tape.proof),
            "deterministic_unrecorded_tool_results": sum(p["deterministic_unrecorded_tool_results"] for p in tape.proof),
            "cached_response_count": len(tape.proof), "late_response_count": sum(not p["previously_ingested"] for p in tape.proof),
            "consumed_responses": tape.proof, "boundary": {k: v for k, v in tape.boundary.items() if k != "request"},
            "input_inventory": tape.inputs, "input_inventory_digest": digest(tape.inputs),
            "checkpoint_digest": digest(checkpoint), "checkpoint": checkpoint,
            "path_note": "Temporary scratch paths restored to source names in state; old files never written. Checkpoint is data, not a serialized Python stack. Resume requires deterministic replay to this boundary."}
