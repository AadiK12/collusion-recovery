"""Export paper tables from a completed, independently verified empirical run.

Usage from the repository root:
  PYTHONPATH=src python3 paper/latex/export_verified_results.py RUN_DIRECTORY OUTPUT_DIRECTORY

The experiment owner must first produce empirical_analysis.json. This helper
does not run models, alter scientific artifacts, write narrative conclusions,
or insert its tables into the manuscript automatically.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from collusion_recovery.common import digest


ARM_LABELS = {
    "A": "Rejecting peer, restored evidence",
    "B": "Accepting peer, restored evidence",
    "C": "Rejecting peer, correction",
    "D": "Accepting peer, correction",
    "E": "Accepting peer, correction and reset",
    "F": "Accepting peer, constrained evidence",
}
CONTRAST_LABELS = {
    "B_minus_A": r"$B-A$ (primary)",
    "B_minus_F": r"$B-F$ (restoration)",
    "D_minus_B": r"$D-B$ (correction)",
    "C_minus_A": r"$C-A$ (correction)",
    "correction_interaction": r"$(D-B)-(C-A)$",
    "E_minus_D": r"$E-D$ (added reset)",
}


def load_verified_summary(run: Path) -> dict:
    summary = json.loads((run / "empirical_analysis.json").read_text())
    body = {key: value for key, value in summary.items() if key != "analysis_hash"}
    if summary.get("analysis_hash") != digest(body):
        raise ValueError("Analysis self-hash does not match")
    expected = {
        "artifact_type": "controlled_peer_recovery_empirical_summary_v1",
        "protocol_version": "controlled-peer-recovery-v1",
        "source": "empirical-live",
        "empirical": True,
        "independent_blocks": 5,
        "scheduled_study_rows": 340,
        "bootstrap_replicates": 10000,
        "raw_evidence_replay_verified": True,
        "raw_provider_outputs_independently_rescored": True,
        "calibration_excluded_from_estimands": True,
    }
    if any(summary.get(key) != value for key, value in expected.items()):
        raise ValueError("Paper export requires the complete verified empirical analysis")
    from experiments.controlled_peer_recovery_live import _artifact, verify_control_run

    verified = verify_control_run(run)
    if not (verified["complete"] and verified["empirical"]):
        raise ValueError("Run is not complete and empirical")
    for key in ("run_id", "status", "journal_tail", "proposal_hash", "projection_hash",
                "plan_hash", "implementation_hash"):
        if summary.get(key) != verified[key]:
            raise ValueError(f"Analysis does not match the verified run: {key}")
    for filename, kind in (
        ("reviews", "controlled_peer_recovery_reviews_v1"),
        ("calibration", "controlled_peer_recovery_calibration_rows_v1"),
        ("checkpoints", "controlled_peer_recovery_checkpoints_v1"),
    ):
        artifact = _artifact(run / f"{filename}.json", kind)
        if summary.get(f"{filename}_artifact_hash") != artifact["artifact_hash"]:
            raise ValueError(f"Analysis has stale {filename} provenance")
    return summary


def aggregate_rate(values: list[dict]) -> dict:
    """Equal block weighting; unknowns never become negative decisions."""
    return {
        "estimate": (mean(value["estimate"] for value in values)
                     if all(value["estimate"] is not None for value in values) else None),
        "bounds": [mean(value["bounds"][i] for value in values) for i in (0, 1)],
        **({key: sum(value[key] for value in values)
            for key in ("scheduled", "valid", "missing_or_invalid", "approvals")}
           if all("scheduled" in value for value in values) else {}),
    }


def point(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return r"\textemdash"
    scaled = 100 * value
    if abs(scaled) < 0.05:
        scaled = 0.0
    return f"{scaled:+.1f}" if signed else f"{scaled:.1f}"


def interval(value: list[float] | None) -> str:
    return (f"[{point(value[0])}, {point(value[1])}]" if value is not None
            else r"\textemdash")


def rate_cell(value: dict) -> str:
    return point(value["estimate"]) if value["estimate"] is not None else interval(value["bounds"])


def export(run: Path, output: Path) -> None:
    summary = load_verified_summary(run)
    blocks = summary["per_block_recovery"]
    if set(map(str, blocks)) != {str(index) for index in range(5)}:
        raise ValueError("Expected all five paired blocks")
    absolute = {
        arm: {metric: aggregate_rate([block[arm][metric] for block in blocks.values()])
              for metric in ("far", "cpa", "balanced_accuracy")}
        for arm in ARM_LABELS
    }
    for arm in absolute.values():
        if any(arm[metric]["scheduled"] != 20 for metric in ("far", "cpa")):
            raise ValueError("Each arm must include 20 correct and 20 faulty diagnostics")
    absolute_rows = [
        f"{arm} & {ARM_LABELS[arm]} & "
        + " & ".join(rate_cell(values[metric]) for metric in ("far", "cpa", "balanced_accuracy"))
        + r" \\"
        for arm, values in absolute.items()
    ]
    contrast_rows = []
    for name, label in CONTRAST_LABELS.items():
        value = summary["recovery_contrasts"][name]["far"]
        contrast_rows.append(
            f"{label} & {point(value['estimate'], signed=True)} & "
            f"{interval(value['claim_interval_ci95'])} & {interval(value['bounds'])}" + r" \\"
        )
    tables = r"""\begin{table}[htbp]
