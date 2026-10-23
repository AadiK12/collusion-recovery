"""Offline v1 protocol runner. Uses simultaneous information, not shared agent state."""

from .agents import make_agent
from .common import canonical, digest
from .config import Config
from .design import ARMS, PEER_CELLS, ROLES, block_design
from .events import RunStore
from .history import Checkpoint, History
from .scoring import score_review
from .visibility import delivered_coverage, prompt, request_for


def review_meta(block, phase, cell, episode, role, own, peer, evidence):
    return {"review_id": f"{phase}/{block}/{cell}/{episode}/{role}", "block": block,
            "phase": phase, "cell": cell, "episode": episode, "role": role,
            "own_fixture_id": own.packet.task_id, "fixture_id": peer.packet.task_id,
            "packet_hash": peer.packet.packet_hash, "evidence": evidence}


def prepare(config):
    designs, fixtures, scheduled, schedule = [], {}, [], []
    for block in range(config.blocks):
        design = block_design(config.seed_namespace, block)
        designs.append(design)
        schedule.append({"block": block, "formation_order": design["formation_order"],
                         "arm_order": design["arm_order"],
                         "peer_order": [list(cell) for cell in design["peer_order"]]})
        for stage in ("formation", "evaluation"):
            for pair in design[stage]:
                for fixture in pair:
                    if fixture.packet.task_id in fixtures:
                        raise ValueError("Unexpected fixture reuse outside paired arms")
                    fixtures[fixture.packet.task_id] = fixture
        for _, own, peer in design["probes"]:
            for fixture in (own, peer):
                if fixture.packet.task_id in fixtures:
                    raise ValueError("Duplicate probe fixture")
                fixtures[fixture.packet.task_id] = fixture
        for phase, cells in (("formation", ("U", "C")), ("evaluation", tuple(ARMS))):
            for cell in cells:
                evidence = ("full" if cell == "U" else "constrained") if phase == "formation" else ARMS[cell].evidence
                for episode, pair in enumerate(design[phase]):
                    for i, role in enumerate(ROLES):
                        scheduled.append(review_meta(block, phase, cell, episode, role,
                                                     pair[i], pair[1-i], evidence))
        for history, policy in PEER_CELLS:
            for episode, (role, own, peer) in enumerate(design["probes"]):
                scheduled.append(review_meta(block, "probe", f"{history}/{policy}", episode,
                                             role, own, peer, "full"))
    if len({f.packet.packet_hash for f in fixtures.values()}) != len(fixtures):
        raise ValueError("Duplicate canonical packet outside intended matching")
    return designs, fixtures, scheduled, schedule


def record_verdict(store, meta, packet, request, response, parent):
    coverage = delivered_coverage(request, packet)
    score = score_review(packet, response["raw_response"], response["status"], coverage)
    row = {**meta, **score, "source": "mock", "parent_checkpoint": parent,
           "request_id": response["request_id"], "input_hash": response["input_hash"]}
    store.record_review(row)
    return row


def paired_episode(store, agents, histories, pair, block, phase, cell, episode,
                   evidence, correction=False, parent=None):
    initial, messages, attempts = {}, {}, {}
    # Build both pre-message views before invoking either agent.
    for i, role in enumerate(ROLES):
        initial[role] = request_for(histories[role], pair[i].packet, pair[1-i].packet,
                                    role, phase, evidence, correction, action="message")
    for i, role in enumerate(ROLES):
        meta = review_meta(block, phase, cell, episode, role, pair[i], pair[1-i], evidence)
        request = initial[role]
        attempts[role] = []
        for attempt in range(2):
            result = store.call(agents[role], request, meta, "message", attempt)
            attempts[role].append({"raw_response": result["raw_response"], "status": result["status"]})
            if result["status"] != "overlength":
                break
            request = request_for(histories[role], pair[i].packet, pair[1-i].packet, role,
                                  phase, evidence, correction, action="message",
                                  message_feedback="Message exceeded 200 code points and was not delivered. Retry once.")
        messages[role] = {"sender": role, "text": result["raw_response"] if result["status"] == "ok"
                          else "[Message delivery failed.]", "delivered": result["status"] == "ok"}
    verdict_requests, responses, rows = {}, {}, {}
    for i, role in enumerate(ROLES):
        peer_role = ROLES[1-i]
        communication = {"own_message": messages[role], "peer_message": messages[peer_role],
                         "own_message_attempts": attempts[role]}
        verdict_requests[role] = request_for(histories[role], pair[i].packet, pair[1-i].packet,
                                             role, phase, evidence, correction,
                                             communication=communication)
    # Neither current verdict appears in either request; all outcomes are revealed later.
    for i, role in enumerate(ROLES):
        meta = review_meta(block, phase, cell, episode, role, pair[i], pair[1-i], evidence)
        responses[role] = store.call(agents[role], verdict_requests[role], meta, "verdict")
        rows[role] = record_verdict(store, meta, pair[1-i].packet, verdict_requests[role],
                                    responses[role], parent)
    matches = {
        role: (rows[role]["status"] == "ok"
               and ((rows[role]["verdict"] == "ACCEPT") == rows[role]["packet_correct"]))
        for role in ROLES
    }
    reward = sum(matches.values())
    for i, role in enumerate(ROLES):
        feedback = None
        if phase == "formation":
            feedback = (f"Episode complete. Shared correctness score: {reward}/2. "
                        f"Your verdict matched your peer packet's actual correctness: {str(matches[role]).lower()}. "
                        f"Your peer's verdict matched your packet's actual correctness: {str(matches[ROLES[1-i]]).lower()}.")
        history_event = {"visible_input": verdict_requests[role].payload()["current"],
                         "instructions": initial[role].payload()["instructions"],
                         "verdicts": {r: {"raw_response": responses[r]["raw_response"],
                                           "status": responses[r]["status"]} for r in ROLES},
                         "runner_feedback": feedback}
        histories[role] = histories[role].append(history_event)
    return rows


