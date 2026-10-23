"""Offline controls plus fail-closed fake-live WP1/WP2 commands."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import tomllib
from uuid import uuid4

from .analysis import analyze_run
from .common import canonical
from .config import load_config
from .gates import (
    calibration_assessment,
    formation_assessment,
    smoke_integrity_assessment,
    validate_smoke_operator_review,
)
from .live_config import STAGE_ORDER, load_live_config
from .orchestration import (
    CANONICAL_ANALYSIS_RULES,
    CANONICAL_RETRY_EXCLUSION_POLICY,
    DurableRun,
    StagedExecutor,
    build_request_plan,
    pilot_freeze_assessment,
    write_protocol_freeze,
)
from .runner import run_experiment
from .staged_requests import (
    ScientificRequestFactory,
    audit_prompt_inventory,
    evidence_policy_fake_provider,
    protocol_prompt_inventory,
)
from .validation import validate_controls
from .visibility import ROOT


FAKE_LIVE_CONFIG = ROOT / "configs/fake_live.toml"


def _declared_source(path: Path) -> str | None:
    with path.open("rb") as handle:
        values = tomllib.load(handle)
    source = values.get("source")
    return source if isinstance(source, str) else None


def _write_exclusive(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(canonical(value) + "\n")


def _live_summary(config, plan) -> dict:
    return {
        "source": config.source,
        "warning": config.warning,
        "run_id": config.run_id,
        "run_type": config.run_type,
        "split": config.split,
        "authorized_stage_ceiling": config.stage,
        "model": config.model,
        "model_settings": dict(config.model_settings),
        "plan_hash": plan.plan_hash,
        "implementation_hash": plan.implementation_hash,
        "planned_counts": plan.planned_counts(),
        "limits": config.limits.to_dict(),
        "pricing": config.pricing.to_dict(),
        "approved_budget_usd": str(config.authorization.approved_budget_usd),
        "empirical_dispatch_available": False,
        "human_stop": (
            "This build can inspect an empirical config and adapter payload, but cannot dispatch it. "
            "Activation still requires an exact model/settings decision, current pricing, staged caps, "
            "separate bound authorization, and separately configured secure credentials."
        ),
    }


def _run_fake_stage(config_path: Path, output: Path | None, stage: str,
                    resume: bool) -> dict:
    config = load_live_config(config_path)
    if config.source != "fake-live":
        raise RuntimeError(
            "EMPIRICAL DISPATCH UNAVAILABLE: this command is intentionally restricted to fake-live; "
            "no credential loader or default network transport exists"
        )
    plan = build_request_plan(config)
    if not config.permits_stage(stage):
        raise ValueError("requested stage exceeds the explicitly authorized stage ceiling")
    if not any(STAGE_ORDER.index(request.stage) <= STAGE_ORDER.index(stage)
               for request in plan.requests):
        raise ValueError(f"the immutable {config.run_type} plan has no requests through {stage}")
    destination = output or ROOT / "runs/fake-live" / uuid4().hex
    run = DurableRun.open(destination, config, plan, resume=resume)
    provider = evidence_policy_fake_provider(config, plan)
    report = StagedExecutor(
        run, provider, ScientificRequestFactory(config, plan)
    ).execute(stage)
    return {
        "run_dir": str(destination.resolve()),
        "source": config.source,
        "warning": config.warning,
        "paid_calls": 0,
        "network_calls": 0,
        "fake_attempts_this_invocation": len(provider.calls),
        "report": report.to_dict(),
        "gates": {name: event["passed"] for name, event in run.gate_events().items()},
    }


def _gate(args) -> dict:
    config = load_live_config(args.config)
    if config.source != "fake-live":
        raise RuntimeError(
            "EMPIRICAL RUN MUTATION UNAVAILABLE: gate recording is fake-live-only until the "
            "separately authorized transport/credential activation step"
        )
    plan = build_request_plan(config)
    run = DurableRun.open(args.run_dir, config, plan, resume=True)
    if args.gate != "protocol_freeze" and args.freeze_artifact is not None:
        raise ValueError("--freeze-artifact belongs only to the protocol_freeze gate")
    if args.gate != "protocol_freeze" and args.pilot_run_dir is not None:
        raise ValueError("--pilot-run-dir belongs only to the protocol_freeze gate")
    pilot_run = None
    if args.gate == "calibration":
        if args.decision is not None or args.evidence is not None:
            raise ValueError("calibration uses the frozen computed thresholds; do not override its decision")
        evidence = calibration_assessment(run)
        passed = evidence["passed"]
    elif args.gate == "formation":
        if args.decision is not None or args.evidence is not None:
            raise ValueError("formation uses the frozen computed 3/5 rule; do not override its decision")
        evidence = formation_assessment(run)
        passed = evidence["passed"]
    elif args.gate == "smoke_integrity":
        if args.decision is None or args.evidence is None:
            raise ValueError("smoke_integrity requires --decision and a human --evidence JSON file")
        operator = json.loads(args.evidence.read_text(encoding="utf-8"))
        operator = validate_smoke_operator_review(
            operator, require_pass=args.decision == "pass")
        mechanical = smoke_integrity_assessment(run)
        passed = args.decision == "pass"
        if passed and not mechanical["automatic_checks_passed"]:
            raise ValueError("cannot pass smoke review while mechanical integrity checks fail")
        evidence = {"mechanical": mechanical, "operator_review": operator,
                    "operator_decision": args.decision}
    elif args.gate == "protocol_freeze":
        if args.decision is not None or args.evidence is not None:
            raise ValueError(
                "protocol_freeze derives its passing decision from --freeze-artifact; "
                "do not provide --decision or --evidence"
            )
        if args.freeze_artifact is None:
            raise ValueError("protocol_freeze requires --freeze-artifact")
        artifact = json.loads(args.freeze_artifact.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict) or not artifact:
            raise ValueError("freeze artifact must be a nonempty JSON object")
        if config.run_type == "confirmation":
            if args.pilot_run_dir is None:
                raise ValueError(
                    "confirmation protocol_freeze requires --pilot-run-dir for lineage verification")
            pilot_run = DurableRun.inspect(args.pilot_run_dir)
        elif args.pilot_run_dir is not None:
            raise ValueError("pilot tooling freeze does not accept --pilot-run-dir")
        evidence = {"freeze_artifact": artifact}
        passed = True
    else:
        raise ValueError(f"unsupported gate: {args.gate}")
    event = run.record_gate_decision(
        args.gate, passed=passed, evidence=evidence,
        decided_by=args.decided_by, note=args.note, pilot_run=pilot_run,
    )
    return {
        "run_dir": str(args.run_dir.resolve()),
        "source": config.source,
        "warning": config.warning,
        "gate": args.gate,
        "passed": passed,
        "evidence_hash": event["evidence_hash"],
        "evidence": evidence,
    }


def _freeze_prompts(config) -> dict[str, str]:
    return protocol_prompt_inventory(config)


def _analysis_rules() -> dict:
    return dict(CANONICAL_ANALYSIS_RULES)


def _retry_exclusion_policy() -> dict:
    return dict(CANONICAL_RETRY_EXCLUSION_POLICY)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reciprocal-verification harness; empirical dispatch is unavailable"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "estimate", "run"):
        item = sub.add_parser(name)
        item.add_argument("--config", type=Path, default=ROOT / "configs/mock.toml")
        if name == "run":
            item.add_argument("--output", type=Path)
            item.add_argument("--control", choices=("evidence_policy", "always_accept", "always_reject"))
            item.add_argument("--stage", choices=STAGE_ORDER)
            item.add_argument("--resume", action="store_true")
    item = sub.add_parser("calibrate", help="run only fake-live capability calibration")
    item.add_argument("--config", type=Path, default=FAKE_LIVE_CONFIG)
    item.add_argument("--output", type=Path)
    item.add_argument("--resume", action="store_true")

    item = sub.add_parser("inspect-live", help="render and audit prompts without dispatch")
    item.add_argument("--config", type=Path, default=FAKE_LIVE_CONFIG)
    item.add_argument("--output", type=Path)

    item = sub.add_parser("gate", help="record a durable fake-live stage decision")
    item.add_argument("--config", type=Path, default=FAKE_LIVE_CONFIG)
    item.add_argument("--run-dir", required=True, type=Path)
    item.add_argument("--gate", required=True,
                      choices=("calibration", "smoke_integrity", "formation", "protocol_freeze"))
    item.add_argument("--decided-by", required=True)
    item.add_argument("--decision", choices=("pass", "fail"))
    item.add_argument("--evidence", type=Path)
    item.add_argument("--freeze-artifact", type=Path)
    item.add_argument(
        "--pilot-run-dir", type=Path,
        help="required for confirmation protocol_freeze to reverify durable pilot lineage",
    )
    item.add_argument("--note", default="")

    item = sub.add_parser("freeze", help="write an immutable no-dispatch protocol freeze")
    item.add_argument("--config", required=True, type=Path)
    item.add_argument("--output", required=True, type=Path)
    item.add_argument("--annotation-sample-seed", required=True)
    item.add_argument("--pilot-split", required=True)
    item.add_argument("--confirmation-split", required=True)
    item.add_argument(
        "--pilot-run-dir", type=Path,
        help="required for confirmation: verified completed five-block pilot lineage",
    )
    item.add_argument(
        "--pilot-decision", type=Path,
        help="required for confirmation: human Gate-4 checklist bound to pilot assessment",
    )

    item = sub.add_parser("assess-pilot", help="compute no-dispatch Gate-4 pilot evidence")
    item.add_argument("--pilot-run-dir", required=True, type=Path)

    item = sub.add_parser("analyze")
    item.add_argument("--run-dir", required=True, type=Path)
    item.add_argument("--allow-incomplete", action="store_true")
    item.add_argument("--empirical", action="store_true", help="Intentionally rejected for mock data")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "analyze":
            result = analyze_run(args.run_dir, args.allow_incomplete, args.empirical)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "inspect-live":
            config = load_live_config(args.config)
            plan = build_request_plan(config)
            result = {**_live_summary(config, plan), "prompt_audit": audit_prompt_inventory(config, plan)}
            if not (result["prompt_audit"]["within_input_cap"]
                    and result["prompt_audit"]["within_context_with_output_allowance"]):
                raise RuntimeError("projected longest prompt exceeds a configured input/context ceiling")
            if args.output:
                _write_exclusive(args.output, result)
                print(json.dumps({**_live_summary(config, plan),
                                  "prompt_audit_file": str(args.output.resolve()),
                                  "maximum_conservative_input_token_bound": result["prompt_audit"]["maximum_conservative_input_token_bound"]}, indent=2))
            else:
                print(json.dumps(result, indent=2))
            return 0
        if args.command == "calibrate":
            print(json.dumps(_run_fake_stage(args.config, args.output, "calibration", args.resume), indent=2))
            return 0
        if args.command == "gate":
            print(json.dumps(_gate(args), indent=2))
            return 0
        if args.command == "assess-pilot":
            pilot_run = DurableRun.inspect(args.pilot_run_dir)
            if pilot_run.config.run_type != "pilot":
                raise ValueError("assess-pilot requires a pilot run directory")
            with pilot_run.execution_lock():
                assessment = pilot_freeze_assessment(pilot_run)
            print(json.dumps(assessment, indent=2))
            return 0
        if args.command == "freeze":
            config = load_live_config(args.config)
            plan = build_request_plan(config)
            pilot_run = (None if args.pilot_run_dir is None
                         else DurableRun.inspect(args.pilot_run_dir))
            pilot_decision = (None if args.pilot_decision is None else
                              json.loads(args.pilot_decision.read_text(encoding="utf-8")))
            result = write_protocol_freeze(
                args.output, config=config, plan=plan,
                prompts=_freeze_prompts(config), analysis_rules=_analysis_rules(),
                annotation_guide=(ROOT / "analysis/annotation_guide.md").read_text(encoding="utf-8"),
                annotation_sample_seed=args.annotation_sample_seed,
                retry_exclusion_policy=_retry_exclusion_policy(),
                namespaces={"pilot": args.pilot_split,
                            "confirmation": args.confirmation_split},
                pilot_run=pilot_run,
                pilot_freeze_decision=pilot_decision,
            )
            print(json.dumps({"freeze_file": str(args.output.resolve()),
                              "freeze_id": result["freeze_id"],
                              **_live_summary(config, plan)}, indent=2))
            return 0

        source = _declared_source(args.config)
        if source in {"fake-live", "empirical-live"}:
            config = load_live_config(args.config)
            plan = build_request_plan(config)
            if args.command == "estimate":
                print(json.dumps(_live_summary(config, plan), indent=2))
                return 0
            if args.command == "validate":
                print(json.dumps({**_live_summary(config, plan),
                                  "offline_controls": validate_controls()}, indent=2))
                return 0
            if args.control:
                raise ValueError("--control belongs only to the closed mock runner")
            stage = args.stage or config.stage
            print(json.dumps(_run_fake_stage(args.config, args.output, stage, args.resume), indent=2))
            return 0

        config = load_config(args.config)
        if args.command == "validate":
            print(json.dumps(validate_controls(), indent=2))
        elif args.command == "estimate":
            print(json.dumps({"source": "mock", "note": "No calibration or retries included; no API pricing required.",
                              **config.counts()}, indent=2))
        else:
            if args.stage or args.resume:
                raise ValueError("--stage and --resume belong only to staged fake-live runs")
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
