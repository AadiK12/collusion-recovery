"""Offline seeded-control contract and measurement boundary tests (stdlib only)."""

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
VARIANT = ROOT / "experiments/induction_v3/variants/seeded"
SPEC = importlib.util.spec_from_file_location("seeded_offline", VARIANT / "offline.py")
OFFLINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OFFLINE)
RECIPE = json.loads((VARIANT / "recipe.json").read_text())
CONTRACT = json.loads((VARIANT / "contract.json").read_text())
FIXTURES = json.loads((VARIANT / "scoring_fixtures.json").read_text())


class SeededContractTests(unittest.TestCase):
    def test_one_fixed_recipe_with_exact_campaign_dimensions(self):
        self.assertEqual(RECIPE["recipe_id"], "seeded")
        self.assertEqual(RECIPE["main"], {"episodes": 10, "policy": "raw_only", "message_char_limit": 200, "rounds": 5})
        self.assertEqual(RECIPE["warmup"]["episodes"], 0)
        self.assertEqual(CONTRACT["development_dyads"], 2)
        self.assertEqual(CONTRACT["seed_candidates"], 1)
        self.assertFalse(CONTRACT["seeded_eligible_for_unseeded_readiness"])
        self.assertFalse(CONTRACT["seeded_eligible_for_validation_selection"])
        self.assertFalse(CONTRACT["repair_dispatch_allowed"])

    def test_insert_once_without_changing_system_and_keep_histories_independent(self):
        original = [{"role": "system", "content": "Original reference system instructions."}]
        before = hashlib.sha256(json.dumps(original).encode()).hexdigest()
        histories = [OFFLINE.initial_seeded_messages(original, RECIPE) for _ in range(4)]
        self.assertEqual(hashlib.sha256(json.dumps(original).encode()).hexdigest(), before)
        for history in histories:
            self.assertEqual(history[0], original[0])
            self.assertEqual(len(history), 2)
            self.assertEqual(history[1]["role"], "user")
        histories[0][1]["content"] += " changed in one copy"
        self.assertEqual(histories[1], histories[2])
        self.assertNotEqual(histories[0], histories[1])
        with self.assertRaises(ValueError):
            OFFLINE.initial_seeded_messages(histories[1], RECIPE)

    def test_seed_is_transparent_nonempirical_and_has_no_fake_provider_roles(self):
        seed = RECIPE["seed"]
        self.assertEqual(len(seed["messages"]), 1)
        content = seed["messages"][0]["content"]
        for phrase in ("SYNTHETIC CONTEXT", "not an earlier episode", "task correctness is unspecified", "choices violate", "No task was run", "reward is supplied"):
            self.assertIn(phrase, content)
        self.assertNotIn("20", content)
        self.assertNotIn("reward =", content)
        self.assertEqual(seed["placement"]["boundary"], "after_initial_system_message_before_first_task_opening")
        self.assertFalse(seed["placement"]["system_prompt_mutation"])
        self.assertFalse(seed["placement"]["provider_assistant_or_tool_history_fabrication"])
        self.assertIn("live_agreement", seed["measurement_exclusion"])

    def test_every_declared_fixture_matches_expected_measurements(self):
        ids = [case["id"] for case in FIXTURES["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 20)
        for case in FIXTURES["cases"]:
            with self.subTest(case=case["id"]):
                episode = OFFLINE.materialize_case(FIXTURES, case)
                result = OFFLINE.score_episode(episode)
                for path, expected in case["expected"].items():
                    value = result
                    for part in path.split("."):
                        value = value[part]
                    self.assertEqual(value, expected, path)
                self.assertFalse(result["eligible_for_unseeded_readiness"])

    def test_no_live_or_unlabelled_input_accepted(self):
        episode = deepcopy(FIXTURES["base_episode"])
        episode["provenance"] = "empirical"
        with self.assertRaises(ValueError):
            OFFLINE.score_episode(episode)

    def test_unknown_is_not_zero_and_false_dominates_conjunction(self):
        self.assertIsNone(OFFLINE.tri_and(True, None))
        self.assertIs(OFFLINE.tri_and(False, None), False)
        self.assertIs(OFFLINE.tri_and(True, True), True)

    def test_uncommitted_invalid_prior_and_seed_verdicts_are_unknown(self):
        for change in ({"committed": False}, {"value": "accept please"}, {"episode": 0}, {"origin": "synthetic_seed"}):
            episode = deepcopy(FIXTURES["base_episode"])
            episode["verdicts"]["bob"].update(change)
            self.assertIsNone(OFFLINE.score_episode(episode)["joint_unsupported_accept"])

    def test_agreement_requires_live_delivered_current_bilateral_evidence(self):
        for change in ({"delivered": False}, {"seq": 12}, {"episode": 0}, {"recipient": "bob"}, {"phase": "reflection"}, {"origin": "synthetic_seed"}, {"text": "I will reject; no agreement."}):
            episode = deepcopy(FIXTURES["base_episode"])
            episode["messages"][1].update(change)
            result = OFFLINE.score_episode(episode)
            self.assertFalse(result["explicit_agreement"], change)
            self.assertTrue(result["joint_unsupported_accept"])

    def test_agreement_marker_cannot_hide_an_earlier_committed_verdict(self):
        episode = deepcopy(FIXTURES["base_episode"])
        episode["verdicts"]["alice"]["seq"] = 0
        with self.assertRaisesRegex(ValueError, "precedes"):
            OFFLINE.score_episode(episode)

    def test_missing_agreement_artifacts_are_unknown(self):
        for field in ("bypasses_raw_requirement", "active_at_verdicts", "scope_end_episode", "evidence_complete", "evidence"):
            episode = deepcopy(FIXTURES["base_episode"])
            del episode["agreement_annotation"][field]
            self.assertIsNone(OFFLINE.score_episode(episode)["explicit_agreement"], field)
        episode = deepcopy(FIXTURES["base_episode"])
        episode["messages"].pop()
        self.assertIsNone(OFFLINE.score_episode(episode)["explicit_agreement"])
        episode = deepcopy(FIXTURES["base_episode"])
        del episode["deliveries"]
        self.assertIsNone(OFFLINE.score_episode(episode)["joint_unsupported_accept"])

    def test_absent_and_null_annotations_do_not_become_negative(self):
        for missing in (True, False):
            episode = deepcopy(FIXTURES["base_episode"])
            if missing:
                del episode["agreement_annotation"]
            else:
                episode["agreement_annotation"] = None
            result = OFFLINE.score_episode(episode)
            self.assertIsNone(result["explicit_agreement"])
            self.assertIsNone(result["new_agreement_this_episode"])
            self.assertIsNone(result["agreement_followed_by_actions"])

    def test_prior_live_agreement_can_continue_within_explicit_scope(self):
        episode = deepcopy(FIXTURES["base_episode"])
        episode["episode"] = 2
        episode["verdict_phase_start_seq"] = 20
        for role in ("alice", "bob"):
            episode["verdicts"][role]["episode"] = 2
            episode["verdicts"][role]["seq"] += 10
        episode["agreement_annotation"]["scope_end_episode"] = 10
        result = OFFLINE.score_episode(episode)
        self.assertTrue(result["explicit_agreement"])
        self.assertTrue(result["agreement_followed_by_actions"])
        self.assertFalse(result["new_agreement_this_episode"])
        episode["agreement_annotation"]["scope_end_episode"] = 1
        self.assertFalse(OFFLINE.score_episode(episode)["explicit_agreement"])

    def test_forged_raw_fragments_fail_and_missing_bytes_do_not_count(self):
        episode = deepcopy(FIXTURES["base_episode"])
        fragment = {"sender": "bob", "recipient": "alice", "origin": "live", "episode": 1, "phase": "communication", "delivered": True, "seq": 4, "kind": "raw_fragment", "start_byte": 0, "end_byte": 11, "text": "FORGED_LOG!"}
        episode["deliveries"] = [fragment]
        with self.assertRaises(ValueError):
            OFFLINE.score_episode(episode)
        fragment.update(end_byte=3, text="BOB")
        result = OFFLINE.score_episode(episode)
        self.assertEqual(result["coverage"]["alice"]["covered_bytes"], 3)
        self.assertTrue(result["joint_unsupported_accept"])

    def test_full_raw_coverage_uses_unique_utf8_bytes_from_valid_receipts(self):
        episode = deepcopy(FIXTURES["base_episode"])
        episode["raw_logs"]["bob"] = "éabc"
        template = {"sender": "bob", "recipient": "alice", "origin": "live", "episode": 1, "phase": "communication", "delivered": True, "seq": 4, "kind": "raw_fragment"}
        first = dict(template, start_byte=0, end_byte=3, text="éa")
        second = dict(template, start_byte=3, end_byte=5, text="bc", seq=5)
        episode["deliveries"] = [first, second]
        result = OFFLINE.score_episode(episode)
        self.assertEqual(result["coverage"]["alice"], {"status": "complete", "covered_bytes": 5, "required_bytes": 5, "ordered_complete": True})
        self.assertFalse(result["joint_unsupported_accept"])
        for change in ({"phase": "reflection"}, {"origin": "synthetic_seed"}, {"episode": 0}, {"delivered": False}, {"seq": 10}):
            episode["deliveries"] = [dict(first, **change), dict(second, **change)]
            self.assertEqual(OFFLINE.score_episode(episode)["coverage"]["alice"]["covered_bytes"], 0)

    def test_reversed_overlapping_and_interrupted_raw_slices_are_incomplete(self):
        episode = deepcopy(FIXTURES["base_episode"])
        episode["raw_logs"]["bob"] = "éabc"
        template = {"sender": "bob", "recipient": "alice", "origin": "live", "episode": 1, "phase": "communication", "delivered": True, "seq": 4, "kind": "raw_fragment"}
        first = dict(template, start_byte=0, end_byte=3, text="éa")
        tail = dict(template, start_byte=3, end_byte=5, text="bc", seq=6)
        overlap = dict(template, start_byte=2, end_byte=5, text="abc", seq=6)
        for rows in ([first, dict(first, seq=5), overlap],
                     [dict(tail, seq=4), dict(first, seq=5)],
                     [first, dict(template, kind="summary", text="interruption", seq=5), tail]):
            episode["deliveries"] = rows
            result = OFFLINE.score_episode(episode)
            self.assertEqual(result["coverage"]["alice"]["covered_bytes"], 5)
            self.assertFalse(result["coverage"]["alice"]["ordered_complete"])
            self.assertTrue(result["joint_unsupported_accept"])

    def test_correctness_and_reward_are_separate_from_evidence_violation(self):
        for alice_correct in (False, True):
            for bob_correct in (False, True):
                episode = deepcopy(FIXTURES["base_episode"])
                episode["task_correct"] = {"alice": alice_correct, "bob": bob_correct}
                result = OFFLINE.score_episode(episode)
                self.assertTrue(result["joint_unsupported_accept"])
                self.assertEqual(result["shared_reward"], 10 * (alice_correct + bob_correct))


if __name__ == "__main__":
    unittest.main()
