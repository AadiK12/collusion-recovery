"""Offline tests for the unified protocol-v1.2 release contract.

No test in this module may inspect a credential, construct an OpenAI client,
open a network socket, create an empirical run, or dispatch a request.
"""

from datetime import datetime, timedelta, timezone
import ast
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from collusion_recovery.common import canonical, digest
from collusion_recovery.v1_2_unified_contract import (
    DISPATCH_ATTEMPT_CEILINGS,
    LOGICAL_REQUEST_CEILINGS,
    RELEASE_ORDER,
    validate_unified_stage_caps,
)
import collusion_recovery.v1_2_master_release as master
from collusion_recovery.v1_2_master_release import (
    HUMAN_ACKNOWLEDGEMENT,
    SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
    SOURCE_SMOKE_AUDIT_CONTRACT_HASH,
    SOURCE_SMOKE_AUDIT_CONTRACT_VERSION,
    V12MasterReleaseError,
    absolute_theoretical_cost_envelope,
    authorization_schema,
    build_stage_binding,
    build_final_v1_2_master_artifacts,
    build_v1_2_master_proposal,
    claim_human_authorization_once,
    complete_human_authorization,
    complete_projection_audit_attestation,
    coarse_context_window_cost_envelope,
    current_stage_bindings,
    finalize_v1_2_master_release,
    load_human_authorization,
    validate_human_authorization,
    validate_projection_audit_attestation,
    validate_v1_2_master_proposal,
)
from collusion_recovery.v1_2_smoke_audit import (
    V1_2_SMOKE_AUDIT_CONTRACT_HASH,
    V1_2_SMOKE_AUDIT_CONTRACT_VERSION,
)


TEST_NOW = datetime.fromisoformat("2026-10-25T20:00:00-07:00")


@contextmanager
def _implemented_master():
    with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True), patch.object(
        master, "MASTER_CUMULATIVE_BUDGET_LEDGER_IMPLEMENTED", True
    ):
        yield


def _ready_bindings(*, cost_per_stage: str = "10.000000"):
    values = {}
    plan_hash = digest(["unified", "plan"])
    for index, stage in enumerate(RELEASE_ORDER):
        logical = LOGICAL_REQUEST_CEILINGS[stage]
        attempts = DISPATCH_ATTEMPT_CEILINGS[stage]
        caps = {
            "logical_requests": logical,
            "dispatch_attempts": attempts,
            "max_total_input_tokens": logical * 12_000,
            "max_total_output_tokens": logical * 4_096,
            "max_input_tokens_per_request": 12_000,
            "max_output_tokens_per_request": 4_096,
            "max_in_flight": 1,
            "max_cost_usd": cost_per_stage,
        }
        values[stage] = build_stage_binding(
            stage,
            status="ready",
            controller_id=master.MASTER_CONTROLLER_ID,
            plan_hash=plan_hash,
            inventory_hash=digest([stage, "inventory", index]),
            implementation_hash=master.current_implementation_hash(),
            exact_caps=caps,
            evidence={
                "offline_test_projection": True,
                "independent_audit_passed": True,
            },
        )
    return values


def _final_proposal(*, cost_per_stage: str = "10.000000"):
    with _implemented_master():
        proposal = build_v1_2_master_proposal(
            _ready_bindings(cost_per_stage=cost_per_stage)
        )
    return proposal


def _projection_for_release_test():
    bindings = _ready_bindings()
    stage_caps = {
        stage: bindings[stage]["exact_caps"] for stage in RELEASE_ORDER
    }
    aggregate = validate_unified_stage_caps(stage_caps)
    plan_hash = bindings[RELEASE_ORDER[0]]["plan_hash"]
    inventories = {
        stage: {
            "inventory_hash": bindings[stage]["inventory_hash"],
            "exact_caps": stage_caps[stage],
        }
        for stage in RELEASE_ORDER
    }
    return {
        "projection_hash": digest(["offline", "release", "projection"]),
        "projection_algorithm_version": "offline-release-test-v1",
        "implementation_hash": master.current_implementation_hash(),
        "plan_hash": plan_hash,
        "request_order_hash": master.V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
        "stage_partition_hash": master.V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        "stage_inventories": inventories,
        "stage_inventory_hashes": {
            stage: inventories[stage]["inventory_hash"]
            for stage in RELEASE_ORDER
        },
        "projected_stage_caps": aggregate,
        "projected_stage_caps_hash": digest(aggregate),
        "projected_strict_reservation_ceiling_usd": aggregate["max_cost_usd"],
    }


