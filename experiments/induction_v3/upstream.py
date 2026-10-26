"""Load pinned upstream modules unchanged alongside the distinct V3 package.

The outer experiments package is a namespace package. Upstream's absolute imports
resolve its unique submodule names through the appended, verified source root.
No vendored source is rewritten. This module never calls an upstream CLI.
"""
import os
import sys
from .common import VENDOR

def load():
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    os.environ["DO_NOT_TRACK"] = "1"
    sys.dont_write_bytecode = True
    import experiments
    path = str(VENDOR / "experiments")
    if path not in experiments.__path__:
        experiments.__path__ = [*experiments.__path__, path]
    from experiments import episode_runner, agents, tasks
    for module in (episode_runner, agents, tasks):
        if not str(module.__file__).startswith(str(VENDOR) + "/"):
            raise RuntimeError("upstream namespace collision")
    return episode_runner, agents, tasks
