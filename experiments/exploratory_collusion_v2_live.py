"""Isolated capability + development execution; never dispatches validation/repairs.

Preparation and replay are credential-free. The sole empirical entry point builds
the locked official transport lazily, under the shared program lock. Every call
has one fsynced reservation and at most one result; no retries exist. A crash with
an open reservation is ambiguous and blocks resumption rather than redispatching.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import uuid

from collusion_recovery.budget import BudgetExhausted, BudgetLedger
from collusion_recovery.common import canonical, contains_secret_like_string, digest, utc_now
from collusion_recovery.history import Checkpoint, History
from collusion_recovery.live_config import ExecutionLimits, Pricing
from collusion_recovery.providers.base import ProviderResult, ResponseMode, TextTurn, ProviderRequest
from collusion_recovery.providers.openai import OpenAIResponsesProvider, provider_result_matches_raw_response
from . import controlled_peer_recovery_live as shared
from . import exploratory_collusion_v2 as study
from . import exploratory_collusion_v2_assessment as assessment
from . import exploratory_collusion_v2_capability as capability

ROOT = study.ROOT
VERSION = "exploratory-collusion-v2-live-pilot-v1"
MAX_REQUESTS = 192
MAX_RAW_VERDICT_BYTES = 8192
MAX_RESULT_BYTES = 1024 * 1024
MAX_OUTPUT_TOKENS = 4096
MODEL_CONTEXT_TOKENS = 1_050_000
TERMINAL = "completed_development_awaiting_blinded_annotations"


class LivePilotError(RuntimeError):
    pass


def source_inventory():
    names = set(shared.source_inventory()) | set(study.source_inventory()) | {
        "experiments/exploratory_collusion_v2_live.py",
        "experiments/exploratory_collusion_v2_capability.py",
        "tests/test_exploratory_collusion_v2_live.py",
        "tests/test_exploratory_collusion_v2_capability.py",
        "docs/EXPLORATORY_COLLUSION_V2_LIVE.md",
    }
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}


def _config(release):
    return study.StudyConfig("development", release["development_namespace"])


def _provider_request(spec, rendered):
    action = spec["action"]
    return ProviderRequest(spec["logical_request_id"], study.MODEL,
        tuple(TextTurn(**turn) for turn in rendered["turns"]),
        ResponseMode.VERDICT if action == "verdict" else ResponseMode.TEXT,
        MAX_OUTPUT_TOKENS, model_settings_json=study.MODEL_SETTINGS_JSON,
        message_max_codepoints=study.POLICY_NOTE_LIMIT if action == "policy_note" else study.MESSAGE_LIMIT)


def schedule(release, source="empirical_unverified"):
    """Deterministic coroutine: requests in, observations out; no side effects."""
    cap_plan = capability.build_plan(release["capability_namespace"])
    observations = {}
    for original in cap_plan["requests"]:
        spec = {**original, "stage": "capability", "action": "verdict"}
        observed = yield {"kind": "request", "spec": spec,
                          "request": capability.provider_request(cap_plan, original["ordinal"])}
        observations[spec["logical_request_id"]] = observed
    cap_report = capability.assess(cap_plan, observations)
    yield {"kind": "capability_gate", "assessment": cap_report}
    if cap_report["passed"] is not True:
        return {"status": "stopped_capability_gate", "capability": cap_report,
                "trace": None, "checkpoints": []}
    config = _config(release)
    plan = study.build_plan(config)
    indexed = {(row["dyad"], row["episode"], row["action"], row["message_round"], row["role"]): row
               for row in plan["requests"]}
    trace = {"source": source, "phase": "development", "plan_hash": plan["plan_hash"], "dyads": []}
    checkpoints = []
    for dyad in range(config.dyads):
        histories = {role: History() for role in study.ROLES}
        dyad_row = {"dyad": dyad, "episodes": [], "annotations": []}
        for episode, fixtures in enumerate(study.build_dyad_design(config, dyad)["episodes"]):
            pair = tuple(fixture.packet for fixture in fixtures)
            rounds, verdicts, notes = {}, {}, {}
            for action, number in [("message", n) for n in range(1, 6)] + [("verdict", None), ("policy_note", None)]:
                # Both roles are rendered against exactly the same frontier before
                # either response is inserted, even though transport is sequential.
                pending, received = [], {}
                for i, role in enumerate(study.ROLES):
                    original = indexed[(dyad, episode, action, number, role)]
                    spec = {**original, "ordinal": original["ordinal"] + 24, "stage": "development"}
                    request = study.formation_request(histories[role], pair[i], pair[1-i], role, action,
                        message_round=number, rounds=rounds,
                        verdicts=verdicts if action == "policy_note" else None)
                    pending.append((role, spec, _provider_request(spec, study.render_request(request))))
                for role, spec, request in pending:
                    received[role] = yield {"kind": "request", "spec": spec, "request": request}
                if action == "message":
                    rounds[number] = received
                elif action == "verdict":
                    verdicts = received
                else:
                    notes = received
            dyad_row["episodes"].append({"episode": episode,
                "messages": [{"round": n, "speaker": role, "observation": rounds[n][role]}
                    for n in range(1, 6) for role in study.ROLES],
                "verdicts": verdicts, "policy_notes": notes,
                "direct_records": {role: [] for role in study.ROLES}})
            histories = study.append_episode(histories, pair, rounds, verdicts, notes)
        trace["dyads"].append(dyad_row)
        checkpoint = {"dyad": dyad, **Checkpoint(**histories).to_dict()}
        checkpoints.append(checkpoint)
        yield {"kind": "checkpoint", "checkpoint": checkpoint}
    return {"status": TERMINAL, "capability": cap_report, "trace": trace, "checkpoints": checkpoints}


def _projection_observation(item, cap_plan):
    spec = item["spec"]
    if spec["stage"] == "capability":
        # Projection is an oracle-only shape simulation, never empirical data.
        row = cap_plan["requests"][spec["ordinal"]]
        expected = row["expected_verdict"]
        return {"status": "ok", "raw_response": canonical({"verdict": expected,
            "evidence_record_ids": [], "brief_reason": "shape"})}
    if spec["action"] == "verdict":
        return {"status": "ok", "raw_response": canonical({"verdict": "REJECT", "evidence_record_ids": [], "brief_reason": "shape"})}
    return {"status": "ok", "raw_response": "shape"}


def _projection(release, pricing):
    machine = schedule(release, "mock")
    cap_plan = capability.build_plan(release["capability_namespace"])
    shapes, previous = [], None
    while True:
        try:
            item = machine.send(previous)
        except StopIteration:
            break
        previous = None
        if item["kind"] != "request":
            continue
        spec, request = item["spec"], item["request"]
        bound = request.conservative_input_token_bound
        if spec["stage"] == "development":
            e = spec["episode"]
            current_rounds = (spec["message_round"] - 1) if spec["action"] == "message" else 5
            # Canonical user text is itself JSON-encoded in provider-bound material.
            # A Unicode scalar takes <=6 bytes in inner JSON and <=8 in the outer.
            # Raw verdict JSON is embedded twice, hence <=4 times its byte cap.
            bound += (e * 10 + current_rounds * 2) * study.MESSAGE_LIMIT * 8
            bound += e * study.POLICY_NOTE_LIMIT * 8
            bound += (e * 2 + (2 if spec["action"] == "policy_note" else 0)) * MAX_RAW_VERDICT_BYTES * 4
            bound += 4096  # Remaining bounded status/feedback/serialization framing.
        if bound + MAX_OUTPUT_TOKENS > MODEL_CONTEXT_TOKENS:
            raise LivePilotError("projected request exceeds model context")
        shapes.append({"ordinal": spec["ordinal"], "logical_request_id": spec["logical_request_id"],
                       "max_input_tokens": bound, "max_output_tokens": MAX_OUTPUT_TOKENS})
        previous = _projection_observation(item, cap_plan)
    if len(shapes) != MAX_REQUESTS:
        raise LivePilotError("projection did not cover exactly 24 capability + 168 development requests")
    limits = ExecutionLimits(MAX_REQUESTS, sum(row["max_input_tokens"] for row in shapes),
        MAX_REQUESTS * MAX_OUTPUT_TOKENS, max(row["max_input_tokens"] for row in shapes), MAX_OUTPUT_TOKENS, 1)
    ledger = BudgetLedger(limits, pricing, Decimal("1000000"))
    cost = sum((ledger.reservation_price(row["max_input_tokens"], MAX_OUTPUT_TOKENS) for row in shapes), Decimal(0))
    return {"shapes": shapes, "limits": limits.to_dict(), "conservative_all_shapes_cost_usd": str(cost),
            "bound_method": "exact static shape plus per-field nested-JSON byte ceilings"}


def build_release(run_id, prior_footprint, pricing_verified_at, *, _verify_prior=True):
    parsed = uuid.UUID(run_id)
    if parsed.version != 4 or str(parsed) != run_id:
        raise LivePilotError("run ID must be a lowercase UUIDv4")
    relative = f"runs/empirical/exploratory-collusion-v2-development-{run_id}"
    prior = (shared.validate_budget_reconciliation(prior_footprint, excluded_run_directory=relative)
             if _verify_prior else json.loads(canonical(prior_footprint)))
    pricing = Pricing(shared.PRICING_CONTRACT["source"], pricing_verified_at,
                      Decimal("2.00"), Decimal("0.10"), Decimal("2.50"), Decimal("10.00"))
    core = {"artifact_type": VERSION, "run_id": run_id, "run_directory": relative,
            "development_namespace": f"development/{run_id}", "capability_namespace": f"capability/{run_id}"}
    development = study.build_plan(_config(core))
    cap_plan = capability.build_plan(core["capability_namespace"])
    projection = _projection(core, pricing)
    body = {**core, "source_inventory": source_inventory(), "development_plan": development,
        "capability_plan": cap_plan, "pricing": pricing.to_dict(), "projection": projection,
        "prior_footprint": prior, "max_cost_usd": prior["verified_program_remainder_usd"],
        "scope": {"capability_requests": 24, "development_requests": 168, "maximum_attempts": 192,
                  "retries": 0, "validation_requests": 0, "repair_requests": 0},
        "transport_contract": shared.TRANSPORT_CONTRACT,
        "representation_guards": {"max_result_bytes": MAX_RESULT_BYTES, "max_raw_verdict_bytes": MAX_RAW_VERDICT_BYTES},
        "terminal": TERMINAL, "induction_established": False}
    body["authorization_context"] = "Owner approved V2 capability + development in the current chat; no claimed signed receipt."
    return {**body, "release_hash": digest(body)}


def validate_release(release, *, verify_budget=True):
    if not isinstance(release, dict):
        raise LivePilotError("release must be an object")
    body = dict(release)
    if body.pop("release_hash", None) != digest(body):
        raise LivePilotError("release hash mismatch")
    # Rebuilding proves dimensions/settings/fixtures/projection have not been
    # widened by editing and rehashing the proposal.
    rebuilt = build_release(release["run_id"], release["prior_footprint"], release["pricing"]["verified_at"],
                            _verify_prior=verify_budget)
    if rebuilt != release:
        raise LivePilotError("release differs from exact reconstructed pilot")
    return release


def _observation(request, result):
    if not isinstance(result, dict) or contains_secret_like_string(result):
        raise LivePilotError("provider result is malformed or contains secret-shaped content")
    if len(canonical(result).encode("utf-8")) > MAX_RESULT_BYTES:
        raise LivePilotError("provider result exceeds raw storage guard")
    if result.get("status") not in {"ok", "overlength"}:
        raise LivePilotError("provider did not return a completed usable response")
    if not provider_result_matches_raw_response(result, response_mode=request.response_mode,
            message_max_codepoints=request.message_max_codepoints):
        raise LivePilotError("raw provider result derivatives do not match")
    raw = result["raw_response"]
    if (result.get("returned_model") != study.MODEL or raw.get("service_tier") != "default"
            or result.get("model_metadata", {}).get("model") != study.MODEL
            or result.get("usage") is None or not isinstance(result.get("provider_request_id"), str)
            or not result["provider_request_id"].strip()):
        raise LivePilotError("provider model, tier, or usage does not match release")
    text = result.get("output_text")
    if request.response_mode is ResponseMode.VERDICT:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RAW_VERDICT_BYTES:
            raise LivePilotError("raw verdict exceeds its history representation guard")
        observed = study.verdict_observation(text)
    else:
        # Long completed text is documented nondelivery, never a revision.
        observed = study.text_observation(text, request.message_max_codepoints,
                                         "overlength" if result["status"] == "overlength" else "ok")
    if observed["status"] not in {"ok", "overlength"}:
        raise LivePilotError("response normalization failed")
    return observed


def _ledger(release, events):
    return BudgetLedger.replay(ExecutionLimits.from_mapping(release["projection"]["limits"]),
        Pricing.from_mapping(release["pricing"]), Decimal(release["max_cost_usd"]), events)


class _Store:
    def __init__(self, path, release, mode):
        self.path, self.release, self.mode = Path(path), release, mode
        if self.path.is_symlink():
            raise LivePilotError("run directory must not be a symlink")
        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=False)
            shared._atomic_json(self.path / "release.json", release, exclusive=True)
            for name, expected in release["source_inventory"].items():
                raw = (ROOT / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != expected:
                    raise LivePilotError("source changed while creating source snapshot")
                target = self.path / "source_snapshot" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            self.events = []
            self.append("binding", release_hash=release["release_hash"], mode=mode,
                        transport_contract=release["transport_contract"] if mode == "empirical_live" else {"transport": "offline-fake"})
        else:
            if shared._read_json(self.path / "release.json", "release") != release:
                raise LivePilotError("saved release differs")
            self.events = shared._verify_journal(self.path / "journal.jsonl")
            if not self.events or self.events[0].get("mode") != mode or self.events[0].get("release_hash") != release["release_hash"]:
                raise LivePilotError("saved run binding differs")
            for name, expected in release["source_inventory"].items():
                if hashlib.sha256((self.path / "source_snapshot" / name).read_bytes()).hexdigest() != expected:
                    raise LivePilotError("archived source snapshot differs on resume")
        self.update_manifest()

    def append(self, kind, **fields):
        body = {"sequence": len(self.events), "previous_event_hash": self.events[-1]["event_hash"] if self.events else "initial",
                "kind": kind, "recorded_at": utc_now(), **fields}
        row = {**body, "event_hash": digest(body)}
        descriptor = os.open(self.path / "journal.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write((canonical(row) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        self.events.append(row)
        self.update_manifest()
        return row

    def update_manifest(self):
        ledger = _ledger(self.release, self.events)
        terminal = self.events[-1] if self.events and self.events[-1]["kind"] == "terminal" else None
        shared._atomic_json(self.path / "manifest.json", {"artifact_type": VERSION,
            "run_id": self.release["run_id"], "empirical": self.mode == "empirical_live", "execution_mode": self.mode,
            "release_hash": self.release["release_hash"], "status": terminal["status"] if terminal else "running",
            "journal_tail": self.events[-1]["event_hash"] if self.events else "initial", "event_count": len(self.events),
            "ledger": ledger.snapshot(), "induction_established": False,
            "paid_calls": ledger.requests if self.mode == "empirical_live" else 0,
            "paid_cost_usd": str(ledger.cost_usd) if self.mode == "empirical_live" else "0"})


def _request_artifact(item):
    request = item["request"]
    return {"spec": item["spec"], "request": request.to_dict(),
            "wire_payload": OpenAIResponsesProvider.build_payload(request)}


def _check_shape(release, item):
    request, spec = item["request"], item["spec"]
    shape = release["projection"]["shapes"][spec["ordinal"]]
    if (shape["logical_request_id"] != request.request_identity
            or request.conservative_input_token_bound > shape["max_input_tokens"]
            or request.conservative_input_token_bound + request.max_output_tokens > MODEL_CONTEXT_TOKENS):
        raise LivePilotError("exact input exceeds frozen shape/context projection")


def _replay(release, events, mode):
    """Reconstruct every input from prior raw outputs; return the exact frontier."""
    if mode not in {"empirical_live", "offline_fake"} or not events:
        raise LivePilotError("unknown or absent run mode binding")
    binding = events[0]
    if (binding.get("kind") != "binding" or binding.get("release_hash") != release["release_hash"]
            or binding.get("mode") != mode or binding.get("transport_contract") != (
                release["transport_contract"] if mode == "empirical_live" else {"transport": "offline-fake"})):
        raise LivePilotError("run mode or transport binding differs")
    ledger = _ledger(release, events)
    machine = schedule(release, "empirical_unverified" if mode == "empirical_live" else "mock")
    cursor, item, completed = 1, next(machine), None
    while cursor < len(events):
        event = events[cursor]
        if event["kind"] == "terminal":
            if cursor != len(events) - 1:
                raise LivePilotError("events exist after terminal")
            if event["status"] in {TERMINAL, "stopped_capability_gate"} and (completed is None or completed["status"] != event["status"]):
                raise LivePilotError("terminal does not match reconstructed schedule")
            if event["status"] not in {TERMINAL, "stopped_capability_gate", "stopped_source_drift", "stopped_budget_ceiling"}:
                raise LivePilotError("terminal status is not valid at this replay frontier")
            if completed is not None and (event.get("capability_assessment_hash") != completed["capability"]["assessment_hash"]
                    or event.get("trace_hash") != digest(completed["trace"])
                    or event.get("checkpoints_hash") != digest(completed["checkpoints"])):
                raise LivePilotError("terminal artifact hashes differ")
            return machine, item, completed, ledger, event["status"]
        if item is None:
            raise LivePilotError("events exceed the fixed schedule")
        if item["kind"] != "request":
            expected = {key: value for key, value in item.items() if key != "kind"}
            if event["kind"] != item["kind"] or any(event.get(key) != value for key, value in expected.items()):
                raise LivePilotError("control event differs from reconstructed state")
            cursor += 1
            observed = None
        else:
            request, artifact = item["request"], _request_artifact(item)
            _check_shape(release, item)
            if (event["kind"] != "reservation" or event.get("artifact") != artifact
                    or event.get("request_hash") != digest(request.to_dict())
                    or event.get("wire_payload_hash") != digest(artifact["wire_payload"])):
                raise LivePilotError("reserved request differs from deterministic replay")
            exact = BudgetLedger(ExecutionLimits.from_mapping(release["projection"]["limits"]),
                Pricing.from_mapping(release["pricing"]), Decimal(release["max_cost_usd"]))
            expected_reservation = exact.reserve(request.request_identity, 0,
                input_tokens=request.conservative_input_token_bound, output_tokens=request.max_output_tokens).to_dict()
            if (event.get("reservation") != expected_reservation or event.get("identity") != request.request_identity
                    or event.get("attempt") != 0):
                raise LivePilotError("reservation amount/identity differs from exact reconstructed request")
            cursor += 1
            if cursor == len(events):
                return machine, item, completed, ledger, "blocked_ambiguous_open_reservation"
            result = events[cursor]
            if result["kind"] != "result" or result.get("reservation_id") != event["reservation"]["reservation_id"]:
                raise LivePilotError("reservation lacks its exact result")
            cursor += 1
            try:
                observed = _observation(request, result["provider_result"])
            except LivePilotError:
                if cursor < len(events) and events[cursor]["kind"] == "terminal" and cursor == len(events) - 1:
                    if events[cursor].get("status") != "stopped_provider_result":
                        raise LivePilotError("failed provider result cannot produce a completed terminal")
                    return machine, item, completed, ledger, events[cursor]["status"]
                if cursor < len(events):
                    raise LivePilotError("scientific/control events continue after failed provider result")
                return machine, item, completed, ledger, "stopped_provider_result"
        try:
            item = machine.send(observed)
        except StopIteration as stop:
            completed, item = stop.value, None
    return machine, item, completed, ledger, None


def annotation_artifact(trace):
    """Message-only review view: never expose text the peer did not receive."""
    result = assessment.annotation_view(trace)
    for dyad in result["dyads"]:
        for episode in dyad["episodes"]:
            for message in episode["messages"]:
                if message["observation"]["status"] != "ok":
                    message["observation"]["raw_response"] = None
    return result


def _save_completed(store, completed):
    shared._atomic_json(store.path / "capability_assessment.json", completed["capability"], exclusive=True)
    if completed["trace"] is not None:
        shared._atomic_json(store.path / "normalized_trace.unannotated.json", completed["trace"], exclusive=True)
        shared._atomic_json(store.path / "annotation_messages_only.json", annotation_artifact(completed["trace"]), exclusive=True)
        shared._atomic_json(store.path / "checkpoints.json", {"checkpoints": completed["checkpoints"]}, exclusive=True)


def _execute(release, path, mode, provider_factory):
    store = _Store(path, release, mode)
    machine, item, completed, ledger, status = _replay(release, store.events, mode)
    if status is not None:
        if status == "stopped_provider_result" and store.events[-1]["kind"] != "terminal":
            store.append("terminal", status=status)
        if completed is not None:
            _save_completed(store, completed)
        return {"status": status, "run_directory": str(store.path), "ledger": ledger.snapshot(), "induction_established": False}
    provider = None
    while item is not None:
        if item["kind"] != "request":
            store.append(item["kind"], **{key: value for key, value in item.items() if key != "kind"})
            observed = None
        else:
            if source_inventory() != release["source_inventory"]:
                store.append("terminal", status="stopped_source_drift")
                raise LivePilotError("source inventory changed before dispatch")
            if mode == "empirical_live":
                shared.validate_budget_reconciliation(release["prior_footprint"], excluded_run_directory=release["run_directory"])
            _check_shape(release, item)
            request = item["request"]
            if provider is None:
                provider = provider_factory()
            try:
                reservation = ledger.reserve(request.request_identity, 0,
                    input_tokens=request.conservative_input_token_bound, output_tokens=request.max_output_tokens)
            except BudgetExhausted:
                store.append("terminal", status="stopped_budget_ceiling")
                return {"status": "stopped_budget_ceiling", "run_directory": str(store.path),
                        "ledger": ledger.snapshot(), "induction_established": False}
            artifact = _request_artifact(item)
            request_hash, wire_hash = digest(request.to_dict()), digest(artifact["wire_payload"])
            store.append("reservation", reservation=reservation.to_dict(), identity=request.request_identity, attempt=0,
                request_hash=request_hash, wire_payload_hash=wire_hash, artifact=artifact)
            # No exception retry: if transport violates its ProviderResult contract,
            # the durable reservation remains conservatively open and blocks resume.
            returned = provider.complete(request)
            if not isinstance(returned, ProviderResult):
                raise LivePilotError("provider violated result contract; reservation remains ambiguous")
            result = returned.to_dict()
            if contains_secret_like_string(result) or len(canonical(result).encode("utf-8")) > MAX_RESULT_BYTES:
                raise LivePilotError("unsafe or oversized result; reservation remains ambiguous")
            accounting = ledger.preview_reconciliation(reservation.reservation_id, result["usage"],
                ambiguous=result["ambiguous"], known_not_completed=result["known_not_completed"])
            store.append("result", reservation_id=reservation.reservation_id, identity=request.request_identity, attempt=0,
                request_hash=request_hash, wire_payload_hash=wire_hash, status=result["status"],
                ambiguous=result["ambiguous"], known_not_completed=result["known_not_completed"], usage=result["usage"],
                provider_result=result, budget_accounting=accounting)
            ledger = _ledger(release, store.events)
            try:
                observed = _observation(request, result)
            except LivePilotError:
                store.append("terminal", status="stopped_provider_result")
                return {"status": "stopped_provider_result", "run_directory": str(store.path),
                        "ledger": ledger.snapshot(), "induction_established": False}
        try:
            item = machine.send(observed)
        except StopIteration as stop:
            completed, item = stop.value, None
    _save_completed(store, completed)
    store.append("terminal", status=completed["status"], capability_assessment_hash=completed["capability"]["assessment_hash"],
                 trace_hash=digest(completed["trace"]), checkpoints_hash=digest(completed["checkpoints"]))
    return {"status": completed["status"], "run_directory": str(store.path), "ledger": ledger.snapshot(), "induction_established": False}


def run_offline_fake(release, output_dir, provider):
    """Injectable path is permanently marked offline-fake and cannot read a key."""
    validate_release(release)
    output_dir = Path(output_dir).resolve()
    if output_dir == shared.RUNS_ROOT.resolve() or shared.RUNS_ROOT.resolve() in output_dir.parents:
        raise LivePilotError("offline outputs may not enter empirical runs")
    with shared._exclusive_file_lock(output_dir.parent / ("." + output_dir.name + ".lock"), "offline V2 run"):
        return _execute(release, output_dir, "offline_fake", lambda: provider)


def run_live(proposal_path, env_file=ROOT / ".env.local"):
    """Execute the authorized 192-attempt pilot only, never an injected provider."""
    release = shared._read_json(Path(proposal_path), "V2 release")
    clients = []
    with ExitStack() as stack:
        stack.enter_context(shared._exclusive_file_lock(shared.PROGRAM_LOCK_PATH, "shared empirical program"))
        validate_release(release)
        stamp = datetime.fromisoformat(release["pricing"]["verified_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if not 0 <= age <= 24 * 60 * 60:
            raise LivePilotError("pricing verification is not fresh")
        path = ROOT / release["run_directory"]
        stack.enter_context(shared._exclusive_file_lock(shared.RUNS_ROOT / ".v2-locks" / (release["run_id"] + ".lock"), "V2 pilot"))
        def factory():
            shared.validate_budget_reconciliation(release["prior_footprint"], excluded_run_directory=release["run_directory"])
            provider, client, http_client = shared._official_provider(Path(env_file).resolve())
            clients.append((client, http_client))
            return provider
        try:
            return _execute(release, path, "empirical_live", factory)
        finally:
            for client, http_client in clients:
                client.close()
                http_client.close()


def audit_run(proposal, run_dir):
    """Read-only independent raw-result replay; no provider/client construction."""
    validate_release(proposal, verify_budget=False)
    run_dir = Path(run_dir)
    if shared._read_json(run_dir / "release.json", "saved release") != proposal:
        raise LivePilotError("saved release differs")
    events = shared._verify_journal(run_dir / "journal.jsonl")
    mode = events[0]["mode"]
    _, _, completed, ledger, status = _replay(proposal, events, mode)
    manifest = shared._read_json(run_dir / "manifest.json", "manifest")
    for name, expected in proposal["source_inventory"].items():
        if hashlib.sha256((run_dir / "source_snapshot" / name).read_bytes()).hexdigest() != expected:
            raise LivePilotError("archived source snapshot differs")
    expected_status = events[-1]["status"] if events[-1]["kind"] == "terminal" else "running"
    if (manifest.get("ledger") != ledger.snapshot() or manifest.get("journal_tail") != events[-1]["event_hash"]
            or manifest.get("status") != expected_status or manifest.get("event_count") != len(events)
            or manifest.get("release_hash") != proposal["release_hash"] or manifest.get("run_id") != proposal["run_id"]
            or manifest.get("execution_mode") != mode or manifest.get("empirical") != (mode == "empirical_live")
            or manifest.get("paid_calls") != (ledger.requests if mode == "empirical_live" else 0)
            or manifest.get("paid_cost_usd") != (str(ledger.cost_usd) if mode == "empirical_live" else "0")):
        raise LivePilotError("manifest accounting/tail differs from journal")
    if completed is not None:
        artifacts = {"capability_assessment.json": completed["capability"]}
        if completed["trace"] is not None:
            artifacts.update({"normalized_trace.unannotated.json": completed["trace"],
                "annotation_messages_only.json": annotation_artifact(completed["trace"]),
                "checkpoints.json": {"checkpoints": completed["checkpoints"]}})
        for filename, expected in artifacts.items():
            if shared._read_json(run_dir / filename, filename) != expected:
                raise LivePilotError("derived artifact differs from raw replay")
    usable, request = 0, None
    for event in events:
        if event["kind"] == "reservation":
            raw = event["artifact"]["request"]
            request = ProviderRequest(raw["request_identity"], raw["model"],
                tuple(TextTurn(**turn) for turn in raw["turns"]), ResponseMode(raw["response_mode"]),
                raw["max_output_tokens"], canonical(raw["model_settings"]), raw["message_max_codepoints"],
                raw["message_retry_history_max_utf8_bytes"], raw["attempt"])
        elif event["kind"] == "result":
            try:
                _observation(request, event["provider_result"])
                usable += 1
            except LivePilotError:
                pass
    return {"status": status or "running", "integrity_verified": True,
        "accounted_attempts": ledger.requests, "dispatch_reservations": ledger.requests + len(ledger.active),
        "logical_requests_completed": usable,
        "open_reservations": len(ledger.active),
        "ledger": ledger.snapshot(), "journal_tail": events[-1]["event_hash"],
        "trace": None if completed is None else completed["trace"],
        "induction_established": False, "repairs_authorized": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--pricing-verified-at", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    execute = sub.add_parser("run")
    execute.add_argument("--proposal", type=Path, required=True)
    execute.add_argument("--execute-authorized-capability-development", action="store_true", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--proposal", type=Path, required=True)
    audit.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with shared._exclusive_file_lock(shared.PROGRAM_LOCK_PATH, "shared empirical program"):
            prior = shared.complete_budget_reconciliation(verified_at=utc_now())
            release = build_release(args.run_id, prior, args.pricing_verified_at)
            shared._atomic_json(args.output, release, exclusive=True)
        print(canonical({"release_hash": release["release_hash"], "scope": release["scope"], "output": str(args.output)}))
    elif args.command == "run":
        print(canonical(run_live(args.proposal)))
    else:
        result = audit_run(shared._read_json(args.proposal, "release"), args.run_dir)
        result.pop("trace")
        print(canonical(result))


if __name__ == "__main__":
    main()
