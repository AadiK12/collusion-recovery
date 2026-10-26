from types import SimpleNamespace
import unittest

from collusion_recovery.common import digest
from collusion_recovery.live_config import LiveConfig, STAGE_ORDER
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    DEDICATED_EXECUTION_CONTROLLERS,
    StagedExecutor,
    V1_2_CALIBRATION_NAMESPACE,
    V1_2_GATE1_NAMESPACE,
    V1_2_GATE1_RUN_ID,
    V1_2_GATE2_NAMESPACE,
    V1_2_GATE2_RUN_ID,
    V1_2_GATE3_PHASE1_RUN_ID,
    V1_2_UNIFIED_PILOT_CONTROLLER_ID,
    V1_2_UNIFIED_PILOT_NAMESPACE,
    V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
    V1_2_UNIFIED_PILOT_RUN_ID,
    V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
    build_request_plan,
    build_v1_2_unified_pilot_plan,
)


EXPECTED_ORDER_HASH = (
    "78a89d8f541d3c00bd8411a8ac23f612a503ebe12c663e4d80cdbac42fec2757"
)
EXPECTED_PARTITION_HASH = (
    "69632142a0f8c2d143914b09cc2f4b5dd05d208b0f258fbb00b033a2880a15cc"
)


def _unified_config(**overrides):
    values = {
        "run_id": V1_2_UNIFIED_PILOT_RUN_ID,
        "protocol_version": "v1.2",
        "source": "empirical-live",
        "provider": "openai",
        "model": "gpt-6.1-sol",
        "model_context_tokens": 1_050_000,
        "model_settings": {
            "reasoning": {"effort": "low", "mode": "standard"},
            "service_tier": "default",
        },
        "pricing": {
            "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
            "verified_at": "2026-10-25T12:21:28-07:00",
            "input_usd_per_million": "2.00",
            "cached_input_usd_per_million": "0.10",
            "cache_write_input_usd_per_million": "2.50",
            "output_usd_per_million": "10.00",
        },
        "authorization": {
            "kind": "paid",
            "authorization_id": "offline-unified-plan-test",
            "approved_at": "2026-10-25T12:21:28-07:00",
            "approved_budget_usd": "1",
            "allow_paid_calls": True,
        },
        "limits": {
            "max_requests": 5517,
            "max_total_input_tokens": 100_000_000,
            "max_total_output_tokens": 10_000_000,
            "max_input_tokens_per_request": 750_000,
            "max_output_tokens_per_request": 4096,
            "max_in_flight": 1,
        },
        "retry_policy": {
            "transport_retries": 2,
            "message_overlength_retries": 1,
            "initial_backoff_seconds": "1",
            "maximum_backoff_seconds": "8",
        },
        "run_type": "pilot",
        "split": V1_2_UNIFIED_PILOT_NAMESPACE,
        "stage": "probes",
        "seed_namespace": V1_2_UNIFIED_PILOT_NAMESPACE,
        "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
        "blocks": 5,
    }
    values.update(overrides)
    return LiveConfig.from_mapping(values)


def _partition(plan):
    return [
        {
            "stage": stage,
            "identities": [
                request.identity
                for request in plan.requests
                if request.stage == stage
            ],
        }
        for stage in STAGE_ORDER
        if any(request.stage == stage for request in plan.requests)
    ]


