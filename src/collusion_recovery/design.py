"""Fixed v1 arm definitions and balanced, precomputed task schedules."""

from dataclasses import asdict, dataclass
import random

from .common import seed_for
from .fixtures import DEFECTS, generate_fixture

ROLES = ("alice", "bob")


@dataclass(frozen=True)
class Arm:
    history: str
    evidence: str
    correction: bool = False
    reset: bool = False


ARMS = {"A": Arm("U", "full"), "B": Arm("C", "full"),
        "C": Arm("U", "full", True), "D": Arm("C", "full", True),
        "E": Arm("C", "full", True, True), "F": Arm("C", "constrained")}
PEER_CELLS = tuple((h, p) for h in ("U", "C") for p in ("compliant", "shortcut"))


def arm_manifest():
    return {key: asdict(value) for key, value in ARMS.items()}


def block_design(namespace, block):
    base = f"{namespace}/block-{block}"
    rng = random.Random(seed_for(base + "/schedule"))
    formation = [tuple(generate_fixture(f"{base}/formation/{i}/{role}")
                       for role in ROLES) for i in range(10)]
    # Exactly three faulty packets per role; each defect occurs twice.
    labels = [(role, defect) for role in ROLES for defect in DEFECTS]
    rng.shuffle(labels)
    evaluation = [tuple(generate_fixture(f"{base}/evaluation/{i}/{role}",
                                         defect if role == faulty_role else None)
                        for role in ROLES)
                  for i, (faulty_role, defect) in enumerate(labels)]
    probe_labels = [(own_fault, peer_fault, j) for own_fault in (False, True)
                    for peer_fault in (False, True) for j in range(3)]
    rng.shuffle(probe_labels)
    roles = ["alice"] * 6 + ["bob"] * 6
    rng.shuffle(roles)
    probes = []
    for i, ((own_fault, peer_fault, j), role) in enumerate(zip(probe_labels, roles)):
        own = generate_fixture(f"{base}/probe/{i}/own", DEFECTS[j] if own_fault else None)
        peer = generate_fixture(f"{base}/probe/{i}/peer", DEFECTS[j] if peer_fault else None)
        probes.append((role, own, peer))
    formation_order, arm_order, peer_order = ["U", "C"], list(ARMS), list(PEER_CELLS)
    for order in (formation_order, arm_order, peer_order):
        rng.shuffle(order)
    return {"formation": formation, "evaluation": evaluation, "probes": probes,
            "formation_order": formation_order, "arm_order": arm_order,
            "peer_order": peer_order}
