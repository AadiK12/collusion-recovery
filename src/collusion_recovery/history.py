"""Visible histories are immutable canonical strings, never mutable shared lists."""

from dataclasses import dataclass
import json

from .common import canonical, digest


@dataclass(frozen=True)
class History:
    episodes: tuple[str, ...] = ()

    def append(self, episode):
        return History(self.episodes + (canonical(episode),))

    def payload(self):
        # Every caller gets its own deep reconstruction.
        return [json.loads(x) for x in self.episodes]

    def reset(self):
        return History()

    @property
    def history_hash(self):
        return digest(self.episodes)


@dataclass(frozen=True)
class Checkpoint:
    alice: History
    bob: History

    @property
    def checkpoint_hash(self):
        return digest({"alice": self.alice.episodes, "bob": self.bob.episodes})

    def branch(self, reset=False):
        return {"alice": self.alice.reset() if reset else History(self.alice.episodes),
                "bob": self.bob.reset() if reset else History(self.bob.episodes)}

    def to_dict(self):
        return {"checkpoint_hash": self.checkpoint_hash,
                "alice": self.alice.payload(), "bob": self.bob.payload()}
