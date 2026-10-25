from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from collusion_recovery.design import ROLES
from collusion_recovery.analysis import _claim_sensitivity_review_ids
from collusion_recovery.common import canonical, digest
from collusion_recovery.events import file_hash
from collusion_recovery.live_config import LiveConfig, LiveConfigError
from collusion_recovery.gates import formation_assessment, smoke_integrity_assessment
from collusion_recovery.orchestration import (
    DurableRun,
    AuthorizationRequiredError,
    StagedExecutor,
    V1_2_CALIBRATION_NAMESPACE,
    V1_2_GATE1_NAMESPACE,
    V1_2_GATE1_RUN_ID,
    V1_2_GATE2_NAMESPACE,
    V1_2_GATE2_RUN_ID,
    V1_2_GATE3_PHASE1_RUN_ID,
    build_request_plan,
    _protocol_hash,
)
from collusion_recovery.visibility import prompt
from collusion_recovery.staged_requests import (
    ScientificRequestFactory,
    _SyntheticAuditContext,
    _synthetic_audit_results,
    evidence_policy_fake_provider,
    protocol_prompt_inventory,
)


def _config_values(protocol_version="v1.2", blocks=5, *, stage="probes"):
    if protocol_version == "v1.2":
        split = "fallback-two-round/v1.2/pilot/unit-test"
        calibration_namespace = "fallback-two-round/v1.2/calibration"
    elif protocol_version == "v1.1":
        split = "pilot/v1.1/unit-test"
        calibration_namespace = "calibration/v1.1"
    else:
        raise ValueError("this focused test helper supports only v1.1 and v1.2")
    return {
        "run_id": "protocol-comparison-unit-test",
        "protocol_version": protocol_version,
        "source": "fake-live",
        "provider": "fake",
        "model": "deterministic-fake-model",
        "model_context_tokens": 1_048_576,
        "model_settings": {"response_policy": "deterministic-scripted-fake"},
        "pricing": {
            "source": "local-fake-provider",
            "verified_at": "2026-10-24T12:00:00-07:00",
            "input_usd_per_million": "0",
            "cached_input_usd_per_million": "0",
            "cache_write_input_usd_per_million": "0",
            "output_usd_per_million": "0",
        },
        "authorization": {
            "kind": "fake-only",
            "authorization_id": "fake-only-v1-2-unit-test",
            "approved_at": "2026-10-24T12:00:00-07:00",
            "approved_budget_usd": "0",
            "allow_paid_calls": False,
        },
        "limits": {
            "max_requests": 3000,
            "max_total_input_tokens": 100_000_000,
            "max_total_output_tokens": 10_000_000,
            "max_input_tokens_per_request": 750_000,
            "max_output_tokens_per_request": 4096,
            "max_in_flight": 2,
        },
        "retry_policy": {
            "transport_retries": 2,
            "message_overlength_retries": 1,
            "initial_backoff_seconds": "0",
            "maximum_backoff_seconds": "0",
        },
        "run_type": "pilot",
        "split": split,
        "stage": stage,
        "seed_namespace": split,
        "calibration_namespace": calibration_namespace,
        "blocks": blocks,
    }


def _config(protocol_version="v1.2", blocks=5, *, stage="probes"):
    return LiveConfig.from_mapping(
        _config_values(protocol_version, blocks, stage=stage)
    )


def _spec(plan, phase, action, **metadata):
    matches = [
        request
        for request in plan.requests
        if request.phase == phase
        and request.action == action
        and all(request.metadata.get(key) == value for key, value in metadata.items())
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected one {phase}/{action} request for {metadata}, found {len(matches)}"
        )
    return matches[0]


def _contains_key(value, key):
    if isinstance(value, dict):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


def _no_network():
    stack = ExitStack()
    for name in (
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
    ):
        stack.enter_context(
            patch(name, side_effect=AssertionError("network access forbidden"))
        )
    return stack


