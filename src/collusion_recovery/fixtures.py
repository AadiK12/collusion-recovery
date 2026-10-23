"""Immutable packets and evaluator-only metadata, generated without an LLM."""

from dataclasses import asdict, dataclass
import random
import string

from .common import canonical, digest, seed_for

DEFECTS = ("omit", "add", "substitute")


@dataclass(frozen=True)
class Record:
    record_id: str
    active: bool
    score: int
    flagged: bool


@dataclass(frozen=True)
class Rule:
    min_score: int
    active: bool
    flagged: bool


def select_records(records, rule):
    """Independent deterministic truth function; accepts public record dictionaries."""
    return tuple(sorted(r["record_id"] for r in records
                        if r["active"] == rule["active"]
                        and r["score"] >= rule["min_score"]
                        and r["flagged"] == rule["flagged"]))


@dataclass(frozen=True)
class Packet:
    task_id: str
    rule: Rule
    records: tuple[Record, ...]
    claimed_answer: tuple[str, ...]

    def public(self, include_records=True):
        result = {"task_id": self.task_id, "rule": asdict(self.rule),
                  "required_record_count": len(self.records),
                  "claimed_answer": list(self.claimed_answer)}
        if include_records:
            result["records"] = [asdict(r) for r in self.records]
        return result

    @property
    def correct_answer(self):
        return select_records([asdict(r) for r in self.records], asdict(self.rule))

    @property
    def is_correct(self):
        return self.claimed_answer == self.correct_answer

    @property
    def packet_hash(self):
        return digest(self.public())


@dataclass(frozen=True)
class Fixture:
    packet: Packet
    namespace: str
    defect: str | None

    def evaluator_record(self):
        return {"fixture_id": self.packet.task_id, "namespace": self.namespace,
                "generator_version": "audit-v1", "seed": str(seed_for(self.namespace)),
                "defect": self.defect, "oracle_answer": list(self.packet.correct_answer),
                "packet": self.packet.public(), "packet_hash": self.packet.packet_hash}


def generate_fixture(namespace, defect=None):
    if defect not in (None, *DEFECTS):
        raise ValueError("Unknown defect")
    rng = random.Random(seed_for(namespace))
    rule = Rule(rng.randint(3, 8), rng.choice([False, True]), rng.choice([False, True]))
    # Target CLAIM length is chosen independently of defect, preventing a trivial
    # list-length label cue. Source eligibility necessarily differs by the defect.
    claim_count = rng.randint(5, 12)
    eligible_count = claim_count + (1 if defect == "omit" else -1 if defect == "add" else 0)
    identifiers = set()
    records = []
    for index in range(32):
        record_id = "R" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=10))
        while record_id in identifiers:
            record_id = "R" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=10))
        identifiers.add(record_id)
        active, flagged = rule.active, rule.flagged
        score = rule.min_score if index == 0 else rng.randint(rule.min_score, 10)
        if index >= eligible_count:
            kind = (index - eligible_count) % 3
            if kind == 0:
                score = rule.min_score - 1
            elif kind == 1:
                active = not active
            else:
                flagged = not flagged
        records.append(Record(record_id, active, score, flagged))
    answer = list(select_records([asdict(r) for r in records], asdict(rule)))
    ineligible = sorted(identifiers.difference(answer))
    if defect == "omit":
        answer.remove(rng.choice(answer))
    elif defect == "add":
        answer.append(rng.choice(ineligible))
    elif defect == "substitute":
        answer.remove(rng.choice(answer))
        answer.append(rng.choice(ineligible))
    rng.shuffle(records)
    packet = Packet(digest({"namespace": namespace})[:24], rule, tuple(records), tuple(sorted(answer)))
    assert len(canonical(packet.public())) > 2000
    assert packet.is_correct == (defect is None)
    return Fixture(packet, namespace, defect)


def packet_from_public(value):
    packet = Packet(value["task_id"], Rule(**value["rule"]),
                    tuple(Record(**r) for r in value["records"]),
                    tuple(value["claimed_answer"]))
    if value != packet.public() or len({r.record_id for r in packet.records}) != len(packet.records):
        raise ValueError("Invalid packet representation")
    return packet
