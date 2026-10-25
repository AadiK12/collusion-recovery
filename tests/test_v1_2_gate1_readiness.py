import json
from pathlib import Path
import unittest

from collusion_recovery.common import canonical
from collusion_recovery.orchestration import V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH
from collusion_recovery.v1_2_gate1 import (
    HUMAN_ACKNOWLEDGEMENT,
    authorization_schema,
    build_v1_2_gate1_proposal,
    validate_v1_2_gate1_proposal,
    V12Gate1ReadinessError,
)


class V12Gate1ReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proposal = build_v1_2_gate1_proposal()

    def test_projection_is_calibration_only_and_includes_ambiguous_slot(self):
        proposal = self.proposal
        self.assertFalse(proposal["dispatch_enabled"])
        self.assertFalse(proposal["execution_ready"])
        self.assertFalse(proposal["live_controller_implemented"])
        self.assertFalse(proposal["authorization_issuance_enabled"])
        self.assertFalse(proposal["pricing_reverified_for_v1_2"])
        self.assertFalse(proposal["experiment_started"])
        self.assertEqual(proposal["scope"]["logical_requests"], 20)
        self.assertEqual(proposal["scope"]["maximum_dispatch_attempts"], 61)
        self.assertEqual(proposal["scope"]["later_stages_authorized"], [])
        self.assertFalse(proposal["scope"]["hypothesis_bearing_requests_authorized"])
        projection = proposal["calibration_projection"]
        self.assertEqual(projection["known_not_completed_retry_slots"], 40)
        self.assertEqual(projection["ambiguous_5xx_retry_slots"], 1)
        self.assertTrue(projection["within_model_context"])
        self.assertEqual(proposal["exact_caps"]["max_dispatch_attempts"], 61)

    def test_fresh_lineage_and_retry_policy_are_bound(self):
        proposal = self.proposal
        self.assertEqual(proposal["protocol"]["protocol_version"], "v1.2")
        self.assertTrue(proposal["activation_decision"]["early_futility_deviation"])
        self.assertTrue(
            proposal["run"]["split"].startswith("fallback-two-round/v1.2/pilot/"))
        self.assertEqual(
            proposal["ambiguous_5xx_retry_policy_hash"],
            V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
        )
        self.assertIn("No v1.1 calibration observation", proposal["capability_bridge"]["non_reuse_rule"])

    def test_authorization_schema_is_exact_but_creates_no_authorization(self):
        schema = authorization_schema(self.proposal)
        properties = schema["properties"]
        self.assertEqual(properties["proposal_hash"]["const"], self.proposal["proposal_hash"])
        self.assertEqual(
            properties["approved_retry_policy_hash"]["const"],
            V1_2_AMBIGUOUS_5XX_RETRY_POLICY_HASH,
        )
        self.assertEqual(properties["human_acknowledgement"]["const"], HUMAN_ACKNOWLEDGEMENT)
        self.assertFalse(self.proposal["external_authorization"]["created"])
        self.assertIsNone(self.proposal["external_authorization"]["schema_artifact"])
        self.assertFalse(
            self.proposal["external_authorization"]["schema_artifact_created"])

    def test_tampering_is_rejected(self):
        value = json.loads(canonical(self.proposal))
        value["exact_caps"]["max_dispatch_attempts"] += 1
        with self.assertRaises(V12Gate1ReadinessError):
            validate_v1_2_gate1_proposal(value)

    def test_readiness_module_has_no_credential_or_dispatch_surface(self):
        source = Path("src/collusion_recovery/v1_2_gate1.py").read_text(encoding="utf-8")
        self.assertNotIn("OPENAI_KEY", source)
        self.assertNotIn(".env.local", source)
        self.assertNotIn("OpenAI(", source)
        self.assertNotIn(".complete(", source)


if __name__ == "__main__":
    unittest.main()
