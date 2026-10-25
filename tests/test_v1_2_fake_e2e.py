from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collusion_recovery.analysis import (
    analyze_artifact_run,
    load_durable_and_rescore,
    summarize_result,
)
from collusion_recovery.common import canonical
from collusion_recovery.gates import (
    SMOKE_REVIEW_BOOLEAN_FIELDS,
    calibration_assessment,
    formation_assessment,
    smoke_integrity_assessment,
)
from collusion_recovery.live_config import load_live_config
from collusion_recovery.orchestration import (
    DurableRun,
    ResumeError,
    StagedExecutor,
    build_request_plan,
)
from collusion_recovery.providers.base import FakeOutcome
from collusion_recovery.staged_requests import (
    ScientificRequestFactory,
    evidence_policy_fake_provider,
)


ROOT = Path(__file__).resolve().parents[1]


def _refresh_event_indexes(run):
    """Incrementally index append-only events for this large integration test."""
    cursor = getattr(run, "_e2e_index_cursor", 0)
    if cursor > len(run.events):
        cursor = 0
        run._e2e_results = []
        run._e2e_results_by_identity = {}
        run._e2e_checkpoints = {}
        run._e2e_gates = {}
    if not hasattr(run, "_e2e_results"):
        run._e2e_results = []
        run._e2e_results_by_identity = {}
        run._e2e_checkpoints = {}
        run._e2e_gates = {}
    for event in run.events[cursor:]:
        kind = event["kind"]
        if kind == "result":
            run._e2e_results.append(event)
            run._e2e_results_by_identity.setdefault(event["identity"], []).append(event)
        elif kind == "checkpoint":
            prior = run._e2e_checkpoints.get(event["checkpoint_id"])
            if prior is not None and prior["checkpoint_hash"] != event["checkpoint_hash"]:
                raise ResumeError("checkpoint identity was mutated")
            run._e2e_checkpoints[event["checkpoint_id"]] = event
        elif kind == "gate":
            if event["gate"] in run._e2e_gates:
                raise ResumeError("a gate decision was recorded more than once")
            run._e2e_gates[event["gate"]] = event
    run._e2e_index_cursor = len(run.events)


def _indexed_result_events(run, identity=None):
    _refresh_event_indexes(run)
    if identity is None:
        return list(run._e2e_results)
    return list(run._e2e_results_by_identity.get(identity, ()))


def _indexed_checkpoint_events(run):
    _refresh_event_indexes(run)
    return dict(run._e2e_checkpoints)


def _indexed_gate_events(run):
    _refresh_event_indexes(run)
    return dict(run._e2e_gates)


def no_network_or_empirical_provider():
    stack = ExitStack()
    for name in (
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
        "collusion_recovery.providers.openai.OpenAISDKTransport.send",
        "collusion_recovery.providers.openai.OpenAIResponsesProvider.complete",
    ):
        stack.enter_context(
            patch(name, side_effect=AssertionError(
                "network and empirical providers are forbidden in this test"
            ))
        )
    # The production accessors intentionally favor simple replay. This full
    # 1,580-request test uses equivalent append-only indexes so it does not add
    # quadratic test-suite time. Dedicated durability tests cover fsyncs.
    stack.enter_context(patch.object(
        DurableRun, "result_events", _indexed_result_events
    ))
    stack.enter_context(patch.object(
        DurableRun, "checkpoint_events", _indexed_checkpoint_events
    ))
    stack.enter_context(patch.object(
        DurableRun, "gate_events", _indexed_gate_events
    ))
    stack.enter_context(patch("collusion_recovery.orchestration.os.fsync"))
    stack.enter_context(patch("collusion_recovery.orchestration._fsync_directory"))
    return stack


def formation_positive_fake_provider(config, plan):
    """Return the local control with a deterministic positive formation marker.

    The stock evidence-policy control correctly rejects every constrained packet,
    which makes the computed formation gate fail and therefore cannot exercise
    the post-gate stages.  This test-only override makes both agents ACCEPT in
    the last three constrained episodes.  It remains explicitly fake-live and
    exists only to validate the complete gated plumbing.
    """
    provider = evidence_policy_fake_provider(config, plan)
    output = canonical({
        "verdict": "ACCEPT",
        "evidence_record_ids": [],
        "brief_reason": (
            "Deterministic positive-formation fake-live control; not empirical."
        ),
    })
    for spec in plan.requests:
        if (spec.phase == "formation" and spec.action == "verdict"
                and spec.metadata.get("history") == "C"
                and spec.metadata.get("episode") in {7, 8, 9}):
            provider._script[spec.identity] = [FakeOutcome(output_text=output)]
    return provider


