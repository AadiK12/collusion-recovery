"""Post-run V2 development report export; never executes an experiment.

The caller supplies SHA-256 hashes frozen before scored outcomes are disclosed.
Independent review and blinding remain procedural requirements; reviewer IDs
and Boolean declarations are not proof that those requirements were satisfied.
This exporter preserves the assessor's conservative provenance/repair fields.
It writes only to a previously nonexistent directory outside the audited run.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence

from collusion_recovery.common import canonical, digest
from . import exploratory_collusion_v2 as study
from . import exploratory_collusion_v2_assessment as assessment
from . import exploratory_collusion_v2_live as live


VERSION = "exploratory-collusion-v2-postrun-report-v1"
ANNOTATION_KEYS = {"reviewer_id", "outcome_blinded", "reciprocal_agreement", "proposal", "uptake",
                   "agreement_covers_late_window", "no_unresolved_retraction"}
SCOPE = {"capability_requests": 24, "development_requests": 168, "maximum_attempts": 192,
         "retries": 0, "validation_requests": 0, "repair_requests": 0}


class ReportExportError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load(path: Path) -> tuple[bytes, dict[str, Any]]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ReportExportError("input JSON contains duplicate keys")
            result[key] = value
        return result

    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReportExportError("input must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ReportExportError("input JSON must be an object")
    return raw, value


def _annotation_bundle(value: dict[str, Any]) -> dict[int, dict[str, Any]]:
    if set(value) != {"reviewer_id", "dyads"}:
        raise ReportExportError("annotation file must contain only reviewer_id and dyads")
    reviewer = value["reviewer_id"]
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip():
        raise ReportExportError("reviewer_id must be a nonempty trimmed string")
    if not isinstance(value["dyads"], list) or len(value["dyads"]) != 2:
        raise ReportExportError("each reviewer must annotate exactly both development dyads")
    indexed = {}
    for row in value["dyads"]:
        if not isinstance(row, dict) or set(row) != {"dyad", "annotation"}:
            raise ReportExportError("each annotation row must contain dyad and annotation")
        dyad = row["dyad"]
        if type(dyad) is not int or dyad not in {0, 1} or dyad in indexed:
            raise ReportExportError("development dyad IDs must be exactly 0 and 1")
        annotation = row["annotation"]
        if not isinstance(annotation, dict) or set(annotation) != ANNOTATION_KEYS:
            raise ReportExportError("annotation does not match the frozen assessment schema")
        if annotation["reviewer_id"] != reviewer:
            raise ReportExportError("inner reviewer_id differs from the file reviewer_id")
        for field in ("outcome_blinded", "reciprocal_agreement", "agreement_covers_late_window", "no_unresolved_retraction"):
            if type(annotation[field]) is not bool:
                raise ReportExportError("annotation labels must be Boolean")
        if annotation["outcome_blinded"] is not True:
            raise ReportExportError("both reviewers must declare outcome-blinded review")
        if annotation["reciprocal_agreement"] is False:
            if (annotation["proposal"] is not None or annotation["uptake"] is not None
                    or annotation["agreement_covers_late_window"] is not False
                    or annotation["no_unresolved_retraction"] is not False):
                raise ReportExportError("negative annotations require null spans and false scope/retraction labels")
        else:
            for field in ("proposal", "uptake"):
                span = annotation[field]
                if not isinstance(span, dict) or set(span) != {"episode", "round", "speaker", "start", "end", "quote"}:
                    raise ReportExportError("positive annotations require exact assessment span fields")
                if (any(type(span[key]) is not int for key in ("episode", "round", "start", "end"))
                        or not 0 <= span["episode"] < 6 or not 1 <= span["round"] <= 5
                        or span["speaker"] not in study.ROLES or not isinstance(span["quote"], str)
                        or not 0 <= span["start"] < span["end"]):
                    raise ReportExportError("annotation span indices or speaker are invalid")
        indexed[dyad] = copy.deepcopy(annotation)
    return indexed


def _require_completed(audit: dict[str, Any]) -> None:
    if not isinstance(audit, dict) or audit.get("integrity_verified") is not True:
        raise ReportExportError("independent run audit did not verify integrity")
    if audit.get("status") != live.TERMINAL:
        raise ReportExportError("development has not reached its required terminal hard stop")
    for key in ("accounted_attempts", "dispatch_reservations", "logical_requests_completed"):
        if type(audit.get(key)) is not int or audit[key] != 192:
            raise ReportExportError("exactly 192 accounted and usable requests are required")
    if type(audit.get("open_reservations")) is not int or audit["open_reservations"] != 0:
        raise ReportExportError("open reservations prohibit post-run scoring")
    ledger = audit.get("ledger", {})
    consumed, reserved = ledger.get("consumed", {}), ledger.get("reserved", {})
    if type(consumed.get("requests")) is not int or consumed["requests"] != 192:
        raise ReportExportError("audit ledger count differs from the completed schedule")
    for key in ("requests", "input_tokens", "output_tokens"):
        if type(reserved.get(key)) is not int or reserved[key] != 0:
            raise ReportExportError("audit ledger still contains reservations")
    try:
        if Decimal(reserved["cost_usd"]) != 0:
            raise ReportExportError("audit ledger still contains reserved cost")
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise ReportExportError("audit reserved-cost accounting is malformed") from exc
    trace = audit.get("trace")
    if not isinstance(trace, dict) or trace.get("source") != "empirical_unverified":
        raise ReportExportError("only a completed empirical run may produce this report")


def export_report(release_path: str | Path, run_dir: str | Path,
                  annotation_paths: Sequence[str | Path], output_dir: str | Path,
                  expected_annotation_sha256: Sequence[str]) -> dict[str, Any]:
    """Audit then score a COPY of the trace using two previously frozen reviews.

    ``expected_annotation_sha256`` is required. Computing hashes for the first
    time after viewing outcomes would not establish a frozen blinded handoff.
    No callback/provider is accepted and no experiment execution path exists.
    """
    if (isinstance(annotation_paths, (str, bytes)) or len(annotation_paths) != 2
            or isinstance(expected_annotation_sha256, (str, bytes))
            or len(expected_annotation_sha256) != 2):
        raise ReportExportError("exactly two annotation paths and two frozen hashes are required")
    if any(not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
           for value in expected_annotation_sha256):
        raise ReportExportError("annotation hashes must be lowercase SHA-256 values")
    release_path, run_dir = Path(release_path).resolve(), Path(run_dir).resolve()
    paths = tuple(Path(path).resolve() for path in annotation_paths)
    output_dir = Path(output_dir).absolute()
    if output_dir.exists() or output_dir.is_symlink():
        raise ReportExportError("output directory must not already exist")
    output_dir = output_dir.resolve()
    if output_dir == run_dir or run_dir in output_dir.parents:
        raise ReportExportError("output directory must be outside the preserved empirical run")
    if not output_dir.parent.is_dir():
        raise ReportExportError("output directory parent must already exist")
    if len(set(paths)) != 2:
        raise ReportExportError("two distinct annotation files are required")
    release_raw, release = _load(release_path)
    if release.get("artifact_type") != live.VERSION or release.get("scope") != SCOPE:
        raise ReportExportError("release is not the exact capability-development pilot")
    if release.get("release_hash") != digest({key: value for key, value in release.items() if key != "release_hash"}):
        raise ReportExportError("release self-hash is invalid")
    loaded, bundles = [], []
    for path, expected in zip(paths, expected_annotation_sha256):
        raw, value = _load(path)
        if _sha(raw) != expected:
            raise ReportExportError("annotation source does not match its frozen SHA-256")
        bundles.append(_annotation_bundle(value))
        loaded.append((raw, value))
    reviewers = [value["reviewer_id"] for _, value in loaded]
    if len(set(reviewers)) != 2:
        raise ReportExportError("two distinct reviewer IDs are required")

    # This is the only path to the run data. It verifies raw outputs, source
    # snapshots, accounting, completion, and derived artifacts without dispatch.
    audited = live.audit_run(release, run_dir)
    _require_completed(audited)
    config = study.StudyConfig("development", release["development_namespace"])
    if release["development_plan"]["config"] != config.to_dict():
        raise ReportExportError("saved development configuration differs from the fixed source")
    annotated = copy.deepcopy(audited["trace"])
    dyads = annotated.get("dyads")
    if (not isinstance(dyads, list) or len(dyads) != 2
            or any(not isinstance(row, dict) or type(row.get("dyad")) is not int for row in dyads)
            or {row["dyad"] for row in dyads} != {0, 1}):
        raise ReportExportError("audited trace must contain exactly both development dyads")
    for row in dyads:
        if row.get("annotations") != []:
            raise ReportExportError("audited raw trace already contains annotations")
        row["annotations"] = [bundle[row["dyad"]] for bundle in bundles]
    operational = assessment.assess_formation(config, annotated)

    # Preserve exact original UTF-8 text inside immutable JSON envelopes. This
    # retains whitespace and source hashes while reusing the existing fsynced
    # JSON writer; parsed copies also remain convenient to inspect/re-score.
    artifacts = {"annotated_trace.json": annotated}
    annotation_bindings = []
    for index, (path, (raw, value)) in enumerate(zip(paths, loaded), 1):
        filename = f"frozen_annotation_{index}.json"
        envelope = {"artifact_type": "frozen_independent_v2_annotation",
                    "reviewer_id": value["reviewer_id"], "source_path": str(path),
                    "source_sha256": _sha(raw), "source_utf8": raw.decode("utf-8"),
                    "annotation": value, "annotation_semantic_hash": digest(value)}
        artifacts[filename] = envelope
        annotation_bindings.append({"reviewer_id": value["reviewer_id"], "source_path": str(path),
            "source_sha256": _sha(raw), "semantic_hash": digest(value), "frozen_copy": filename})
    artifact_hashes = {name: _sha((canonical(value) + "\n").encode("utf-8")) for name, value in artifacts.items()}
    provenance = {key: copy.deepcopy(value) for key, value in audited.items() if key != "trace"}
    report_body = {"artifact_type": VERSION, "protocol_version": study.VERSION,
        "run_id": release["run_id"], "run_directory": str(run_dir),
        "release_path": str(release_path), "release_hash": release["release_hash"],
        "release_file_sha256": _sha(release_raw), "source_trace_hash": digest(audited["trace"]),
        "annotated_trace_hash": digest(annotated), "provenance_audit": provenance,
        "operational_assessment": operational, "annotation_bindings": annotation_bindings,
        "copied_artifact_sha256": artifact_hashes,
        "annotation_procedure_independently_verified": False,
        "interpretation": "The separate run audit verifies execution provenance. The operational assessment "
            "is copied without upgrading its provenance, empirical-formation, causal, or repair-authority fields. "
            "Frozen hashes bind review files, not the independence or blinding of their authors. "
            "No validation, probe, repair, or additional paid call is executed by this exporter.",
        "exporter_source_sha256": _sha(Path(__file__).read_bytes())}
    report = {**report_body, "report_hash": digest(report_body)}
    if release_path.read_bytes() != release_raw or any(path.read_bytes() != raw for path, (raw, _) in zip(paths, loaded)):
        raise ReportExportError("release or reviewer files changed during export preparation")
    output_dir.mkdir(mode=0o700, exist_ok=False)
    for name, value in artifacts.items():
        live.shared._atomic_json(output_dir / name, value, exclusive=True)
        if _sha((output_dir / name).read_bytes()) != artifact_hashes[name]:
            raise ReportExportError("saved artifact bytes differ from the report binding")
    # Report is the final completion marker. Partial exports are retained and
    # cannot be silently overwritten or resumed into a different report.
    live.shared._atomic_json(output_dir / "report.json", report, exclusive=True)
    report_sha256 = _sha((output_dir / "report.json").read_bytes())
    return {"output_directory": str(output_dir), "report_path": str(output_dir / "report.json"),
            "report_sha256": report_sha256, "report_hash": report["report_hash"],
            "behavioral_feasibility_gate": operational["behavioral_feasibility_gate"],
            "repairs_allowed": operational["repairs_allowed"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, action="append", required=True)
    parser.add_argument("--annotation-sha256", action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(canonical(export_report(args.release, args.run_dir, args.annotation, args.output_dir,
                                  args.annotation_sha256)))


if __name__ == "__main__":
    main()
