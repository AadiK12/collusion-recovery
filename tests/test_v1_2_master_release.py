"""Offline tests for the unified protocol-v1.2 release contract.

No test in this module may inspect a credential, construct an OpenAI client,
open a network socket, create an empirical run, or dispatch a request.
"""

from datetime import datetime, timedelta, timezone
import ast
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
)
import collusion_recovery.v1_2_master_release as master
from collusion_recovery.v1_2_master_release import (
    HUMAN_ACKNOWLEDGEMENT,
    SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
    V12MasterReleaseError,
    absolute_theoretical_cost_envelope,
    authorization_schema,
    build_stage_binding,
    build_v1_2_master_proposal,
    claim_human_authorization_once,
    complete_human_authorization,
    current_stage_bindings,
    load_human_authorization,
    validate_human_authorization,
    validate_v1_2_master_proposal,
)


TEST_NOW = datetime.fromisoformat("2026-10-25T20:00:00-07:00")


def _ready_bindings(*, cost_per_stage: str = "10.000000"):
    values = {}
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
            controller_id=f"unified-subplan-{stage}",
            plan_hash=digest([stage, "plan", index]),
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
    patcher = patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True)
    patcher.start()
    try:
        proposal = build_v1_2_master_proposal(
            _ready_bindings(cost_per_stage=cost_per_stage)
        )
    finally:
        patcher.stop()
    return proposal


def _authorization(proposal, *, auth_uuid=None, confirmed_at=None):
    auth_uuid = auth_uuid or str(uuid.uuid4())
    fields = {
        "authorization_uuid": auth_uuid,
        "authorization_id": f"v1-2-unified-human-{auth_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "confirmed_by": "offline test human",
        "confirmed_at": (confirmed_at or TEST_NOW).isoformat(),
    }
    with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True):
        return complete_human_authorization(fields, proposal)


class V12MasterReleaseTests(unittest.TestCase):
    def test_current_draft_is_non_authorizable_and_supersedes_gate1(self):
        draft = build_v1_2_master_proposal()
        self.assertEqual(draft["status"], "offline_draft_not_authorizable")
        self.assertFalse(draft["execution_ready"])
        self.assertFalse(draft["authorization_issuance_enabled"])
        self.assertFalse(draft["dispatch_enabled"])
        self.assertEqual(draft["controller"]["implemented"], False)
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

    def test_operational_cap_is_source_bound_and_separate_from_theoretical(self):
        draft = build_v1_2_master_proposal()
        operational = draft["operational_cost_hard_cap"]
        theoretical = draft["absolute_theoretical_cost_envelope"]
        self.assertEqual(
            operational["max_cost_usd"],
            SOURCE_BOUND_OPERATIONAL_COST_HARD_CAP_USD,
        )
        self.assertFalse(operational["runtime_override_allowed"])
        self.assertFalse(operational["resets_at_stage_boundary"])
        self.assertFalse(theoretical["authorizes_spend"])
        self.assertEqual(theoretical["maximum_dispatch_attempts"], 5517)
        self.assertEqual(theoretical["per_attempt_cost_usd"], "5.290960")
        self.assertEqual(theoretical["maximum_cost_usd"], "29190.226320")
        self.assertGreater(
            float(theoretical["maximum_cost_usd"]),
            float(operational["max_cost_usd"]),
        )
        self.assertEqual(theoretical, absolute_theoretical_cost_envelope())
        signature = inspect.signature(build_v1_2_master_proposal)
        self.assertNotIn("operational_cost_cap_usd", signature.parameters)

    def test_ready_subplans_fit_under_one_cumulative_cap(self):
        with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True):
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
            self.assertEqual(
                validate_v1_2_master_proposal(proposal), proposal
            )

    def test_projected_reservations_above_operational_cap_are_not_issuable(self):
        with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True):
            proposal = build_v1_2_master_proposal(
                _ready_bindings(cost_per_stage="70.000000")
            )
            self.assertFalse(proposal["execution_ready"])
            self.assertFalse(proposal["authorization_issuance_enabled"])
            self.assertIn(
                "rendered stage reservation ceilings exceed the source-bound "
                "operational hard cap",
                proposal["non_ready_reasons"],
            )
            with self.assertRaises(V12MasterReleaseError):
                authorization_schema(proposal)

    def test_one_authorization_binds_all_stages_caps_and_stop_rules(self):
        proposal = _final_proposal()
        with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True):
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

    def test_authorization_tampering_fails_even_with_rehashed_outer_object(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        authorization["approved_operational_cost_hard_cap_usd"] = "9999"
        body = dict(authorization)
        body.pop("authorization_hash")
        authorization["authorization_hash"] = digest(body)
        with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True):
            with self.assertRaises(V12MasterReleaseError):
                validate_human_authorization(authorization, proposal)

    def test_external_authorization_must_be_owner_only_and_outside_repo(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            path = Path(directory) / "authorization.json"
            path.write_text(canonical(authorization) + "\n", encoding="utf-8")
            path.chmod(0o600)
            with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True):
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
        with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True):
            with self.assertRaises(V12MasterReleaseError):
                validate_v1_2_master_proposal(proposal)

    def test_receipt_is_single_use_and_idempotent_for_same_authorization(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            trusted_home = Path(directory) / "home"
            trusted_home.mkdir(mode=0o700)
            receipt_dir = trusted_home / ".collusion-recovery-authorization-receipts" / "v1.2-unified"
            with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True), patch.object(
                master, "AUTHORIZATION_RECEIPT_DIRECTORY", receipt_dir
            ):
                first = claim_human_authorization_once(
                    authorization, proposal, now=TEST_NOW
                )
                second = claim_human_authorization_once(
                    authorization,
                    proposal,
                    now=TEST_NOW + timedelta(seconds=10),
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
                    claim_human_authorization_once(other, proposal, now=TEST_NOW)

    def test_unclaimed_authorization_expiry_fails_before_receipt(self):
        proposal = _final_proposal()
        authorization = _authorization(proposal)
        with tempfile.TemporaryDirectory() as directory:
            trusted_home = Path(directory) / "home"
            trusted_home.mkdir(mode=0o700)
            receipt_dir = trusted_home / ".collusion-recovery-authorization-receipts" / "v1.2-unified"
            with patch.object(master, "MASTER_CONTROLLER_IMPLEMENTED", True), patch.object(
                master, "AUTHORIZATION_RECEIPT_DIRECTORY", receipt_dir
            ):
                with self.assertRaises(V12MasterReleaseError):
                    claim_human_authorization_once(
                        authorization,
                        proposal,
                        now=TEST_NOW + timedelta(minutes=16),
                    )
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
