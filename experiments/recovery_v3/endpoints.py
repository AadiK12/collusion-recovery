"""Read-only, outcome-independent recovery checkpoints and replay provenance.

Only the six preassigned unseeded development dyads are eligible. Histories are
copied as opaque JSON, never combined across actors or semantically inspected.
No provider, credential, lock, or induction-run mutation is performed here.
"""
import copy
import hashlib
import json
from pathlib import Path

from experiments.induction_v3.common import ROOT, digest


DEFAULT_RUN_DIR = Path("runs/empirical/induction-v3-resumption-02c8e4c3-7cda-4aac-b058-7b4df6878ff6")
RECIPES = ("reference", "warmup_raw", "warmup_summary")
ACTORS = ("alice", "bob")
MAIN_EPISODES = 10


def _path(root, name, *, must_exist=True):
    root = Path(root).resolve()
    candidate = Path(name)
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            raise ValueError("artifact path must not traverse parents")
        candidate = root / candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact path escapes root") from exc
    if not relative.parts or ".." in relative.parts:
        raise ValueError("invalid artifact path")
    if any(root.joinpath(*relative.parts[:i]).is_symlink()
           for i in range(1, len(relative.parts) + 1)):
        raise ValueError("symlink artifacts are forbidden")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError("artifact path escapes root")
    if must_exist and not candidate.is_file():
        raise ValueError("missing regular artifact: " + str(relative))
    return candidate, str(relative)


def _read(root, name):
    path, relative = _path(root, name)
    data = path.read_bytes()
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("malformed JSON artifact: " + relative) from exc
    if not isinstance(value, dict):
        raise ValueError("artifact must be a JSON object: " + relative)
    return value, hashlib.sha256(data).hexdigest(), relative


def _validate_history(slot, actor):
    peer = "bob" if actor == "alice" else "alice"
    if (not isinstance(slot, dict) or slot.get("agent_id") != actor
            or slot.get("peer_id") != peer):
        raise ValueError("wrong actor or peer in checkpoint")
    messages = slot.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("missing actor history")
    first = messages[0]
    if (not isinstance(first, dict) or first.get("role") != "system"
            or not isinstance(first.get("content"), str)
            or not first["content"].startswith("## Role\n\nYou are " + actor.title() + ".\n")):
        raise ValueError("wrong role in initial system history")
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValueError("malformed history message role")
        role = message["role"]
        if role == "system" and index != 0:
            raise ValueError("unexpected additional system message")
        if not isinstance(message.get("content"), str) and not (role == "assistant" and message.get("content") is None):
            raise ValueError("malformed history message content")
        if role == "tool" and not isinstance(message.get("tool_call_id"), str):
            raise ValueError("missing tool call identity")
        if "tool_calls" in message and not isinstance(message["tool_calls"], list):
            raise ValueError("malformed tool calls")
        if role == "assistant":
            items = message.get("provider_output_items")
            if not isinstance(items, list) or not items:
                raise ValueError("missing exact ordered provider output items")
            for item in items:
                if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                        or not item["id"] or not isinstance(item.get("type"), str)):
                    raise ValueError("malformed provider output item identity")
        elif "provider_output_items" in message:
            raise ValueError("provider output items belong only to the actor's assistant messages")
    # Equality checks preserve opaque private text without interpreting it. The
    # source appends outcome feedback, reflection prompt, then reflection reply.
    for field in ("outcome_feedback", "reflection_prompt", "reflection"):
        if not isinstance(slot.get(field), str) or not slot[field].strip():
            raise ValueError("incomplete final outcome/reflection checkpoint")
    if (len(messages) < 4 or [m["role"] for m in messages[-3:]] != ["user", "user", "assistant"]
            or [m.get("content") for m in messages[-3:]] != [slot["outcome_feedback"], slot["reflection_prompt"], slot["reflection"]]):
        raise ValueError("final outcome/reflection not retained in exact history order")


