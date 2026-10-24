from contextlib import ExitStack
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch

from collusion_recovery.common import canonical, digest
from collusion_recovery.gates import calibration_assessment
from collusion_recovery.gate1_calibration import (
    AUTHORIZATION_CLAIM_EVENT,
    AUTHORIZATION_RECEIPT_DIRECTORY,
    CONTROLLER_ID,
    EMPIRICAL_EXECUTION_MODE,
    EXECUTION_MODE_BINDING_EVENT,
    GATE1_CALIBRATION_NAMESPACE,
    GATE1_RUN_ID,
    Gate1CalibrationError,
    Gate1CalibrationExecutor,
    HUMAN_ACKNOWLEDGEMENT,
    _authorization_receipt_directory,
    _authorization_receipt_path,
    _validate_pricing_window,
    build_gate1_calibration_proposal,
    claim_gate1_authorization_once,
    complete_gate1_human_authorization,
    gate1_authorization_schema,
    inspect_gate1_calibration,
    prepare_gate1_calibration_run,
    run_gate1_calibration,
    validate_gate1_authorization_file,
    validate_gate1_calibration_proposal,
    validate_gate1_human_authorization,
    _verify_completed_gate1_calibration,
    verify_gate1_passed_lineage,
)
from collusion_recovery.live_config import LiveConfig, LiveConfigError
from collusion_recovery.orchestration import (
    AuthorizationRequiredError,
    DurableRun,
    GATE1_CALIBRATION_BINDING_EVENT,
    ResumeError,
    StagedExecutor,
    build_request_plan,
)
from collusion_recovery.providers.base import (
    ProviderResult,
    ProviderStatus,
    ProviderTiming,
    TransportFailure,
    TransportResponse,
)
from collusion_recovery.providers.openai import (
    OpenAIResponsesProvider,
    OpenAISDKTransport,
)
from collusion_recovery.stage2 import (
    STAGE2_SDK_VERSION,
    STAGE2_TIMEOUT_SECONDS,
)
from collusion_recovery.stage2_lineage_snapshot import (
    build_stage2_lineage_snapshot,
    load_stage2_lineage_snapshot,
    validate_stage2_lineage_snapshot,
)


TEST_NOW = datetime.fromisoformat("2026-10-23T22:05:00-07:00")


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


def authorization(
        proposal, authorization_uuid=None, *,
        confirmed_at="2026-10-23T22:00:00-07:00"):
    authorization_uuid = authorization_uuid or str(uuid.uuid4())
    external = {
        "artifact_type": "gate_1_calibration_human_authorization_v1_1",
        "authorization_uuid": authorization_uuid,
        "authorization_id": f"gate1-human-{authorization_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "gate1_run_id": proposal["gate1_protocol"]["run_id"],
        "gate1_run_directory": proposal["gate1_protocol"]["run_directory"],
        "confirmed_by": "test human",
        "confirmed_at": confirmed_at,
        "proposal_hash": proposal["proposal_hash"],
        "approved_scope": proposal["calibration_scope"],
        "approved_caps": proposal["exact_caps"],
        "approved_pricing_contract_hash": proposal["pricing_contract_hash"],
        "approved_transport_hash": proposal["transport_hash"],
        "approved_provider_identity": proposal["required_provider_identity"],
        "approved_protocol_bridge_hash": proposal["protocol_bridge"][
            "bridge_hash"
        ],
        "approved_calibration_inventory_hash": proposal[
            "calibration_projection"
        ]["inventory_hash"],
        "allow_paid_calls": True,
        "human_acknowledgement": HUMAN_ACKNOWLEDGEMENT,
    }
    return complete_gate1_human_authorization(
        external, proposal, _proposal_verified=True
    )


