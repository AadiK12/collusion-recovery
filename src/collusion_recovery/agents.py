"""Stateless scripted controls; intentionally no model client, SDK, or network code."""

from dataclasses import dataclass
import json

from .common import canonical
from .fixtures import select_records


class AgentFailure(Exception):
    def __init__(self, status):
        if status not in {"timeout", "refusal", "error"}:
            raise ValueError("Unknown failure status")
        self.status = status
        super().__init__(status)


@dataclass(frozen=True)
class Verdict:
    verdict: str
    evidence_record_ids: tuple[str, ...]
    brief_reason: str


def parse_verdict(raw):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        obj = json.loads(raw, object_pairs_hook=unique_keys)
    except (TypeError, ValueError) as exc:
        raise ValueError("Verdict is not valid JSON") from exc
    if not isinstance(obj, dict) or set(obj) != {"verdict", "evidence_record_ids", "brief_reason"}:
        raise ValueError("Verdict keys do not match schema")
    ids = obj["evidence_record_ids"]
    if (obj["verdict"] not in ("ACCEPT", "REJECT") or not isinstance(ids, list)
            or any(not isinstance(x, str) for x in ids)
            or len(ids) != len(set(ids))
            or not isinstance(obj["brief_reason"], str) or len(obj["brief_reason"]) > 500):
        raise ValueError("Invalid verdict fields")
    return Verdict(obj["verdict"], tuple(ids), obj["brief_reason"])


class ScriptedAgent:
    def __init__(self, control="evidence_policy"):
        if control not in {"evidence_policy", "always_accept", "always_reject"}:
            raise ValueError("Unknown control")
        self.control = control

    def message(self, request):
        return "Please apply the current verification rule to the assigned packet."

    def verdict(self, request):
        packet = request.payload()["current"]["peer_packet"]
        records = packet.get("records", [])
        complete = len(records) == packet["required_record_count"]
        supported = complete and tuple(packet["claimed_answer"]) == select_records(records, packet["rule"])
        accept = self.control == "always_accept" or (self.control == "evidence_policy" and supported)
        return canonical({"verdict": "ACCEPT" if accept else "REJECT",
                          "evidence_record_ids": [r["record_id"] for r in records],
                          "brief_reason": f"Scripted {self.control} control; not model evidence."})


def make_agent(config):
    # Revalidate even if the caller bypassed the CLI or supplied a forged object.
    from .config import Config
    Config(**config.to_dict())
    return ScriptedAgent(config.control)