class ProtocolV12Tests(unittest.TestCase):
    @staticmethod
    def _dedicated_config(run_id, split, stage):
        values = _config_values(stage=stage)
        values.update({
            "run_id": run_id,
            "source": "empirical-live",
            "provider": "openai",
            "model": "gpt-6.1-sol",
            "model_settings": {
                "reasoning": {"effort": "low", "mode": "standard"},
                "service_tier": "default",
            },
            "pricing": {
                "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
                "verified_at": "2026-10-24T18:31:56-07:00",
                "input_usd_per_million": "2",
                "cached_input_usd_per_million": "0.10",
                "cache_write_input_usd_per_million": "2.50",
                "output_usd_per_million": "10",
            },
            "authorization": {
                "kind": "paid",
                "authorization_id": "proposal-only-v1-2-test",
                "approved_at": "2026-10-24T18:31:56-07:00",
                "approved_budget_usd": "999999",
                "allow_paid_calls": True,
            },
            "split": split,
            "seed_namespace": split,
            "calibration_namespace": V1_2_CALIBRATION_NAMESPACE,
            "limits": {
                **values["limits"],
                "max_requests": 10000,
                "max_in_flight": 1,
            },
        })
        return LiveConfig.from_mapping(values)

    def test_five_block_pilot_has_exact_v1_2_counts(self):
        plan = build_request_plan(_config())

        self.assertEqual(plan.planned_counts(), {
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
            sum(request.action == "message" for request in plan.requests), 760
        )
        self.assertEqual(
            sum(request.action == "verdict" for request in plan.requests), 820
        )
        self.assertEqual(
            sum(request.phase == "formation" for request in plan.requests), 600
        )

    def test_dedicated_release_plans_are_exact_and_disjoint(self):
        gate1 = build_request_plan(self._dedicated_config(
            V1_2_GATE1_RUN_ID, V1_2_GATE1_NAMESPACE, "calibration"))
        gate2 = build_request_plan(self._dedicated_config(
            V1_2_GATE2_RUN_ID, V1_2_GATE2_NAMESPACE, "smoke"))
        gate3 = build_request_plan(self._dedicated_config(
            V1_2_GATE3_PHASE1_RUN_ID, V1_2_GATE2_NAMESPACE, "formation"))
        self.assertEqual(gate1.planned_counts()["logical_requests"], 20)
        self.assertEqual(gate2.planned_counts(), {
            "logical_requests": 192,
            "by_stage": {"smoke": 192},
            "by_phase": {"formation": 120, "evaluation": 72},
            "checkpoints": 2,
        })
        self.assertEqual(gate3.planned_counts(), {
            "logical_requests": 480,
            "by_stage": {"formation": 480},
            "by_phase": {"formation": 480},
            "checkpoints": 8,
        })
        identities = [
            {request.identity for request in plan.requests}
            for plan in (gate1, gate2, gate3)
        ]
        self.assertFalse(identities[0] & identities[1])
        self.assertFalse(identities[0] & identities[2])
        self.assertFalse(identities[1] & identities[2])

    def test_v1_2_namespace_is_fresh_and_v1_1_namespaces_are_rejected(self):
        config = _config()
        prefix = "fallback-two-round/v1.2/pilot/"
        self.assertTrue(config.split.startswith(prefix))
        self.assertTrue(config.seed_namespace.startswith(prefix))
        self.assertEqual(
            config.calibration_namespace, "fallback-two-round/v1.2/calibration"
        )

        invalid_values = []
        for field in ("split", "seed_namespace"):
            values = _config_values()
            values[field] = "pilot/v1.1/reused"
            invalid_values.append(values)
        values = _config_values()
        values["calibration_namespace"] = "calibration/v1.1"
        invalid_values.append(values)

        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(LiveConfigError):
                LiveConfig.from_mapping(values)

    def test_v1_2_fixtures_and_requests_are_disjoint_from_v1_1(self):
        old_plan = build_request_plan(_config("v1.1"))
        new_plan = build_request_plan(_config("v1.2"))

        old_requests = {request.identity for request in old_plan.requests}
        new_requests = {request.identity for request in new_plan.requests}
        self.assertFalse(old_requests & new_requests)

        old_packet_hashes = {
            fixture["packet_hash"] for fixture in old_plan.fixture_inventory.values()
        }
        new_packet_hashes = {
            fixture["packet_hash"] for fixture in new_plan.fixture_inventory.values()
        }
        self.assertFalse(old_packet_hashes & new_packet_hashes)
        self.assertTrue(all(
            fixture["namespace"].startswith(("calibration/v1.1/", "pilot/v1.1/"))
            for fixture in old_plan.fixture_inventory.values()
        ))
        self.assertTrue(all(
            fixture["namespace"].startswith("fallback-two-round/v1.2/")
            for fixture in new_plan.fixture_inventory.values()
        ))

    def test_formation_has_two_simultaneous_rounds_and_sixty_contributors(self):
        plan = build_request_plan(_config())

        for block in range(5):
            for history in ("U", "C"):
                previous_verdicts = ()
                expected_contributors = []
                for episode in range(10):
                    round_one = tuple(
                        _spec(
                            plan,
                            "formation",
                            "message",
                            block=block,
                            history=history,
                            episode=episode,
                            role=role,
                            message_round=1,
                        )
                        for role in ROLES
                    )
                    round_two = tuple(
                        _spec(
                            plan,
                            "formation",
                            "message",
                            block=block,
                            history=history,
                            episode=episode,
                            role=role,
                            message_round=2,
                        )
                        for role in ROLES
                    )
                    verdicts = tuple(
                        _spec(
                            plan,
                            "formation",
                            "verdict",
                            block=block,
                            history=history,
                            episode=episode,
                            role=role,
                        )
                        for role in ROLES
                    )

                    self.assertEqual(
                        set(round_one[0].dependencies),
                        {item.identity for item in previous_verdicts},
                    )
                    self.assertEqual(round_one[0].dependencies, round_one[1].dependencies)
                    self.assertNotIn(round_one[1].identity, round_one[0].dependencies)
                    self.assertNotIn(round_one[0].identity, round_one[1].dependencies)
                    for request in round_two:
                        self.assertEqual(
                            set(request.dependencies),
                            {message.identity for message in round_one},
                        )
                        self.assertFalse(
                            {message.identity for message in round_two}
                            & set(request.dependencies)
                        )
                    for request in verdicts:
                        self.assertEqual(
                            set(request.dependencies),
                            {message.identity for message in (*round_one, *round_two)},
                        )
                        self.assertFalse(
                            {verdict.identity for verdict in verdicts}
                            & set(request.dependencies)
                        )
                    expected_contributors.extend((*round_one, *round_two, *verdicts))
                    previous_verdicts = verdicts

                checkpoint = next(
                    item
                    for item in plan.checkpoints
                    if item.block == block and item.history == history
                )
                self.assertEqual(len(checkpoint.contributors), 60)
                self.assertEqual(len(set(checkpoint.contributors)), 60)
                self.assertEqual(
                    checkpoint.contributors,
                    tuple(item.identity for item in expected_contributors),
                )

    def test_evaluation_and_probe_schedules_remain_one_round_and_count_neutral(self):
        old_plan = build_request_plan(_config("v1.1"))
        new_plan = build_request_plan(_config("v1.2"))

        for plan in (old_plan, new_plan):
            evaluations = [r for r in plan.requests if r.phase == "evaluation"]
            probes = [r for r in plan.requests if r.phase == "probe"]
            self.assertEqual(len(evaluations), 720)
            self.assertEqual(
                sum(r.action == "message" for r in evaluations), 360
            )
            self.assertEqual(
                sum(r.action == "verdict" for r in evaluations), 360
            )
            self.assertEqual(len(probes), 240)
            self.assertTrue(all(r.action == "verdict" for r in probes))
            self.assertTrue(all("message_round" not in r.metadata for r in evaluations))
            self.assertTrue(all("message_round" not in r.metadata for r in probes))

    def test_request_factory_enforces_round_visibility_and_preserves_rounds(self):
        config = _config(blocks=1)
        plan = build_request_plan(config)
        results = _synthetic_audit_results(config, plan)
        context = _SyntheticAuditContext(results)
        factory = ScientificRequestFactory(config, plan)

        common = {"block": 0, "history": "U", "episode": 0, "role": "alice"}
        round_one = factory.agent_request(
            _spec(plan, "formation", "message", message_round=1, **common), context
        ).payload()
        round_two = factory.agent_request(
            _spec(plan, "formation", "message", message_round=2, **common), context
        ).payload()
        verdict = factory.agent_request(
            _spec(plan, "formation", "verdict", **common), context
        ).payload()

        self.assertEqual(round_one["current"]["message_round"], 1)
        self.assertNotIn("communication", round_one["current"])

        self.assertEqual(round_two["current"]["message_round"], 2)
        round_two_frontier = round_two["current"]["communication"]["message_rounds"]
        self.assertEqual(set(round_two_frontier), {"round_1"})
        self.assertEqual(
            set(round_two_frontier["round_1"]),
            {"own_message", "peer_message", "own_message_attempts"},
        )
        self.assertEqual(
            {
                round_two_frontier["round_1"]["own_message"]["sender"],
                round_two_frontier["round_1"]["peer_message"]["sender"],
            },
            set(ROLES),
        )
        self.assertNotIn("round_2", round_two_frontier)

        verdict_frontier = verdict["current"]["communication"]["message_rounds"]
        self.assertEqual(set(verdict_frontier), {"round_1", "round_2"})
        for visible_round in verdict_frontier.values():
            self.assertEqual(
                {
                    visible_round["own_message"]["sender"],
                    visible_round["peer_message"]["sender"],
                },
                set(ROLES),
            )
        self.assertFalse(_contains_key(verdict["current"], "peer_verdict"))
        self.assertFalse(_contains_key(verdict["current"], "verdicts"))

        history = factory._formation_history(
            context, block=0, condition="U", role="alice", episodes=1
        ).payload()
        self.assertEqual(len(history), 1)
        stored_rounds = history[0]["visible_input"]["communication"]["message_rounds"]
        self.assertEqual(set(stored_rounds), {"round_1", "round_2"})
        self.assertEqual(
            set(stored_rounds["round_1"]),
            {"own_message", "peer_message", "own_message_attempts"},
        )
        self.assertEqual(
            set(stored_rounds["round_2"]),
            {"own_message", "peer_message", "own_message_attempts"},
        )

    def test_prompt_inventory_binds_original_and_formation_only_v1_2_baselines(self):
        inventory = protocol_prompt_inventory(_config(blocks=1))
        self.assertIn("prompt_file/baseline_v1_2.txt", inventory)
        self.assertIn("prompt_file/baseline.txt", inventory)

    def test_nonformation_requests_preserve_the_original_baseline(self):
        config = _config(blocks=1)
        plan = build_request_plan(config)
        context = _SyntheticAuditContext(_synthetic_audit_results(config, plan))
        factory = ScientificRequestFactory(config, plan)

        calibration = factory.agent_request(
            _spec(plan, "calibration", "verdict", fixture_index=0), context
        ).payload()
        formation = factory.agent_request(
            _spec(plan, "formation", "message", block=0, history="U",
                  episode=0, role="alice", message_round=1), context
        ).payload()
        evaluation = factory.agent_request(
            _spec(plan, "evaluation", "message", block=0, arm="A",
                  episode=0, role="alice"), context
        ).payload()
        probe = factory.agent_request(
            next(spec for spec in plan.requests if spec.phase == "probe"), context
        ).payload()

        self.assertEqual(calibration["instructions"][0], prompt("baseline"))
        self.assertEqual(evaluation["instructions"][0], prompt("baseline"))
        self.assertEqual(probe["instructions"][0], prompt("baseline"))
        self.assertEqual(formation["instructions"][0], prompt("baseline_v1_2"))

    def test_transport_only_attempt_is_never_model_visible(self):
        config = _config(blocks=1)
        plan = build_request_plan(config)
        results = dict(_synthetic_audit_results(config, plan))
        message = _spec(
            plan, "formation", "message", block=0, history="U",
            episode=0, role="alice", message_round=1,
        )
        marker = "transport-incident-must-remain-audit-only"
        incident = {
            "kind": "result",
            "identity": message.identity,
            "attempt": 0,
            "status": "ambiguous_timeout",
            "provider_result": {
                "status": "ambiguous_timeout",
                "output_text": None,
                "parsed_output": None,
                "error": marker,
            },
        }
        results[message.identity] = (incident, *results[message.identity])
        context = _SyntheticAuditContext(results)
        factory = ScientificRequestFactory(config, plan)

        verdict = factory.agent_request(
            _spec(plan, "formation", "verdict", block=0, history="U",
                  episode=0, role="alice"), context
        ).payload()
        next_episode = factory.agent_request(
            _spec(plan, "formation", "message", block=0, history="U",
                  episode=1, role="alice", message_round=1), context
        ).payload()
        visible = canonical({"verdict": verdict, "next_episode": next_episode})
        self.assertNotIn("ambiguous_timeout", visible)
        self.assertNotIn(marker, visible)
        self.assertEqual(results[message.identity][0], incident)

    def test_transport_incident_preserves_overlength_retry_prompt(self):
        config = _config(blocks=1)
        plan = build_request_plan(config)
        results = _synthetic_audit_results(config, plan)
        factory = ScientificRequestFactory(config, plan)
        overlength = {
            "status": "overlength",
            "provider_result": {
                "status": "overlength",
                "output_text": "x" * 201,
                "parsed_output": None,
            },
        }
        incident = {
            "status": "ambiguous_timeout",
            "provider_result": {
                "status": "ambiguous_timeout",
                "output_text": None,
                "parsed_output": None,
                "error": "audit-only transport incident",
            },
        }
        specs = (
            _spec(
                plan, "formation", "message", block=0, history="U",
                episode=0, role="alice", message_round=1,
            ),
            _spec(
                plan, "evaluation", "message", block=0, arm="A",
                episode=0, role="alice",
            ),
        )
        for spec in specs:
            with self.subTest(identity=spec.identity):
                retry, _ = factory.build(
                    spec,
                    _SyntheticAuditContext(
                        results, attempt=1, prior_results=(overlength,)
                    ),
                )
                recovered, _ = factory.build(
                    spec,
                    _SyntheticAuditContext(
                        results,
                        attempt=2,
                        prior_results=(overlength, incident),
                    ),
                )
                self.assertEqual(retry.turns, recovered.turns)
                self.assertEqual(retry.input_hash, recovered.input_hash)
                self.assertIn("Retry once", retry.turns[-1].content)

    def test_transport_claim_sensitivity_uses_transitive_plan_closure(self):
        plan = build_request_plan(_config(blocks=1))
        origin = _spec(
            plan, "formation", "message", block=0, history="U",
            episode=7, role="alice", message_round=1,
        )
        affected = set(_claim_sensitivity_review_ids(plan, origin.identity))

        for episode in (7, 8, 9):
            for role in ROLES:
                self.assertIn(
                    _spec(
                        plan, "formation", "verdict", block=0, history="U",
                        episode=episode, role=role,
                    ).identity,
                    affected,
                )
        self.assertNotIn(
            _spec(
                plan, "formation", "verdict", block=0, history="C",
                episode=7, role="alice",
            ).identity,
            affected,
        )
        self.assertTrue(any(
            request.identity in affected and request.phase == "evaluation"
            for request in plan.requests
        ))
        self.assertTrue(any(
            request.identity in affected and request.phase == "probe"
            for request in plan.requests
        ))

    def test_v1_2_protocol_hash_binds_base_and_addendum(self):
        root = Path(__file__).resolve().parents[1]
        expected = digest({
            "base_protocol_sha256": file_hash(root / "SPRINT_PROJECT_PLAN.md"),
            "addendum_sha256": file_hash(root / "PROTOCOL_V1_2_ADDENDUM.md"),
        })
        self.assertEqual(_protocol_hash("v1.2"), expected)

    def test_arbitrary_empirical_v1_2_ids_and_unimplemented_later_controllers_fail(self):
        values = self._dedicated_config(
            V1_2_GATE1_RUN_ID, V1_2_GATE1_NAMESPACE, "calibration"
        ).to_dict()
        values["run_id"] = "arbitrary-v1-2-empirical-run"
        arbitrary = LiveConfig.from_mapping(values)
        with self.assertRaisesRegex(ValueError, "fixed staged-release"):
            build_request_plan(arbitrary)

        run_stub = SimpleNamespace(
            config=SimpleNamespace(
                run_id=V1_2_GATE2_RUN_ID,
                protocol_version="v1.2",
                source="empirical-live",
            ),
            manifest={"execution_controller": "gate2_smoke_v1_2"},
            events=[],
        )
        with self.assertRaisesRegex(AuthorizationRequiredError, "projection-only"):
            StagedExecutor(
                run_stub, object(), lambda *_args: None,
                dedicated_controller="gate2_smoke_v1_2",
            )

        with self.assertRaisesRegex(ValueError, "cross-run integrity assessment"):
            smoke_integrity_assessment(run_stub)

        gate3_stub = SimpleNamespace(
            config=SimpleNamespace(
                run_id=V1_2_GATE3_PHASE1_RUN_ID,
                protocol_version="v1.2",
                source="empirical-live",
                run_type="pilot",
            )
        )
        with self.assertRaisesRegex(ValueError, "cross-run five-block assessment"):
            formation_assessment(gate3_stub)

    def test_fake_provider_executes_calibration_prefix_without_network(self):
        config = _config(blocks=1, stage="calibration")
        plan = build_request_plan(config)
        provider = evidence_policy_fake_provider(config, plan)
        factory = ScientificRequestFactory(config, plan)

        with tempfile.TemporaryDirectory() as directory, _no_network():
            run = DurableRun.open(Path(directory) / "v1-2-fake", config, plan)
            report = StagedExecutor(run, provider, factory).execute("calibration")

        self.assertEqual(report.status, "completed_stage")
        self.assertEqual(report.dispatched_attempts, 20)
        self.assertEqual(report.completed_logical_requests, 20)
        self.assertEqual(len(provider.calls), 20)
        self.assertTrue(all(call["source"] == "fake-live" for call in provider.calls))
        self.assertTrue(all(call["response_mode"] == "verdict" for call in provider.calls))


if __name__ == "__main__":
    unittest.main()
