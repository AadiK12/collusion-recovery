"""Closed offline configuration. There is deliberately no live escape hatch."""

from dataclasses import asdict, dataclass
from pathlib import Path
import tomllib


class OfflineOnlyError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    protocol_version: str = "v1.0"
    source: str = "mock"
    provider: str = "scripted"
    allow_paid_calls: bool = False
    approved_budget_usd: int = 0
    control: str = "evidence_policy"
    blocks: int = 2
    seed_namespace: str = "offline-smoke/v1"
    bootstrap_replicates: int = 10000
    max_requests: int = 10000

    def __post_init__(self):
        if (self.source != "mock" or self.provider != "scripted"
                or self.allow_paid_calls is not False
                or type(self.approved_budget_usd) is not int
                or self.approved_budget_usd != 0):
            raise OfflineOnlyError("Only scripted mock execution with zero spending is supported.")
        if self.protocol_version != "v1.0":
            raise ValueError("Unsupported protocol version")
        if not isinstance(self.control, str) or self.control not in {"evidence_policy", "always_accept", "always_reject"}:
            raise ValueError("Unknown scripted control")
        for name in ("blocks", "bootstrap_replicates", "max_requests"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.blocks > 100 or self.bootstrap_replicates > 100000:
            raise ValueError("Offline run size exceeds the supported bound")
        if not isinstance(self.seed_namespace, str) or not self.seed_namespace.strip():
            raise ValueError("seed_namespace must be a nonempty string")

    def counts(self):
        b = self.blocks
        return {"independent_blocks": b, "formation_reviews": b * 40,
                "main_reviews": b * 72, "probe_reviews": b * 48,
                "scheduled_reviews": b * 160, "nominal_requests": b * 272,
                "calibration_included": False, "paid_calls": 0, "cost_usd": 0}

    def to_dict(self):
        return asdict(self)


def load_config(path):
    with Path(path).open("rb") as handle:
        values = tomllib.load(handle)
    try:
        return Config(**values)
    except TypeError as exc:
        raise ValueError(f"Invalid/unknown configuration field: {exc}") from exc