def _inspect_dyad(value, recipe, index):
    identity = f"development-{recipe}-{index}"
    if (value.get("dyad_id") != identity or value.get("recipe_id") != recipe
            or value.get("phase") != "development" or value.get("synthetic_seed") is not False):
        raise ValueError("checkpoint dyad identity, phase, or seed mismatch")
    if value.get("status") not in {"running", "stopped", "completed"}:
        raise ValueError("malformed dyad status")
    episodes = value.get("episodes")
    if not isinstance(episodes, list) or any(not isinstance(e, dict) for e in episodes):
        raise ValueError("malformed checkpoint episodes")
    phases = [e.get("campaign_phase") for e in episodes]
    expected = (["warmup"] * 3 if recipe != "reference" else []) + ["main"] * MAIN_EPISODES
    if phases != expected[:len(phases)] or len(phases) > len(expected):
        raise ValueError("checkpoint episode schedule is malformed")
    count = phases.count("main")
    if value["status"] == "completed" and phases != expected:
        raise ValueError("completed dyad lacks exact main-10 checkpoint")
    if value["status"] != "completed":
        return count, None
    checkpoint = episodes[-1]
    if (not isinstance(checkpoint.get("episode_id"), str)
            or not isinstance(checkpoint.get("task_type"), str)
            or not isinstance(checkpoint.get("task_ids"), list)
            or len(checkpoint["task_ids"]) != 2
            or not all(isinstance(task, str) for task in checkpoint["task_ids"])
            or not isinstance(checkpoint.get("agents"), dict)
            or set(checkpoint.get("agents", {})) != set(ACTORS)):
        raise ValueError("malformed main-10 checkpoint")
    for actor in ACTORS:
        _validate_history(checkpoint["agents"][actor], actor)
    return count, checkpoint


def inventory_endpoints(*, root=ROOT, run_dir=DEFAULT_RUN_DIR):
    """Return all six assigned endpoints; availability never depends on scores.

    A running/stopped/missing dyad is pending even if it has useful earlier
    episodes. Only status=completed with the exact final main10 is loadable.
    File hashes bind the full dyad; checkpoint_sha256 binds the complete final
    episode as canonical JSON, including both original opaque actor histories.
    """
    directory, _ = _path(root, run_dir, must_exist=False)
    rows = []
    for recipe in RECIPES:
        for index in (1, 2):
            identity = f"development-{recipe}-{index}"
            path, relative = _path(root, directory / "dyads" / (identity + ".json"), must_exist=False)
            row = {"endpoint_id": identity, "recipe": recipe, "recipe_id": recipe,
                   "dyad_index": index, "status": "pending", "main_completed": 0,
                   "warmup_completed": 0, "episode_id": None,
                   "role_history_sha256": None, "reflection_complete": None,
                   "outcome_feedback_complete": None,
                   "path": None, "sha256": None, "checkpoint_path": None,
                   "dyad_file_sha256": None, "checkpoint_sha256": None}
            if path.exists():
                value, file_sha, _ = _read(root, relative)
                row["main_completed"], checkpoint = _inspect_dyad(value, recipe, index)
                row["warmup_completed"] = len(value["episodes"]) - row["main_completed"]
                if checkpoint is not None:
                    row.update(status="completed", path=relative, sha256=file_sha,
                               checkpoint_path=relative, dyad_file_sha256=file_sha,
                               checkpoint_sha256=digest(checkpoint), episode_id=checkpoint["episode_id"],
                               role_history_sha256={a: digest(checkpoint["agents"][a]["messages"]) for a in ACTORS},
                               reflection_complete={a: True for a in ACTORS},
                               outcome_feedback_complete={a: True for a in ACTORS})
            rows.append(row)
    return rows


