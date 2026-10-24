from contextlib import ExitStack, nullcontext
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
from unittest.mock import Mock

from collusion_recovery.common import canonical, digest
from collusion_recovery.gate2_successor import (
    AUTHORIZATION_CLAIM_EVENT,
    CONTROLLER_ID,
    EXECUTION_MODE_BINDING_EVENT,
    EXPERIMENT_STARTED_EVENT,
    HUMAN_ACKNOWLEDGEMENT,
    SMOKE_COMPLETED_EVENT,
    SUCCESSOR_PROTOCOL_VERSION,
    Gate2SmokeExecutor,
    Gate2SuccessorError,
    build_gate2_successor_proposal,
    claim_gate2_authorization_once,
    complete_gate2_human_authorization,
    gate2_authorization_schema,
    inspect_gate2_successor,
    record_gate2_smoke_decision,
    _authorization_receipt_directory,
    _authorization_receipt_path,
    _audit_successor_smoke_inventory,
    _SMOKE_INVENTORY_CACHE,
    _smoke_integrity_assessment_locked,
    _successor_config_and_plan,
    _verify_completed_gate2_smoke,
    prepare_gate2_successor_run,
    run_gate2_successor,
    validate_gate2_human_authorization,
    validate_gate2_smoke_review,
)
from collusion_recovery.gates import (
    SMOKE_REVIEW_BOOLEAN_FIELDS,
)
from collusion_recovery.live_config import LiveConfig, LiveConfigError
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    DurableRun,
    ResumeError,
    StagedExecutor,
)
from collusion_recovery.providers.openai import OpenAIResponsesProvider
from collusion_recovery.providers.base import (
    ProviderStatus,
    TransportFailure,
    TransportResponse,
)
from collusion_recovery.staged_requests import ScientificRequestFactory


def no_network():
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


TEST_NOW = datetime.fromisoformat("2026-10-23T22:05:00-07:00")


def authorization(proposal, authorization_uuid=None, *,
                  confirmed_at="2026-10-23T22:00:00-07:00"):
    authorization_uuid = authorization_uuid or str(uuid.uuid4())
    run_id = proposal["successor_protocol"]["run_id"]
    value = {
        "artifact_type": "gate_2_smoke_human_authorization_v1_1",
        "authorization_uuid": authorization_uuid,
        "authorization_id": f"gate2-human-{authorization_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "successor_run_id": run_id,
        "successor_run_directory": f"runs/empirical/gate2-smoke-v1.1-{run_id}",
        "confirmed_by": "test human",
        "confirmed_at": confirmed_at,
        "proposal_hash": proposal["proposal_hash"],
        "approved_scope": proposal["smoke_scope"],
        "approved_caps": proposal["exact_caps"],
        "approved_pricing_contract_hash": proposal["pricing_contract_hash"],
        "approved_transport_hash": proposal["transport_hash"],
        "approved_provider_identity": proposal["required_provider_identity"],
        "approved_protocol_bridge_hash": proposal["protocol_bridge"]["bridge_hash"],
        "approved_smoke_inventory_hash": proposal["smoke_projection"]["inventory_hash"],
        "allow_paid_calls": True,
        "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
    }
    return complete_gate2_human_authorization(
        value, proposal, _proposal_verified=True
    )


class FakeResponsesTransport:
    """No-network Responses transport returning exact Stage 2 identity metadata."""

    def __init__(self, audit_metadata=None):
        self.payloads = []
        self.audit_metadata = audit_metadata

    def send(self, payload):
        self.payloads.append(json.loads(canonical(payload)))
        is_verdict = payload["text"]["format"]["type"] == "json_schema"
        text = (
            canonical({
                "verdict": "REJECT",
                "evidence_record_ids": [],
                "brief_reason": "Deterministic fake transport test output.",
            })
            if is_verdict else
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
            "created_at": 1792760400,
            "system_fingerprint": None,
            "output": [
                {"type": "reasoning", "content": []},
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": text}],
                },
            ],
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


class OverlengthRetrySmokeTransport(FakeResponsesTransport):
    """First message: overlength, known-not-completed, then success."""

    def send(self, payload):
        position = len(self.payloads)
        if position == 1:
            self.payloads.append(json.loads(canonical(payload)))
            raise TransportFailure(
                ProviderStatus.RETRYABLE_ERROR,
                "known not completed after overlength",
                known_not_completed=True,
            )
        if position != 0:
            return super().send(payload)
        self.payloads.append(json.loads(canonical(payload)))
        body = {
            "id": "fake-overlength-1",
            "object": "response",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": "gpt-6.1-sol",
            "service_tier": "default",
            "created_at": 1792760400,
            "system_fingerprint": None,
            "output": [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "x" * 201}],
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


