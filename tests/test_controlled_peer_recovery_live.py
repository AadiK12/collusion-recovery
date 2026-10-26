"""Launch-critical offline tests for the controlled-history recovery shell."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock

from collusion_recovery.common import canonical, digest, utc_now
from collusion_recovery.fixtures import generate_fixture
from collusion_recovery.history import History
from collusion_recovery.providers.base import ProviderResult, ProviderStatus, ProviderTiming
from collusion_recovery.providers.openai import OpenAIResponsesProvider
from collusion_recovery.visibility import request_for

from experiments import controlled_peer_recovery_live as live


RUN_ID = "33333333-3333-4333-8333-333333333333"
NAMESPACE = f"controlled-peer-recovery/v1/{RUN_ID}"


class ProjectionAndAuthorizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.projection = live.build_projection(RUN_ID, NAMESPACE)
        cls.reconciliation = live.complete_budget_reconciliation(
            verified_at=live.PRICING_CONTRACT["reverified_at"],
            excluded_run_directory=(
                f"runs/empirical/controlled-peer-recovery-v1-{RUN_ID}"
            ),
        )
        cls.proposal = live.build_release_proposal(
            cls.projection, cls.reconciliation
        )

    def test_exact_schedule_caps_and_shared_remainder(self):
        self.assertEqual(self.projection["counts"], {
            "logical_requests": 560,
            "calibration_requests": 20,
            "hypothesis_requests": 540,
            "message_identities": 200,
            "verdict_identities": 360,
            "base_billable_content_shapes": 760,
            "ambiguous_5xx_billable_slots": 6,
            "maximum_billable_shapes": 766,
            "known_not_completed_retry_slots": 1120,
            "maximum_dispatch_attempts": 1886,
            "calibration_decisions": 20,
            "hypothesis_scored_decisions": 340,
            "checkpoints": 10,
        })
        self.assertEqual(
            self.reconciliation["historical_program_spend_usd"], "14.549712"
        )
        self.assertEqual(
            self.proposal["exact_caps"]["max_cost_usd"], "235.450288"
        )
        self.assertFalse(self.proposal["authorization"]["completion_guaranteed"])
        self.assertTrue(
            self.proposal["authorization"][
                "conservative_envelope_exceeds_operational_cap"
            ]
        )

    def _scope_evidence(self):
        body = {
            "artifact_type": "controlled_peer_recovery_scope_evidence_v1",
            "source_kind": "direct_human_instruction",
            "source_reference": "unit-test/direct-user-message",
            "verbatim_human_instruction": (
                "Proceed with the exact fixed 20 plus 540 scope under the existing cap."
            ),
            "instruction_recorded_at": "2026-10-25T20:00:00-07:00",
        }
        return {**body, "evidence_hash": digest(body)}

    def _authorization(self, packaged_at: str):
        return live.complete_human_authorization(self.proposal, {
            "authorization_uuid": "44444444-4444-4444-8444-444444444444",
            "authorization_id": (
                "controlled-peer-recovery-v1-human-"
                "44444444-4444-4444-8444-444444444444"
            ),
            "authorization_nonce": "a" * 64,
            "authorized_by": "direct-user-scope-evidence",
            "packaged_at": packaged_at,
            "scope_evidence": self._scope_evidence(),
        })

    def test_scope_evidence_is_verbatim_while_machine_binds_final_hashes(self):
        authorization = self._authorization("2026-10-25T20:10:00-07:00")
        self.assertEqual(
            authorization["scope_evidence"]["verbatim_human_instruction"],
            "Proceed with the exact fixed 20 plus 540 scope under the existing cap.",
        )
        self.assertEqual(
            authorization["approved_scope_hash"], self.proposal["scope_hash"]
        )
        self.assertNotIn("human_acknowledgement", authorization)

    def test_existing_exact_receipt_can_resume_after_packaging_freshness(self):
        authorization = self._authorization("2026-10-25T20:10:00-07:00")
        with tempfile.TemporaryDirectory() as temporary:
            receipt_root = Path(temporary) / "receipts"
            with mock.patch.object(live, "RECEIPT_ROOT", receipt_root):
                receipt = live._receipt_value(
                    authorization, self.proposal,
                    datetime.now(timezone.utc).isoformat(),
                )
                live._atomic_json(
                    live._receipt_path(self.proposal), receipt, exclusive=True
                )
                observed, created = live._claim_authorization_once(
                    authorization, self.proposal
                )
        self.assertFalse(created)
        self.assertEqual(observed, receipt)

    def test_fresh_empirical_prefix_verifies_before_credential_constructor(self):
        now = datetime.now(timezone.utc).isoformat()
        authorization = self._authorization(now)
        plan = live.build_live_plan(RUN_ID, NAMESPACE)

        class CredentialSentinel(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            fake_root = base / "repo"
            fake_runs = fake_root / "runs" / "empirical"
            proposal_path = base / "proposal.json"
            authorization_path = base / "authorization.json"
            proposal_path.write_text(json.dumps(self.proposal), encoding="utf-8")
            authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
            proposal_path.chmod(0o600)
            authorization_path.chmod(0o600)
            with (
                mock.patch.object(live, "ROOT", fake_root),
                mock.patch.object(live, "RUNS_ROOT", fake_runs),
                mock.patch.object(
                    live, "PROGRAM_LOCK_PATH", fake_runs / ".program.lock"
                ),
                mock.patch.object(live, "RECEIPT_ROOT", base / "receipts"),
                mock.patch.object(
                    live, "validate_release_proposal", return_value=self.proposal
                ),
                mock.patch.object(
                    live, "build_projection", return_value=self.projection
                ),
                mock.patch.object(
                    live, "build_live_plan",
                    return_value=plan,
                ),
                mock.patch.object(
                    live, "_official_provider",
                    side_effect=CredentialSentinel("credential boundary reached"),
                ) as credential,
            ):
                with self.assertRaises(CredentialSentinel):
                    live.run_controlled_peer_recovery(
                        proposal_path, authorization_path, fake_root / ".env.local"
                    )
                run_path = fake_root / self.proposal["run"]["run_directory"]
                events = live._verify_journal(run_path / "journal.jsonl")
                self.assertEqual(
                    [row["kind"] for row in events],
                    ["mode_binding", "transport_binding"],
                )
                self.assertTrue((base / "receipts" / f"{RUN_ID}.json").is_file())
                self.assertEqual(credential.call_count, 1)


class RetryAndCrashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = live.build_live_plan(RUN_ID, NAMESPACE)
        cls.projection = live.build_projection(RUN_ID, NAMESPACE)
        cls.proposal = live._offline_proposal(cls.projection)

    @staticmethod
    def _build_calibration(_retry: bool):
        peer = generate_fixture(f"{NAMESPACE}/capability-calibration/0", None)
        own = generate_fixture(f"{NAMESPACE}/capability-calibration/0/own")
        if _retry:
            raise AssertionError("verdict request cannot use a content revision")
        return request_for(
            History(), own.packet, peer.packet, "alice", "calibration", "full",
            protocol_version="v1.2",
        )

    def _new_store(self, path: Path):
        return live._RunStore(
            path, self.proposal, self.projection, self.plan, None,
            execution_mode="offline_fake", create=True,
        )

    def test_two_known_not_completed_redispatches_are_available(self):
        class TwoFailuresThenSuccess:
            def __init__(self):
                self.calls = 0
                self.success = OpenAIResponsesProvider(live._OfflineResponsesTransport())

            def complete(self, request):
                self.calls += 1
                if self.calls <= 2:
                    now = utc_now()
                    return ProviderResult(
                        status=ProviderStatus.RETRYABLE_ERROR,
                        raw_response_json=None,
                        output_text=None,
                        parsed_output_json=None,
                        refusal=None,
                        error="offline known-not-completed test failure",
                        provider_request_id=None,
                        returned_model=None,
                        model_metadata_json="{}",
                        usage=None,
                        timing=ProviderTiming(now, now, 0.0),
                        ambiguous=False,
                        known_not_completed=True,
                    )
                return self.success.complete(request)

        with tempfile.TemporaryDirectory() as temporary:
            store = self._new_store(Path(temporary) / "run")
            provider = TwoFailuresThenSuccess()
            observation = live._execute_logical(
                store, live._ledger(store), provider, self.plan["requests"][0],
                self._build_calibration, lambda _seconds: None,
            )
            self.assertEqual(provider.calls, 3)
            self.assertEqual(observation["status"], "ok")
            self.assertEqual(
                len([row for row in store.events if row.get("kind") == "result"]), 3
            )

    def test_inflight_reservation_resume_is_charged_and_never_redispatched(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run"
            store = self._new_store(path)
            spec = self.plan["requests"][0]
            request, rendered = live._provider_request(
                spec, self._build_calibration(False), 0
            )
            wire = OpenAIResponsesProvider.build_payload(request)
            request_hash, wire_hash = store.save_request(request, rendered, wire)
            ledger = live._ledger(store)
            reservation = ledger.reserve(
                spec["logical_request_id"], 0,
                input_tokens=request.conservative_input_token_bound,
                output_tokens=request.max_output_tokens,
            )
            store.append(
                "reservation", identity=spec["logical_request_id"],
                ordinal=spec["ordinal"], attempt=0, content_revision=0,
                action=spec["action"], response_mode=request.response_mode.value,
                request_hash=request_hash, prompt_hash=rendered.prompt_hash,
                wire_payload_hash=wire_hash, reservation=reservation.to_dict(),
            )
            resumed = live._RunStore(
                path, self.proposal, self.projection, self.plan, None,
                execution_mode="offline_fake", create=False,
            )
            resumed_ledger = live._ledger(resumed)
            with self.assertRaises(live._RunStop) as stopped:
                live._recover_unmatched_reservation(resumed, resumed_ledger)
            self.assertEqual(stopped.exception.status, "stopped_recovered_ambiguous_dispatch")
            self.assertEqual(
                len([row for row in resumed.events if row.get("kind") == "reservation"]), 1
            )
            self.assertEqual(
                len([row for row in resumed.events if row.get("kind") == "result"]), 1
            )
            resumed.terminal(
                stopped.exception.status, resumed_ledger,
                stop_reason=stopped.exception.reason,
                completed_logical_requests=1,
                hard_stop_before_additional_study=True,
            )
            verification = live.verify_control_run(
                path, require_complete=False
            )
            self.assertEqual(verification["dispatch_attempts"], 1)
            self.assertFalse(verification["complete"])

    def test_pristine_mode_binding_crash_repairs_transport_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run"
            store = self._new_store(path)
            first = store.events[0]
            (path / "journal.jsonl").write_text(
                canonical(first) + "\n", encoding="utf-8"
            )
            manifest = live._read_json(path / "manifest.json", "manifest")
            manifest.update(
                event_count=1, journal_tail=first["event_hash"],
                updated_at=first["recorded_at"],
            )
            live._atomic_json(path / "manifest.json", manifest)
            resumed = live._RunStore(
                path, self.proposal, self.projection, self.plan, None,
                execution_mode="offline_fake", create=False,
            )
            self.assertEqual(
                [row["kind"] for row in resumed.events],
                ["mode_binding", "transport_binding"],
            )
            verification = live.verify_control_run(
                path, require_complete=False, allow_nonterminal=True
            )
            self.assertEqual(verification["dispatch_attempts"], 0)

    def test_calibration_retry_is_counted_by_identity_at_gate(self):
        class RetryThenInvalidThenSuccess:
            def __init__(self):
                self.calls = 0
                self.success = OpenAIResponsesProvider(live._OfflineResponsesTransport())

            def complete(self, request):
                self.calls += 1
                now = utc_now()
                if self.calls == 1:
                    return ProviderResult(
                        status=ProviderStatus.RETRYABLE_ERROR,
                        raw_response_json=None, output_text=None,
                        parsed_output_json=None, refusal=None,
                        error="offline known-not-completed calibration retry",
                        provider_request_id=None, returned_model=None,
                        model_metadata_json="{}", usage=None,
                        timing=ProviderTiming(now, now, 0.0),
                        ambiguous=False, known_not_completed=True,
                    )
                if self.calls == 2:
                    return ProviderResult(
                        status=ProviderStatus.INVALID_RESPONSE,
                        raw_response_json=canonical({"id": "offline-fake-invalid"}),
                        output_text="invalid calibration output",
                        parsed_output_json=None, refusal=None,
                        error="offline intentional invalid calibration output",
                        provider_request_id="offline-fake-invalid",
                        returned_model=live.MODEL,
                        model_metadata_json=canonical({
                            "model": live.MODEL,
                            "service_tier": "default",
                            "system_fingerprint": None,
                        }),
                        usage=None,
                        timing=ProviderTiming(now, now, 0.0),
                        ambiguous=False, known_not_completed=False,
                    )
                return self.success.complete(request)

        with tempfile.TemporaryDirectory() as temporary:
            store = self._new_store(Path(temporary) / "run")
            provider = RetryThenInvalidThenSuccess()
            with store.execution_lock():
                verification = live._run_controller(
                    store, provider, sleeper=lambda _seconds: None
                )
            self.assertEqual(provider.calls, 21)
            self.assertEqual(verification["dispatch_attempts"], 21)
            self.assertEqual(
                verification["status"], "stopped_capability_calibration_failed"
            )
            self.assertFalse(verification["complete"])


class OfflineEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temporary.name) / "run"
        def blocked(*_args, **_kwargs):
            raise AssertionError("offline fake attempted network access")
        with mock.patch.object(socket, "create_connection", blocked), mock.patch.object(
            socket.socket, "connect", blocked
        ):
            cls.verification = live.run_offline_fake_e2e(
                RUN_ID, NAMESPACE, cls.path
            )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_complete_560_request_gate_and_artifacts(self):
        self.assertTrue(self.verification["complete"])
        self.assertEqual(self.verification["logical_requests_with_results"], 560)
        self.assertEqual(self.verification["dispatch_attempts"], 560)
        self.assertEqual(self.verification["calibration_decisions"], 20)
        self.assertEqual(self.verification["scored_decisions"], 340)
        events = live._verify_journal(self.path / "journal.jsonl")
        gate = [row for row in events if row.get("kind") == "gate"]
        self.assertEqual(len(gate), 1)
        self.assertTrue(gate[0]["passed"])
        first_study = next(
            row for row in events
            if row.get("kind") == "reservation" and row.get("ordinal") == 20
        )
        self.assertLess(gate[0]["sequence"], first_study["sequence"])
        self.assertEqual(len(live._artifact(
            self.path / "reviews.json", "controlled_peer_recovery_reviews_v1"
        )["rows"]), 340)
        self.assertEqual(len(live._artifact(
            self.path / "checkpoints.json", "controlled_peer_recovery_checkpoints_v1"
        )["rows"]), 10)

    def test_verifier_rejects_calibration_artifact_tamper(self):
        artifact_path = self.path / "calibration.json"
        original = artifact_path.read_bytes()
        value = json.loads(original)
        value["rows"][0]["verdict"] = "REJECT"
        body = dict(value)
        body.pop("artifact_hash")
        value["artifact_hash"] = digest(body)
        live._atomic_json(artifact_path, value)
        try:
            with self.assertRaises(live.FormationPeerLiveError):
                live.verify_control_run(self.path)
        finally:
            artifact_path.write_bytes(original)

    def test_resume_repairs_manifest_lag_from_journal(self):
        proposal = live._read_json(self.path / "proposal.json", "proposal")
        projection = live._read_json(self.path / "projection.json", "projection")
        plan = live._read_json(self.path / "plan.json", "plan")
        manifest_path = self.path / "manifest.json"
        manifest = live._read_json(manifest_path, "manifest")
        manifest.update(event_count=0, journal_tail="initial", status="created")
        live._atomic_json(manifest_path, manifest)
        store = live._RunStore(
            self.path, proposal, projection, plan, None,
            execution_mode="offline_fake", create=False,
        )
        self.assertEqual(store.manifest["event_count"], len(store.events))
        self.assertEqual(store.manifest["journal_tail"], store.events[-1]["event_hash"])
        self.assertTrue(live.verify_control_run(self.path)["complete"])


if __name__ == "__main__":
    unittest.main()
