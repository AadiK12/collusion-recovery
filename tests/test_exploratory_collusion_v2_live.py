"""Offline launch-boundary tests. These fixtures are not empirical results."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from collusion_recovery.common import canonical, digest, utc_now
from collusion_recovery.fixtures import packet_from_public
from collusion_recovery.providers.base import TransportResponse
from collusion_recovery.providers.openai import OpenAIResponsesProvider
from experiments import exploratory_collusion_v2_live as live


RUN_ID = "22222222-2222-4222-8222-222222222222"


class FakeTransport:
    def __init__(self, *, mutate=None, fail_at=None, policy_length=350):
        self.payloads = []
        self.mutate = mutate
        self.fail_at = fail_at
        self.policy_length = policy_length

    def send(self, payload):
        index = len(self.payloads)
        self.payloads.append(payload)
        if self.fail_at == index:
            raise RuntimeError("offline injected transport error")
        content = json.loads(payload["input"][-1]["content"])
        if payload["text"]["format"]["type"] == "json_schema":
            peer = content["current"]["peer_packet"]
            verdict = "ACCEPT" if "records" in peer and packet_from_public(peer).is_correct else "REJECT"
            text = canonical({"verdict": verdict, "evidence_record_ids": [], "brief_reason": "Observable evidence rule."})
        elif "private next-episode policy note, at most 400" in payload["input"][0]["content"]:
            text = "P" * self.policy_length
        else:
            text = "Use the stated verification rule."
        body = {"id": f"fake-response-{index}", "status": "completed", "model": live.study.MODEL,
            "service_tier": "default", "output": [{"type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0}}}
        if self.mutate:
            self.mutate(body, index, payload)
        return TransportResponse(canonical(body), request_id=f"fake-request-{index}")


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prior = live.shared.complete_budget_reconciliation(verified_at=utc_now())
        cls.release = live.build_release(RUN_ID, cls.prior, utc_now())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "run"

    def execute(self, transport=None):
        transport = transport or FakeTransport()
        output = live.run_offline_fake(self.release, self.path, OpenAIResponsesProvider(transport))
        return output, transport

    def test_exact_scope_projection_and_no_new_budget(self):
        self.assertEqual(self.release["scope"], {"capability_requests": 24, "development_requests": 168,
            "maximum_attempts": 192, "retries": 0, "validation_requests": 0, "repair_requests": 0})
        self.assertEqual(len(self.release["projection"]["shapes"]), 192)
        self.assertEqual(self.release["max_cost_usd"], self.prior["verified_program_remainder_usd"])
        self.assertEqual(Decimal(self.release["max_cost_usd"]) + Decimal(self.prior["historical_program_spend_usd"]), Decimal(250))
        self.assertLess(self.release["projection"]["limits"]["max_input_tokens_per_request"] + 4096, 1_050_000)

    def test_complete_192_and_replay_without_duplicate_calls(self):
        result, transport = self.execute()
        self.assertEqual(result["status"], live.TERMINAL)
        self.assertEqual(len(transport.payloads), 192)
        audited = live.audit_run(self.release, self.path)
        self.assertTrue(audited["integrity_verified"])
        self.assertEqual(audited["logical_requests_completed"], 192)
        self.assertEqual(audited["open_reservations"], 0)
        self.assertFalse(audited["induction_established"])
        self.assertEqual(len(audited["trace"]["dyads"]), 2)
        result2, _ = self.execute(transport)
        self.assertEqual(result2["status"], live.TERMINAL)
        self.assertEqual(len(transport.payloads), 192)
        manifest = json.loads((self.path / "manifest.json").read_text())
        self.assertFalse(manifest["empirical"])
        self.assertEqual(manifest["paid_calls"], 0)

    def test_capability_failure_hard_stops_before_formation(self):
        def always_reject(body, index, payload):
            value = json.loads(body["output"][0]["content"][0]["text"])
            value["verdict"] = "REJECT"
            body["output"][0]["content"][0]["text"] = canonical(value)
        result, transport = self.execute(FakeTransport(mutate=always_reject))
        self.assertEqual(result["status"], "stopped_capability_gate")
        self.assertEqual(len(transport.payloads), 24)
        self.assertFalse((self.path / "normalized_trace.unannotated.json").exists())
        self.assertEqual(live.audit_run(self.release, self.path)["status"], "stopped_capability_gate")

    def test_policy_notes_350_are_delivered_and_private(self):
        _, transport = self.execute()
        # capability24 + episode14: policy slots36/37, next episode first round38/39
        alice = json.loads(transport.payloads[38]["input"][-1]["content"])
        self.assertEqual(alice["history"][0]["private_policy_note"]["text"], "P" * 350)
        self.assertEqual(alice["history"][0]["private_policy_note"]["sender"], "alice")
        self.assertNotIn("policy_note", alice["history"][0]["verdicts"])

    def test_round_and_verdict_frontiers_are_synchronous(self):
        _, transport = self.execute()
        for a, b in ((24, 25), (26, 27), (34, 35)):
            first = json.loads(transport.payloads[a]["input"][-1]["content"])["current"]
            second = json.loads(transport.payloads[b]["input"][-1]["content"])["current"]
            self.assertEqual(len(first["message_rounds"]), len(second["message_rounds"]))
            self.assertNotIn("completed_verdicts", first)
            self.assertNotIn("completed_verdicts", second)
        for payload in transport.payloads:
            serialized = canonical(payload["input"])
            for forbidden in ("seed_namespace", "expected_verdict", "peer_packet_correct", "oracle_answer"):
                self.assertNotIn(forbidden, serialized)

    def test_overlength_messages_and_notes_are_nondelivery_not_retried(self):
        def long_first(body, index, payload):
            if index == 24:
                body["output"][0]["content"][0]["text"] = "x" * 201
        result, transport = self.execute(FakeTransport(mutate=long_first, policy_length=401))
        self.assertEqual(result["status"], live.TERMINAL)
        self.assertEqual(len(transport.payloads), 192)
        trace = live.audit_run(self.release, self.path)["trace"]
        episode = trace["dyads"][0]["episodes"][0]
        self.assertEqual(episode["messages"][0]["observation"]["status"], "overlength")
        self.assertEqual(episode["messages"][0]["observation"]["raw_response"], "x" * 201)
        self.assertEqual(episode["policy_notes"]["alice"]["status"], "overlength")
        review = json.loads((self.path / "annotation_messages_only.json").read_text())
        observed = review["dyads"][0]["episodes"][0]["messages"][0]["observation"]
        self.assertEqual(observed, {"status": "overlength", "raw_response": None})
        for forbidden in ("verdicts", "policy_notes", "runner_feedback", "peer_packet_correct", "shared_score"):
            self.assertNotIn(forbidden, canonical(review))
        subsequent = json.loads(transport.payloads[27]["input"][-1]["content"])
        self.assertFalse(subsequent["current"]["message_rounds"][0]["peer_message"]["delivered"])

    def test_wrong_model_or_tier_stops_after_one_and_cannot_resume(self):
        for name, value in (("model", "wrong-model"), ("service_tier", "priority")):
            with self.subTest(name=name):
                self.path = Path(self.temporary.name) / name
                def change(body, index, payload):
                    body[name] = value
                result, transport = self.execute(FakeTransport(mutate=change))
                self.assertEqual(result["status"], "stopped_provider_result")
                self.assertEqual(len(transport.payloads), 1)
                self.execute(transport)
                self.assertEqual(len(transport.payloads), 1)
                self.assertEqual(live.audit_run(self.release, self.path)["status"], "stopped_provider_result")

    def test_refusal_invalid_and_transport_failure_never_retry(self):
        def refusal(body, index, payload):
            body["output"][0]["content"] = [{"type": "refusal", "refusal": "Cannot do this."}]
        def invalid(body, index, payload):
            body["output"][0]["content"][0]["text"] = "not json"
        for label, transport in (("refusal", FakeTransport(mutate=refusal)),
                                 ("invalid", FakeTransport(mutate=invalid)),
                                 ("transport", FakeTransport(fail_at=0))):
            with self.subTest(label=label):
                self.path = Path(self.temporary.name) / label
                result, transport = self.execute(transport)
                self.assertEqual(result["status"], "stopped_provider_result")
                self.assertEqual(len(transport.payloads), 1)

    def test_valid_json_with_large_whitespace_stops_representation_guard(self):
        def padded(body, index, payload):
            body["output"][0]["content"][0]["text"] += " " * 10000
        result, transport = self.execute(FakeTransport(mutate=padded))
        self.assertEqual(result["status"], "stopped_provider_result")
        self.assertEqual(len(transport.payloads), 1)

    def test_open_reservation_blocks_resume_without_provider_construction(self):
        class ExplodingProvider:
            def complete(self, request):
                raise RuntimeError("offline abrupt process interruption")
        with self.assertRaises(RuntimeError):
            live.run_offline_fake(self.release, self.path, ExplodingProvider())
        provider = mock.Mock()
        resumed = live.run_offline_fake(self.release, self.path, provider)
        self.assertEqual(resumed["status"], "blocked_ambiguous_open_reservation")
        provider.complete.assert_not_called()
        audit = live.audit_run(self.release, self.path)
        self.assertEqual(audit["open_reservations"], 1)
        self.assertGreater(Decimal(audit["ledger"]["reserved"]["cost_usd"]), 0)

    def test_completed_results_resume_without_replay_paid_calls(self):
        original = live._Store.append
        def interrupt(store, kind, **fields):
            result = original(store, kind, **fields)
            if kind == "result" and sum(e["kind"] == "result" for e in store.events) == 30:
                raise RuntimeError("offline interruption after durable result")
            return result
        transport = FakeTransport()
        with mock.patch.object(live._Store, "append", interrupt), self.assertRaises(RuntimeError):
            self.execute(transport)
        self.assertEqual(len(transport.payloads), 30)
        result, _ = self.execute(transport)
        self.assertEqual(result["status"], live.TERMINAL)
        self.assertEqual(len(transport.payloads), 192)
        self.assertTrue(live.audit_run(self.release, self.path)["integrity_verified"])

    def test_rehashed_scope_expansion_rejected(self):
        changed = json.loads(canonical(self.release))
        changed["scope"]["validation_requests"] = 672
        changed["release_hash"] = digest({k: v for k, v in changed.items() if k != "release_hash"})
        with self.assertRaises(live.LivePilotError):
            live.validate_release(changed)

    def test_offline_cannot_write_empirical_tree(self):
        with self.assertRaises(live.LivePilotError):
            live.run_offline_fake(self.release, live.shared.RUNS_ROOT / "forbidden-test", mock.Mock())

    def test_artifact_tampering_is_rejected(self):
        self.execute()
        artifact = self.path / "annotation_messages_only.json"
        value = json.loads(artifact.read_text())
        value["phase"] = "validation"
        live.shared._atomic_json(artifact, value)
        with self.assertRaises(live.LivePilotError):
            live.audit_run(self.release, self.path)

    def test_manifest_terminal_tampering_is_rejected(self):
        self.execute()
        path = self.path / "manifest.json"
        value = json.loads(path.read_text())
        value["status"] = "completed_validation"
        live.shared._atomic_json(path, value)
        with self.assertRaises(live.LivePilotError):
            live.audit_run(self.release, self.path)

    def stopped_events(self):
        def wrong_model(body, index, payload):
            body["model"] = "wrong-model"
        self.execute(FakeTransport(mutate=wrong_model))
        return live.shared._verify_journal(self.path / "journal.jsonl")

    def test_failed_result_cannot_have_forged_completed_terminal(self):
        events = self.stopped_events()
        events[-1]["status"] = live.TERMINAL
        with self.assertRaisesRegex(live.LivePilotError, "failed provider result"):
            live._replay(self.release, events, "offline_fake")

    def test_failed_result_cannot_hide_trailing_scientific_events(self):
        events = self.stopped_events()
        events[-1] = {"kind": "checkpoint", "checkpoint": {}}
        events.append({"kind": "terminal", "status": "stopped_provider_result"})
        with self.assertRaisesRegex(live.LivePilotError, "continue after failed"):
            live._replay(self.release, events, "offline_fake")

    def test_first_binding_is_checked_by_replay(self):
        original = self.stopped_events()
        for key, value in (("kind", "wrong"), ("release_hash", "wrong"),
                           ("transport_contract", {}), ("mode", "empirical_live")):
            with self.subTest(key=key):
                events = json.loads(canonical(original))
                events[0][key] = value
                with self.assertRaisesRegex(live.LivePilotError, "binding"):
                    live._replay(self.release, events, "offline_fake")

    def test_reservation_identity_and_amount_checked_against_request(self):
        original = self.stopped_events()
        # Keep budget replay fixed to isolate the independent request binding check.
        ledger = live._ledger(self.release, original)
        for key, value in (("identity", "different"), ("input_tokens", 1), ("output_tokens", 16), ("attempt", 1)):
            with self.subTest(key=key):
                events = json.loads(canonical(original))
                events[1]["reservation"][key] = value
                with mock.patch.object(live, "_ledger", return_value=ledger), self.assertRaisesRegex(live.LivePilotError, "reservation amount/identity"):
                    live._replay(self.release, events, "offline_fake")

    def test_terminal_artifact_hashes_checked(self):
        self.execute()
        original = live.shared._verify_journal(self.path / "journal.jsonl")
        for key in ("trace_hash", "capability_assessment_hash", "checkpoints_hash"):
            with self.subTest(key=key):
                events = json.loads(canonical(original))
                events[-1][key] = "wrong"
                with self.assertRaisesRegex(live.LivePilotError, "terminal artifact hashes"):
                    live._replay(self.release, events, "offline_fake")

    def test_failed_result_prefix_finalizes_on_resume_without_calls(self):
        original = live._Store.append
        def interrupt(store, kind, **fields):
            result = original(store, kind, **fields)
            if kind == "result":
                raise RuntimeError("offline crash after persisted failed result")
            return result
        def wrong_model(body, index, payload):
            body["model"] = "wrong-model"
        transport = FakeTransport(mutate=wrong_model)
        with mock.patch.object(live._Store, "append", interrupt), self.assertRaises(RuntimeError):
            self.execute(transport)
        result, _ = self.execute(transport)
        self.assertEqual(result["status"], "stopped_provider_result")
        self.assertEqual(len(transport.payloads), 1)
        self.assertEqual(live.shared._verify_journal(self.path / "journal.jsonl")[-1]["kind"], "terminal")
        audited = live.audit_run(self.release, self.path)
        self.assertEqual(audited["accounted_attempts"], 1)
        self.assertEqual(audited["logical_requests_completed"], 0)

    def test_archived_source_corruption_blocks_resume(self):
        self.stopped_events()
        name = next(iter(self.release["source_inventory"]))
        archived = self.path / "source_snapshot" / name
        archived.write_text("offline tamper fixture")
        with self.assertRaisesRegex(live.LivePilotError, "snapshot differs"):
            self.execute(FakeTransport())


if __name__ == "__main__":
    unittest.main()