class Gate2SuccessorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate1_lineage = {
            "run_id": "1b61ea2d-e609-4c67-a649-b889722dba9b",
            "protocol_version": "v1.1",
            "protocol_hash": "1" * 64,
            "config_hash": "2" * 64,
            "plan_hash": "3" * 64,
            "implementation_hash": "4" * 64,
            "fixture_inventory_hash": "5" * 64,
            "calibration_inventory_hash": "6" * 64,
            "proposal_hash": "7" * 64,
            "authorization_hash": "8" * 64,
            "authorization_receipt_hash": "9" * 64,
            "authorization_claim_event_hash": "0" * 64,
            "binding_event_hash": "a" * 64,
            "assessment_hash": "b" * 64,
            "gate_decision_event_hash": "c" * 64,
            "report_hash": "d" * 64,
            "journal_tail": "e" * 64,
            "journal_event_count": 42,
            "transport_hash": "f" * 64,
            "provider_identity": {
                "requested_model": "gpt-6.1-sol",
                "returned_model": "gpt-6.1-sol",
                "metadata_model": "gpt-6.1-sol",
                "requested_service_tier": "default",
                "returned_service_tier": "default",
                "system_fingerprint": None,
            },
            "passed": True,
        }
        cls.gate1_patcher = patch(
            "collusion_recovery.gate2_successor.verify_gate1_passed_lineage",
            return_value=cls.gate1_lineage,
        )
        cls.gate1_patcher.start()
        with no_network():
            cls.proposal = build_gate2_successor_proposal()

    @classmethod
    def tearDownClass(cls):
        cls.gate1_patcher.stop()

    def _claim(self, run, proposal, auth, *, receipt_dir, now):
        with patch(
            "collusion_recovery.gate2_successor.AUTHORIZATION_RECEIPT_DIRECTORY",
            receipt_dir,
        ):
            return claim_gate2_authorization_once(
                run,
                proposal,
                auth,
                receipt_dir=receipt_dir,
                now=now,
            )

    def _smoke_review(self, proposal, report, assessment, completion, *,
                      decision="pass"):
        completed = max(
            datetime.fromisoformat(
                completion["recorded_at"].replace("Z", "+00:00")
            ),
            datetime.fromisoformat(
                assessment["smoke_completed_at"].replace("Z", "+00:00")
            ),
        )
        body = {
            "artifact_type": "gate_2_v1_1_smoke_operator_review_v1",
            "review_uuid": str(uuid.uuid4()),
            "reviewer": "test smoke reviewer",
            "reviewed_at": (completed + timedelta(seconds=1)).isoformat(),
            "notes": "Reviewed the complete bound smoke evidence.",
            **{
                name: True for name in SMOKE_REVIEW_BOOLEAN_FIELDS
            },
            "operator_decision": decision,
            "proposal_hash": proposal["proposal_hash"],
            "gate2_run_id": proposal["successor_protocol"]["run_id"],
            "smoke_report_hash": report["report_hash"],
            "smoke_completed_event_hash": completion["event_hash"],
            "smoke_completed_at": completion["recorded_at"],
            "reviewed_journal_tail": completion["event_hash"],
            "reviewed_journal_event_count": completion["sequence"] + 1,
            "mechanical_assessment_hash": digest(assessment),
            "gate1_lineage_hash": proposal["gate1_passed_lineage"][
                "lineage_hash"
            ],
        }
        return {**body, "review_hash": digest(body)}

    def test_proposal_generation_requires_exact_passed_gate1_verifier(self):
        with patch(
            "collusion_recovery.gate2_successor.verify_gate1_passed_lineage",
            side_effect=Gate2SuccessorError("missing passed Gate 1"),
        ):
            with self.assertRaisesRegex(Gate2SuccessorError, "Gate 1"):
                build_gate2_successor_proposal()

    def test_proposal_binds_exact_scope_caps_and_external_schema(self):
        proposal = self.proposal
        self.assertEqual(proposal["status"], "awaiting_external_human_authorization")
        self.assertIs(proposal["dispatch_enabled"], False)
        self.assertIs(proposal["experiment_started"], False)
        self.assertEqual(
            proposal["successor_protocol"]["protocol_version"],
            SUCCESSOR_PROTOCOL_VERSION,
        )
        self.assertEqual(proposal["stage2_lineage"]["protocol_version"], "v1.0")
        self.assertNotEqual(
            proposal["stage2_lineage"]["protocol_hash"],
            proposal["successor_protocol"]["protocol_hash"],
        )
        self.assertEqual(
            proposal["protocol_bridge"]["bridge_hash"],
            digest({
                key: value for key, value in proposal["protocol_bridge"].items()
                if key != "bridge_hash"
            }),
        )
        self.assertEqual(proposal["smoke_scope"]["logical_request_count"], 152)
        self.assertEqual(proposal["smoke_scope"]["maximum_dispatch_attempts"], 532)
        self.assertEqual(
            proposal["smoke_projection"]["maximum_billable_attempt_shapes"], 228
        )
        self.assertEqual(proposal["exact_caps"]["max_dispatch_attempts"], 532)
        self.assertEqual(
            proposal["smoke_projection"][
                "maximum_known_not_completed_transport_retry_attempts"
            ],
            304,
        )
        self.assertIs(
            proposal["attempt_accounting"][
                "transport_retry_allowance_resets_after_overlength"
            ],
            False,
        )
        self.assertEqual(
            proposal["attempt_accounting"]["maximum_dispatch_attempts"], 532
        )
        self.assertEqual(
            proposal["exact_caps"]["max_total_input_tokens"],
            proposal["smoke_projection"]["conservative_input_token_bound_sum"],
        )
        self.assertEqual(
            proposal["exact_caps"]["max_total_output_tokens"],
            proposal["smoke_projection"]["output_token_allowance_sum"],
        )
        self.assertEqual(
            proposal["exact_caps"]["max_cost_usd"],
            proposal["smoke_projection"]["pricing_projection"][
                "strict_all_shapes_reservation_ceiling_usd"
            ],
        )
        self.assertEqual(
            proposal["pricing_contract"]["standard_rates_usd_per_million"],
            proposal["successor_config_template"]["pricing"],
        )
        self.assertGreater(float(proposal["exact_caps"]["max_cost_usd"]), 0)
        self.assertNotIn("requests", proposal["smoke_projection"])
        self.assertIs(proposal["hard_stop"]["later_stage_dispatch_possible"], False)
        self.assertIs(
            proposal["external_authorization"][
                "expired_unclaimed_prepared_run_requires_new_versioned_release"
            ],
            True,
        )
        schema = gate2_authorization_schema(proposal)
        self.assertEqual(
            schema["properties"]["proposal_hash"]["const"], proposal["proposal_hash"]
        )
        self.assertEqual(
            schema["properties"]["approved_caps"]["const"], proposal["exact_caps"]
        )
        config, plan, _dispatch, inventory = _successor_config_and_plan(
            proposal,
            authorization(proposal),
            _proposal_verified=True,
        )
        self.assertEqual(len(plan.requests), 152)
        self.assertEqual(len(plan.checkpoints), 2)
        self.assertEqual(len(plan.fixture_inventory), 32)
        self.assertEqual(
            {spec.stage for spec in plan.requests}, {"smoke"}
        )
        self.assertTrue(all(
            spec.metadata.get("block") == 0 for spec in plan.requests
        ))
        self.assertEqual(
            {spec.metadata.get("arm") for spec in plan.requests
             if spec.phase == "evaluation"},
            {"A", "B", "F"},
        )
        self.assertEqual(inventory["logical_request_count"], 152)
        self.assertEqual(config.calibration_namespace, "calibration/v1.1")

    def test_transport_retry_allowance_does_not_reset_after_overlength(self):
        """One message identity has two transport retries total, not per shape."""
        controller = object.__new__(Gate2SmokeExecutor)
        controller._execution_mode = "fake_transport_test"
        controller.run = SimpleNamespace(
            config=SimpleNamespace(
                retry_policy=SimpleNamespace(
                    transport_retries=2,
                    message_overlength_retries=1,
                )
            ),
            plan=SimpleNamespace(requests=[]),
        )
        spec = SimpleNamespace(action="message")
        results = [
            {"status": "retryable_error"},
            {"status": "overlength"},
            {"status": "retryable_error"},
        ]
        self.assertIs(controller._status_terminal(spec, results), False)

        # A third retryable outcome is terminal even though it occurs after
        # the overlength content revision.  No fifth dispatch is possible.
        results.append({"status": "retryable_error"})
        self.assertIs(controller._status_terminal(spec, results), True)

        # Exhausting the content-revision allowance is terminal but never a
        # successful resolution that could unlock dependent smoke requests.
        spec.identity = "message-under-test"
        controller.run.plan.requests = [spec]
        overlength = [{"status": "overlength"}, {"status": "overlength"}]
        controller.run.result_events = lambda _identity: overlength
        self.assertIs(controller._status_terminal(spec, overlength), True)
        self.assertIs(controller._resolved(spec.identity), False)
        self.assertEqual(
            controller._hard_stop(spec), "paused_overlength_exhausted"
        )

        verdict = SimpleNamespace(
            action="verdict", identity="verdict-under-test"
        )
        verdict_overlength = [{"status": "overlength"}]
        controller.run.plan.requests = [verdict]
        controller.run.result_events = lambda _identity: verdict_overlength
        self.assertIs(
            controller._status_terminal(verdict, verdict_overlength), True
        )
        self.assertIs(controller._resolved(verdict.identity), False)
        self.assertEqual(
            controller._hard_stop(verdict), "paused_overlength_exhausted"
        )

    def test_inventory_cache_skips_only_rendering_and_returns_defensive_copy(self):
        first_auth = authorization(self.proposal)
        first_config, first_plan, *_ = _successor_config_and_plan(
            self.proposal, first_auth, _proposal_verified=True
        )
        second_auth = authorization(self.proposal)
        second_config, second_plan, *_ = _successor_config_and_plan(
            self.proposal, second_auth, _proposal_verified=True
        )
        _SMOKE_INVENTORY_CACHE.clear()
        original = ScientificRequestFactory.build
        calls = 0

        def counted(factory, *args, **kwargs):
            nonlocal calls
            calls += 1
            return original(factory, *args, **kwargs)

        with patch(
            "collusion_recovery.gate2_successor.ScientificRequestFactory.build",
            new=counted,
        ):
            first = _audit_successor_smoke_inventory(first_config, first_plan)
            first_render_count = calls
            first["requests"].clear()
            second = _audit_successor_smoke_inventory(second_config, second_plan)
        self.assertEqual(first_render_count, 228)
        self.assertEqual(calls, first_render_count)
        self.assertEqual(len(second["requests"]), 228)
        self.assertEqual(second["plan_hash"], second_plan.plan_hash)

    def test_completed_manifest_cannot_relabel_terminal_non_observation(self):
        """A derivative manifest edit cannot turn exhaustion into evidence."""
        controller = object.__new__(Gate2SmokeExecutor)
        spec = SimpleNamespace(identity="exhausted-message")
        controller.run = SimpleNamespace(
            manifest={
                "execution_controller": CONTROLLER_ID,
                "status": "completed_smoke_awaiting_operator_review_hard_stop",
            },
            execution_lock=lambda: nullcontext(),
        )
        controller._replay_budget = lambda: SimpleNamespace(active={})
        controller._validate_exact_scope_history = lambda: None
        controller._bind_execution_mode = lambda: None
        controller._bind_live_transport = lambda: None
        controller._ensure_experiment_start = lambda *_args: None
        controller._recover_unmatched_as_ambiguous = lambda: None
        controller._verify_provider_identity_history = lambda: None
        controller._materialize_checkpoints = lambda: None
        controller._selected = lambda: [spec]
        controller._resolved = lambda _identity: False
        with self.assertRaisesRegex(ResumeError, "non-observation"):
            controller.execute()

    def test_v1_1_config_requires_versioned_split_and_seed_namespaces(self):
        values = json.loads(canonical(self.proposal["successor_config_template"]))
        values["authorization"] = {
            "kind": "paid",
            "authorization_id": "namespace-validation-test",
            "approved_at": "2026-10-23T22:00:00-07:00",
            "approved_budget_usd": self.proposal["exact_caps"]["max_cost_usd"],
            "allow_paid_calls": True,
        }
        for field in ("split", "seed_namespace"):
            invalid = json.loads(canonical(values))
            invalid[field] = "pilot/unversioned-successor"
            with self.subTest(field=field), self.assertRaisesRegex(
                    LiveConfigError, "pilot/v1.1/"):
                LiveConfig.from_mapping(invalid)

    def test_authorization_is_absent_mismatch_and_nonce_fail_closed(self):
        proposal = self.proposal
        with self.assertRaises(Gate2SuccessorError):
            validate_gate2_human_authorization(
                {}, proposal, _proposal_verified=True
            )
        valid = authorization(proposal)
        self.assertEqual(
            validate_gate2_human_authorization(
                valid, proposal, _proposal_verified=True
            ),
            valid,
        )

        mismatched = json.loads(canonical(valid))
        mismatched["approved_caps"]["max_dispatch_attempts"] = 531
        mismatched["authorization_hash"] = digest({
            key: value for key, value in mismatched.items()
            if key != "authorization_hash"
        })
        with self.assertRaisesRegex(Gate2SuccessorError, "exact smoke scope"):
            validate_gate2_human_authorization(
                mismatched, proposal, _proposal_verified=True
            )

        weak_nonce = json.loads(canonical(valid))
        weak_nonce["authorization_nonce"] = "a" * 64
        weak_nonce["authorization_hash"] = digest({
            key: value for key, value in weak_nonce.items()
            if key != "authorization_hash"
        })
        with self.assertRaisesRegex(Gate2SuccessorError, "fresh 64-hex"):
            validate_gate2_human_authorization(
                weak_nonce, proposal, _proposal_verified=True
            )

    def test_inspection_has_no_authorization_credential_or_network_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            proposal_path = Path(directory) / "proposal.json"
            proposal_path.write_text(
                canonical(self.proposal) + "\n", encoding="utf-8"
            )
            with no_network(), patch(
                    "collusion_recovery.gate2_successor._load_local_openai_key",
                    side_effect=AssertionError("credential access forbidden")):
                result = inspect_gate2_successor(proposal_path, now=TEST_NOW)
        self.assertEqual(
            result["status"], "ready_awaiting_external_human_authorization"
        )
        self.assertIs(result["credentials_accessed"], False)
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["paid_calls"], 0)
        self.assertIs(result["experiment_started"], False)

    def test_receipt_is_same_run_resumable_but_not_replayable(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / auth["successor_run_directory"]
            run, _config, _plan, _dispatch, _inventory = (
                prepare_gate2_successor_run(
                    run_path,
                    proposal,
                    auth,
                    workspace_root=root,
                    _proposal_verified=True,
                )
            )
            receipts = root / "receipts"
            first = self._claim(
                run, proposal, auth, receipt_dir=receipts, now=TEST_NOW
            )
            second = self._claim(
                run, proposal, auth, receipt_dir=receipts, now=TEST_NOW
            )
            self.assertEqual(first, second)

            receipt_path = _authorization_receipt_path(receipts)
            extra = json.loads(canonical(first))
            extra["unexpected"] = "self-rehashed but not schema-valid"
            extra_body = dict(extra)
            extra_body.pop("receipt_hash")
            extra["receipt_hash"] = digest(extra_body)
            receipt_path.write_text(canonical(extra) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "different or invalid run"):
                self._claim(
                    run, proposal, auth, receipt_dir=receipts, now=TEST_NOW
                )

            malformed_time = json.loads(canonical(first))
            malformed_time["claimed_at"] = "not-a-timestamp"
            malformed_body = dict(malformed_time)
            malformed_body.pop("receipt_hash")
            malformed_time["receipt_hash"] = digest(malformed_body)
            receipt_path.write_text(
                canonical(malformed_time) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(Gate2SuccessorError, "claimed_at"):
                self._claim(
                    run, proposal, auth, receipt_dir=receipts, now=TEST_NOW
                )
            receipt_path.write_text(canonical(first) + "\n", encoding="utf-8")

            other = SimpleNamespace(
                path=root / "different-run",
                config=run.config,
                plan=run.plan,
                manifest=run.manifest,
                events=run.events,
                execution_lock=run.execution_lock,
            )
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "different or invalid run"):
                self._claim(
                    other, proposal, auth, receipt_dir=receipts, now=TEST_NOW
                )

            # Removing/replacing the repository-side run cannot reset the
            # external one-use claim.  A fresh run at the same authorized path
            # receives a new cryptographic instance binding and is rejected.
            preserved = root / "preserved-old-run"
            run_path.rename(preserved)
            recreated, *_ = prepare_gate2_successor_run(
                run_path,
                proposal,
                auth,
                workspace_root=root,
                _proposal_verified=True,
            )
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "different or invalid run"):
                self._claim(
                    recreated, proposal, auth, receipt_dir=receipts, now=TEST_NOW
                )

            # A replacement UUID cannot reset the one fixed release either.
            replacement = authorization(proposal)
            replacement_path = root / replacement["successor_run_directory"]
            preserved_recreated = root / "preserved-recreated-run"
            replacement_path.rename(preserved_recreated)
            replacement_run, *_ = prepare_gate2_successor_run(
                replacement_path,
                proposal,
                replacement,
                workspace_root=root,
                _proposal_verified=True,
            )
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "different or invalid run"):
                self._claim(
                    replacement_run,
                    proposal,
                    replacement,
                    receipt_dir=receipts,
                    now=TEST_NOW,
                )

    def test_unclaimed_authorization_expires_and_rejects_future_timestamp(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / auth["successor_run_directory"]
            run, *_ = prepare_gate2_successor_run(
                run_path,
                proposal,
                auth,
                workspace_root=root,
                _proposal_verified=True,
            )
            receipts = root / "receipts"
            with self.assertRaisesRegex(Gate2SuccessorError, "expired"):
                self._claim(
                    run,
                    proposal,
                    auth,
                    receipt_dir=receipts,
                    now=datetime.fromisoformat("2026-10-23T22:16:00-07:00"),
                )
            with self.assertRaisesRegex(Gate2SuccessorError, "future"):
                self._claim(
                    run,
                    proposal,
                    auth,
                    receipt_dir=receipts,
                    now=datetime.fromisoformat("2026-10-23T21:58:00-07:00"),
                )

        with tempfile.TemporaryDirectory() as directory:
            proposal_path = Path(directory) / "proposal.json"
            proposal_path.write_text(canonical(proposal) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(Gate2SuccessorError, "pricing is stale"):
                inspect_gate2_successor(
                    proposal_path,
                    now=datetime.fromisoformat("2026-10-24T22:00:00-07:00"),
                )

    def test_live_path_claims_before_credential_or_client_access(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory)
            proposal_path = external / "proposal.json"
            authorization_path = external / "authorization.json"
            proposal_path.write_text(canonical(proposal) + "\n", encoding="utf-8")
            authorization_path.write_text(canonical(auth) + "\n", encoding="utf-8")
            authorization_path.chmod(0o600)

            config = object()
            plan = SimpleNamespace(to_dict=lambda: {"plan": "bound"})
            dispatch = SimpleNamespace(to_dict=lambda: {"dispatch": "bound"})
            inventory = {"inventory_hash": "bound"}
            prepared = (config, plan, dispatch, inventory)
            registry = external / "stable-owner-state" / "gate2"
            with patch(
                    "collusion_recovery.gate2_successor.load_gate2_human_authorization",
                    return_value=auth), patch(
                    "collusion_recovery.gate2_successor._authorization_receipt_directory",
                    return_value=registry), patch(
                    "collusion_recovery.gate2_successor._successor_config_and_plan",
                    return_value=prepared), patch(
                    "collusion_recovery.gate2_successor.prepare_gate2_successor_run",
                    return_value=(SimpleNamespace(), *prepared)), patch(
                    "collusion_recovery.gate2_successor.claim_gate2_authorization_once",
                    side_effect=Gate2SuccessorError("claim rejected")) as claim, patch(
                    "collusion_recovery.gate2_successor._load_local_openai_key"
                    ) as load_key:
                with self.assertRaisesRegex(Gate2SuccessorError, "claim rejected"):
                    run_gate2_successor(
                        authorization_path,
                        proposal_path=proposal_path,
                        env_file=external / ".env.local",
                        _now=TEST_NOW,
                    )
            claim.assert_called_once()
            self.assertEqual(
                claim.call_args.kwargs["receipt_dir"],
                registry,
            )
            load_key.assert_not_called()

    def test_first_use_validity_is_checked_before_run_preparation(self):
        proposal = self.proposal
        auth = authorization(proposal)
        expired_now = datetime.fromisoformat("2026-10-23T22:16:00-07:00")
        with tempfile.TemporaryDirectory() as directory:
            authorization_path = Path(directory) / "authorization.json"
            authorization_path.write_text("{}\n", encoding="utf-8")
            authorization_path.chmod(0o600)
            receipt_dir = Path(directory) / "stable-owner-state" / "gate2"
            with patch(
                    "collusion_recovery.gate2_successor.load_gate2_successor_proposal",
                    return_value=proposal), patch(
                    "collusion_recovery.gate2_successor.load_gate2_human_authorization",
                    return_value=auth), patch(
                    "collusion_recovery.gate2_successor._authorization_receipt_directory",
                    return_value=receipt_dir), patch(
                    "collusion_recovery.gate2_successor._successor_config_and_plan"
                    ) as derive, patch(
                    "collusion_recovery.gate2_successor.prepare_gate2_successor_run"
                    ) as prepare:
                with self.assertRaisesRegex(Gate2SuccessorError, "expired"):
                    run_gate2_successor(
                        authorization_path,
                        _now=expired_now,
                    )
            derive.assert_not_called()
            prepare.assert_not_called()

            # A matching-path receipt cannot bypass first-use validity or
            # poison the fixed run identity when its durable run is absent.
            receipt_dir.mkdir(parents=True, mode=0o700)
            receipt = _authorization_receipt_path(receipt_dir)
            receipt.write_text("{}\n", encoding="utf-8")
            receipt.chmod(0o600)
            root = Path(directory) / "workspace"
            with patch(
                    "collusion_recovery.gate2_successor.ROOT", root), patch(
                    "collusion_recovery.gate2_successor.load_gate2_successor_proposal",
                    return_value=proposal), patch(
                    "collusion_recovery.gate2_successor.load_gate2_human_authorization",
                    return_value=auth), patch(
                    "collusion_recovery.gate2_successor._authorization_receipt_directory",
                    return_value=receipt_dir), patch(
                    "collusion_recovery.gate2_successor._successor_config_and_plan"
                    ) as derive, patch(
                    "collusion_recovery.gate2_successor.prepare_gate2_successor_run"
                    ) as prepare:
                with self.assertRaisesRegex(
                        Gate2SuccessorError, "without its durable successor run"):
                    run_gate2_successor(
                        authorization_path,
                        _now=expired_now,
                    )
            derive.assert_not_called()
            prepare.assert_not_called()
            self.assertFalse((root / auth["successor_run_directory"]).exists())

            # A directory plus a receipt is not durable resume evidence.  It
            # must fail closed before contract derivation or secret access
            # unless it contains an exact inspectable successor run.
            (root / auth["successor_run_directory"]).mkdir(parents=True)
            with patch(
                    "collusion_recovery.gate2_successor.ROOT", root), patch(
                    "collusion_recovery.gate2_successor.load_gate2_successor_proposal",
                    return_value=proposal), patch(
                    "collusion_recovery.gate2_successor.load_gate2_human_authorization",
                    return_value=auth), patch(
                    "collusion_recovery.gate2_successor._authorization_receipt_directory",
                    return_value=receipt_dir), patch(
                    "collusion_recovery.gate2_successor._successor_config_and_plan",
                    side_effect=AssertionError("contract derivation forbidden")
                    ) as derive, patch(
                    "collusion_recovery.gate2_successor._load_local_openai_key",
                    side_effect=AssertionError("credential access forbidden")
                    ) as load_key:
                with self.assertRaisesRegex(
                        ResumeError, "valid inspectable manifest"):
                    run_gate2_successor(
                        authorization_path,
                        _now=expired_now,
                    )
            derive.assert_not_called()
            load_key.assert_not_called()

    def test_exact_live_transport_binding_is_idempotent_on_resume(self):
        metadata = json.loads(canonical(self.proposal["transport"]))
        sdk_version = metadata["sdk_version"]
        runtime = {
            "python_version": "test",
            "python_implementation": "test",
            "third_party_dependencies": [
                {"name": "openai", "version": sdk_version}
            ],
        }

        def unexpected_status_write(*_args, **_kwargs):
            raise AssertionError("exact live resume rewrote its manifest")

        controller = object.__new__(Gate2SmokeExecutor)
        controller.fake_transport_test = False
        controller._execution_mode = "empirical_live"
        controller.proposal = self.proposal
        controller.provider = SimpleNamespace(
            transport=SimpleNamespace(audit_metadata=metadata)
        )
        controller.run = SimpleNamespace(
            manifest={
                "status": "completed_smoke_awaiting_operator_review_hard_stop",
                "authorized_transport_hash": digest(metadata),
                "transport": metadata,
                "transport_hash": digest(metadata),
                "runtime": runtime,
            },
            set_status=unexpected_status_write,
        )
        with patch(
                "collusion_recovery.gate2_successor.importlib_metadata.version",
                return_value=sdk_version):
            controller._bind_live_transport()

    def test_expired_prepared_unclaimed_run_requires_new_versioned_release(self):
        proposal = self.proposal
        auth = authorization(proposal)
        expired_now = datetime.fromisoformat("2026-10-23T22:16:00-07:00")
        with tempfile.TemporaryDirectory() as directory, no_network():
            root = Path(directory)
            run_path = root / auth["successor_run_directory"]
            prepare_gate2_successor_run(
                run_path,
                proposal,
                auth,
                workspace_root=root,
                _proposal_verified=True,
            )
            authorization_path = root / "external-authorization.json"
            authorization_path.write_text("{}\n", encoding="utf-8")
            authorization_path.chmod(0o600)
            receipt_dir = root / "receipts"
            with patch(
                    "collusion_recovery.gate2_successor.ROOT", root), patch(
                    "collusion_recovery.gate2_successor.load_gate2_successor_proposal",
                    return_value=proposal), patch(
                    "collusion_recovery.gate2_successor.load_gate2_human_authorization",
                    return_value=auth), patch(
                    "collusion_recovery.gate2_successor._authorization_receipt_directory",
                    return_value=receipt_dir), patch(
                    "collusion_recovery.gate2_successor._successor_config_and_plan"
                    ) as derive, patch(
                    "collusion_recovery.gate2_successor.prepare_gate2_successor_run"
                    ) as prepare, patch(
                    "collusion_recovery.gate2_successor._load_local_openai_key"
                    ) as load_key:
                with self.assertRaisesRegex(
                        Gate2SuccessorError,
                        "new versioned release/run ID"):
                    run_gate2_successor(
                        authorization_path,
                        _now=expired_now,
                    )
            derive.assert_not_called()
            prepare.assert_not_called()
            load_key.assert_not_called()

    def test_stable_receipt_registry_and_symlinked_run_path_fail_closed(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / "owner-state" / "gate2"
            with patch(
                "collusion_recovery.gate2_successor.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ):
                self.assertEqual(
                    _authorization_receipt_directory(root / "a" / "auth.json"),
                    _authorization_receipt_directory(root / "b" / "copy.json"),
                )
            workspace = root / "repo"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            (workspace / "runs").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "outside|symlink"):
                prepare_gate2_successor_run(
                    workspace / auth["successor_run_directory"],
                    proposal,
                    auth,
                    workspace_root=workspace,
                    _proposal_verified=True,
                )
            self.assertEqual(list(outside.iterdir()), [])

    def test_receipt_registry_rejects_symlinked_parent_without_writes(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory, no_network():
            root = Path(directory)
            workspace = root / "repo"
            workspace.mkdir()
            target = root / "replaceable-target"
            target.mkdir()
            redirect = root / "owner-state"
            redirect.symlink_to(target, target_is_directory=True)
            registry = redirect / "gate2-v1.1"
            run, *_ = prepare_gate2_successor_run(
                workspace / auth["successor_run_directory"],
                proposal,
                auth,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "symlink components"):
                self._claim(
                    run,
                    proposal,
                    auth,
                    receipt_dir=registry,
                    now=TEST_NOW,
                )
            self.assertEqual(list(target.iterdir()), [])

    def test_receipt_registry_rejects_writable_or_wrong_owner_parent(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory, no_network():
            root = Path(directory)
            workspace = root / "repo"
            workspace.mkdir()
            registry = root / "owner-state" / "gate2-v1.1"
            run, *_ = prepare_gate2_successor_run(
                workspace / auth["successor_run_directory"],
                proposal,
                auth,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            root.chmod(0o777)
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "current-user-owned non-writable"):
                self._claim(
                    run, proposal, auth, receipt_dir=registry, now=TEST_NOW
                )
            self.assertFalse(registry.exists())
            root.chmod(0o700)
            with patch(
                    "collusion_recovery.gate2_successor.os.getuid",
                    return_value=10_000_000):
                with self.assertRaisesRegex(
                        Gate2SuccessorError,
                        "current-user-owned non-writable"):
                    self._claim(
                        run, proposal, auth,
                        receipt_dir=registry, now=TEST_NOW,
                    )
            self.assertFalse(registry.exists())

    def test_empirical_first_ambiguous_call_persists_experiment_start(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory, no_network():
            root = Path(directory)
            run_path = root / auth["successor_run_directory"]
            run, _config, _plan, _dispatch, inventory = (
                prepare_gate2_successor_run(
                    run_path,
                    proposal,
                    auth,
                    workspace_root=root,
                    _proposal_verified=True,
                )
            )
            receipt = self._claim(
                run,
                proposal,
                auth,
                receipt_dir=root / "receipts",
                now=TEST_NOW,
            )
            transport = OverlengthRetrySmokeTransport(proposal["transport"])
            with patch(
                    "collusion_recovery.gate2_successor.OpenAISDKTransport",
                    FakeResponsesTransport):
                controller = Gate2SmokeExecutor(
                    run,
                    OpenAIResponsesProvider(transport),
                    proposal,
                    inventory,
                    authorization_receipt=receipt,
                    fake_transport_test=False,
                )
            controller._bind_live_transport = lambda: None
            unsafe_result = Mock(status=ProviderStatus.RETRYABLE_ERROR)
            unsafe_result.to_dict.return_value = {
                "status": "retryable_error",
                "ambiguous": False,
                "known_not_completed": True,
                "usage": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "total_tokens": 0,
                },
                "error": "malformed zero-usage proof",
            }
            controller.provider.complete = Mock(
                return_value=unsafe_result
            )
            with self.assertRaises(Gate2SuccessorError):
                controller.execute()
            starts = [
                event for event in run.events
                if event.get("kind") == EXPERIMENT_STARTED_EVENT
            ]
            self.assertEqual(len(starts), 1)
            first_reservation = next(
                event for event in run.events
                if event.get("kind") == "reservation"
            )
            first_result = next(
                event for event in run.events
                if event.get("kind") == "result"
            )
            self.assertLess(
                first_reservation["sequence"], starts[0]["sequence"]
            )
            self.assertLess(starts[0]["sequence"], first_result["sequence"])
            self.assertIs(run.manifest["experiment_started"], True)
            self.assertEqual(
                run.manifest["experiment_start_event_hash"],
                starts[0]["event_hash"],
            )
            self.assertEqual(run.manifest["status"], "paused_ambiguous_timeout")
            self.assertEqual(
                first_result["provider_result"]["original_status"],
                "retryable_error",
            )
            self.assertIs(first_result["known_not_completed"], False)
            before = len(run.result_events())
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "remains stopped"):
                controller.execute()
            self.assertEqual(len(run.result_events()), before)

    def test_gate2_executor_rejects_manifest_only_consumption_claim(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory, no_network():
            root = Path(directory)
            run, _config, _plan, _dispatch, inventory = (
                prepare_gate2_successor_run(
                    root / auth["successor_run_directory"],
                    proposal,
                    auth,
                    workspace_root=root,
                    _proposal_verified=True,
                )
            )
            binding = next(
                event for event in run.events
                if event.get("kind") == "gate2_successor_binding"
            )
            body = {
                "artifact_type": "gate_2_authorization_consumption_receipt_v1_1",
                "authorization_uuid": auth["authorization_uuid"],
                "authorization_hash": auth["authorization_hash"],
                "proposal_hash": proposal["proposal_hash"],
                "protocol_bridge_hash": proposal["protocol_bridge"][
                    "bridge_hash"
                ],
                "successor_run_id": auth["successor_run_id"],
                "run_directory": str(run.path.resolve()),
                "config_hash": digest(run.config.to_dict()),
                "plan_hash": run.plan.plan_hash,
                "dispatch_authorization_hash": run.manifest[
                    "dispatch_authorization_hash"
                ],
                "run_instance_event_hash": binding["event_hash"],
                "run_instance_hash": digest(binding["run_instance_nonce"]),
                "claimed_at": "2026-10-23T22:05:00-07:00",
            }
            receipt = {**body, "receipt_hash": digest(body)}
            run.set_status(
                run.manifest["status"],
                gate2_authorization_consumed=True,
                gate2_authorization_receipt_hash=receipt["receipt_hash"],
                gate2_authorization_claim_event_hash="0" * 64,
            )
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "journal-bound"):
                Gate2SmokeExecutor(
                    run,
                    OpenAIResponsesProvider(FakeResponsesTransport()),
                    proposal,
                    inventory,
                    authorization_receipt=receipt,
                    fake_transport_test=True,
                )

    def test_post_smoke_review_records_once_and_reviewed_resume_is_noop(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory, no_network():
            root = Path(directory) / "workspace"
            root.mkdir()
            run_path = root / auth["successor_run_directory"]
            run, _config, _plan, _dispatch, inventory = (
                prepare_gate2_successor_run(
                    run_path,
                    proposal,
                    auth,
                    workspace_root=root,
                    _proposal_verified=True,
                )
            )
            receipt_dir = Path(directory) / "receipts"
            receipt = self._claim(
                run, proposal, auth, receipt_dir=receipt_dir, now=TEST_NOW
            )
            transport = OverlengthRetrySmokeTransport(proposal["transport"])
            with patch(
                    "collusion_recovery.gate2_successor.OpenAISDKTransport",
                    FakeResponsesTransport), patch(
                    "collusion_recovery.gate2_successor.importlib_metadata.version",
                    return_value=proposal["transport"]["sdk_version"]):
                report = Gate2SmokeExecutor(
                    run,
                    OpenAIResponsesProvider(transport),
                    proposal,
                    inventory,
                    authorization_receipt=receipt,
                    fake_transport_test=False,
                ).execute()
            self.assertEqual(len(transport.payloads), 154)
            self.assertIs(report["experiment_started"], True)
            completion = next(
                event for event in run.events
                if event.get("kind") == SMOKE_COMPLETED_EVENT
            )
            with run.execution_lock(), patch(
                    "collusion_recovery.gate1_calibration.verify_gate1_passed_lineage",
                    return_value=self.gate1_lineage):
                assessment = _smoke_integrity_assessment_locked(run)
            assessment["scheduled_through_smoke"] = -1
            with run.execution_lock(), patch(
                    "collusion_recovery.gate2_successor.smoke_integrity_assessment",
                    side_effect=AssertionError(
                        "identical durable smoke must use the exact cache"
                    )):
                assessment = _smoke_integrity_assessment_locked(run)
            self.assertIs(assessment["automatic_checks_passed"], True)
            self.assertEqual(assessment["scheduled_through_smoke"], 152)
            first_identity = run.plan.requests[0].identity
            self.assertEqual(
                [row["status"] for row in run.result_events(first_identity)],
                ["overlength", "retryable_error", "ok"],
            )
            successor_evidence = assessment["successor_evidence_integrity"]
            self.assertEqual(successor_evidence["attempts_recorded"], 154)
            self.assertEqual(successor_evidence["request_payloads_verified"], 154)
            self.assertEqual(successor_evidence["wire_payloads_verified"], 154)
            self.assertEqual(
                successor_evidence["raw_evidence_attempts_required"], 153
            )
            self.assertEqual(successor_evidence["provider_audits_complete"], 153)

            # Even a fully rehashed local event stream cannot turn an
            # observation into a nonfinal row and silently resample that
            # logical identity.  Only retryable transport failures and the
            # single transcript-visible overlength revision may precede the
            # final observation.
            original_events = run.events
            tampered_events = json.loads(canonical(original_events))
            tampered = next(
                event for event in tampered_events
                if event.get("kind") == "result"
                and event.get("identity") == first_identity
                and event.get("status") == "overlength"
            )
            tampered["status"] = "invalid_response"
            tampered["provider_result"]["status"] = "invalid_response"
            previous_hash = None
            for sequence, event in enumerate(tampered_events):
                event.pop("event_hash", None)
                event["sequence"] = sequence
                event["previous_event_hash"] = previous_hash
                event["event_hash"] = digest(event)
                previous_hash = event["event_hash"]
            run.events = tampered_events
            try:
                with self.assertRaisesRegex(
                        Gate2SuccessorError,
                        "unauthorized nonfinal observation"):
                    _verify_completed_gate2_smoke(run, proposal)
            finally:
                run.events = original_events

            with self.assertRaisesRegex(
                    AuthorizationRequiredError, "generic gate mutation"):
                run.record_gate_decision(
                    "smoke_integrity",
                    passed=True,
                    evidence={"bypass": True},
                    decided_by="bypass",
                )
            self.assertEqual(run.gate_events(), {})
            review = self._smoke_review(
                proposal, report, assessment, completion
            )
            reviewed_at = datetime.fromisoformat(review["reviewed_at"])

            premature = json.loads(canonical(review))
            premature["reviewed_at"] = (
                datetime.fromisoformat(completion["recorded_at"])
                - timedelta(seconds=1)
            ).isoformat()
            premature_body = dict(premature)
            premature_body.pop("review_hash")
            premature["review_hash"] = digest(premature_body)
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "after durable smoke completion"):
                validate_gate2_smoke_review(
                    premature, proposal, report, assessment, completion,
                    now=reviewed_at,
                )

            future = json.loads(canonical(review))
            future["reviewed_at"] = (
                reviewed_at + timedelta(seconds=61)
            ).isoformat()
            future_body = dict(future)
            future_body.pop("review_hash")
            future["review_hash"] = digest(future_body)
            with self.assertRaisesRegex(Gate2SuccessorError, "future"):
                validate_gate2_smoke_review(
                    future, proposal, report, assessment, completion,
                    now=reviewed_at,
                )

            review_path = root / "external-smoke-review.json"
            review_path.write_text(canonical(review) + "\n", encoding="utf-8")
            review_path.chmod(0o600)
            proposal_path = root / "proposal.json"
            proposal_path.write_text(
                canonical(proposal) + "\n", encoding="utf-8"
            )
            with patch(
                    "collusion_recovery.gate1_calibration.verify_gate1_passed_lineage",
                    return_value=self.gate1_lineage), patch(
                    "collusion_recovery.gate2_successor._load_local_openai_key",
                    side_effect=AssertionError("credential access forbidden")):
                with patch.object(
                        DurableRun,
                        "set_status",
                        side_effect=RuntimeError("simulated crash after gate append")):
                    with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                        record_gate2_smoke_decision(
                            review_path,
                            proposal_path=proposal_path,
                            run_dir=run_path,
                            workspace_root=root,
                            _now=reviewed_at,
                        )
                crashed = DurableRun.inspect(
                    run_path, probe_installed_dependencies=False
                )
                self.assertIn("smoke_integrity", crashed.gate_events())
                self.assertEqual(
                    crashed.manifest["status"],
                    "completed_smoke_awaiting_operator_review_hard_stop",
                )
                self.assertFalse(
                    (run_path / "gate2_smoke_decision.json").exists()
                )
                decision = record_gate2_smoke_decision(
                    review_path,
                    proposal_path=proposal_path,
                    run_dir=run_path,
                    workspace_root=root,
                    _now=reviewed_at,
                )
            self.assertEqual(decision["decision"], "pass")
            self.assertEqual(
                run.manifest["status"], "completed_smoke_passed_hard_stop"
            )
            before_events = run.manifest["event_count"]
            before_manifest = (run_path / "manifest.json").read_bytes()
            before_report = (run_path / "gate2_smoke_report.json").read_bytes()
            before_decision = (
                run_path / "gate2_smoke_decision.json"
            ).read_bytes()

            with patch(
                    "collusion_recovery.gate1_calibration.verify_gate1_passed_lineage",
                    return_value=self.gate1_lineage):
                same = record_gate2_smoke_decision(
                    review_path,
                    proposal_path=proposal_path,
                    run_dir=run_path,
                    workspace_root=root,
                    _now=reviewed_at,
                )
            self.assertEqual(same["report_hash"], decision["report_hash"])
            self.assertEqual((run_path / "manifest.json").read_bytes(), before_manifest)
            self.assertEqual(
                (run_path / "gate2_smoke_report.json").read_bytes(), before_report
            )
            self.assertEqual(
                (run_path / "gate2_smoke_decision.json").read_bytes(),
                before_decision,
            )

            with patch(
                    "collusion_recovery.orchestration.importlib_metadata.version",
                    return_value=proposal["transport"]["sdk_version"]):
                reopened, *_ = prepare_gate2_successor_run(
                    run_path,
                    proposal,
                    auth,
                    workspace_root=root,
                    _proposal_verified=True,
                )
            same_receipt = self._claim(
                reopened, proposal, auth,
                receipt_dir=receipt_dir, now=reviewed_at,
            )
            empty = FakeResponsesTransport(proposal["transport"])
            with patch(
                    "collusion_recovery.gate2_successor.OpenAISDKTransport",
                    FakeResponsesTransport), patch(
                    "collusion_recovery.gate2_successor.importlib_metadata.version",
                    return_value=proposal["transport"]["sdk_version"]):
                resumed = Gate2SmokeExecutor(
                    reopened,
                    OpenAIResponsesProvider(empty),
                    proposal,
                    inventory,
                    authorization_receipt=same_receipt,
                    fake_transport_test=False,
                ).execute()
            self.assertEqual(resumed["report_hash"], report["report_hash"])
            self.assertEqual(empty.payloads, [])
            self.assertEqual(reopened.manifest["event_count"], before_events)
            self.assertEqual((run_path / "manifest.json").read_bytes(), before_manifest)

            authorization_path = root / "external-authorization.json"
            authorization_path.write_text("{}\n", encoding="utf-8")
            authorization_path.chmod(0o600)
            with patch(
                    "collusion_recovery.gate2_successor.load_gate2_successor_proposal",
                    return_value=proposal), patch(
                    "collusion_recovery.gate2_successor.load_gate2_human_authorization",
                    return_value=auth), patch(
                    "collusion_recovery.gate2_successor.ROOT",
                    root), patch(
                    "collusion_recovery.gate2_successor.AUTHORIZATION_RECEIPT_DIRECTORY",
                    receipt_dir), patch(
                    "collusion_recovery.gate2_successor._authorization_receipt_directory",
                    return_value=receipt_dir), patch(
                    "collusion_recovery.gate2_successor._validate_pricing_window"), patch(
                    "collusion_recovery.gate1_calibration.verify_gate1_passed_lineage",
                    return_value=self.gate1_lineage), patch(
                    "collusion_recovery.gate2_successor._load_local_openai_key",
                    side_effect=AssertionError("credential access forbidden")
                    ) as load_key, patch(
                    "collusion_recovery.gate2_successor.importlib_metadata.version",
                    side_effect=AssertionError("SDK construction forbidden")
                    ) as sdk:
                public_resume = run_gate2_successor(
                    authorization_path,
                    _now=reviewed_at,
                )
            self.assertEqual(public_resume["report_hash"], report["report_hash"])
            load_key.assert_not_called()
            sdk.assert_not_called()
            self.assertEqual((run_path / "manifest.json").read_bytes(), before_manifest)

    def test_fake_transport_executes_exact_smoke_once_and_resume_is_noop(self):
        proposal = self.proposal
        auth = authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / auth["successor_run_directory"]
            run, config, plan, _dispatch, inventory = prepare_gate2_successor_run(
                run_path,
                proposal,
                auth,
                workspace_root=root,
                _proposal_verified=True,
            )
            receipts = root / "receipts"
            receipt = self._claim(
                run, proposal, auth, receipt_dir=receipts, now=TEST_NOW
            )
            transport = FakeResponsesTransport()
            provider = OpenAIResponsesProvider(transport)
            controller = Gate2SmokeExecutor(
                run,
                provider,
                proposal,
                inventory,
                authorization_receipt=receipt,
                fake_transport_test=True,
            )
            report = controller.execute()
            self.assertEqual(
                report["status"],
                "completed_smoke_awaiting_operator_review_hard_stop",
            )
            self.assertEqual(report["logical_requests"], 152)
            self.assertEqual(report["dispatch_attempts"], 152)
            self.assertEqual(report["preflight_dispatches"], 0)
            self.assertEqual(report["calibration_dispatches"], 0)
            self.assertEqual(report["later_stage_dispatches"], 0)
            self.assertIs(report["next_stage_authorized"], False)
            self.assertIs(report["experiment_started"], False)
            self.assertEqual(report["execution_mode"], "fake_transport_test")
            self.assertIsNone(report["experiment_start_event_hash"])
            self.assertEqual(sum(
                event.get("kind") == AUTHORIZATION_CLAIM_EVENT
                for event in run.events
            ), 1)
            self.assertEqual(sum(
                event.get("kind") == EXECUTION_MODE_BINDING_EVENT
                for event in run.events
            ), 1)
            self.assertFalse(any(
                event.get("kind") == EXPERIMENT_STARTED_EVENT
                for event in run.events
            ))
            self.assertEqual(len(transport.payloads), 152)
            with self.assertRaisesRegex(
                    Gate2SuccessorError, "fake-transport smoke"):
                _verify_completed_gate2_smoke(run, proposal)
            smoke_ids = {
                spec.identity for spec in plan.requests if spec.stage == "smoke"
            }
            forbidden = {
                spec.identity for spec in plan.requests
                if spec.stage in {"preflight", "calibration"}
            }
            durable_ids = {
                event["identity"] for event in run.events
                if event.get("kind") == "reservation"
            }
            self.assertEqual(durable_ids, smoke_ids)
            self.assertTrue(durable_ids.isdisjoint(forbidden))
            completed_event_count = run.manifest["event_count"]

            # A locally edited report is not trusted merely because an editor
            # recomputed its unkeyed self-hash.  Resume reconstructs the exact
            # report from the hash-chained run and rejects the mismatch.
            report_path = run_path / "gate2_smoke_report.json"
            tampered = json.loads(canonical(report))
            tampered["logical_requests"] = 151
            tampered_body = dict(tampered)
            tampered_body.pop("report_hash")
            tampered["report_hash"] = digest(tampered_body)
            report_path.write_text(canonical(tampered) + "\n", encoding="utf-8")

            tampered_run, *_ = prepare_gate2_successor_run(
                run_path,
                proposal,
                auth,
                workspace_root=root,
                _proposal_verified=True,
            )
            tampered_receipt = self._claim(
                tampered_run, proposal, auth, receipt_dir=receipts, now=TEST_NOW
            )
            with self.assertRaisesRegex(
                    ResumeError, "completion report differs"):
                Gate2SmokeExecutor(
                    tampered_run,
                    OpenAIResponsesProvider(FakeResponsesTransport()),
                    proposal,
                    inventory,
                    authorization_receipt=tampered_receipt,
                    fake_transport_test=True,
                ).execute()
            report_path.write_text(canonical(report) + "\n", encoding="utf-8")

            reopened, *_ = prepare_gate2_successor_run(
                run_path,
                proposal,
                auth,
                workspace_root=root,
                _proposal_verified=True,
            )
            self.assertEqual(
                reopened.manifest["status"],
                "completed_smoke_awaiting_operator_review_hard_stop",
            )
            same_receipt = self._claim(
                reopened, proposal, auth, receipt_dir=receipts, now=TEST_NOW
            )
            empty_transport = FakeResponsesTransport()
            resumed = Gate2SmokeExecutor(
                reopened,
                OpenAIResponsesProvider(empty_transport),
                proposal,
                inventory,
                authorization_receipt=same_receipt,
                fake_transport_test=True,
            ).execute()
            self.assertEqual(resumed["report_hash"], report["report_hash"])
            self.assertEqual(empty_transport.payloads, [])
            self.assertEqual(reopened.manifest["event_count"], completed_event_count)

            # Generic staged execution cannot reinterpret the plan and
            # duplicate Stage 2 preflight/calibration calls.
            with self.assertRaisesRegex(
                    AuthorizationRequiredError, "smoke-only controller"):
                StagedExecutor(
                    reopened,
                    OpenAIResponsesProvider(empty_transport),
                    ScientificRequestFactory(config, plan),
                )


if __name__ == "__main__":
    unittest.main()