\centering\small
\begin{tabularx}{\linewidth}{@{}lXrrr@{}}
\toprule
Arm & Recovery condition & FAR (\%) & CPA (\%) & BA (\%) \\
\midrule
""" + "\n".join(absolute_rows) + r"""
\bottomrule
\end{tabularx}
\caption{Absolute recovery performance, equally weighted across five paired blocks.
Each arm contains 20 faulty and 20 correct claims. FAR is false approval rate,
CPA is correct-claim approval, and BA is balanced accuracy. Bracketed entries
are worst-case bounds for unknown outcomes, rather than confidence intervals.}
\label{tab:recovery-absolute}
\end{table}

\begin{table}[htbp]
\centering\small
\begin{tabular}{@{}lrrr@{}}
\toprule
Contrast & FAR change & 95\% interval & Outcome bounds \\
\midrule
""" + "\n".join(contrast_rows) + r"""
\bottomrule
\end{tabular}
\caption{Paired recovery contrasts in percentage points. Negative FAR changes
indicate fewer faulty claims accepted. Intervals are the prespecified outer hull
of paired-block bootstrap and Student-$t$ intervals; secondary intervals are
pointwise. Outcome bounds retain all scheduled decisions and allow unknown
outcomes to take either verdict. A missing estimate or interval is shown by a dash.}
\label{tab:recovery-contrasts}
\end{table}
"""
    facts = {
        "run_id": summary["run_id"],
        "analysis_hash": summary["analysis_hash"],
        "source": "verified-empirical-analysis",
        "independent_blocks": 5,
        "scheduled_study_rows": 340,
        "valid_rows": summary["valid_rows"],
        "invalid_or_missing": summary["invalid_or_missing"],
        "absolute_recovery": absolute,
        "recovery_contrasts": summary["recovery_contrasts"],
        "formation_checks": summary["formation_checks"],
        "claim_assessment": summary["claim_assessment"],
        "directional_claims_allowed": summary["directional_claims_allowed"],
        "transport_ambiguity": summary["transport_ambiguity"],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "recovery_tables.tex").write_text(tables, encoding="utf-8")
    (output / "paper_numbers.json").write_text(json.dumps(facts, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": summary["run_id"], "output": str(output.resolve()),
                      "directional_claims_allowed": summary["directional_claims_allowed"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()
    export(args.run_directory.resolve(), args.output_directory.resolve())
