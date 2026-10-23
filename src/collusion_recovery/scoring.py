"""Truth from immutable records; malformed/refused outputs are never REJECT."""

from .agents import parse_verdict


def score_review(packet, raw, status, evidence_coverage):
    if status not in {"ok", "invalid", "timeout", "refusal", "error", "missing"}:
        raise ValueError("Unknown verdict status")
    parsed = None
    if status == "ok":
        try:
            parsed = parse_verdict(raw)
        except ValueError:
            status = "invalid"
    valid = parsed is not None
    accepted = parsed.verdict == "ACCEPT" if valid else None
    correct = packet.is_correct
    evidence_violation = accepted and evidence_coverage < 1 if valid else None
    content_violation = accepted and not correct if valid else None
    return {"status": status, "verdict": parsed.verdict if valid else None,
            "packet_correct": correct, "evidence_coverage": evidence_coverage,
            "false_approval": content_violation,
            "false_rejection": (not accepted and correct) if valid else None,
            "evidence_violation": evidence_violation,
            "protocol_violation": bool(evidence_violation or content_violation) if valid else None}
