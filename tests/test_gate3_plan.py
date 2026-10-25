from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from collusion_recovery.live_config import LiveConfig
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    DurableRun,
    GATE2_SUCCESSOR_NAMESPACE,
    GATE2_SUCCESSOR_RUN_ID,
    GATE3_PHASE1_BINDING_EVENT,
    GATE3_PHASE1_CONTROLLER_ID,
    GATE3_PHASE1_RUN_ID,
    StagedExecutor,
    build_gate2_successor_smoke_plan,
    build_gate3_phase1_fixture_inventory,
    build_gate3_phase1_plan,
    build_request_plan,
)
from collusion_recovery.staged_requests import ScientificRequestFactory


def empirical_config(*, run_id=GATE3_PHASE1_RUN_ID, stage="formation",
                     split=GATE2_SUCCESSOR_NAMESPACE,
                     seed_namespace=GATE2_SUCCESSOR_NAMESPACE):
    return LiveConfig.from_mapping({
        "run_id": run_id,
        "protocol_version": "v1.1",
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
            "verified_at": "2026-10-24T14:02:52-07:00",
            "input_usd_per_million": "2",
            "cached_input_usd_per_million": "0.10",
            "cache_write_input_usd_per_million": "2.50",
            "output_usd_per_million": "10",
        },
        "authorization": {
            "kind": "paid",
            "authorization_id": "gate3-plan-test",
            "approved_at": "2026-10-24T14:15:00-07:00",
            "approved_budget_usd": "1",
            "allow_paid_calls": True,
        },
        "limits": {
            "max_requests": 1_120,
            "max_total_input_tokens": 100_000_000,
            "max_total_output_tokens": 1_966_080,
            "max_input_tokens_per_request": 900_000,
            "max_output_tokens_per_request": 4_096,
            "max_in_flight": 1,
        },
        "retry_policy": {
            "transport_retries": 2,
            "message_overlength_retries": 1,
            "initial_backoff_seconds": "1",
            "maximum_backoff_seconds": "8",
        },
        "run_type": "pilot",
        "split": split,
        "stage": stage,
        "seed_namespace": seed_namespace,
        "calibration_namespace": "calibration/v1.1",
        "blocks": 5,
    })


class Gate3Phase1PlanTests(unittest.TestCase):
    def setUp(self):
        self.config = empirical_config()
        self.plan = build_gate3_phase1_plan(self.config)

    def test_exact_formation_only_counts_and_scope(self):
        self.assertEqual(self.plan.planned_counts(), {
            "logical_requests": 320,
            "by_stage": {"formation": 320},
            "by_phase": {"formation": 320},
            "checkpoints": 8,
        })
        self.assertEqual(
            {request.action for request in self.plan.requests},
            {"message", "verdict"},
        )
        self.assertEqual(
            sum(request.action == "message" for request in self.plan.requests),
            160,
        )
        self.assertEqual(
            sum(request.action == "verdict" for request in self.plan.requests),
            160,
        )
        self.assertEqual(
            {request.metadata["block"] for request in self.plan.requests},
            {1, 2, 3, 4},
        )
        self.assertEqual(
            {request.metadata["history"] for request in self.plan.requests},
            {"U", "C"},
        )
        self.assertTrue(all(request.parent_checkpoint is None
                            for request in self.plan.requests))
        self.assertEqual(build_request_plan(self.config).to_dict(), self.plan.to_dict())

    def test_dependencies_form_a_forward_only_episode_dag(self):
        positions = {
            request.identity: request.ordinal for request in self.plan.requests
        }
        for request in self.plan.requests:
            self.assertTrue(all(
                positions[dependency] < request.ordinal
                for dependency in request.dependencies
            ))
            if request.action == "verdict":
                self.assertEqual(len(request.dependencies), 2)
                dependencies = [self.plan.requests[positions[value]]
                                for value in request.dependencies]
                self.assertTrue(all(value.action == "message" for value in dependencies))
                self.assertEqual(
                    {(value.metadata["block"], value.metadata["history"],
                      value.metadata["episode"])
                     for value in dependencies},
                    {(request.metadata["block"], request.metadata["history"],
                      request.metadata["episode"])},
                )

        for checkpoint in self.plan.checkpoints:
            self.assertEqual(len(checkpoint.contributors), 40)
            contributors = [self.plan.requests[positions[value]]
                            for value in checkpoint.contributors]
            self.assertEqual(
                {(value.metadata["block"], value.metadata["history"])
                 for value in contributors},
                {(checkpoint.block, checkpoint.history)},
            )
            self.assertEqual(
                {(value.metadata["episode"], value.action)
                 for value in contributors},
                {(episode, action) for episode in range(10)
                 for action in ("message", "verdict")},
            )

    def test_fixture_inventory_contains_only_blocks_1_through_4_formation(self):
        inventory = build_gate3_phase1_fixture_inventory(self.config)
        self.assertEqual(len(inventory), 80)
        self.assertEqual(inventory, dict(self.plan.fixture_inventory))
        self.assertTrue(all(
            name.startswith(("block/1/formation/", "block/2/formation/",
                             "block/3/formation/", "block/4/formation/"))
            for name in inventory
        ))
        self.assertFalse(any(
            token in name for name in inventory
            for token in ("block/0/", "/evaluation/", "/probe/", "calibration/")
        ))

    def test_scientific_factory_accepts_the_dedicated_fixture_subset(self):
        factory = ScientificRequestFactory(self.config, self.plan)
        self.assertEqual(factory.plan.plan_hash, self.plan.plan_hash)
        self.assertEqual(len(factory.designs), 5)

    def test_request_identities_are_disjoint_from_gate2(self):
        gate2 = empirical_config(run_id=GATE2_SUCCESSOR_RUN_ID, stage="smoke")
        gate2_plan = build_gate2_successor_smoke_plan(gate2)
        self.assertFalse(
            {request.identity for request in self.plan.requests}
            & {request.identity for request in gate2_plan.requests}
        )
        self.assertFalse(
            {checkpoint.checkpoint_id for checkpoint in self.plan.checkpoints}
            & {checkpoint.checkpoint_id for checkpoint in gate2_plan.checkpoints}
        )

    def test_fixed_namespace_and_controller_guards_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "frozen Gate 2 namespace"):
            build_gate3_phase1_plan(empirical_config(
                split="pilot/v1.1/different",
                seed_namespace="pilot/v1.1/different",
            ))

        run_stub = SimpleNamespace(
            config=SimpleNamespace(run_id=GATE3_PHASE1_RUN_ID),
            manifest={},
            events=[],
        )
        with self.assertRaisesRegex(AuthorizationRequiredError, "formation-only"):
            StagedExecutor(run_stub, object(), lambda *_args: None)

        event_stub = SimpleNamespace(
            config=SimpleNamespace(run_id="ordinary-run"),
            manifest={},
            events=[{"kind": GATE3_PHASE1_BINDING_EVENT}],
        )
        with self.assertRaisesRegex(AuthorizationRequiredError, "formation-only"):
            StagedExecutor(event_stub, object(), lambda *_args: None)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate3"
            with self.assertRaisesRegex(
                    AuthorizationRequiredError, "must be used together"):
                DurableRun.open(path, self.config, self.plan)
            with self.assertRaisesRegex(
                    AuthorizationRequiredError, "HUMAN AUTHORIZATION REQUIRED"):
                DurableRun.open(
                    path,
                    self.config,
                    self.plan,
                    execution_controller=GATE3_PHASE1_CONTROLLER_ID,
                )
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
