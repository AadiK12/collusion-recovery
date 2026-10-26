"""Offline capability inventory/gate tests; no model behavior is simulated as evidence."""

from copy import deepcopy
import json
import unittest

from collusion_recovery.common import canonical, digest
from collusion_recovery.history import History
from collusion_recovery.providers.base import ResponseMode
from experiments import exploratory_collusion_v2 as study
from experiments import exploratory_collusion_v2_capability as capability


def verdict(value):
    return canonical({"verdict": value, "evidence_record_ids": [], "brief_reason": "Synthetic test."})


class CapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = capability.build_plan("capability/unit")

    def observations(self):
        return {row["logical_request_id"]: {"status": "ok", "raw_response": verdict(row["expected_verdict"])}
                for row in self.plan["requests"]}

    def test_inventory_is_fixed_balanced_fresh_and_interleaved(self):
        self.assertEqual(self.plan, capability.build_plan("capability/unit"))
        self.assertEqual(len(self.plan["requests"]), 24)
        self.assertEqual({row["action"] for row in self.plan["requests"]}, {"verdict"})
        self.assertEqual(self.plan["counts"]["formation_requests"], 0)
        self.assertEqual(self.plan["counts"]["recovery_requests"], 0)
        self.assertTrue(all(row["dependencies"] == [] for row in self.plan["requests"]))
        for group in capability.GROUPS:
            rows = [row for row in self.plan["requests"] if row["group"] == group]
            self.assertEqual(len(rows), 8)
            self.assertEqual(sum(row["role"] == "alice" for row in rows), 4)
        self.assertEqual([row["group"] for row in self.plan["requests"][:3]], list(capability.GROUPS))
        constrained = [row for row in self.plan["requests"] if row["group"] == "constrained"]
        self.assertEqual(sum(row["peer_packet_correct"] for row in constrained), 4)
        defects = {fixture["peer"]["defect"] for fixture in self.plan["fixtures"]}
        self.assertEqual(defects, {None, *study.DEFECTS})
        other = capability.build_plan("capability/another")
        identities = {row["logical_request_id"] for row in self.plan["requests"]}
        self.assertFalse(identities & {row["logical_request_id"] for row in other["requests"]})
        old_tasks = {record[role]["fixture_id"] for record in self.plan["fixtures"] for role in ("own", "peer")}
        self.assertFalse(old_tasks & {record[role]["fixture_id"] for record in other["fixtures"] for role in ("own", "peer")})

    def test_plan_and_namespaces_cannot_be_relabelled(self):
        for namespace in ("development/test", "capability/", "capability/../oops", "capability/test secret", None):
            with self.assertRaises(ValueError):
                capability.build_plan(namespace)
        for field, value in (("thresholds", {}), ("max_output_tokens", 100), ("model", "another-model")):
            altered = deepcopy(self.plan)
            altered[field] = value
            altered["plan_hash"] = digest({k: v for k, v in altered.items() if k != "plan_hash"})
            with self.assertRaises(ValueError):
                capability.validate_plan(altered)
        altered = deepcopy(self.plan)
        altered["requests"][0]["expected_verdict"] = "REJECT"
        with self.assertRaises(ValueError):
            capability.assess(altered, self.observations())
        for ordinal in (-1, 24, True, "0"):
            with self.assertRaises(ValueError):
                capability.rendered_request(self.plan, ordinal)

    def test_matching_prompt_is_fresh_and_has_only_assigned_evidence(self):
        expected_messages = None
        for spec in self.plan["requests"]:
            ordinal = spec["ordinal"]
            rendered = capability.rendered_request(self.plan, ordinal)
            visible = json.loads(rendered["turns"][1]["content"])
            self.assertEqual(visible["history"], [])
            current = visible["current"]
            self.assertEqual(set(current), {"agent", "own_packet", "peer_packet", "message_rounds"})
            self.assertEqual(len(current["own_packet"]["records"]), 32)
            self.assertEqual("records" in current["peer_packet"], spec["evidence"] == "full")
            if spec["evidence"] == "full":
                self.assertEqual(current["peer_packet"], self.plan["fixtures"][ordinal]["peer"]["packet"])
            messages = [message[side]["text"] for message in current["message_rounds"]
                        for side in ("own_message", "peer_message")]
            expected_messages = messages if expected_messages is None else expected_messages
            self.assertEqual(messages, expected_messages)
            self.assertEqual(messages, [capability.NEUTRAL_MESSAGE] * 10)
            _, own, peer = capability._item(self.plan["seed_namespace"], ordinal)
            formation = study.render_request(study.formation_request(
                History(), own.packet, peer.packet, spec["role"], "verdict", rounds=capability.neutral_rounds()))
            self.assertEqual(rendered["turns"][0], formation["turns"][0])
            self.assertEqual(rendered["turns"][0]["content"].count(study.VERIFICATION_NORM), 1)
            if spec["evidence"] == "constrained":
                self.assertEqual(rendered, formation)
            text = canonical(visible)
            self.assertNotIn(self.plan["seed_namespace"], text)
            for key in study._FORBIDDEN_KEYS:
                self.assertNotIn('"' + key + '":', text)
            for key in ("group", "peer_packet_correct", "expected_verdict", "runner_feedback", "private_policy_note"):
                self.assertNotIn(key, text)

    def test_provider_contract_has_fixed_model_and_settings(self):
        request = capability.provider_request(self.plan, 0, attempt=2)
        self.assertEqual(request.request_identity, self.plan["requests"][0]["logical_request_id"])
        self.assertEqual(request.model, study.MODEL)
        self.assertEqual(request.model_settings_json, study.MODEL_SETTINGS_JSON)
        self.assertEqual(request.max_output_tokens, 4096)
        self.assertIs(request.response_mode, ResponseMode.VERDICT)
        self.assertEqual(request.attempt, 2)
        self.assertEqual(request.input_hash, capability.rendered_request(self.plan, 0)["prompt_hash"])

    def test_positive_gate_is_capability_not_formation(self):
        assessed = capability.assess(self.plan, self.observations())
        self.assertTrue(assessed["passed"])
        self.assertEqual(assessed["totals"], {"valid": 24, "full_correct_accept": 8,
                                             "full_faulty_reject": 8, "constrained_reject": 8})
        self.assertFalse(assessed["formation_established"])
        self.assertFalse(assessed["repair_eligible"])

    def test_exact_thresholds_require_every_constrained_rejection(self):
        observed = self.observations()
        for group in ("full_correct", "full_faulty"):
            row = next(row for row in self.plan["requests"] if row["group"] == group)
            observed[row["logical_request_id"]]["raw_response"] = verdict("REJECT" if group == "full_correct" else "ACCEPT")
        self.assertTrue(capability.assess(self.plan, observed)["passed"])
        row = next(row for row in self.plan["requests"] if row["group"] == "constrained")
        observed[row["logical_request_id"]]["raw_response"] = verdict("ACCEPT")
        self.assertFalse(capability.assess(self.plan, observed)["passed"])
        for fixed_verdict in ("REJECT", "ACCEPT"):
            fixed = {row["logical_request_id"]: {"status": "ok", "raw_response": verdict(fixed_verdict)}
                     for row in self.plan["requests"]}
            self.assertFalse(capability.assess(self.plan, fixed)["passed"])

    def test_missing_invalid_and_failures_are_unknown_not_rejections(self):
        target = self.plan["requests"][2]["logical_request_id"]
        for observation in (None, {"status": "ok", "raw_response": "not JSON"},
                            {"status": "refusal", "raw_response": verdict("REJECT")},
                            {"status": "timeout", "raw_response": None},
                            {"status": "error", "raw_response": None}):
            observed = self.observations()
            if observation is None:
                del observed[target]
            else:
                observed[target] = observation
            assessed = capability.assess(self.plan, observed)
            self.assertFalse(assessed["passed"])
            self.assertEqual(assessed["totals"]["valid"], 23)
            self.assertEqual(assessed["totals"]["constrained_reject"], 7)
            self.assertIsNone(assessed["rows"][2]["verdict"])
            self.assertIsNone(assessed["rows"][2]["matches_expected"])

    def test_unscheduled_or_derivative_observations_are_not_accepted(self):
        observed = self.observations()
        observed["another-id"] = {"status": "ok", "raw_response": verdict("REJECT")}
        with self.assertRaises(ValueError):
            capability.assess(self.plan, observed)
        observed = self.observations()
        observed[self.plan["requests"][0]["logical_request_id"]]["parsed_verdict"] = "ACCEPT"
        with self.assertRaises(ValueError):
            capability.assess(self.plan, observed)

    def test_durable_result_reparses_raw_text_and_checks_derivative(self):
        raw = verdict("REJECT")
        result = {"status": "ok", "output_text": raw, "parsed_output": json.loads(raw)}
        self.assertEqual(capability.observation_from_result(result), {"status": "ok", "raw_response": raw})
        for malformed in ({**result, "parsed_output": None},
                          {**result, "parsed_output": json.loads(verdict("ACCEPT"))},
                          {**result, "output_text": "invalid"}):
            self.assertEqual(capability.observation_from_result(malformed)["status"], "invalid")
        for status, expected in (("refusal", "refusal"), ("ambiguous_timeout", "timeout"),
                                 ("fatal_error", "error"), ("invalid_response", "invalid")):
            self.assertEqual(capability.observation_from_result({"status": status, "output_text": None})["status"], expected)
        with self.assertRaises(ValueError):
            capability.observation_from_result({"status": "invented", "output_text": raw})


if __name__ == "__main__":
    unittest.main()