class ProtocolV12FakeEndToEndTests(unittest.TestCase):
    def test_generic_five_block_pilot_completes_and_analyzes_offline(self):
        config = load_live_config(ROOT / "configs/fake_live_v1_2.toml")
        plan = build_request_plan(config)
        provider = formation_positive_fake_provider(config, plan)
        factory = ScientificRequestFactory(config, plan)

        self.assertEqual(plan.planned_counts()["logical_requests"], 1580)
        self.assertEqual(config.protocol_version, "v1.2")
        self.assertEqual(config.source, "fake-live")
        self.assertFalse(config.authorization.allow_paid_calls)

        with tempfile.TemporaryDirectory() as directory, no_network_or_empirical_provider():
            run_path = Path(directory) / "v1-2-five-block-pilot"
            run = DurableRun.open(run_path, config, plan)
            executor = StagedExecutor(run, provider, factory)

            calibration = executor.execute("calibration")
            self.assertEqual(calibration.status, "completed_stage")
            self.assertEqual(calibration.dispatched_attempts, 20)
            calibration_gate = calibration_assessment(run)
            self.assertTrue(calibration_gate["passed"])
            run.record_gate_decision(
                "calibration",
                passed=calibration_gate["passed"],
                evidence=calibration_gate,
                decided_by="offline-v1.2-end-to-end-test",
                note="Computed fake-live calibration gate only.",
            )

            smoke = executor.execute("smoke")
            self.assertEqual(smoke.status, "completed_stage")
            self.assertEqual(smoke.dispatched_attempts, 192)
            self.assertEqual(smoke.completed_logical_requests, 212)
            mechanical = smoke_integrity_assessment(run)
            self.assertTrue(mechanical["automatic_checks_passed"])
            operator_review = {
                "reviewer": "offline-v1.2-end-to-end-test",
                "reviewed_at": "2026-10-25T12:00:00-07:00",
                "notes": "Synthetic fake-live transcript checklist only.",
                **{name: True for name in SMOKE_REVIEW_BOOLEAN_FIELDS},
            }
            smoke_evidence = {
                "mechanical": mechanical,
                "operator_review": operator_review,
                "operator_decision": "pass",
            }
            run.record_gate_decision(
                "smoke_integrity",
                passed=True,
                evidence=smoke_evidence,
                decided_by="offline-v1.2-end-to-end-test",
                note="Synthetic fake-live human checklist plus exact mechanical assessment.",
            )

            formation = executor.execute("formation")
            self.assertEqual(formation.status, "completed_stage")
            self.assertEqual(formation.dispatched_attempts, 480)
            formation_gate = formation_assessment(run)
            self.assertTrue(formation_gate["passed"])
            self.assertEqual(formation_gate["qualified_trajectories"], 5)
            run.record_gate_decision(
                "formation",
                passed=formation_gate["passed"],
                evidence=formation_gate,
                decided_by="offline-v1.2-end-to-end-test",
                note="Computed deterministic positive fake-live formation gate only.",
            )

            remaining = executor.execute("remaining_arms")
            self.assertEqual(remaining.status, "completed_stage")
            self.assertEqual(remaining.dispatched_attempts, 648)
            self.assertEqual(remaining.completed_logical_requests, 1340)
            probes = executor.execute("probes")
            self.assertEqual(probes.status, "completed_stage")
            self.assertEqual(probes.dispatched_attempts, 240)
            self.assertEqual(probes.completed_logical_requests, 1580)

            self.assertEqual(len(run.result_events()), 1580)
            self.assertEqual(len(provider.calls), 1580)
            self.assertEqual(
                len({(call["request_identity"], call["attempt"])
                     for call in provider.calls}),
                1580,
            )
            self.assertTrue(all(call["source"] == "fake-live"
                                for call in provider.calls))
            self.assertEqual(run.manifest["status"], "completed_stage")
            self.assertEqual(run.manifest["completed_through_stage"], "probes")
            self.assertEqual(run.manifest["protocol_version"], "v1.2")
            self.assertFalse(run.manifest["empirical"])

            summary = analyze_artifact_run(run_path)
            self.assertEqual(summary["schema_version"], "analysis-v5")
            self.assertEqual(summary["protocol_version"], "v1.2")
            self.assertEqual(summary["request_count"], 1580)
            self.assertEqual(summary["usage"]["logical_requests_attempted"], 1580)
            self.assertEqual(summary["scheduled_reviews"], 800)
            self.assertFalse(summary["empirical"])
            self.assertIn("v1.1 and v1.2 are never pooled", summary["pooling_policy"])
            self.assertFalse(summary["transport_outcome_sensitivity"]["required"])
            self.assertEqual(
                summary["transport_outcome_sensitivity"]["status"],
                "not_required_no_ambiguous_5xx_identity",
            )

            _manifest, rows, attempts = load_durable_and_rescore(run_path)
            self.assertEqual(len(attempts), 1580)
            self.assertEqual({row["protocol_version"] for row in rows}, {"v1.2"})
            mixed_rows = [dict(row) for row in rows]
            mixed_rows[-1]["protocol_version"] = "v1.1"
            with self.assertRaisesRegex(ValueError, "protocol versions"):
                summarize_result(
                    mixed_rows,
                    source="fake-live",
                    run_type="pilot",
                    protocol_version="v1.2",
                    replicates=1,
                )


if __name__ == "__main__":
    unittest.main()