class UnifiedV12PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = _unified_config()
        cls.plan = build_request_plan(cls.config)

    def test_fixed_identity_routes_to_exact_complete_plan(self):
        direct = build_v1_2_unified_pilot_plan(self.config)
        self.assertEqual(self.plan.to_dict(), direct.to_dict())
        self.assertEqual(
            DEDICATED_EXECUTION_CONTROLLERS[V1_2_UNIFIED_PILOT_RUN_ID],
            V1_2_UNIFIED_PILOT_CONTROLLER_ID,
        )
        self.assertEqual(self.plan.run_id, V1_2_UNIFIED_PILOT_RUN_ID)
        self.assertEqual(self.plan.split, V1_2_UNIFIED_PILOT_NAMESPACE)
        self.assertEqual(self.plan.planned_counts(), {
            "logical_requests": 1580,
            "by_stage": {
                "calibration": 20,
                "smoke": 192,
                "formation": 480,
                "remaining_arms": 648,
                "probes": 240,
            },
            "by_phase": {
                "calibration": 20,
                "formation": 600,
                "evaluation": 720,
                "probe": 240,
            },
            "checkpoints": 10,
        })
        self.assertEqual(
            {(checkpoint.block, checkpoint.history)
             for checkpoint in self.plan.checkpoints},
            {(block, history) for block in range(5) for history in ("U", "C")},
        )
        self.assertTrue(all(
            len(checkpoint.contributors) == 60
            for checkpoint in self.plan.checkpoints
        ))

    def test_request_order_and_stage_partitions_are_hash_frozen(self):
        order_hash = digest([
            request.identity for request in self.plan.requests
        ])
        partition_hash = digest(_partition(self.plan))
        self.assertEqual(order_hash, EXPECTED_ORDER_HASH)
        self.assertEqual(partition_hash, EXPECTED_PARTITION_HASH)
        self.assertEqual(order_hash, V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH)
        self.assertEqual(
            partition_hash, V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH
        )

        positions = [STAGE_ORDER.index(request.stage) for request in self.plan.requests]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(
            [entry["stage"] for entry in _partition(self.plan)],
            ["calibration", "smoke", "formation", "remaining_arms", "probes"],
        )

    def test_namespace_and_request_identities_are_fresh(self):
        self.assertTrue(
            V1_2_UNIFIED_PILOT_NAMESPACE.startswith(
                "fallback-two-round/v1.2/pilot/"
            )
        )
        self.assertNotIn(
            V1_2_UNIFIED_PILOT_NAMESPACE,
            {V1_2_GATE1_NAMESPACE, V1_2_GATE2_NAMESPACE},
        )
        self.assertEqual(
            len({request.identity for request in self.plan.requests}), 1580
        )
        block_fixtures = {
            key: fixture
            for key, fixture in self.plan.fixture_inventory.items()
            if key.startswith("block/")
        }
        self.assertTrue(block_fixtures)
        self.assertTrue(all(
            fixture["namespace"].startswith(
                f"{V1_2_UNIFIED_PILOT_NAMESPACE}/"
            )
            for fixture in block_fixtures.values()
        ))

        staged_plans = (
            build_request_plan(_unified_config(
                run_id=V1_2_GATE1_RUN_ID,
                split=V1_2_GATE1_NAMESPACE,
                seed_namespace=V1_2_GATE1_NAMESPACE,
                stage="calibration",
            )),
            build_request_plan(_unified_config(
                run_id=V1_2_GATE2_RUN_ID,
                split=V1_2_GATE2_NAMESPACE,
                seed_namespace=V1_2_GATE2_NAMESPACE,
                stage="smoke",
            )),
            build_request_plan(_unified_config(
                run_id=V1_2_GATE3_PHASE1_RUN_ID,
                split=V1_2_GATE2_NAMESPACE,
                seed_namespace=V1_2_GATE2_NAMESPACE,
                stage="formation",
            )),
        )
        prior_request_identities = {
            request.identity
            for plan in staged_plans
            for request in plan.requests
        }
        prior_checkpoint_identities = {
            checkpoint.checkpoint_id
            for plan in staged_plans
            for checkpoint in plan.checkpoints
        }
        self.assertFalse(
            {request.identity for request in self.plan.requests}
            & prior_request_identities
        )
        self.assertFalse(
            {checkpoint.checkpoint_id for checkpoint in self.plan.checkpoints}
            & prior_checkpoint_identities
        )

    def test_exact_configuration_is_required(self):
        cases = (
            {"run_id": "arbitrary-unified-id"},
            {"stage": "remaining_arms"},
            {"split": V1_2_GATE2_NAMESPACE},
            {"seed_namespace": V1_2_GATE2_NAMESPACE},
            {"blocks": 4},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                build_request_plan(_unified_config(**overrides))

    def test_unified_empirical_plan_is_explicitly_non_dispatchable(self):
        run_stub = SimpleNamespace(
            config=SimpleNamespace(
                run_id=V1_2_UNIFIED_PILOT_RUN_ID,
                protocol_version="v1.2",
                source="empirical-live",
            ),
            manifest={
                "execution_controller": V1_2_UNIFIED_PILOT_CONTROLLER_ID,
            },
            events=[],
        )
        with self.assertRaisesRegex(
            AuthorizationRequiredError, "non-dispatchable.*master controller"
        ):
            StagedExecutor(
                run_stub,
                object(),
                lambda *_args: None,
                dedicated_controller=V1_2_UNIFIED_PILOT_CONTROLLER_ID,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