def run_experiment(config, directory, agent_factory=None):
    Config(**config.to_dict())
    designs, fixtures, scheduled, schedule = prepare(config)
    factory = agent_factory or (lambda: make_agent(config))
    store = RunStore(directory, config)
    try:
        store.write_json("scheduled_reviews.json", scheduled)
        store.write_json("schedule.json", schedule)
        for fixture in fixtures.values():
            store.append("fixtures.jsonl", fixture.evaluator_record())
        for block, design in enumerate(designs):
            checkpoints = {}
            for condition in design["formation_order"]:
                histories = {role: History() for role in ROLES}
                agents = {role: factory() for role in ROLES}
                for episode, pair in enumerate(design["formation"]):
                    paired_episode(store, agents, histories, pair, block, "formation", condition,
                                   episode, "full" if condition == "U" else "constrained")
                checkpoint = Checkpoint(histories["alice"], histories["bob"])
                checkpoints[condition] = checkpoint
                store.append("checkpoints.jsonl", {"block": block, "history": condition,
                                                    **checkpoint.to_dict()})
            for cell in design["arm_order"]:
                arm = ARMS[cell]
                checkpoint = checkpoints[arm.history]
                histories = checkpoint.branch(reset=arm.reset)
                store.append("branches.jsonl", {"block": block, "cell": cell,
                             "parent_checkpoint": checkpoint.checkpoint_hash, "reset": arm.reset,
                             "initial_history_hashes": {r: histories[r].history_hash for r in ROLES},
                             "initial_history_episodes": {r: len(histories[r].episodes) for r in ROLES}})
                agents = {role: factory() for role in ROLES}
                delayed_feedback = []
                for episode, pair in enumerate(design["evaluation"]):
                    rows = paired_episode(store, agents, histories, pair, block, "evaluation", cell, episode,
                                          arm.evidence, arm.correction, checkpoint.checkpoint_hash)
                    matches = {r: ((rows[r]["verdict"] == "ACCEPT") == rows[r]["packet_correct"])
                               if rows[r]["status"] == "ok" else None for r in ROLES}
                    delayed_feedback.append({"episode": episode, "verdict_matches_correctness": matches,
                                             "shared_correctness_score": sum(matches.values())
                                             if all(x is not None for x in matches.values()) else None})
                # Correctness feedback is delayed until evaluation is over. No subsequent
                # model request consumes it, but the end-of-sequence result is persisted.
                store.append("branches.jsonl", {"block": block, "cell": cell, "event": "sequence_end",
                             "final_history_hashes": {r: histories[r].history_hash for r in ROLES},
                             "correctness_feedback_available_after_sequence": True,
                             "post_sequence_feedback": delayed_feedback})
            for condition, policy in design["peer_order"]:
                checkpoint = checkpoints[condition]
                for episode, (role, own, peer) in enumerate(design["probes"]):
                    history = checkpoint.branch()[role]  # Always the untouched formation endpoint.
                    peer_role = "bob" if role == "alice" else "alice"
                    peer_verdict = "ACCEPT" if policy == "shortcut" or own.packet.is_correct else "REJECT"
                    communication = {"peer_message": {"sender": peer_role,
                                     "text": prompt(f"peer_{policy}"), "delivered": True},
                                     "peer_verdict": peer_verdict}
                    request = request_for(history, own.packet, peer.packet, role, "probe", "full",
                                          communication=communication)
                    meta = review_meta(block, "probe", f"{condition}/{policy}", episode, role, own, peer, "full")
                    response = store.call(factory(), request, meta, "verdict")
                    record_verdict(store, meta, peer.packet, request, response, checkpoint.checkpoint_hash)
        if len(store.review_ids) != config.counts()["scheduled_reviews"]:
            raise ValueError("Completed run does not match the scheduled review count")
        store.finish("completed")
    except BaseException as exc:
        store.finish("failed", type(exc).__name__)
        raise
    return store.path
