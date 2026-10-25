import json
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from collusion_recovery.common import canonical, digest, utc_now
from collusion_recovery.gate3_phase1 import (
    CONTROLLER_ID,
    HUMAN_ACKNOWLEDGEMENT,
    RUN_ID,
    Gate3FormationExecutor,
    Gate3Phase1Error,
    _bind_current_implementation_to_git,
    _constrained_payload_verified,
    _formation_gate_decision,
    _gate3_config_and_plan,
    _verify_gate3_result_evidence,
    assess_composite_formation,
    build_gate3_phase1_proposal,
    complete_gate3_human_authorization,
    gate3_authorization_schema,
    inspect_gate3_phase1,
    prepare_gate3_phase1_run,
    claim_gate3_authorization_once,
    run_gate3_phase1,
    validate_gate3_human_authorization,
)
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    StagedExecutor,
)
from collusion_recovery.providers.base import TransportResponse
from collusion_recovery.providers.openai import OpenAIResponsesProvider
from collusion_recovery.staged_requests import ScientificRequestFactory


def authorization(proposal, *, confirmed_at=None):
    authorization_uuid = str(uuid.uuid4())
    external = {
        "artifact_type": "gate_3_phase1_human_authorization_v1_1",
        "authorization_uuid": authorization_uuid,
        "authorization_id": f"gate3-phase1-human-{authorization_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "gate3_run_id": RUN_ID,
        "gate3_run_directory": proposal["gate3_protocol"]["run_directory"],
        "confirmed_by": "test human",
        "confirmed_at": confirmed_at or utc_now(),
        "proposal_hash": proposal["proposal_hash"],
        "approved_scope": proposal["formation_scope"],
        "approved_caps": proposal["exact_caps"],
        "approved_pricing_contract_hash": proposal["pricing_contract_hash"],
        "approved_transport_hash": proposal["transport_hash"],
        "approved_provider_identity": proposal["required_provider_identity"],
        "approved_continuity_bridge_hash": proposal["continuity_bridge"]["bridge_hash"],
        "approved_formation_inventory_hash": proposal["formation_projection"][
            "inventory_hash"
        ],
        "allow_paid_calls": True,
        "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
    }
    return complete_gate3_human_authorization(
        external, proposal, _proposal_verified=True
    )


class FakeResponsesTransport:
    def __init__(self):
        self.payloads = []

    def send(self, payload):
        self.payloads.append(json.loads(canonical(payload)))
        verdict = payload["text"]["format"]["type"] == "json_schema"
        text = (
            canonical({
                "verdict": "REJECT",
                "evidence_record_ids": [],
                "brief_reason": "Deterministic fake transport output.",
            })
            if verdict else
            "I will apply the required evidence verification rule."
        )
        body = {
            "id": f"fake-{len(self.payloads)}",
            "object": "response",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": "gpt-6.1-sol",
            "service_tier": "default",
            "store": False,
            "created_at": 1792900000,
            "system_fingerprint": None,
            "output": [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text}],
            }],
            "usage": {
                "input_tokens": 1,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 1,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 2,
            },
        }
        return TransportResponse.from_mapping(body, request_id=body["id"])