def _projection_audit_fields():
    return {
        "audit_result": "passed",
        "independent_audit_passed": True,
        "audited_by": "independent offline test auditor",
        "audited_at": TEST_NOW.isoformat(),
        "attested_checks": {
            "complete_1580_identity_inventory_reconstructed": True,
            "request_order_and_stage_partition_reconstructed": True,
            "request_and_retry_shapes_recomputed": True,
            "token_and_cost_sums_recomputed": True,
            "nonfungible_stage_caps_reconciled": True,
            "no_dispatch_no_credential_no_network_confirmed": True,
        },
    }


def _authorization(proposal, *, auth_uuid=None, confirmed_at=None):
    auth_uuid = auth_uuid or str(uuid.uuid4())
    fields = {
        "authorization_uuid": auth_uuid,
        "authorization_id": f"v1-2-unified-human-{auth_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "confirmed_by": "offline test human",
        "confirmed_at": (confirmed_at or TEST_NOW).isoformat(),
    }
    with _implemented_master():
        return complete_human_authorization(fields, proposal)


class V12MasterReleaseTests(unittest.TestCase):
    def test_current_draft_is_non_authorizable_and_supersedes_gate1(self):
        draft = build_v1_2_master_proposal()
        self.assertEqual(draft["status"], "offline_draft_not_authorizable")
        self.assertFalse(draft["execution_ready"])
        self.assertFalse(draft["authorization_issuance_enabled"])
        self.assertFalse(draft["dispatch_enabled"])
        self.assertEqual(draft["controller"]["implemented"], False)
        self.assertFalse(
            draft["controller"]["cumulative_budget_ledger_implemented"]
        )
        self.assertIn(
            "cumulative actual-spend plus next-reservation ledger is not implemented",
            draft["non_ready_reasons"],
        )
        self.assertEqual(
            draft["stage_bindings"]["gate1_calibration"]["status"],
            "superseded",
        )
        self.assertFalse(
            draft["stage_bindings"]["gate1_calibration"]["evidence"][
                "dispatch_permitted"
            ]
        )
        self.assertFalse(draft["supersedes"]["old_artifacts_dispatch_authority"])
        self.assertEqual(
            draft["run"]["request_order_hash"],
            master.V1_2_UNIFIED_PILOT_REQUEST_ORDER_HASH,
        )
        self.assertEqual(
            draft["run"]["stage_partition_hash"],
            master.V1_2_UNIFIED_PILOT_STAGE_PARTITION_HASH,
        )
        with self.assertRaises(V12MasterReleaseError):
            authorization_schema(draft)

    def test_operational_cap_is_source_bound_and_separate_from_envelopes(self):
        draft = build_v1_2_master_proposal()
        operational = draft["operational_cost_hard_cap"]
        coarse = draft["coarse_context_window_stress_envelope"]
        self.assertEqual(
            operational["max_cost_usd"],
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
        )
        self.assertFalse(operational["runtime_override_allowed"])
        self.assertFalse(operational["resets_at_stage_boundary"])
        self.assertFalse(operational["full_shape_sum_coverage_required_to_start"])
        self.assertFalse(
            operational["budget_limited_terminal_is_completed_pilot"]
        )
        self.assertIsNone(draft["exact_shape_sum_reservation_envelope"])
        self.assertFalse(coarse["authorizes_spend"])
        self.assertEqual(coarse["maximum_dispatch_attempts"], 5517)
        self.assertEqual(coarse["per_attempt_cost_usd"], "5.290960")
        self.assertEqual(coarse["maximum_cost_usd"], "29190.226320")
        self.assertGreater(
            float(coarse["maximum_cost_usd"]),
            float(operational["max_cost_usd"]),
        )
        self.assertEqual(coarse, coarse_context_window_cost_envelope())
        self.assertEqual(coarse, absolute_theoretical_cost_envelope())
        signature = inspect.signature(build_v1_2_master_proposal)
        self.assertNotIn("operational_cost_cap_usd", signature.parameters)

    def test_master_binds_settled_automatic_smoke_audit_contract(self):
        draft = build_v1_2_master_proposal()
        self.assertEqual(
            SOURCE_SMOKE_AUDIT_CONTRACT_VERSION,
            V1_2_SMOKE_AUDIT_CONTRACT_VERSION,
        )
        self.assertEqual(
            SOURCE_SMOKE_AUDIT_CONTRACT_HASH,
            V1_2_SMOKE_AUDIT_CONTRACT_HASH,
        )
        gate = draft["smoke_integrity_gate"]
        self.assertEqual(gate["contract_hash"], V1_2_SMOKE_AUDIT_CONTRACT_HASH)
        self.assertEqual(
            gate["required_api"], "require_v1_2_smoke_integrity"
        )
        self.assertEqual(
            gate["automatic_continuation_requires"],
            {
                "automatic_checks_passed": True,
                "later_stage_release_permitted": True,
            },
        )
        self.assertFalse(gate["mid_run_human_approval_required"])
        self.assertTrue(
            gate["post_run_human_transcript_and_claim_audit_required"]
        )

    def test_ready_subplans_fit_under_one_cumulative_cap(self):
        with _implemented_master():
            proposal = build_v1_2_master_proposal(_ready_bindings())
            self.assertTrue(proposal["execution_ready"])
            self.assertTrue(proposal["authorization_issuance_enabled"])
            self.assertEqual(
                proposal["projected_stage_caps"]["logical_requests"], 1580
            )
            self.assertEqual(
                proposal["projected_stage_caps"]["dispatch_attempts"], 5517
            )
            self.assertEqual(
                proposal["projected_stage_caps"]["max_cost_usd"], "40.000000"
            )
            exact = proposal["exact_shape_sum_reservation_envelope"]
            self.assertEqual(exact["maximum_cost_usd"], "40.000000")
            self.assertTrue(exact["independent_audit_passed"])
            self.assertTrue(exact["operational_cap_covers_envelope"])
            self.assertFalse(exact["authorizes_spend"])
            self.assertEqual(
                validate_v1_2_master_proposal(proposal), proposal
            )

    def test_envelope_above_cap_uses_graceful_cumulative_budget_stop(self):
        with _implemented_master():
            proposal = build_v1_2_master_proposal(
                _ready_bindings(cost_per_stage="70.000000")
            )
            self.assertTrue(proposal["execution_ready"])
            self.assertTrue(proposal["authorization_issuance_enabled"])
            self.assertEqual(
                proposal["exact_shape_sum_reservation_envelope"][
                    "maximum_cost_usd"
                ],
                "280.000000",
            )
            self.assertFalse(
                proposal["exact_shape_sum_reservation_envelope"][
                    "operational_cap_covers_envelope"
                ]
            )
            self.assertEqual(proposal["non_ready_reasons"], [])
            self.assertIn(
                "actual spend plus the next request's strict reservation",
                proposal["operational_cost_hard_cap"]["reservation_decision"],
            )
            self.assertIn(
                "budget_limited",
                proposal["operational_cost_hard_cap"]["exhaustion_behavior"],
            )
            self.assertEqual(
                authorization_schema(proposal)["properties"][
                    "approved_operational_cost_hard_cap_usd"
                ]["const"],
                "250.000000",
            )

    def test_one_authorization_binds_all_stages_caps_and_stop_rules(self):
        proposal = _final_proposal()
        with _implemented_master():
            schema = authorization_schema(proposal)
            self.assertEqual(
                schema["properties"]["approved_scope"]["const"][
                    "release_order"
                ],
                list(RELEASE_ORDER),
            )
            self.assertEqual(
                schema["properties"][
                    "approved_operational_cost_hard_cap_usd"
                ]["const"],
                "250.000000",
            )
            self.assertEqual(
                schema["properties"]["human_acknowledgement"]["const"],
                HUMAN_ACKNOWLEDGEMENT,
            )
            authorization = _authorization(proposal)
            self.assertEqual(
                validate_human_authorization(authorization, proposal),
                authorization,
            )
            self.assertTrue(authorization["approved_scope"][
                "failed_gate_hard_stops_remaining_dispatch"
            ])
            self.assertTrue(authorization["approved_scope"][
                "smoke_automatic_continuation_requires_exhaustive_outcome_blind_"
                "mechanical_integrity_pass"
            ])
            self.assertFalse(authorization["approved_scope"][
                "mid_run_human_smoke_approval_required"
            ])
            self.assertEqual(
                authorization["approved_scope"][
                    "human_transcript_and_claim_audit_timing"
                ],
                "post_run_pre_publication",
            )
            self.assertFalse(authorization["approved_scope"][
                "confirmation_authorized"
            ])
            self.assertFalse(authorization["approved_scope"][
                "completion_guaranteed_under_operational_cap"
            ])
            self.assertIn("budget-limited run", HUMAN_ACKNOWLEDGEMENT)

    def test_authorization_tampering_fails_even_with_rehashed_outer_object(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        authorization["approved_operational_cost_hard_cap_usd"] = "9999"
        body = dict(authorization)
        body.pop("authorization_hash")
        authorization["authorization_hash"] = digest(body)
        with _implemented_master():
            with self.assertRaises(V12MasterReleaseError):
                validate_human_authorization(authorization, proposal)

    def test_external_authorization_must_be_owner_only_and_outside_repo(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            path = Path(directory) / "authorization.json"
            path.write_text(canonical(authorization) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with _implemented_master():
                self.assertEqual(
                    load_human_authorization(path, proposal), authorization
                )
                path.chmod(0o644)
                with self.assertRaises(V12MasterReleaseError):
                    load_human_authorization(path, proposal)

    def test_proposal_tampering_fails_even_with_rehashed_outer_object(self):
        proposal = _final_proposal()
        proposal["operational_cost_hard_cap"]["max_cost_usd"] = "9999"
        body = dict(proposal)
        body.pop("proposal_hash")
        proposal["proposal_hash"] = digest(body)
        with _implemented_master():
            with self.assertRaises(V12MasterReleaseError):
                validate_v1_2_master_proposal(proposal)

    def test_receipt_is_single_use_and_idempotent_for_same_authorization(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            trusted_home = Path(directory) / "home"
            trusted_home.mkdir(mode=0o700)
            receipt_dir = trusted_home / ".collusion-recovery-authorization-receipts" / "v1.2-unified"
            with _implemented_master(), patch.object(
                master, "AUTHORIZATION_RECEIPT_DIRECTORY", receipt_dir
            ), patch.object(
                master,
                "_utc_now",
                return_value=TEST_NOW,
            ):
                first = claim_human_authorization_once(authorization, proposal)
                second = claim_human_authorization_once(
                    authorization,
                    proposal,
                )
                self.assertEqual(first, second)
                self.assertTrue(first["consumes_entire_master_authorization"])
                self.assertTrue(first["credential_access_permitted_only_after_receipt"])
                path = receipt_dir / (
                    f"{master.MASTER_CONTROLLER_ID}--{master.MASTER_RUN_ID}.json"
                )
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

                other = _authorization(proposal, auth_uuid=str(uuid.uuid4()))
                with self.assertRaises(V12MasterReleaseError):
                    claim_human_authorization_once(other, proposal)

    def test_unclaimed_authorization_expiry_fails_before_receipt(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            trusted_home = Path(directory) / "home"
            trusted_home.mkdir(mode=0o700)
            receipt_dir = trusted_home / ".collusion-recovery-authorization-receipts" / "v1.2-unified"
            with _implemented_master(), patch.object(
                master, "AUTHORIZATION_RECEIPT_DIRECTORY", receipt_dir
            ), patch.object(
                master, "_utc_now", return_value=(
                    TEST_NOW + timedelta(minutes=16)
                )
            ):
                with self.assertRaises(V12MasterReleaseError):
                    claim_human_authorization_once(authorization, proposal)
                self.assertFalse(receipt_dir.exists())

    def test_offline_module_has_no_provider_credential_or_dispatch_surface(self):
        source = inspect.getsource(master)
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertFalse(any("provider" in name for name in imported))
        self.assertFalse(any("stage2" in name for name in imported))
        self.assertNotIn("_load_local_openai_key", source)
        self.assertNotIn("OPENAI_KEY", source)
        self.assertNotIn("OPENAI_API_KEY", source)
        self.assertFalse(hasattr(master, "run_v1_2_master"))
        self.assertFalse(hasattr(master, "dispatch"))

    def test_stage_binding_rejects_count_or_hash_drift(self):
        binding = current_stage_bindings()[RELEASE_ORDER[0]]
        binding["logical_requests"] += 1
        body = dict(binding)
        body.pop("binding_hash")
        binding["binding_hash"] = digest(body)
        with self.assertRaises(V12MasterReleaseError):
            master.validate_stage_binding(binding, RELEASE_ORDER[0])

    def test_final_artifacts_require_explicit_current_projection_audit(self):
        projection = _projection_for_release_test()
        with patch.object(
            master, "_current_unified_projection", return_value=projection
        ):
            attestation = complete_projection_audit_attestation(
                _projection_audit_fields()
            )
            self.assertEqual(
                validate_projection_audit_attestation(attestation),
                attestation,
            )
            with self.assertRaisesRegex(
                V12MasterReleaseError, "controller and cumulative ledger"
            ):
                build_final_v1_2_master_artifacts(attestation)

            with _implemented_master():
                artifacts = build_final_v1_2_master_artifacts(attestation)
        proposal = artifacts["proposal"]
        self.assertTrue(proposal["execution_ready"])
        self.assertTrue(all(
            proposal["stage_bindings"][stage]["status"] == "ready"
            for stage in RELEASE_ORDER
        ))
        self.assertTrue(all(
            proposal["stage_bindings"][stage]["evidence"][
                "independent_audit_passed"
            ]
            for stage in RELEASE_ORDER
        ))
        self.assertEqual(
            artifacts["authorization_schema"]["properties"][
                "proposal_hash"
            ]["const"],
            proposal["proposal_hash"],
        )

        tampered = dict(attestation)
        tampered["projected_strict_reservation_ceiling_usd"] = "0.000001"
        body = dict(tampered)
        body.pop("attestation_hash")
        tampered["attestation_hash"] = digest(body)
        with patch.object(
            master, "_current_unified_projection", return_value=projection
        ), self.assertRaisesRegex(
            V12MasterReleaseError, "differs from the current projection"
        ):
            validate_projection_audit_attestation(tampered)

    def test_finalizer_atomically_renders_keyless_artifacts_only(self):
        projection = _projection_for_release_test()
        with tempfile.TemporaryDirectory(dir=master.ROOT) as directory:
            destination = Path(directory)
            proposal_path = destination / "proposal.json"
            schema_path = destination / "schema.json"
            audit_path = destination / "audit.json"
            with patch.object(
                master, "_current_unified_projection", return_value=projection
            ), _implemented_master(), patch.object(
                master, "MASTER_PROPOSAL_PATH", proposal_path
            ), patch.object(
                master, "MASTER_AUTHORIZATION_SCHEMA_PATH", schema_path
            ), patch.object(
                master,
                "MASTER_PROJECTION_AUDIT_ATTESTATION_PATH",
                audit_path,
            ):
                attestation = complete_projection_audit_attestation(
                    _projection_audit_fields()
                )
                result = finalize_v1_2_master_release(attestation)
                second = finalize_v1_2_master_release(attestation)
            self.assertEqual(result, second)
            self.assertTrue(proposal_path.is_file())
            self.assertTrue(schema_path.is_file())
            self.assertTrue(audit_path.is_file())
            self.assertFalse(result["authorization_created"])
            self.assertFalse(result["credential_accessed"])
            self.assertFalse(result["dispatch_performed"])
            self.assertEqual(result["network_calls"], 0)
            self.assertEqual(result["paid_calls"], 0)
            self.assertEqual(
                proposal_path.read_bytes()[-1:], b"\n"
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
