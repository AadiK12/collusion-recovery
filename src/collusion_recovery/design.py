"""Versioned arm definitions and balanced, precomputed task schedules.

``v1.0`` is retained as the historical one-correct/one-faulty design.  The
explicit ``v1.1`` branch implements the revised CC/FF/mixed evaluation and
three-policy peer diagnostic without silently reinterpreting an old run.
"""

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
PROTOCOL_VERSIONS = ("v1.0", "v1.1")
PEER_POLICIES = {
    "v1.0": ("compliant", "shortcut"),
    "v1.1": ("compliant", "approval_only", "reciprocal_request"),
}
# Historical offline runner import.  It is intentionally pinned to v1.0.
PEER_CELLS = tuple((h, p) for h in ("U", "C") for p in PEER_POLICIES["v1.0"])


def peer_cells_for_version(protocol_version: str):
    try:
        policies = PEER_POLICIES[protocol_version]
    except KeyError as exc:
        raise ValueError(f"Unsupported protocol version: {protocol_version}") from exc
    return tuple((history, policy) for history in ("U", "C") for policy in policies)


def arm_manifest():
    return {key: asdict(value) for key, value in ARMS.items()}


def _v1_0_evaluation(base, rng):
    """Historical v1.0 schedule; keep operation order exactly stable."""
    labels = [(role, defect) for role in ROLES for defect in DEFECTS]
    rng.shuffle(labels)
    return [tuple(generate_fixture(f"{base}/evaluation/{i}/{role}",
                                   defect if role == faulty_role else None)
                  for role in ROLES)
            for i, (faulty_role, defect) in enumerate(labels)]


def _v1_1_evaluation(base, block, role_offset, rng):
    """Six CC/FF/mixed pairs satisfying every frozen balance constraint."""
    # Episode 1 is the prespecified immediate mixed challenge.  Episodes 2--3
    # randomize CC/FF, while episodes 4--6 randomize the complete composition
    # set.  This preserves one of each composition in both frozen halves.
    early_tail = ["both_correct", "both_faulty"]
    late = ["both_correct", "both_faulty", "mixed"]
    rng.shuffle(early_tail)
    rng.shuffle(late)
    compositions = ["mixed", *early_tail, *late]

    # The two mixed pairs put the fault on opposite roles.  Combined with one
    # both-faulty pair per half, each role therefore owns three correct and
    # three faulty packets over the six episodes.
    # A stable namespace hash selects the extra role for odd-sized splits, then
    # block parity alternates roles exactly.  The late mixed challenge always
    # uses the opposite role.
    first_mixed_faulty_role = ROLES[(role_offset + block) % len(ROLES)]
    mixed_faulty_roles = iter((
        first_mixed_faulty_role,
        ROLES[1 - ROLES.index(first_mixed_faulty_role)],
    ))
    faulty_roles_by_episode = []
    for composition in compositions:
        if composition == "both_correct":
            faulty_roles_by_episode.append(())
        elif composition == "both_faulty":
            faulty_roles_by_episode.append(ROLES)
        else:
            faulty_roles_by_episode.append((next(mixed_faulty_roles),))

    # Every role receives each primary defect exactly once.  Consequently the
    # complete arm contains exactly two examples of each defect type.
    defects_by_role = {}
    for role in ROLES:
        values = list(DEFECTS)
        rng.shuffle(values)
        defects_by_role[role] = iter(values)
    evaluation = []
    for episode, faulty_roles in enumerate(faulty_roles_by_episode):
        evaluation.append(tuple(
            generate_fixture(
                f"{base}/evaluation/{episode}/{role}",
                next(defects_by_role[role]) if role in faulty_roles else None,
            )
            for role in ROLES
        ))
    return evaluation, compositions


def _v1_0_probes(base, rng):
    """Historical twelve-probe template; keep operation order stable."""
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
    return probes


def _v1_1_probes(base, rng):
    """Eight action-matched templates, each truth combination once per role."""
    labels = [(own_fault, peer_fault, role)
              for own_fault in (False, True)
              for peer_fault in (False, True)
              for role in ROLES]
    rng.shuffle(labels)
    own_defects = [*DEFECTS, rng.choice(DEFECTS)]
    peer_defects = [*DEFECTS, rng.choice(DEFECTS)]
    rng.shuffle(own_defects)
    rng.shuffle(peer_defects)
    own_defects = iter(own_defects)
    peer_defects = iter(peer_defects)
    probes = []
    for episode, (own_fault, peer_fault, role) in enumerate(labels):
        # Each side covers all three defect families plus one seeded repeat.
        # Because the block-specific RNG shuffles both labels and defect lists,
        # no fixed probe index is tied to one systematically easier defect.  The
        # exact resulting template is still reused across all six policy cells.
        own_defect = next(own_defects) if own_fault else None
        peer_defect = next(peer_defects) if peer_fault else None
        own = generate_fixture(f"{base}/probe/{episode}/own", own_defect)
        peer = generate_fixture(f"{base}/probe/{episode}/peer", peer_defect)
        probes.append((role, own, peer))
    return probes


def block_design(namespace, block, protocol_version="v1.0"):
    if protocol_version not in PROTOCOL_VERSIONS:
        raise ValueError(f"Unsupported protocol version: {protocol_version}")
    # v1.0 keeps its historical namespaces byte-for-byte.  v1.1 introduces a
    # version component internally as a second line of defense against a
    # mistakenly reused caller namespace.
    base = (f"{namespace}/block-{block}" if protocol_version == "v1.0" else
            f"{namespace}/protocol-v1.1/block-{block}")
    rng = random.Random(seed_for(base + "/schedule"))
    formation = [tuple(generate_fixture(f"{base}/formation/{i}/{role}")
                       for role in ROLES) for i in range(10)]
    if protocol_version == "v1.0":
        evaluation = _v1_0_evaluation(base, rng)
        probes = _v1_0_probes(base, rng)
        compositions = None
    else:
        # Protocol v1.1 freezes the odd-split extra assignment with this exact
        # namespace label.  Block parity then alternates the episode-1 faulty
        # role, so even splits remain exactly balanced and odd splits differ by
        # only the one role selected here.
        role_offset = seed_for(
            f"{namespace}/episode1-extra-role"
        ) % len(ROLES)
        evaluation, compositions = _v1_1_evaluation(base, block, role_offset, rng)
        probes = _v1_1_probes(base, rng)
    formation_order = ["U", "C"]
    arm_order = list(ARMS)
    peer_order = list(peer_cells_for_version(protocol_version))
    for order in (formation_order, arm_order, peer_order):
        rng.shuffle(order)
    result = {"formation": formation, "evaluation": evaluation, "probes": probes,
              "formation_order": formation_order, "arm_order": arm_order,
              "peer_order": peer_order}
    if protocol_version == "v1.1":
        result.update(protocol_version=protocol_version,
                      evaluation_compositions=compositions)
    return result
