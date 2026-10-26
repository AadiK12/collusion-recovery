"""Production-shaped, no-network end-to-end test for the v1.2 master.

This test uses the real immutable 1,580-request plan, DurableRun journal,
V12MasterExecutor, OpenAI response adapter, gates, ledgers, checkpoints,
scientific artifacts, crash/reopen path, and terminal integrity replay.  Its
transport is the sealed finite data-only fake and its terminal artifact remains
conspicuously non-empirical.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import socket
import tempfile
import unittest
import uuid
from unittest.mock import patch

from collusion_recovery.analysis import load_durable_and_rescore
from collusion_recovery.common import canonical, digest
from collusion_recovery.orchestration import DurableRun, ResumeError
from collusion_recovery.providers.base import TransportResponse
from collusion_recovery.v1_2_master_controller import (
    FAKE_FINAL_STATUS,
    V12MasterControllerError,
    V12MasterExecutor,
    _verify_v1_2_master_offline_fake_integrity,
    build_fake_v1_2_master_executor_for_test,
    prepare_v1_2_master_run,
    verify_v1_2_master_empirical_integrity,
)
import collusion_recovery.v1_2_master_release as master_release
from collusion_recovery.v1_2_master_projection import build_unified_projection
from collusion_recovery.v1_2_master_release import (
    build_authorization_receipt,
    build_stage_binding,
    build_v1_2_master_proposal,
    complete_human_authorization,
)
from collusion_recovery.v1_2_unified_contract import RELEASE_ORDER


TEST_NOW = datetime.fromisoformat("2026-10-25T20:00:00-07:00")


def _refresh_indexes(run: DurableRun) -> None:
    cursor = getattr(run, "_master_e2e_index_cursor", 0)
    if cursor > len(run.events) or not hasattr(run, "_master_e2e_results"):
        cursor = 0
        run._master_e2e_results = []
        run._master_e2e_results_by_identity = {}
        run._master_e2e_checkpoints = {}
        run._master_e2e_gates = {}
    for event in run.events[cursor:]:
        if event.get("kind") == "result":
            run._master_e2e_results.append(event)
            run._master_e2e_results_by_identity.setdefault(
                event["identity"], []
            ).append(event)
        elif event.get("kind") == "checkpoint":
            prior = run._master_e2e_checkpoints.get(event["checkpoint_id"])
            if prior is not None and prior != event:
                raise ResumeError("checkpoint identity was duplicated")
            run._master_e2e_checkpoints[event["checkpoint_id"]] = event
        elif event.get("kind") == "gate":
            if event["gate"] in run._master_e2e_gates:
                raise ResumeError("gate identity was duplicated")
            run._master_e2e_gates[event["gate"]] = event
    run._master_e2e_index_cursor = len(run.events)


def _indexed_result_events(run: DurableRun, identity=None):
    _refresh_indexes(run)
    if identity is None:
        return list(run._master_e2e_results)
    return list(run._master_e2e_results_by_identity.get(identity, ()))


def _indexed_checkpoint_events(run: DurableRun):
    _refresh_indexes(run)
    return dict(run._master_e2e_checkpoints)


def _indexed_gate_events(run: DurableRun):
    _refresh_indexes(run)
    return dict(run._master_e2e_gates)


def _strictly_offline_fast_durability():
    stack = ExitStack()
    for name in (
        "socket.socket",
        "socket.create_connection",
        "urllib.request.urlopen",
        "http.client.HTTPConnection.connect",
        "http.client.HTTPSConnection.connect",
        "collusion_recovery.providers.openai.OpenAISDKTransport.send",
        "collusion_recovery.v1_2_master_controller._load_local_openai_key",
    ):
        stack.enter_context(patch(
            name,
            side_effect=AssertionError(
                "network, SDK transport, and credentials are forbidden"
            ),
        ))
    stack.enter_context(patch.object(
        DurableRun, "result_events", _indexed_result_events
    ))
    stack.enter_context(patch.object(
        DurableRun, "checkpoint_events", _indexed_checkpoint_events
    ))
    stack.enter_context(patch.object(
        DurableRun, "gate_events", _indexed_gate_events
    ))
    stack.enter_context(patch("collusion_recovery.orchestration.os.fsync"))
    stack.enter_context(patch(
        "collusion_recovery.orchestration._fsync_directory"
    ))
    return stack


def _ready_bindings(projection):
    return {
        stage: build_stage_binding(
            stage,
            status="ready",
            controller_id=master_release.MASTER_CONTROLLER_ID,
            plan_hash=projection["plan_hash"],
            inventory_hash=projection["stage_inventories"][stage][
                "inventory_hash"
            ],
            implementation_hash=projection["implementation_hash"],
            exact_caps=projection["stage_inventories"][stage]["exact_caps"],
            evidence={
                "independent_audit_passed": True,
                "offline_e2e_test_only": True,
                "projection_hash": projection["projection_hash"],
            },
        )
        for stage in RELEASE_ORDER
    }


def _proposal_authorization_receipt(projection):
    proposal = build_v1_2_master_proposal(_ready_bindings(projection))
    authorization_uuid = str(uuid.uuid4())
    authorization = complete_human_authorization({
        "authorization_uuid": authorization_uuid,
        "authorization_id": f"v1-2-unified-human-{authorization_uuid}",
        "authorization_nonce": "0123456789abcdef" * 4,
        "confirmed_by": "offline production-shaped master E2E",
        "confirmed_at": TEST_NOW.isoformat(),
    }, proposal)
    receipt = build_authorization_receipt(
        authorization, proposal, claimed_at=TEST_NOW
    )
    return proposal, authorization, receipt


def _output_for_spec(spec) -> str:
    if spec.action == "message":
        return "Apply the assigned verification rule to the packet."
    if spec.phase == "calibration":
        verdict = "ACCEPT" if spec.metadata["fixture_index"] < 10 else "REJECT"
    elif (spec.phase == "formation"
          and spec.metadata.get("history") == "C"
          and spec.metadata.get("episode") in {7, 8, 9}):
        verdict = "ACCEPT"
    else:
        verdict = "REJECT"
    return canonical({
        "verdict": verdict,
        "evidence_record_ids": [],
        "brief_reason": "Deterministic offline master E2E; not empirical.",
    })


def _scripted_outcomes(plan, model: str):
    outcomes = []
    for index, spec in enumerate(plan.requests):
        output = _output_for_spec(spec)
        request_id = f"resp_offline_master_{index:04d}"
        raw = {
            "id": request_id,
            "object": "response",
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "model": model,
            "service_tier": "default",
            "created_at": 1792987200,
            "system_fingerprint": None,
            "output": [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": output}],
            }],
            "usage": {
                "input_tokens": 10,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                },
                "output_tokens": 2,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 12,
            },
        }
        outcomes.append(TransportResponse.from_mapping(raw, request_id))
    return outcomes


class _SyntheticCrash(RuntimeError):
    pass


class V12MasterProductionShapedOfflineE2E(unittest.TestCase):
    def test_real_master_crash_resume_completes_fake_and_verifies(self):
        with patch.object(
            master_release, "MASTER_CONTROLLER_IMPLEMENTED", True
        ), patch.object(
            master_release,
            "MASTER_CUMULATIVE_BUDGET_LEDGER_IMPLEMENTED",
            True,
        ), _strictly_offline_fast_durability(), tempfile.TemporaryDirectory() as directory:
            projection = build_unified_projection()
            proposal, authorization, receipt = (
                _proposal_authorization_receipt(projection)
            )
            workspace = Path(directory).resolve()
            run_path = workspace / authorization["master_run_directory"]
            run, config, plan, _dispatch, projection = prepare_v1_2_master_run(
                run_path,
                proposal,
                authorization,
                receipt,
                workspace_root=workspace,
                receipt_created=True,
                projection=projection,
            )
            outcomes = _scripted_outcomes(plan, config.model)
            self.assertEqual(len(outcomes), 1580)

            first = build_fake_v1_2_master_executor_for_test(
                run,
                outcomes,
                proposal,
                projection,
                authorization_receipt=receipt,
                now=lambda: TEST_NOW,
            )
            original_execute_stage = V12MasterExecutor._execute_stage
            crashed = False

            def crash_after_smoke(executor, stage, block):
                nonlocal crashed
                report = original_execute_stage(executor, stage, block)
                if stage == "smoke" and not crashed:
                    crashed = True
                    raise _SyntheticCrash("crash after durable smoke results")
                return report

            with patch.object(
                V12MasterExecutor, "_execute_stage", crash_after_smoke
            ), self.assertRaisesRegex(_SyntheticCrash, "durable smoke"):
                first.execute()
            self.assertTrue(crashed)
            self.assertEqual(len(run.result_events()), 212)
            self.assertNotIn("smoke_integrity", run.gate_events())

            reopened, _config, reopened_plan, _dispatch, projection = (
                prepare_v1_2_master_run(
                    run_path,
                    proposal,
                    authorization,
                    receipt,
                    workspace_root=workspace,
                    receipt_created=False,
                    projection=projection,
                )
            )
            remaining = build_fake_v1_2_master_executor_for_test(
                reopened,
                outcomes[212:],
                proposal,
                projection,
                authorization_receipt=receipt,
                now=lambda: TEST_NOW,
            )
            report = remaining.execute()
            self.assertEqual(report["status"], FAKE_FINAL_STATUS)
            self.assertEqual(report["completed_logical_requests"], 1580)
            self.assertEqual(report["dispatch_attempts"], 1580)
            self.assertEqual(report["execution_mode"], "fake_transport_test")
            self.assertFalse(report["empirical_evidence"])
            self.assertEqual(report["network_calls"], 0)
            self.assertEqual(report["paid_calls"], 0)
            self.assertFalse(report["completed_pilot"])
            self.assertFalse(report["gate4_authorized"])
            self.assertFalse(report["confirmation_authorized"])
            self.assertEqual(len(reopened.result_events()), 1580)
            self.assertEqual(len(reopened_plan.requests), 1580)
            fake_smoke = reopened.gate_events()["smoke_integrity"][
                "evidence"
            ]["audit_report"]
            self.assertTrue(fake_smoke["automatic_checks_passed"])
            self.assertTrue(fake_smoke["offline_test_only"])
            self.assertFalse(fake_smoke["empirical_evidence"])

            verified = _verify_v1_2_master_offline_fake_integrity(
                reopened, proposal, receipt, projection
            )
            self.assertEqual(len(verified["results"]), 1580)
            with self.assertRaisesRegex(
                V12MasterControllerError,
                "wrong terminal execution provenance|terminal empirical",
            ):
                verify_v1_2_master_empirical_integrity(
                    reopened, proposal, receipt, projection
                )
            with self.assertRaisesRegex(
                ValueError, "Offline v1.2 master artifacts cannot be analyzed"
            ):
                load_durable_and_rescore(run_path)

            terminal_reopen, *_ = prepare_v1_2_master_run(
                run_path,
                proposal,
                authorization,
                receipt,
                workspace_root=workspace,
                receipt_created=False,
                projection=projection,
            )
            terminal = build_fake_v1_2_master_executor_for_test(
                terminal_reopen,
                [],
                proposal,
                projection,
                authorization_receipt=receipt,
                now=lambda: TEST_NOW,
            )
            replayed = terminal.execute()
            self.assertEqual(replayed["status"], FAKE_FINAL_STATUS)
            self.assertEqual(replayed["report_hash"], report["report_hash"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
