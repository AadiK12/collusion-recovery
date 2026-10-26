"""Native macOS lsof field parsing for the reviewed read-only handoff observer."""
import re

from .admin_handoff import HandoffError, SystemObserver


def parse_lock_holders(output):
    holders = set()
    owner_seen = False
    for line in output.splitlines():
        if re.fullmatch(r"p[1-9][0-9]*", line):
            holders.add(int(line[1:]))
            owner_seen = True
        elif owner_seen and re.fullmatch(r"f[0-9]+", line):
            # lsof -Fp still emits a descriptor record for each open file.
            continue
        else:
            raise HandoffError("unrecognized native lock holder output")
    return holders


class NativeSystemObserver(SystemObserver):
    def lock_holders(self, path):
        output = self._run(["/usr/sbin/lsof", "-n", "-Fp", str(path)], allowed=(0, 1))
        return parse_lock_holders(output)