class FakeCalibrationTransport:
    def __init__(self, *, fail_accuracy=False):
        self.payloads = []
        self.fail_accuracy = fail_accuracy
        self.completed = 0

    def send(self, payload):
        self.payloads.append(json.loads(canonical(payload)))
        index = self.completed
        self.completed += 1
        verdict = "ACCEPT" if index < 10 or self.fail_accuracy else "REJECT"
        text = canonical({
            "verdict": verdict,
            "evidence_record_ids": [],
            "brief_reason": "Deterministic fake Gate 1 transport output.",
        })
        body = {
            "id": f"fake-gate1-{len(self.payloads)}",
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


class RetryTwiceCalibrationTransport(FakeCalibrationTransport):
    def send(self, payload):
        position = len(self.payloads) % 3
        if position < 2:
            self.payloads.append(json.loads(canonical(payload)))
            raise TransportFailure(
                ProviderStatus.RETRYABLE_ERROR,
                "known not completed test retry",
                known_not_completed=True,
            )
        return super().send(payload)


class NoCallSDKClient:
    base_url = "https://api.openai.com/v1"

    class Responses:
        @staticmethod
        def create(**_kwargs):
            raise AssertionError("SDK client must not be called in this test")

    responses = Responses()

    def with_options(self, **_kwargs):
        return self


class Gate1CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with no_network():
            cls.proposal = build_gate1_calibration_proposal()

    def _prepared(self, root: Path, *, fail_accuracy=False, transport=None):
        root.mkdir(parents=True, exist_ok=True)
        auth = authorization(self.proposal)
        run_path = root / auth["gate1_run_directory"]
        run, config, plan, dispatch, inventory = prepare_gate1_calibration_run(
            run_path,
            self.proposal,
            auth,
            workspace_root=root,
            _proposal_verified=True,
        )
        receipt_dir = root.parent / f"external-{uuid.uuid4()}" / "receipts"
        with patch(
            "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
            receipt_dir,
        ):
            receipt = claim_gate1_authorization_once(
                run,
                self.proposal,
                auth,
                receipt_dir=receipt_dir,
                now=TEST_NOW,
            )
        transport = transport or FakeCalibrationTransport(
            fail_accuracy=fail_accuracy
        )
        controller = Gate1CalibrationExecutor(
            run,
            OpenAIResponsesProvider(transport),
            self.proposal,
            inventory,
            authorization_receipt=receipt,
            fake_transport_test=True,
        )
        return auth, run, config, plan, dispatch, inventory, receipt, transport, controller

    @staticmethod
    def _write_owner_only_json(path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical(value) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def test_proposal_is_exact_calibration_only_scope(self):
        proposal = self.proposal
        self.assertEqual(proposal["gate1_protocol"]["run_id"], GATE1_RUN_ID)
        self.assertEqual(
            proposal["gate1_protocol"]["calibration_namespace"],
            GATE1_CALIBRATION_NAMESPACE,
        )
        self.assertEqual(proposal["calibration_scope"]["logical_request_count"], 20)
        self.assertEqual(proposal["exact_caps"]["max_dispatch_attempts"], 60)
        self.assertEqual(
            proposal["calibration_projection"][
                "maximum_billable_attempt_shapes"
            ],
            20,
        )
        self.assertEqual(
            proposal["calibration_projection"][
                "maximum_known_not_completed_transport_retry_attempts"
            ],
            40,
        )
        self.assertEqual(
            proposal["calibration_projection"]["faulty_defect_counts"],
            {"add": 3, "omit": 4, "substitute": 3},
        )
        self.assertFalse(proposal["hard_stop"]["smoke_dispatch_possible"])
        self.assertFalse(proposal["experiment_started"])
        self.assertIs(
            proposal["external_authorization"][
                "expired_unclaimed_prepared_run_requires_new_versioned_release"
            ],
            True,
        )
        schema = gate1_authorization_schema(proposal)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["approved_caps"]["const"],
            proposal["exact_caps"],
        )

    def test_historical_snapshot_is_self_hashed_and_cross_checked_to_raw(self):
        self.assertEqual(
            load_stage2_lineage_snapshot(),
            build_stage2_lineage_snapshot(),
        )
        self.assertEqual(
            self.proposal["stage2_capability_lineage"][
                "portability_snapshot_hash"
            ],
            load_stage2_lineage_snapshot()["snapshot_hash"],
        )

    def test_fresh_clone_inspection_is_portable_but_not_execution_ready(self):
        with tempfile.TemporaryDirectory() as td:
            proposal_path = Path(td) / "proposal.json"
            proposal_path.write_text(
                canonical(self.proposal) + "\n", encoding="utf-8"
            )
            with patch(
                "collusion_recovery.gate1_calibration.STAGE2_RUN_DIR",
                Path(td) / "missing-raw-stage2",
            ):
                result = inspect_gate1_calibration(
                    proposal_path, now=TEST_NOW
                )
        self.assertEqual(result["status"], "blocked_missing_raw_stage2_lineage")
        self.assertFalse(result["execution_ready"])
        self.assertFalse(result["experiment_started"])

    def test_portable_proposal_and_snapshot_rehash_tampering_fail(self):
        proposal = json.loads(canonical(self.proposal))
        proposal["exact_caps"]["max_dispatch_attempts"] = 61
        body = dict(proposal)
        body.pop("proposal_hash")
        proposal["proposal_hash"] = digest(body)
        with self.assertRaisesRegex(
                Gate1CalibrationError, "complete deterministic"):
            validate_gate1_calibration_proposal(
                proposal, require_raw_lineage=False
            )

        snapshot = load_stage2_lineage_snapshot()
        snapshot["historical_config"]["limits"]["max_requests"] += 1
        snapshot_body = dict(snapshot)
        snapshot_body.pop("snapshot_hash")
        snapshot["snapshot_hash"] = digest(snapshot_body)
        with self.assertRaisesRegex(Exception, "snapshot"):
            validate_stage2_lineage_snapshot(snapshot)

    def test_plan_has_only_20_independent_fresh_v1_1_verdicts(self):
        auth = authorization(self.proposal)
        from collusion_recovery.gate1_calibration import _gate1_config_and_plan
        config, plan, _dispatch, _inventory = _gate1_config_and_plan(
            self.proposal,
            auth,
            _proposal_verified=True,
        )
        self.assertEqual(len(plan.requests), 20)
        self.assertEqual(plan.checkpoints, ())
        self.assertEqual(
            [spec.metadata["fixture_index"] for spec in plan.requests],
            list(range(20)),
        )
        self.assertTrue(all(spec.stage == "calibration" for spec in plan.requests))
        self.assertTrue(all(spec.phase == "calibration" for spec in plan.requests))
        self.assertTrue(all(spec.action == "verdict" for spec in plan.requests))
        self.assertTrue(all(not spec.dependencies for spec in plan.requests))
        self.assertEqual(
            {spec.metadata["fixture_namespace"] for spec in plan.requests},
            {"calibration/v1.1"},
        )
        self.assertNotEqual(
            self.proposal["protocol_bridge"]["from"][
                "calibration_identity_hash"
            ],
            digest([spec.identity for spec in plan.requests]),
        )
        self.assertEqual(config.calibration_namespace, "calibration/v1.1")
        self.assertEqual(
            set(plan.fixture_inventory),
            {
                f"calibration/{index}/{side}"
                for index in range(20)
                for side in ("peer", "own")
            },
        )
        self.assertEqual(
            [
                plan.fixture_inventory[f"calibration/{index}/peer"]["defect"]
                for index in range(20)
            ],
            [None] * 10
            + ["omit", "add", "substitute", "omit", "add",
               "substitute", "omit", "add", "substitute", "omit"],
        )
        for index in range(20):
            peer = plan.fixture_inventory[f"calibration/{index}/peer"]
            own = plan.fixture_inventory[f"calibration/{index}/own"]
            self.assertEqual(peer["namespace"], f"calibration/v1.1/{index}")
            self.assertEqual(own["namespace"], f"calibration/v1.1/{index}/own")
            self.assertIsNone(own["defect"])

    def test_v1_1_config_rejects_historical_calibration_namespace(self):
        values = self.proposal["gate1_config_template"].copy()
        values = json.loads(canonical(values))
        values["authorization"] = {
            "kind": "paid",
            "authorization_id": "external-test",
            "approved_at": "2026-10-23T22:00:00-07:00",
            "approved_budget_usd": self.proposal["exact_caps"]["max_cost_usd"],
            "allow_paid_calls": True,
        }
        values["calibration_namespace"] = "calibration/v1"
        with self.assertRaisesRegex(LiveConfigError, "calibration/v1.1"):
            LiveConfig.from_mapping(values)

    def test_authorization_exactness_expiry_and_future_rejected(self):
        auth = authorization(self.proposal)
        validate_gate1_human_authorization(
            auth, self.proposal, _proposal_verified=True
        )
        tampered = dict(auth)
        tampered["approved_caps"] = dict(auth["approved_caps"])
        tampered["approved_caps"]["max_dispatch_attempts"] = 61
        body = dict(tampered)
        body.pop("authorization_hash")
        tampered["authorization_hash"] = digest(body)
        with self.assertRaises(Gate1CalibrationError):
            validate_gate1_human_authorization(
                tampered, self.proposal, _proposal_verified=True
            )
        from collusion_recovery.gate1_calibration import _validate_first_claim_window
        expired = authorization(
            self.proposal, confirmed_at="2026-10-23T21:45:00-07:00"
        )
        with self.assertRaisesRegex(Gate1CalibrationError, "expired"):
            _validate_first_claim_window(expired, self.proposal, now=TEST_NOW)
        future = authorization(
            self.proposal, confirmed_at="2026-10-23T22:07:00-07:00"
        )
        with self.assertRaisesRegex(Gate1CalibrationError, "future"):
            _validate_first_claim_window(future, self.proposal, now=TEST_NOW)

    def test_fake_transport_pass_persists_exact_decision_and_noop_resume(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            _auth, run, _config, plan, _dispatch, _inventory, _receipt, transport, controller = prepared
            report = controller.execute()
            self.assertEqual(report["status"], "completed_gate1_passed_hard_stop")
            self.assertTrue(report["assessment"]["passed"])
            self.assertEqual(report["assessment"]["scheduled"], 20)
            self.assertEqual(report["assessment"]["schema_valid"], 20)
            self.assertEqual(report["assessment"]["request_payloads_verified"], 20)
            self.assertEqual(report["assessment"]["wire_payloads_verified"], 20)
            self.assertEqual(report["assessment"]["provider_audits_complete"], 20)
            self.assertEqual(len(transport.payloads), 20)
            self.assertEqual(len(run.result_events()), 20)
            self.assertEqual(set(run.gate_events()), {"calibration"})
            self.assertTrue(run.gate_passed("calibration"))
            self.assertFalse(report["next_stage_authorized"])
            self.assertFalse(report["experiment_started"])
            before = (run.manifest["event_count"], run.manifest["last_event_hash"])
            repeated = controller.execute()
            self.assertEqual(repeated, report)
            self.assertEqual(
                (run.manifest["event_count"], run.manifest["last_event_hash"]),
                before,
            )
            self.assertEqual(len(plan.requests), 20)
            mode_events = [
                event for event in run.events
                if event.get("kind") == EXECUTION_MODE_BINDING_EVENT
            ]
            self.assertEqual(len(mode_events), 1)
            self.assertEqual(mode_events[0]["mode"], "fake_transport_test")

    def test_public_completed_empirical_resume_precedes_sdk_key_and_client(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "workspace"
            prepared = self._prepared(workspace)
            (auth, run, _config, _plan, _dispatch, inventory, receipt,
             _unused_transport, _unused_controller) = prepared
            transport = FakeCalibrationTransport()
            transport.audit_metadata = self.proposal["transport"]
            with (
                patch(
                    "collusion_recovery.gate1_calibration.OpenAISDKTransport",
                    FakeCalibrationTransport,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.importlib_metadata.version",
                    return_value=STAGE2_SDK_VERSION,
                ),
            ):
                report = Gate1CalibrationExecutor(
                    run,
                    OpenAIResponsesProvider(transport),
                    self.proposal,
                    inventory,
                    authorization_receipt=receipt,
                    fake_transport_test=False,
                ).execute()
            self.assertEqual(report["source"], "empirical-live")
            receipt_paths = list(root.glob("external-*/receipts/*.json"))
            self.assertEqual(len(receipt_paths), 1)
            before = (
                run.manifest["event_count"],
                run.manifest["last_event_hash"],
                run.manifest["status"],
            )
            with (
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_calibration_proposal",
                    return_value=self.proposal,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_human_authorization",
                    return_value=auth,
                ),
                patch("collusion_recovery.gate1_calibration.ROOT", workspace),
                patch(
                    "collusion_recovery.gate1_calibration._authorization_receipt_directory",
                    return_value=receipt_paths[0].parent,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                    receipt_paths[0].parent,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.importlib_metadata.version",
                    side_effect=AssertionError("SDK check must not run"),
                ) as sdk_check,
                patch(
                    "collusion_recovery.gate1_calibration._load_local_openai_key",
                    side_effect=AssertionError("key must not be read"),
                ) as key_read,
            ):
                repeated = run_gate1_calibration(
                    root / "authorization.json", _now=TEST_NOW
                )
            self.assertEqual(repeated, report)
            sdk_check.assert_not_called()
            key_read.assert_not_called()
            reopened = DurableRun.inspect(
                run.path, probe_installed_dependencies=False
            )
            self.assertEqual(
                (
                    reopened.manifest["event_count"],
                    reopened.manifest["last_event_hash"],
                    reopened.manifest["status"],
                ),
                before,
            )

            # Rehashing the entire derivative stream cannot make an already
            # observed verdict nonfinal and then silently resample that same
            # calibration identity. Gate 1 authorizes only proven-not-
            # completed transport retries before one final verdict.
            original_events = run.events
            tampered_events = json.loads(canonical(original_events))
            first_result = next(
                event for event in tampered_events
                if event.get("kind") == "result"
            )
            duplicated_final = json.loads(canonical(first_result))
            duplicated_final["attempt"] = first_result["attempt"] + 1
            gate_position = next(
                index for index, event in enumerate(tampered_events)
                if event.get("kind") == "gate"
                and event.get("gate") == "calibration"
            )
            tampered_events.insert(gate_position, duplicated_final)
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
                        Gate1CalibrationError,
                        "unauthorized nonfinal observation"):
                    _verify_completed_gate1_calibration(
                        run, self.proposal, auth, receipt
                    )
            finally:
                run.events = original_events

    def test_gate1_assessment_rejects_parsed_output_raw_text_disagreement(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            _auth, run, _config, plan, _dispatch, _inventory, _receipt, _transport, controller = prepared
            controller.execute()
            first = run.result_events(plan.requests[0].identity)[-1]
            self.assertEqual(
                first["provider_result"]["parsed_output"]["verdict"], "ACCEPT"
            )
            first["provider_result"]["parsed_output"]["verdict"] = "REJECT"

            assessment = calibration_assessment(run)

            self.assertFalse(assessment["passed"])
            self.assertEqual(assessment["schema_valid"], 19)
            self.assertIsNone(assessment["observations"][0]["observed_verdict"])

    def test_gate1_assessment_binds_durable_text_to_raw_provider_response(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            (_auth, run, _config, plan, _dispatch, _inventory, _receipt,
             _transport, controller) = prepared
            controller.execute()
            first = run.result_events(plan.requests[0].identity)[-1]
            forged = {
                "verdict": "REJECT",
                "evidence_record_ids": [],
                "brief_reason": "Forged derivative not present in raw provider evidence.",
            }
            first["provider_result"]["output_text"] = canonical(forged)
            first["provider_result"]["parsed_output"] = forged

            assessment = calibration_assessment(run)

            self.assertFalse(assessment["passed"])
            self.assertEqual(assessment["schema_valid"], 20)
            self.assertEqual(assessment["provider_audits_complete"], 19)
            self.assertFalse(
                assessment["observations"][0]["provider_audit_complete"]
            )

    def test_gate1_assessment_binds_journal_accounting_fields_to_provider_result(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            (_auth, run, _config, plan, _dispatch, _inventory, _receipt,
             _transport, controller) = prepared
            controller.execute()
            first = run.result_events(plan.requests[0].identity)[-1]
            first["usage"] = dict(first["usage"])
            first["usage"]["input_tokens"] += 1

            assessment = calibration_assessment(run)

            self.assertFalse(assessment["passed"])
            self.assertEqual(assessment["provider_audits_complete"], 19)
            self.assertFalse(
                assessment["observations"][0]["provider_audit_complete"]
            )

    def test_fake_pass_cannot_be_rehashed_and_relabelled_empirical(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            prepared = self._prepared(root / "workspace")
            _auth, run, *_rest, controller = prepared
            report = controller.execute()
            proposal_path = root / "proposal.json"
            proposal_path.write_text(
                canonical(self.proposal) + "\n", encoding="utf-8"
            )
            report_path = run.path / "gate1_calibration_report.json"
            forged = dict(report)
            forged["source"] = "empirical-live"
            forged["warning"] = None
            forged["execution_mode"] = EMPIRICAL_EXECUTION_MODE
            body = dict(forged)
            body.pop("report_hash")
            forged["report_hash"] = digest(body)
            report_path.write_text(
                canonical(forged) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "immutable empirical execution mode"):
                verify_gate1_passed_lineage(
                    run.path, proposal_path=proposal_path
                )

    def test_passed_lineage_rejects_dispatch_before_empirical_mode_binding(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            prepared = self._prepared(root / "workspace")
            _auth, run, *_rest, controller = prepared
            controller.execute()
            proposal_path = root / "proposal.json"
            proposal_path.write_text(
                canonical(self.proposal) + "\n", encoding="utf-8"
            )

            # Rebuild a fully self-consistent hash chain that moves and
            # relabels the immutable mode event after the first reservation.
            # The lineage verifier must independently reject the ordering,
            # even though the journal hashes and mutable manifest tail agree.
            originals = [dict(event) for event in run.events]
            mode = next(
                event for event in originals
                if event.get("kind") == EXECUTION_MODE_BINDING_EVENT
            )
            originals.remove(mode)
            first_reservation_index = next(
                index for index, event in enumerate(originals)
                if event.get("kind") == "reservation"
            )
            mode["mode"] = EMPIRICAL_EXECUTION_MODE
            originals.insert(first_reservation_index + 1, mode)
            rebuilt = []
            previous = None
            for sequence, original in enumerate(originals):
                event = dict(original)
                event.pop("event_hash", None)
                event["sequence"] = sequence
                event["previous_event_hash"] = previous
                event_hash = digest(event)
                event["event_hash"] = event_hash
                rebuilt.append(event)
                previous = event_hash
            run.journal_path.write_text(
                "".join(canonical(event) + "\n" for event in rebuilt),
                encoding="utf-8",
            )
            manifest_path = run.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["event_count"] = len(rebuilt)
            manifest["last_event_hash"] = previous
            manifest_path.write_text(
                canonical(manifest) + "\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                    Gate1CalibrationError, "dispatch precedes"):
                verify_gate1_passed_lineage(
                    run.path, proposal_path=proposal_path
                )

    def test_failed_accuracy_decision_is_immutable_and_hard_stops(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(
                Path(td) / "workspace", fail_accuracy=True
            )
            _auth, run, _config, _plan, _dispatch, _inventory, _receipt, _transport, controller = prepared
            report = controller.execute()
            self.assertEqual(report["status"], "completed_gate1_failed_hard_stop")
            self.assertFalse(report["gate1_passed"])
            self.assertFalse(run.gate_passed("calibration"))
            self.assertEqual(
                run.manifest["status"], "completed_gate1_failed_hard_stop"
            )
            self.assertFalse(run.manifest["next_stage_authorized"])

    def test_two_global_transport_retries_each_fit_exact_60_attempt_cap(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            retry_transport = RetryTwiceCalibrationTransport()
            prepared = self._prepared(
                Path(td) / "workspace", transport=retry_transport
            )
            _auth, run, _config, _plan, _dispatch, _inventory, _receipt, transport, controller = prepared
            report = controller.execute()
            self.assertTrue(report["gate1_passed"])
            self.assertEqual(report["dispatch_attempts"], 60)
            self.assertEqual(len(transport.payloads), 60)
            self.assertEqual(len(run.result_events()), 60)
            self.assertEqual(report["budget"]["consumed"]["requests"], 60)
            self.assertEqual(
                report["budget"]["release"]["max_dispatch_attempts"],
                60,
            )

    def test_report_tamper_even_with_rehash_is_rejected(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            _auth, run, _config, _plan, _dispatch, _inventory, _receipt, _transport, controller = prepared
            controller.execute()
            path = run.path / "gate1_calibration_report.json"
            report = json.loads(path.read_text(encoding="utf-8"))
            report["logical_requests"] = 19
            body = dict(report)
            body.pop("report_hash")
            report["report_hash"] = digest(body)
            path.write_text(canonical(report) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ResumeError, "report differs"):
                controller.execute()

    def test_receipt_replay_recreated_run_and_schema_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            workspace = Path(td) / "workspace"
            prepared = self._prepared(workspace)
            auth, run, _config, _plan, _dispatch, _inventory, receipt, _transport, _controller = prepared
            # Locate the external receipt via its unique UUID under the temp tree.
            matches = list(Path(td).glob("external-*/receipts/*.json"))
            self.assertEqual(len(matches), 1)
            receipt_path = matches[0]
            original = receipt_path.read_text(encoding="utf-8")
            tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
            tampered["extra"] = "not allowed"
            body = dict(tampered)
            body.pop("receipt_hash")
            tampered["receipt_hash"] = digest(body)
            receipt_path.write_text(canonical(tampered) + "\n", encoding="utf-8")
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                receipt_path.parent,
            ):
                with self.assertRaises(Gate1CalibrationError):
                    claim_gate1_authorization_once(
                        run,
                        self.proposal,
                        auth,
                        receipt_dir=receipt_path.parent,
                        now=TEST_NOW,
                    )
            receipt_path.write_text(original, encoding="utf-8")

            # Recreating the same authorized path produces a new, timestamped
            # binding event and random nonce. The external receipt remains
            # bound to the first instance and therefore cannot be replayed.
            original_path = run.path
            archived_path = original_path.with_name(original_path.name + "-old")
            original_path.rename(archived_path)
            recreated, *_ = prepare_gate1_calibration_run(
                original_path,
                self.proposal,
                auth,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                receipt_path.parent,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "already claimed"):
                    claim_gate1_authorization_once(
                        recreated,
                        self.proposal,
                        auth,
                        receipt_dir=receipt_path.parent,
                        now=TEST_NOW,
                    )

    def test_fixed_run_and_binding_cannot_use_generic_executor(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            _auth, run, config, plan, dispatch, _inventory, _receipt, _transport, _controller = prepared
            manifest_path = run.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("execution_controller")
            manifest_path.write_text(canonical(manifest) + "\n", encoding="utf-8")
            with self.assertRaises(AuthorizationRequiredError):
                DurableRun.open(
                    run.path,
                    config,
                    plan,
                    resume=True,
                    dispatch_authorization=dispatch,
                    execution_controller=None,
                )
            # Defense in depth: even an already-open object is rejected by its
            # fixed run id and verified binding event.
            with self.assertRaises(AuthorizationRequiredError):
                StagedExecutor(run, object(), lambda *_args: None)
            with self.assertRaisesRegex(
                    AuthorizationRequiredError, "generic gate mutation"):
                run.record_gate_decision(
                    "calibration",
                    passed=False,
                    evidence={"bypass": True},
                    decided_by="bypass",
                )
            self.assertTrue(any(
                event["kind"] == GATE1_CALIBRATION_BINDING_EVENT
                for event in run.events
            ))

    def test_missing_mismatched_and_missing_raw_authorization_fail_before_prepare(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            proposal_path = root / "proposal.json"
            self._write_owner_only_json(proposal_path, self.proposal)
            missing_auth = root / "external" / "missing.json"
            with (
                patch(
                    "collusion_recovery.gate1_calibration.prepare_gate1_calibration_run"
                ) as prepare,
                patch(
                    "collusion_recovery.gate1_calibration._load_local_openai_key"
                ) as load_key,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "authorization is unavailable"):
                    run_gate1_calibration(
                        missing_auth, proposal_path=proposal_path, _now=TEST_NOW
                    )
                prepare.assert_not_called()
                load_key.assert_not_called()

            auth = authorization(self.proposal)
            tampered = json.loads(canonical(auth))
            tampered["approved_caps"]["max_dispatch_attempts"] = 61
            body = dict(tampered)
            body.pop("authorization_hash")
            tampered["authorization_hash"] = digest(body)
            auth_path = root / "external" / "tampered.json"
            self._write_owner_only_json(auth_path, tampered)
            with (
                patch(
                    "collusion_recovery.gate1_calibration.prepare_gate1_calibration_run"
                ) as prepare,
                patch(
                    "collusion_recovery.gate1_calibration._load_local_openai_key"
                ) as load_key,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "exact Gate 1 scope"):
                    run_gate1_calibration(
                        auth_path, proposal_path=proposal_path, _now=TEST_NOW
                    )
                prepare.assert_not_called()
                load_key.assert_not_called()

            valid_path = root / "external" / "valid.json"
            self._write_owner_only_json(valid_path, auth)
            missing_stage2 = root / "missing-raw-stage2"
            with (
                patch(
                    "collusion_recovery.gate1_calibration.STAGE2_RUN_DIR",
                    missing_stage2,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.prepare_gate1_calibration_run"
                ) as prepare,
                patch(
                    "collusion_recovery.gate1_calibration._load_local_openai_key"
                ) as load_key,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "complete raw Stage 2 lineage"):
                    validate_gate1_authorization_file(
                        valid_path, proposal_path, now=TEST_NOW
                    )
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "complete raw Stage 2 lineage"):
                    run_gate1_calibration(
                        valid_path, proposal_path=proposal_path, _now=TEST_NOW
                    )
                prepare.assert_not_called()
                load_key.assert_not_called()

    def test_live_path_claims_before_secret_sdk_or_client_construction(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            registry = root / "owner-state" / "gate1"
            auth = authorization(self.proposal)
            sentinel = Gate1CalibrationError("claim sentinel")
            with (
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_calibration_proposal",
                    return_value=self.proposal,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_human_authorization",
                    return_value=auth,
                ),
                patch("collusion_recovery.gate1_calibration.ROOT", workspace),
                patch(
                    "collusion_recovery.gate1_calibration._authorization_receipt_directory",
                    return_value=registry,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.claim_gate1_authorization_once",
                    side_effect=sentinel,
                ) as claim,
                patch(
                    "collusion_recovery.gate1_calibration._load_local_openai_key"
                ) as load_key,
                patch(
                    "collusion_recovery.gate1_calibration.importlib_metadata.version"
                ) as sdk_version,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "claim sentinel"):
                    run_gate1_calibration(
                        root / "ignored-auth.json", _now=TEST_NOW
                    )
                claim.assert_called_once()
                load_key.assert_not_called()
                sdk_version.assert_not_called()

    def test_stale_future_poisoned_and_stale_pricing_stop_before_prepare(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            registry = root / "owner-state" / "gate1"
            for label, auth in (
                ("stale", authorization(
                    self.proposal,
                    confirmed_at="2026-10-23T21:45:00-07:00",
                )),
                ("future", authorization(
                    self.proposal,
                    confirmed_at="2026-10-23T22:07:00-07:00",
                )),
            ):
                with self.subTest(label=label):
                    with (
                        patch(
                            "collusion_recovery.gate1_calibration.load_gate1_calibration_proposal",
                            return_value=self.proposal,
                        ),
                        patch(
                            "collusion_recovery.gate1_calibration.load_gate1_human_authorization",
                            return_value=auth,
                        ),
                        patch(
                            "collusion_recovery.gate1_calibration.ROOT",
                            workspace,
                        ),
                        patch(
                            "collusion_recovery.gate1_calibration._authorization_receipt_directory",
                            return_value=registry,
                        ),
                        patch(
                            "collusion_recovery.gate1_calibration._gate1_config_and_plan"
                        ) as derive,
                        patch(
                            "collusion_recovery.gate1_calibration.prepare_gate1_calibration_run"
                        ) as prepare,
                    ):
                        with self.assertRaises(Gate1CalibrationError):
                            run_gate1_calibration(
                                root / f"{label}.json", _now=TEST_NOW
                            )
                        derive.assert_not_called()
                        prepare.assert_not_called()

            valid = authorization(self.proposal)
            registry.mkdir(parents=True)
            _authorization_receipt_path(registry).write_text(
                "{}\n", encoding="utf-8"
            )
            with (
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_calibration_proposal",
                    return_value=self.proposal,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_human_authorization",
                    return_value=valid,
                ),
                patch("collusion_recovery.gate1_calibration.ROOT", workspace),
                patch(
                    "collusion_recovery.gate1_calibration._authorization_receipt_directory",
                    return_value=registry,
                ),
                patch(
                    "collusion_recovery.gate1_calibration._gate1_config_and_plan"
                ) as derive,
                patch(
                    "collusion_recovery.gate1_calibration.prepare_gate1_calibration_run"
                ) as prepare,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "receipt exists without"):
                    run_gate1_calibration(root / "copy.json", _now=TEST_NOW)
                derive.assert_not_called()
                prepare.assert_not_called()

            run_path = workspace / valid["gate1_run_directory"]
            run_path.mkdir(parents=True)
            stale_proposal = json.loads(canonical(self.proposal))
            stale_proposal["pricing_contract"]["reverified_at"] = (
                "2026-10-22T00:00:00-07:00"
            )
            with (
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_calibration_proposal",
                    return_value=stale_proposal,
                ),
                patch(
                    "collusion_recovery.gate1_calibration.load_gate1_human_authorization",
                    return_value=valid,
                ),
                patch("collusion_recovery.gate1_calibration.ROOT", workspace),
                patch(
                    "collusion_recovery.gate1_calibration._authorization_receipt_directory",
                    return_value=registry,
                ),
                patch(
                    "collusion_recovery.gate1_calibration._gate1_config_and_plan"
                ) as derive,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "pricing is stale"):
                    run_gate1_calibration(root / "resume.json", _now=TEST_NOW)
                derive.assert_not_called()

    def test_terminal_overlength_and_ambiguous_timeout_hard_stop(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            prepared = self._prepared(root / "overlength")
            _auth, run, *_middle, controller = prepared
            identity = self.proposal["required_provider_identity"]
            provider_result = ProviderResult(
                status=ProviderStatus.OVERLENGTH,
                raw_response_json=None,
                output_text=None,
                parsed_output_json=None,
                refusal=None,
                error="test overlength",
                provider_request_id="test-overlength",
                returned_model=identity["returned_model"],
                model_metadata_json=canonical({
                    "model": identity["metadata_model"],
                    "service_tier": identity["returned_service_tier"],
                    "system_fingerprint": identity["system_fingerprint"],
                }),
                usage=None,
                timing=ProviderTiming(
                    "2026-10-23T22:00:00-07:00",
                    "2026-10-23T22:00:01-07:00",
                    1.0,
                ),
            )
            controller.provider.complete = Mock(return_value=provider_result)
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "overlength"):
                controller.execute()
            self.assertEqual(run.manifest["status"], "paused_overlength_verdict")
            self.assertEqual(len(run.result_events()), 1)
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "remains stopped"):
                controller.execute()
            self.assertEqual(len(run.result_events()), 1)
            self.assertFalse(
                (run.path / "gate1_calibration_report.json").exists()
            )

            prepared = self._prepared(root / "ambiguous")
            _auth, ambiguous_run, *_middle, ambiguous_controller = prepared
            ambiguous_controller.provider.complete = Mock(
                side_effect=RuntimeError("unknown completion")
            )
            with self.assertRaises(Gate1CalibrationError):
                ambiguous_controller.execute()
            self.assertEqual(
                ambiguous_run.manifest["status"], "paused_ambiguous_timeout"
            )
            self.assertEqual(len(ambiguous_run.result_events()), 1)
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "remains stopped"):
                ambiguous_controller.execute()
            self.assertEqual(len(ambiguous_run.result_events()), 1)

            prepared = self._prepared(root / "zero-usage-not-proof")
            _auth, zero_run, *_middle, zero_controller = prepared
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
                "error": "zero usage object is not completion proof",
            }
            zero_controller.provider.complete = Mock(
                return_value=unsafe_result
            )
            with self.assertRaises(Gate1CalibrationError):
                zero_controller.execute()
            row = zero_run.result_events()[0]
            self.assertEqual(row["status"], "ambiguous_timeout")
            self.assertIs(row["known_not_completed"], False)
            self.assertEqual(
                row["provider_result"]["original_status"],
                "retryable_error",
            )

    def test_live_transport_binding_is_exact_idempotent_and_drift_stops(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            prepared = self._prepared(Path(td) / "workspace")
            _auth, run, *_middle, controller = prepared
            transport = OpenAISDKTransport(
                NoCallSDKClient(),
                timeout_seconds=STAGE2_TIMEOUT_SECONDS,
                sdk_version=STAGE2_SDK_VERSION,
                ambient_openai_environment="rejected",
                http_client_trust_env=False,
                http_client_follow_redirects=False,
            )
            controller.provider = OpenAIResponsesProvider(transport)
            controller.fake_transport_test = False
            with patch(
                "collusion_recovery.gate1_calibration.importlib_metadata.version",
                return_value=STAGE2_SDK_VERSION,
            ):
                controller._bind_live_transport()
                with patch.object(
                        run, "set_status", wraps=run.set_status) as status:
                    controller._bind_live_transport()
                    status.assert_not_called()
            self.assertEqual(run.manifest["transport"], self.proposal["transport"])
            self.assertEqual(
                run.manifest["transport_hash"], self.proposal["transport_hash"]
            )

            bad_identity = dict(self.proposal["required_provider_identity"])
            bad_identity["returned_model"] = "wrong-model"
            run.append(
                "result",
                identity=run.plan.requests[0].identity,
                status="ok",
                provider_identity=bad_identity,
                provider_result={
                    "returned_model": "wrong-model",
                    "model_metadata": {
                        "model": bad_identity["metadata_model"],
                        "service_tier": bad_identity[
                            "returned_service_tier"
                        ],
                        "system_fingerprint": bad_identity[
                            "system_fingerprint"
                        ],
                    },
                },
            )
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "provider identity differs"):
                controller._verify_provider_identity_history()
            self.assertEqual(run.manifest["status"], "paused_model_drift")

    def test_claim_event_not_mutable_manifest_is_required(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td) / "workspace"
            root.mkdir()
            auth = authorization(self.proposal)
            run, _config, _plan, _dispatch, inventory = (
                prepare_gate1_calibration_run(
                    root / auth["gate1_run_directory"],
                    self.proposal,
                    auth,
                    workspace_root=root,
                    _proposal_verified=True,
                )
            )
            binding = next(
                event for event in run.events
                if event["kind"] == GATE1_CALIBRATION_BINDING_EVENT
            )
            receipt_body = {
                "artifact_type": "gate_1_authorization_consumption_receipt_v1_1",
                "authorization_uuid": auth["authorization_uuid"],
                "authorization_hash": auth["authorization_hash"],
                "proposal_hash": self.proposal["proposal_hash"],
                "protocol_bridge_hash": self.proposal["protocol_bridge"][
                    "bridge_hash"
                ],
                "gate1_run_id": auth["gate1_run_id"],
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
            receipt = {
                **receipt_body,
                "receipt_hash": digest(receipt_body),
            }
            run.set_status(
                run.manifest["status"],
                gate1_authorization_consumed=True,
                gate1_authorization_receipt_hash=receipt["receipt_hash"],
                gate1_authorization_claim_event_hash="0" * 64,
            )
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "journal-bound"):
                Gate1CalibrationExecutor(
                    run,
                    OpenAIResponsesProvider(FakeCalibrationTransport()),
                    self.proposal,
                    inventory,
                    authorization_receipt=receipt,
                    fake_transport_test=True,
                )

    def test_passed_lineage_rejects_manifest_only_consumption_claim(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            prepared = self._prepared(root / "workspace")
            _auth, run, *_middle, controller = prepared
            controller.execute()
            proposal_path = root / "proposal.json"
            proposal_path.write_text(
                canonical(self.proposal) + "\n", encoding="utf-8"
            )
            remaining = [
                event for event in run.events
                if event.get("kind") != AUTHORIZATION_CLAIM_EVENT
            ]
            rebuilt = []
            previous = None
            for sequence, original in enumerate(remaining):
                event = dict(original)
                event.pop("event_hash")
                event["sequence"] = sequence
                event["previous_event_hash"] = previous
                event_hash = digest(event)
                event["event_hash"] = event_hash
                rebuilt.append(event)
                previous = event_hash
            run.journal_path.write_text(
                "".join(canonical(event) + "\n" for event in rebuilt),
                encoding="utf-8",
            )
            manifest_path = run.path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["event_count"] = len(rebuilt)
            manifest["last_event_hash"] = previous
            # Deliberately retain the mutable consumed flag/receipt/claim hash.
            manifest_path.write_text(
                canonical(manifest) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "not journal-bound"):
                verify_gate1_passed_lineage(
                    run.path, proposal_path=proposal_path
                )

    def test_fixed_release_receipt_blocks_auth_copy_and_replacement_uuid(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            registry = root / "owner-state" / "gate1"
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ):
                self.assertEqual(
                    _authorization_receipt_directory(root / "a" / "auth.json"),
                    _authorization_receipt_directory(root / "b" / "copy.json"),
                )
            first = authorization(self.proposal)
            first_run, *_ = prepare_gate1_calibration_run(
                workspace / first["gate1_run_directory"],
                self.proposal,
                first,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ):
                claim_gate1_authorization_once(
                    first_run,
                    self.proposal,
                    first,
                    receipt_dir=registry,
                    now=TEST_NOW,
                )
            old = first_run.path.with_name(first_run.path.name + "-old")
            first_run.path.rename(old)
            replacement = authorization(self.proposal)
            replacement_run, *_ = prepare_gate1_calibration_run(
                workspace / replacement["gate1_run_directory"],
                self.proposal,
                replacement,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "already claimed"):
                    claim_gate1_authorization_once(
                        replacement_run,
                        self.proposal,
                        replacement,
                        receipt_dir=registry,
                        now=TEST_NOW,
                    )

    def test_authorized_run_path_rejects_symlink_redirection_without_writes(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (workspace / "runs").symlink_to(outside, target_is_directory=True)
            auth = authorization(self.proposal)
            with self.assertRaisesRegex(
                    Gate1CalibrationError, "outside|symlink"):
                prepare_gate1_calibration_run(
                    workspace / auth["gate1_run_directory"],
                    self.proposal,
                    auth,
                    workspace_root=workspace,
                    _proposal_verified=True,
                )
            self.assertEqual(list(outside.iterdir()), [])

    def test_receipt_registry_rejects_symlinked_parent_without_writes(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            target = root / "replaceable-target"
            target.mkdir()
            redirect = root / "owner-state"
            redirect.symlink_to(target, target_is_directory=True)
            registry = redirect / "gate1-v1.1"
            auth = authorization(self.proposal)
            run, *_ = prepare_gate1_calibration_run(
                workspace / auth["gate1_run_directory"],
                self.proposal,
                auth,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError, "symlink components"):
                    claim_gate1_authorization_once(
                        run,
                        self.proposal,
                        auth,
                        receipt_dir=registry,
                        now=TEST_NOW,
                    )
            self.assertEqual(list(target.iterdir()), [])

    def test_receipt_registry_rejects_writable_or_wrong_owner_parent(self):
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            registry = root / "owner-state" / "gate1-v1.1"
            auth = authorization(self.proposal)
            run, *_ = prepare_gate1_calibration_run(
                workspace / auth["gate1_run_directory"],
                self.proposal,
                auth,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            root.chmod(0o777)
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError,
                        "current-user-owned non-writable"):
                    claim_gate1_authorization_once(
                        run, self.proposal, auth,
                        receipt_dir=registry, now=TEST_NOW,
                    )
            self.assertFalse(registry.exists())
            root.chmod(0o700)
            with patch(
                "collusion_recovery.gate1_calibration.AUTHORIZATION_RECEIPT_DIRECTORY",
                registry,
            ), patch(
                "collusion_recovery.gate1_calibration.os.getuid",
                return_value=10_000_000,
            ):
                with self.assertRaisesRegex(
                        Gate1CalibrationError,
                        "current-user-owned non-writable"):
                    claim_gate1_authorization_once(
                        run, self.proposal, auth,
                        receipt_dir=registry, now=TEST_NOW,
                    )
            self.assertFalse(registry.exists())

    def test_expired_prepared_unclaimed_run_requires_new_versioned_release(self):
        expired_now = datetime.fromisoformat("2026-10-23T22:16:00-07:00")
        with tempfile.TemporaryDirectory() as td, no_network():
            root = Path(td)
            workspace = root / "repo"
            workspace.mkdir()
            auth = authorization(self.proposal)
            run_path = workspace / auth["gate1_run_directory"]
            prepare_gate1_calibration_run(
                run_path,
                self.proposal,
                auth,
                workspace_root=workspace,
                _proposal_verified=True,
            )
            registry = root / "receipts"
            with patch(
                    "collusion_recovery.gate1_calibration.load_gate1_calibration_proposal",
                    return_value=self.proposal), patch(
                    "collusion_recovery.gate1_calibration.load_gate1_human_authorization",
                    return_value=auth), patch(
                    "collusion_recovery.gate1_calibration.ROOT",
                    workspace), patch(
                    "collusion_recovery.gate1_calibration._authorization_receipt_directory",
                    return_value=registry), patch(
                    "collusion_recovery.gate1_calibration._gate1_config_and_plan"
                    ) as derive, patch(
                    "collusion_recovery.gate1_calibration.prepare_gate1_calibration_run"
                    ) as prepare, patch(
                    "collusion_recovery.gate1_calibration._load_local_openai_key"
                    ) as load_key:
                with self.assertRaisesRegex(
                        Gate1CalibrationError,
                        "new versioned release/run ID"):
                    run_gate1_calibration(
                        root / "external-authorization.json",
                        _now=expired_now,
                    )
            derive.assert_not_called()
            prepare.assert_not_called()
            load_key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
