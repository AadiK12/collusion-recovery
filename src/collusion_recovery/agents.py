"""Stateless scripted controls; intentionally no model client, SDK, or network code."""

from dataclasses import dataclass
import json
import re

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
            or len(ids) > 32
            or any(not isinstance(x, str) or re.fullmatch(r"R[A-Z0-9]{10}", x) is None
                   for x in ids)
            or len(ids) != len(set(ids))
            or not isinstance(obj["brief_reason"], str) or len(obj["brief_reason"]) > 500):
        raise ValueError("Invalid verdict fields")
    return Verdict(obj["verdict"], tuple(ids), obj["brief_reason"])


def parse_durable_verdict(provider_result):
    """Strictly reparse durable text and verify any stored derivative.

    ``parsed_output`` is an adapter convenience field, never scientific source
    evidence.  Callers that reconstruct histories or gate decisions use the
    persisted ``output_text`` and reject a missing or contradictory derivative.
    """
    if not isinstance(provider_result, dict):
        try:
            provider_result = dict(provider_result)
        except (TypeError, ValueError) as exc:
            raise ValueError("Provider result is not an object") from exc
    raw = provider_result.get("output_text")
    if not isinstance(raw, str):
        raise ValueError("Durable verdict output_text is missing")
    verdict = parse_verdict(raw)
    independently_parsed = {
        "verdict": verdict.verdict,
        "evidence_record_ids": list(verdict.evidence_record_ids),
        "brief_reason": verdict.brief_reason,
    }
    stored = provider_result.get("parsed_output")
    if not isinstance(stored, dict):
        try:
            stored = dict(stored)
        except (TypeError, ValueError) as exc:
            raise ValueError("Stored parsed_output is missing or malformed") from exc
    if canonical(stored) != canonical(independently_parsed):
        raise ValueError("Stored parsed_output disagrees with durable output_text")
    return verdict


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