def load_endpoint(row, *, root=ROOT):
    """Verify a frozen inventory row and return independent own-history copies.

    The returned histories mapping is an orchestration container. A retained
    actor must be passed only histories[actor], never the mapping or its peer's
    entry. No reset reconstruction or cross-actor history synthesis occurs.
    """
    if not isinstance(row, dict) or row.get("status") != "completed":
        raise ValueError("endpoint is pending or malformed")
    recipe, index = row.get("recipe"), row.get("dyad_index")
    if recipe not in RECIPES or type(index) is not int or index not in (1, 2):
        raise ValueError("endpoint is not one of the six preassigned dyads")
    identity = f"development-{recipe}-{index}"
    if (row.get("endpoint_id") != identity or row.get("recipe_id") != recipe
            or row.get("main_completed") != MAIN_EPISODES
            or not isinstance(row.get("path"), str)
            or row.get("checkpoint_path") != row["path"]
            or row.get("dyad_file_sha256") != row.get("sha256")):
        raise ValueError("endpoint inventory identity or binding mismatch")
    value, file_sha, relative = _read(root, row["path"])
    if file_sha != row.get("sha256"):
        raise ValueError("checkpoint file hash mismatch")
    count, checkpoint = _inspect_dyad(value, recipe, index)
    if checkpoint is None or count != MAIN_EPISODES or digest(checkpoint) != row.get("checkpoint_sha256"):
        raise ValueError("complete checkpoint hash mismatch")
    history_hashes = {a: digest(checkpoint["agents"][a]["messages"]) for a in ACTORS}
    if (row.get("role_history_sha256") != history_hashes
            or row.get("episode_id") != checkpoint["episode_id"]
            or row.get("warmup_completed") != len(value["episodes"]) - count
            or row.get("reflection_complete") != {a: True for a in ACTORS}
            or row.get("outcome_feedback_complete") != {a: True for a in ACTORS}):
        raise ValueError("role history or completion binding mismatch")
    metadata_keys = ("episode_id", "task_type", "task_ids", "display_ids", "campaign_phase",
                     "memory_scope", "verdict_policy", "throttled", "max_rounds", "reward_scheme", "v3_metrics")
    metadata = {key: copy.deepcopy(checkpoint[key]) for key in metadata_keys if key in checkpoint}
    metadata.update(endpoint_id=identity, recipe=recipe, dyad_index=index,
                    main_episode=MAIN_EPISODES, source_episode_index=len(value["episodes"]) - 1,
                    main_completed=count, warmup_completed=len(value["episodes"]) - count,
                    role_history_sha256=history_hashes,
                    reflection_complete={a: True for a in ACTORS}, outcome_feedback_complete={a: True for a in ACTORS},
                    path=relative, dyad_file_sha256=file_sha, checkpoint_sha256=row["checkpoint_sha256"])
    return {"endpoint_id": identity,
            "histories": {actor: copy.deepcopy(checkpoint["agents"][actor]["messages"]) for actor in ACTORS},
            "checkpoint": metadata}


def discover_response_archives(*, root=ROOT, run_dir=DEFAULT_RUN_DIR):
    """Snapshot immutable response/request files from continuation and resumption.

    The active run's immutable release supplies its originating continuation.
    All successful responses are included, not just selected endpoint outcomes.
    Mutable manifests and journals are intentionally excluded. Re-run discovery
    before a later release to include responses written since this snapshot.
    """
    directory, run_name = _path(root, run_dir, must_exist=False)
    release, release_sha, release_path = _read(root, directory / "release.json")
    source_name = release.get("source_run")
    if not isinstance(source_name, str):
        raise ValueError("missing originating continuation in frozen release")
    source_dir, source_name = _path(root, source_name, must_exist=False)
    if source_name == run_name:
        raise ValueError("originating continuation must be distinct")
    _, source_release_sha, source_release_path = _read(root, source_dir / "release.json")
    requests, responses = {}, {}
    for run in (source_dir, directory):
        for folder in ("requests", "responses"):
            path, _ = _path(root, run / folder, must_exist=False)
            if not path.is_dir():
                raise ValueError("missing response/request archive directory")
        for path in sorted((run / "requests").glob("*.json")):
            body, sha, relative = _read(root, path)
            if digest(body) != path.stem:
                raise ValueError("archived request canonical hash mismatch")
            requests[relative] = sha
        for path in sorted((run / "responses").glob("*.json")):
            response, sha, relative = _read(root, path)
            body = response.get("body")
            if body is None:
                continue  # Recorded transport errors are not successful bodies.
            if not isinstance(body, dict):
                raise ValueError("malformed archived response body")
            if body.get("status") != "completed":
                continue
            if not isinstance(body.get("output"), list):
                raise ValueError("malformed successful response output")
            request_hash = response.get("request_hash")
            if (not isinstance(request_hash, str) or len(request_hash) != 64
                    or any(c not in "0123456789abcdef" for c in request_hash)):
                raise ValueError("successful response lacks request identity")
            request_path, request_name = _path(root, run / "requests" / (request_hash + ".json"))
            request, request_sha, _ = _read(root, request_path)
            if digest(request) != request_hash:
                raise ValueError("response request hash mismatch")
            requests[request_name] = request_sha
            responses[relative] = sha
    return {"source_runs": [source_name, run_name],
            "run_release_bindings": {source_release_path: source_release_sha, release_path: release_sha},
            "response_bindings": responses, "request_bindings": requests,
            "mutable_manifests_included": False}
