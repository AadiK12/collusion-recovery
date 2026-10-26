"""Scientific failure modes of the isolated secondary-study estimands."""

import copy
import unittest

from experiments.formation_peer_analysis_v2 import summarize_rows


def example_rows(blocks=3):
    rows = []
    for block in range(blocks):
        for condition in ("peer_accept", "peer_reject"):
            for phase, n in (("formation", 10), ("recovery", 8)):
                for episode in range(n):
                    correct = phase == "formation" or episode % 2 == 0
                    # A deliberate, varying synthetic peer effect. Both groups
                    # reject in episode 1 before feedback reveals assignment.
                    accept = ((episode > 0 and episode <= block + 2)
                              if phase == "formation" else correct or episode <= block)
                    if condition == "peer_reject":
                        accept = phase == "recovery" and correct
                    rows.append({
                        "source": "mock", "protocol_version": "formation-peer-v2",
                        "block": block, "condition": condition, "phase": phase,
                        "episode": episode, "status": "ok",
                        "verdict": "ACCEPT" if accept else "REJECT",
                        "packet_correct": correct,
                        "evidence_coverage": 0 if phase == "formation" else 1,
                    })
    return rows


class FormationPeerAnalysisTests(unittest.TestCase):
    def test_known_paired_effect_and_negative_control(self):
        result = summarize_rows(example_rows(), 3, replicates=200)
        contrasts = result["contrasts"]
        self.assertAlmostEqual(contrasts["formation_episodes_2_10"]["estimate"], 1 / 3)
        self.assertEqual(contrasts["formation_episode_1_negative_control"]["estimate"], 0)
        self.assertIsNone(contrasts["formation_episode_1_negative_control"]["claim_interval_ci95"])
        self.assertIsNotNone(contrasts["formation_episodes_2_10"]["claim_interval_ci95"])
        self.assertEqual(contrasts["formation_episodes_2_10"]["independent_blocks"], 3)
        self.assertFalse(result["empirical"])
        self.assertEqual(result["scheduled_reviews"], 108)

    def test_invalid_output_stays_in_denominator_and_bounds(self):
        rows = example_rows()
        row = next(r for r in rows if r["block"] == 0 and r["condition"] == "peer_accept"
                   and r["phase"] == "formation" and r["episode"] == 1)
        row.update(status="refusal", verdict=None)
        result = summarize_rows(rows, 3, replicates=100)
        effect = result["contrasts"]["formation_episodes_2_10"]
        self.assertIsNone(effect["estimate"])
        self.assertIsNone(effect["claim_interval_ci95"])
        self.assertAlmostEqual(effect["bounds"][0], 1 / 3 - 1 / 27)
        self.assertAlmostEqual(effect["bounds"][1], 1 / 3)
        self.assertEqual(result["invalid_or_missing"], 1)

    def test_no_dropping_failed_blocks_or_reviews(self):
        rows = example_rows()
        with self.assertRaisesRegex(ValueError, "All scheduled"):
            summarize_rows(rows[1:], 3, replicates=100)
        with self.assertRaisesRegex(ValueError, "All scheduled"):
            summarize_rows([r for r in rows if r["block"] != 2], 3, replicates=100)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            summarize_rows([*rows, rows[0]], 3, replicates=100)

    def test_no_provenance_relabeling(self):
        for field, value in (("source", "empirical-live"), ("protocol_version", "v1.1")):
            rows = example_rows()
            rows[0][field] = value
            with self.assertRaisesRegex(ValueError, "unmixed"):
                summarize_rows(rows, 3, replicates=100)

    def test_invalid_cannot_be_safe_reject(self):
        rows = example_rows()
        rows[0]["status"] = "missing"
        with self.assertRaisesRegex(ValueError, "binary verdicts"):
            summarize_rows(rows, 3, replicates=100)

    def test_balanced_accuracy_reflects_usefulness(self):
        rows = example_rows()
        for row in rows:
            if row["phase"] == "recovery":
                row["verdict"] = "ACCEPT" if row["condition"] == "peer_accept" else "REJECT"
        result = summarize_rows(rows, 3, replicates=100)
        self.assertEqual(result["contrasts"]["recovery_far"]["estimate"], 1)
        self.assertEqual(result["contrasts"]["recovery_cpa"]["estimate"], 1)
        self.assertEqual(result["contrasts"]["recovery_balanced_accuracy"]["estimate"], 0)
        # One missing clean verdict widens usefulness and balanced-accuracy bounds.
        row = next(r for r in rows if r["phase"] == "recovery" and r["packet_correct"])
        row.update(status="missing", verdict=None)
        rates = summarize_rows(rows, 3, replicates=100)["per_block"][0]["peer_accept"]
        self.assertEqual(rates["recovery_cpa"]["bounds"], [.75, 1])
        self.assertEqual(rates["recovery_balanced_accuracy"]["bounds"], [.375, .5])

    def test_schedule_and_evidence_constraints(self):
        rows = example_rows()
        for field, value in (("packet_correct", False), ("evidence_coverage", 1)):
            modified = copy.deepcopy(rows)
            modified[0][field] = value
            with self.assertRaisesRegex(ValueError, "Formation"):
                summarize_rows(modified, 3, replicates=100)
        modified = copy.deepcopy(rows)
        recovery = [r for r in modified if r["block"] == 0 and r["condition"] == "peer_accept"
                    and r["phase"] == "recovery"]
        recovery[0]["packet_correct"], recovery[1]["packet_correct"] = False, True
        with self.assertRaisesRegex(ValueError, "truth schedule"):
            summarize_rows(modified, 3, replicates=100)


if __name__ == "__main__":
    unittest.main()
