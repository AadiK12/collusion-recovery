import unittest

from collusion_recovery.v1_2_unified_contract import (
    COMPLETE,
    GATE1,
    GATE2,
    GATE3_PHASE1,
    GATE3_PHASE2,
    POST_RUN_HUMAN_AUDIT_CONTRACT,
    RELEASE_ORDER,
    STOPPED,
    TOTAL_DISPATCH_ATTEMPT_CEILING,
    TOTAL_LOGICAL_REQUEST_CEILING,
    UnifiedPilotEvidence,
    UnifiedV12ContractError,
    next_unified_pilot_decision,
    validate_unified_stage_caps,
)


def _caps():
    shapes = {
        GATE1: (20, 61),
        GATE2: (192, 694),
        GATE3_PHASE1: (480, 1765),
        GATE3_PHASE2: (888, 2997),
    }
    return {
        stage: {
            "logical_requests": logical,
            "dispatch_attempts": attempts,
            "max_total_input_tokens": logical * 10_000,
            "max_total_output_tokens": logical * 4_096,
            "max_input_tokens_per_request": 10_000,
            "max_output_tokens_per_request": 4_096,
            "max_in_flight": 1,
            "max_cost_usd": "1.25",
        }
        for stage, (logical, attempts) in shapes.items()
    }


class UnifiedV12ContractTests(unittest.TestCase):
    def test_happy_path_keeps_every_conditional_boundary(self):
        cases = (
            (UnifiedPilotEvidence(), ("release", GATE1)),
            (UnifiedPilotEvidence(calibration="passed"), ("release", GATE2)),
            (
                UnifiedPilotEvidence(
                    calibration="passed", smoke_exhaustive_integrity="passed"
                ),
                ("release", GATE3_PHASE1),
            ),
            (
                UnifiedPilotEvidence(
                    calibration="passed",
                    smoke_exhaustive_integrity="passed",
                    formation="passed",
                ),
                ("release", GATE3_PHASE2),
            ),
            (
                UnifiedPilotEvidence(
                    calibration="passed",
                    smoke_exhaustive_integrity="passed",
                    formation="passed",
                    phase2="completed",
                ),
                ("review", "post_run_human_transcript_and_claim_audit"),
            ),
            (
                UnifiedPilotEvidence(
                    calibration="passed",
                    smoke_exhaustive_integrity="passed",
                    formation="passed",
                    phase2="completed",
                    post_run_human_audit="passed",
                ),
                ("complete", COMPLETE),
            ),
        )
        for evidence, expected in cases:
            with self.subTest(evidence=evidence):
                decision = next_unified_pilot_decision(evidence)
                self.assertEqual((decision.status, decision.next_step), expected)

    def test_every_failed_gate_stops_later_dispatch(self):
        cases = (
            UnifiedPilotEvidence(calibration="failed"),
            UnifiedPilotEvidence(
                calibration="passed", smoke_exhaustive_integrity="failed"
            ),
            UnifiedPilotEvidence(
                calibration="passed",
                smoke_exhaustive_integrity="passed",
                formation="failed",
            ),
            UnifiedPilotEvidence(
                calibration="passed",
                smoke_exhaustive_integrity="passed",
                formation="passed",
                phase2="failed",
            ),
            UnifiedPilotEvidence(
                calibration="passed",
                smoke_exhaustive_integrity="passed",
                formation="passed",
                phase2="completed",
                post_run_human_audit="failed",
            ),
        )
        for evidence in cases:
            with self.subTest(evidence=evidence):
                decision = next_unified_pilot_decision(evidence)
                self.assertEqual(
                    (decision.status, decision.next_step), ("stopped", STOPPED)
                )

    def test_out_of_order_or_post_failure_evidence_is_rejected(self):
        cases = (
            UnifiedPilotEvidence(formation="passed"),
            UnifiedPilotEvidence(
                calibration="failed", smoke_exhaustive_integrity="passed"
            ),
            UnifiedPilotEvidence(
                calibration="passed",
                smoke_exhaustive_integrity="pending",
                formation="passed",
            ),
            UnifiedPilotEvidence(
                calibration="passed",
                smoke_exhaustive_integrity="passed",
                formation="failed",
                phase2="completed",
            ),
            UnifiedPilotEvidence(
                calibration="passed",
                smoke_exhaustive_integrity="passed",
                formation="passed",
                post_run_human_audit="passed",
            ),
            UnifiedPilotEvidence(
                calibration="passed",
                smoke_exhaustive_integrity="passed",
                formation="passed",
                phase2="failed",
                post_run_human_audit="failed",
            ),
        )
        for evidence in cases:
            with self.subTest(evidence=evidence), self.assertRaises(
                UnifiedV12ContractError
            ):
                next_unified_pilot_decision(evidence)

    def test_human_audit_is_post_run_publication_contract_not_dispatch_gate(self):
        self.assertFalse(
            POST_RUN_HUMAN_AUDIT_CONTRACT[
                "mid_run_human_smoke_approval_required"
            ]
        )
        self.assertFalse(
            POST_RUN_HUMAN_AUDIT_CONTRACT[
                "post_run_human_audit_is_dispatch_gate"
            ]
        )
        self.assertTrue(
            POST_RUN_HUMAN_AUDIT_CONTRACT[
                "post_run_human_audit_required_before_claims_or_confirmation"
            ]
        )
        self.assertEqual(
            POST_RUN_HUMAN_AUDIT_CONTRACT[
                "human_transcript_and_claim_audit_timing"
            ],
            "post_run_pre_publication",
        )

        after_smoke = next_unified_pilot_decision(UnifiedPilotEvidence(
            calibration="passed",
            smoke_exhaustive_integrity="passed",
        ))
        self.assertEqual((after_smoke.status, after_smoke.next_step), (
            "release", GATE3_PHASE1,
        ))

    def test_exact_stage_caps_aggregate_to_the_frozen_pilot_envelope(self):
        aggregate = validate_unified_stage_caps(_caps())
        self.assertEqual(aggregate["logical_requests"], TOTAL_LOGICAL_REQUEST_CEILING)
        self.assertEqual(
            aggregate["dispatch_attempts"], TOTAL_DISPATCH_ATTEMPT_CEILING
        )
        self.assertEqual(aggregate["max_cost_usd"], "5.00")
        self.assertEqual(aggregate["conditional_release_order"], list(RELEASE_ORDER))
        self.assertEqual(aggregate["max_in_flight"], 1)

    def test_missing_or_weakened_stage_caps_fail_closed(self):
        caps = _caps()
        del caps[GATE3_PHASE2]
        with self.assertRaises(UnifiedV12ContractError):
            validate_unified_stage_caps(caps)

        caps = _caps()
        caps[GATE2]["logical_requests"] -= 1
        with self.assertRaises(UnifiedV12ContractError):
            validate_unified_stage_caps(caps)

        caps = _caps()
        caps[GATE3_PHASE1]["max_in_flight"] = 2
        with self.assertRaises(UnifiedV12ContractError):
            validate_unified_stage_caps(caps)

        caps = _caps()
        caps[GATE1]["max_cost_usd"] = float("inf")
        with self.assertRaises(UnifiedV12ContractError):
            validate_unified_stage_caps(caps)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
