"""Slow, exhaustive offline tests for the unified v1.2 shape projection.

The projection renders all 1,580 logical requests and their permitted billable
retry shapes.  It never loads a credential or performs provider/network I/O.
"""

import ast
import copy
from contextlib import ExitStack
import inspect
import unittest
from unittest.mock import patch

from collusion_recovery.common import digest
from collusion_recovery.v1_2_master_projection import (
    PROJECTION_ALGORITHM_VERSION,
    V12MasterProjectionError,
    build_unified_projection,
    projected_stage_bindings,
    validate_unified_projection,
)
import collusion_recovery.v1_2_master_projection as projection_module
from collusion_recovery.v1_2_master_release import (
    SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
    build_v1_2_master_proposal,
    validate_v1_2_master_proposal,
)
from collusion_recovery.v1_2_unified_contract import RELEASE_ORDER


def _no_io():
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
        stack.enter_context(patch(
            name,
            side_effect=AssertionError("I/O and provider dispatch are forbidden"),
        ))
    return stack


class V12MasterProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # One exhaustive render is shared across the assertions in this class.
        with _no_io():
            cls.projection = build_unified_projection()

    def test_exact_unified_counts_and_shape_sum(self):
        artifact = self.projection
        self.assertTrue(artifact["projection_only"])
        self.assertFalse(artifact["dispatch_performed"])
        self.assertFalse(artifact["credential_accessed"])
        self.assertEqual(artifact["network_calls"], 0)
        self.assertEqual(artifact["paid_calls"], 0)
        self.assertEqual(artifact["logical_request_count"], 1580)
        self.assertEqual(artifact["initial_attempt_shapes"], 1580)
        self.assertEqual(artifact["overlength_retry_shapes"], 760)
        self.assertEqual(artifact["ambiguous_5xx_retry_slots"], 17)
        self.assertEqual(artifact["maximum_billable_attempt_shapes"], 2357)
        self.assertEqual(
            artifact["maximum_known_not_completed_transport_retry_attempts"],
            3160,
        )
        self.assertEqual(artifact["maximum_dispatch_attempts"], 5517)
        self.assertEqual(
            artifact["projected_strict_reservation_ceiling_usd"],
            "5032.461128",
        )
        self.assertEqual(
            artifact["operational_cost_hard_cap_usd"],
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
        )
        self.assertFalse(
            artifact["operational_cap_covers_full_projected_envelope"]
        )
        self.assertFalse(artifact["authorization_issuance_enabled"])
        self.assertTrue(artifact["independent_audit_required"])

    def test_stage_shape_sums_are_exact_and_partitioned(self):
        expected = {
            "gate1_calibration": (20, 61, 21, 0, "1.344343"),
            "gate2_block0_smoke": (192, 694, 310, 212, "614.038465"),
            "gate3_phase1_formation": (
                480, 1765, 805, 407, "1003.969050"
            ),
            "gate3_phase2_completion": (
                888, 2997, 1221, 1041, "3413.109270"
            ),
        }
        inventories = self.projection["stage_inventories"]
        self.assertEqual(set(inventories), set(RELEASE_ORDER))
        for stage, values in expected.items():
            logical, attempts, billable, long_context, cost = values
            row = inventories[stage]
            self.assertEqual(row["logical_request_count"], logical)
            self.assertEqual(row["maximum_dispatch_attempts"], attempts)
            self.assertEqual(row["maximum_billable_attempt_shapes"], billable)
            self.assertEqual(row["long_context_projected_shape_count"], long_context)
            self.assertEqual(row["exact_caps"]["max_cost_usd"], cost)
            self.assertTrue(row["within_model_context"])

    def test_projected_bindings_keep_master_non_issuable(self):
        bindings = projected_stage_bindings(self.projection)
        self.assertTrue(all(
            binding["status"] == "projected_awaiting_independent_audit"
            for binding in bindings.values()
        ))
        proposal = build_v1_2_master_proposal(bindings)
        self.assertEqual(validate_v1_2_master_proposal(proposal), proposal)
        self.assertFalse(proposal["execution_ready"])
        self.assertFalse(proposal["authorization_issuance_enabled"])
        exact = proposal["exact_shape_sum_reservation_envelope"]
        self.assertEqual(exact["maximum_cost_usd"], "5032.461128")
        self.assertFalse(exact["independent_audit_passed"])
        self.assertFalse(exact["operational_cap_covers_envelope"])
        self.assertFalse(
            proposal["operational_cost_hard_cap"][
                "full_shape_sum_coverage_required_to_start"
            ]
        )
        self.assertIn(
            "cumulative actual-spend plus next-reservation ledger is not implemented",
            proposal["non_ready_reasons"],
        )

    def test_nested_shape_tampering_fails_even_after_rehashing_containers(self):
        tampered = copy.deepcopy(self.projection)
        stage = "gate2_block0_smoke"
        inventory = tampered["stage_inventories"][stage]
        inventory["ambiguous_allowance"][0]["strict_reservation_cost_usd"] = (
            "0.000001"
        )
        inventory["ambiguous_allowance_hash"] = digest(
            inventory["ambiguous_allowance"]
        )
        inventory_body = dict(inventory)
        inventory_body.pop("inventory_hash")
        inventory["inventory_hash"] = digest(inventory_body)
        tampered["stage_inventory_hashes"][stage] = inventory["inventory_hash"]
        projection_body = dict(tampered)
        projection_body.pop("projection_hash")
        tampered["projection_hash"] = digest(projection_body)
        with self.assertRaises(V12MasterProjectionError):
            validate_unified_projection(tampered)

    def test_projection_source_exposes_no_live_execution_or_secret_loader(self):
        source = inspect.getsource(projection_module)
        tree = ast.parse(source)
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("OpenAI", called_names)
        self.assertNotIn("OpenAIResponsesProvider", called_names)
        self.assertNotIn("_load_local_openai_key", source)
        self.assertNotIn("OPENAI_KEY", source)
        self.assertNotIn("OPENAI_API_KEY", source)
        self.assertFalse(hasattr(projection_module, "dispatch"))
        self.assertFalse(hasattr(projection_module, "run_v1_2_master"))
        self.assertEqual(
            self.projection["projection_algorithm_version"],
            PROJECTION_ALGORITHM_VERSION,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
