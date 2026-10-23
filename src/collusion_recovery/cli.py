"""All commands are offline. Live configuration is rejected before dispatch."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from uuid import uuid4

from .analysis import analyze_run
from .common import canonical
from .config import load_config
from .runner import run_experiment
from .validation import validate_controls
from .visibility import ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline reciprocal-verification harness (NO paid API calls)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "estimate", "run"):
        item = sub.add_parser(name)
        item.add_argument("--config", type=Path, default=ROOT / "configs/mock.toml")
        if name == "run":
            item.add_argument("--output", type=Path)
            item.add_argument("--control", choices=("evidence_policy", "always_accept", "always_reject"))
    item = sub.add_parser("analyze")
    item.add_argument("--run-dir", required=True, type=Path)
    item.add_argument("--allow-incomplete", action="store_true")
    item.add_argument("--empirical", action="store_true", help="Intentionally rejected for mock data")
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            result = analyze_run(args.run_dir, args.allow_incomplete, args.empirical)
            print(json.dumps(result, indent=2))
            return 0
        config = load_config(args.config)
        if args.command == "validate":
            print(json.dumps(validate_controls(), indent=2))
        elif args.command == "estimate":
            print(json.dumps({"source": "mock", "note": "No calibration or retries included; no API pricing required.",
                              **config.counts()}, indent=2))
        else:
            if args.control:
                config = replace(config, control=args.control)
            output = args.output or ROOT / "runs/mock" / uuid4().hex
            run_experiment(config, output)
            result = analyze_run(output)
            (output / "summary.json").write_text(canonical(result) + "\n", encoding="utf-8")
            print(json.dumps({"run_dir": str(output.resolve()), "source": "mock", "status": "completed",
                              "warning": result["warning"], "requests": result["request_count"],
                              "scheduled_reviews": result["scheduled_reviews"], "paid_calls": 0,
                              "cost_usd": 0, "main_far": {k: v["far"]["estimate"] for k, v in result["main"].items()},
                              "main_cpa": {k: v["cpa"]["estimate"] for k, v in result["main"].items()}}, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