class Gate3Phase1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proposal = build_gate3_phase1_proposal(
            require_committed_current_source=False
        )
        cls.authorization = authorization(cls.proposal)
        cls.config, cls.plan, cls.dispatch, cls.inventory = _gate3_config_and_plan(
            cls.proposal,
            cls.authorization,
            _proposal_verified=True,
        )

    def test_exact_scope_caps_commitment_and_schema(self):
        proposal = self.proposal
        projection = proposal["formation_projection"]
        self.assertEqual(proposal["status"], "awaiting_external_human_authorization")
        self.assertIs(proposal["dispatch_enabled"], False)
        self.assertEqual(projection["logical_request_count"], 320)
        self.assertEqual(projection["message_request_count"], 160)
        self.assertEqual(projection["verdict_request_count"], 160)
        self.assertEqual(projection["checkpoint_count"], 8)
        self.assertEqual(projection["maximum_billable_attempt_shapes"], 480)
        self.assertEqual(
            projection["maximum_known_not_completed_transport_retry_attempts"], 640
        )
        self.assertEqual(projection["maximum_dispatch_attempts"], 1120)
        self.assertEqual(projection["output_token_allowance_sum"], 1_966_080)
        self.assertEqual(proposal["exact_caps"]["max_total_input_tokens"], 83_647_908)
        self.assertEqual(proposal["exact_caps"]["max_cost_usd"], "312.634350")
        self.assertEqual(proposal["formation_scope"]["blocks"], [1, 2, 3, 4])
        self.assertIs(proposal["formation_scope"]["block0_replay_authorized"], False)
        self.assertEqual(proposal["formation_scope"]["evaluation_arms_authorized"], [])
        self.assertIs(proposal["formation_scope"]["probes_authorized"], False)
        partition = proposal["full_pilot_commitment"]["partition"]
        self.assertEqual(
            [row["logical_requests"] for row in partition.values()],
            [152, 320, 888],
        )
        self.assertIs(proposal["hard_stop"]["next_stage_authorized"], False)
        self.assertIs(
            proposal["hard_stop"]["composite_formation_decision_in_this_release"],
            False,
        )
        continuity = proposal["continuity_bridge"]["current_source_continuity"]
        self.assertEqual(continuity["historical_file_count"], 43)
        self.assertEqual(continuity["current_file_count"], 45)
        self.assertEqual(
            set(continuity["changed_files"]),
            {
                "src/collusion_recovery/orchestration.py",
                "src/collusion_recovery/staged_requests.py",
                "src/collusion_recovery/gate2_successor.py",
            },
        )
        self.assertEqual(
            set(continuity["new_files"]),
            {
                "src/collusion_recovery/gate2_passed_lineage.py",
                "src/collusion_recovery/gate3_phase1.py",
            },
        )
        self.assertEqual(continuity["forbidden_changed_paths"], [])
        self.assertIs(
            continuity["current_git_binding"]["required_for_release"], True
        )
        with patch(
            "collusion_recovery.gate3_phase1.build_gate3_phase1_proposal",
            return_value=proposal,
        ):
            schema = gate3_authorization_schema(proposal)
        self.assertEqual(
            schema["properties"]["approved_caps"]["const"],
            proposal["exact_caps"],
        )
        self.assertEqual(len(self.plan.requests), 320)
        self.assertEqual(len(self.plan.checkpoints), 8)
        self.assertEqual(len(self.plan.fixture_inventory), 80)

    def test_authorization_is_exact_self_hashed_and_derived(self):
        auth = self.authorization
        self.assertEqual(
            validate_gate3_human_authorization(
                auth, self.proposal, _proposal_verified=True
            ),
            auth,
        )
        tampered = json.loads(canonical(auth))
        tampered["approved_caps"]["max_dispatch_attempts"] -= 1
        body = dict(tampered)
        body.pop("authorization_hash")
        tampered["authorization_hash"] = digest(body)
        with self.assertRaisesRegex(Gate3Phase1Error, "exact phase-1 scope"):
            validate_gate3_human_authorization(
                tampered, self.proposal, _proposal_verified=True
            )
        weak_nonce = json.loads(canonical(auth))
        weak_nonce["authorization_nonce"] = "a" * 64
        body = dict(weak_nonce)
        body.pop("authorization_hash")
        weak_nonce["authorization_hash"] = digest(body)
        with self.assertRaisesRegex(Gate3Phase1Error, "fresh 64-hex"):
            validate_gate3_human_authorization(
                weak_nonce, self.proposal, _proposal_verified=True
            )

    def test_inspection_is_inert(self):
        with patch(
                "collusion_recovery.gate3_phase1.load_gate3_phase1_proposal",
                return_value=self.proposal), patch(
                "collusion_recovery.gate3_phase1._load_local_openai_key",
                side_effect=AssertionError("credential access forbidden")):
            result = inspect_gate3_phase1(self.proposal, now=None)
        self.assertEqual(result["status"], "ready_awaiting_external_human_authorization")
        self.assertIs(result["credentials_accessed"], False)
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["paid_calls"], 0)
        self.assertIs(result["next_stage_authorized"], False)

    def test_fixed_implementation_binding_survives_release_only_head_advance(self):
        implementation_commit = "1" * 40
        implementation_tree = "2" * 40
        advanced_head = "3" * 40
        advanced_tree = "4" * 40
        blob = b"fixed implementation bytes\n"
        inventory = {
            "src/collusion_recovery/example.py": hashlib.sha256(blob).hexdigest()
        }

        def tree(commit):
            return {
                implementation_commit: implementation_tree,
                advanced_head: advanced_tree,
            }[commit]

        with patch(
                "collusion_recovery.gate3_phase1._git_tree",
                side_effect=tree), patch(
                "collusion_recovery.gate3_phase1._git_bytes",
                return_value=blob), patch(
                "collusion_recovery.gate3_phase1._git_is_ancestor",
                return_value=True):
            with patch(
                    "collusion_recovery.gate3_phase1._git_head",
                    return_value=implementation_commit):
                initial = _bind_current_implementation_to_git(
                    inventory,
                    require_binding=True,
                    implementation_commit=implementation_commit,
                    implementation_tree=implementation_tree,
                )
            with patch(
                    "collusion_recovery.gate3_phase1._git_head",
                    return_value=advanced_head):
                binding = _bind_current_implementation_to_git(
                    inventory,
                    require_binding=True,
                    implementation_commit=implementation_commit,
                    implementation_tree=implementation_tree,
                )

        self.assertEqual(binding, initial)
        self.assertIs(binding["verified"], True)
        self.assertEqual(binding["implementation_commit"], implementation_commit)
        self.assertEqual(binding["implementation_tree"], implementation_tree)
        self.assertEqual(binding["unbound_inventory_paths"], [])

    def test_retry_allowance_is_global_across_message_shapes(self):
        controller = object.__new__(Gate3FormationExecutor)
        controller.run = type("Run", (), {})()
        controller.run.config = type("Config", (), {})()
        controller.run.config.retry_policy = type("Retry", (), {
            "transport_retries": 2,
            "message_overlength_retries": 1,
        })()
        spec = type("Spec", (), {"action": "message"})()
        rows = [
            {"status": "retryable_error"},
            {"status": "overlength"},
            {"status": "retryable_error"},
        ]
        self.assertIs(controller._status_terminal(spec, rows), False)
        rows.append({"status": "retryable_error"})
        self.assertIs(controller._status_terminal(spec, rows), True)

    def test_constrained_payload_requires_exact_semantic_binding_and_32_records(self):
        identity = "request-" + "a" * 64
        spec = {
            "identity": identity,
            "phase": "formation",
            "action": "verdict",
            "metadata": {
                "block": 2,
                "history": "C",
                "episode": 8,
                "role": "alice",
            },
        }

        def payload(required_record_count):
            tagged = {
                "current": {
                    "phase": "formation",
                    "agent": "alice",
                    "peer_packet": {
                        "required_record_count": required_record_count,
                    },
                },
                "history": [],
            }
            return {
                "request_identity": identity,
                "response_mode": "verdict",
                "turns": [
                    {"role": "developer", "content": "rules"},
                    {"role": "user", "content": "evidence\n" + canonical(tagged)},
                ],
            }

        with tempfile.TemporaryDirectory() as directory:
            payload_directory = Path(directory)
            valid = payload(32)
            valid_hash = digest(valid)
            (payload_directory / f"{valid_hash}.json").write_text(
                canonical(valid) + "\n", encoding="utf-8"
            )
            row = {"request_hash": valid_hash}
            self.assertIs(
                _constrained_payload_verified(
                    payload_directory,
                    row,
                    spec=spec,
                    block=2,
                    episode=8,
                    role="alice",
                ),
                True,
            )
            self.assertIs(
                _constrained_payload_verified(
                    payload_directory,
                    row,
                    spec=spec,
                    block=3,
                    episode=8,
                    role="alice",
                ),
                False,
            )
            invalid = payload(31)
            invalid_hash = digest(invalid)
            (payload_directory / f"{invalid_hash}.json").write_text(
                canonical(invalid) + "\n", encoding="utf-8"
            )
            self.assertIs(
                _constrained_payload_verified(
                    payload_directory,
                    {"request_hash": invalid_hash},
                    spec=spec,
                    block=2,
                    episode=8,
                    role="alice",
                ),
                False,
            )

    def test_composite_rule_is_exactly_three_of_five_with_no_unknown_or_failure(self):
        trajectories = [
            {"block": block, "marker": block in {1, 2, 3}}
            for block in range(5)
        ]
        self.assertIs(
            _formation_gate_decision(trajectories, [])["passed"], True
        )
        trajectories[4]["marker"] = None
        self.assertIs(
            _formation_gate_decision(trajectories, [])["passed"], False
        )
        trajectories[4]["marker"] = False
        self.assertIs(
            _formation_gate_decision(
                trajectories, [{"identity": "x", "status": "bad"}]
            )["passed"],
            False,
        )

    def test_generic_executor_rejects_fixed_gate3_run(self):
        run = type("Run", (), {})()
        run.config = type("Config", (), {"run_id": RUN_ID})()
        run.manifest = {"execution_controller": CONTROLLER_ID}
        run.events = []
        with self.assertRaises(AuthorizationRequiredError):
            StagedExecutor(run, object(), lambda *_args: None)

    def test_run_claims_before_credential_access(self):
        dummy_run = type("Run", (), {"manifest": {"status": "prepared_gate3_phase1"}})()
        with tempfile.TemporaryDirectory() as directory, patch(
                "collusion_recovery.gate3_phase1.load_gate3_phase1_proposal",
                return_value=self.proposal), patch(
                "collusion_recovery.gate3_phase1.load_gate3_human_authorization",
                return_value=self.authorization), patch(
                "collusion_recovery.gate3_phase1._authorization_receipt_path",
                return_value=Path(directory) / "absent-receipt.json"), patch(
                "collusion_recovery.gate3_phase1._validate_first_claim_window"), patch(
                "collusion_recovery.gate3_phase1._gate3_config_and_plan",
                return_value=(self.config, self.plan, self.dispatch, self.inventory)), patch(
                "collusion_recovery.gate3_phase1.prepare_gate3_phase1_run",
                return_value=(
                    dummy_run, self.config, self.plan, self.dispatch, self.inventory
                )), patch(
                "collusion_recovery.gate3_phase1.claim_gate3_authorization_once",
                side_effect=Gate3Phase1Error("claim rejected")), patch(
                "collusion_recovery.gate3_phase1._load_local_openai_key") as key_loader:
            with self.assertRaisesRegex(Gate3Phase1Error, "claim rejected"):
                run_gate3_phase1(Path(directory) / "authorization.json")
        key_loader.assert_not_called()

    def test_fake_execution_hard_stops_before_decision_and_resumes(self):
        auth = authorization(self.proposal)
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as external:
            root = Path(workspace)
            run_path = root / auth["gate3_run_directory"]
            receipt_dir = Path(external) / "receipts" / "gate3-phase1-v1.1"
            with patch(
                    "collusion_recovery.gate3_phase1.AUTHORIZATION_RECEIPT_DIRECTORY",
                    receipt_dir):
                run, _config, _plan, _dispatch, inventory = prepare_gate3_phase1_run(
                    run_path,
                    self.proposal,
                    auth,
                    workspace_root=root,
                    _proposal_verified=True,
                )
                receipt = claim_gate3_authorization_once(
                    run,
                    self.proposal,
                    auth,
                    receipt_dir=receipt_dir,
                )
                transport = FakeResponsesTransport()
                provider = OpenAIResponsesProvider(transport)
                controller = Gate3FormationExecutor(
                    run,
                    provider,
                    self.proposal,
                    inventory,
                    authorization_receipt=receipt,
                    fake_transport_test=True,
                )
                report = controller.execute()
                event_count_before_assessment = run.manifest["event_count"]
                assessment = assess_composite_formation(
                    run_path,
                    _proposal=self.proposal,
                    _allow_fake_transport_test=True,
                )
                self.assertEqual(
                    run.manifest["event_count"], event_count_before_assessment
                )
                final_result = run.result_events()[-1]
                original_store = final_result["provider_result"]["raw_response"][
                    "store"
                ]
                final_result["provider_result"]["raw_response"]["store"] = True
                with self.assertRaisesRegex(
                        Gate3Phase1Error, "raw-response parity"):
                    _verify_gate3_result_evidence(run, self.proposal)
                final_result["provider_result"]["raw_response"][
                    "store"
                ] = original_store
                event_count = run.manifest["event_count"]
                payload_count = len(transport.payloads)
                resumed = Gate3FormationExecutor(
                    run,
                    provider,
                    self.proposal,
                    inventory,
                    authorization_receipt=receipt,
                    fake_transport_test=True,
                ).execute()
            self.assertEqual(report, resumed)
            self.assertEqual(report["logical_requests"], 320)
            self.assertEqual(report["dispatch_attempts"], 320)
            self.assertEqual(payload_count, 320)
            self.assertEqual(len(transport.payloads), 320)
            self.assertEqual(run.manifest["event_count"], event_count)
            self.assertEqual(
                report["status"],
                "completed_gate3_phase1_awaiting_formation_decision_hard_stop",
            )
            self.assertIs(report["formation_decision_recorded"], False)
            self.assertIs(report["next_stage_authorized"], False)
            self.assertEqual(report["block0_dispatches"], 0)
            self.assertEqual(report["evaluation_dispatches"], 0)
            self.assertEqual(report["probe_dispatches"], 0)
            self.assertEqual(
                report["report_hash"],
                digest({key: value for key, value in report.items() if key != "report_hash"}),
            )
            self.assertEqual(len(run.checkpoint_events()), 8)
            self.assertEqual(run.gate_events(), {})
            self.assertEqual(len(assessment["trajectories"]), 5)
            self.assertEqual(
                assessment["gate3_formation_report_hash"], report["report_hash"]
            )
            self.assertEqual(
                assessment["status"], "formation_assessed_no_phase2_release"
            )
            self.assertIs(assessment["trajectories"][0]["marker"], False)
            self.assertIs(assessment["block0_asserted_false"], True)
            self.assertEqual(assessment["integrity_failure_count"], 0)
            self.assertIs(assessment["passed"], False)
            self.assertIs(assessment["phase2_authorized"], False)
            self.assertIs(assessment["next_stage_authorized"], False)
            self.assertEqual(assessment["network_calls"], 0)
            self.assertEqual(assessment["paid_calls"], 0)
            self.assertEqual(
                assessment["assessment_hash"],
                digest({
                    key: value for key, value in assessment.items()
                    if key != "assessment_hash"
                }),
            )


if __name__ == "__main__":
    unittest.main()
