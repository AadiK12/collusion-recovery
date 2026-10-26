"""Independent runner boundary tests; temporary ledgers and mock HTTP only."""
import threading
import time

import httpx
import pytest

from experiments.induction_v3 import pacing, transport
from experiments.induction_v3.common import atomic_json, digest
from experiments.recovery_v3 import runner
from tests.recovery_v3 import test_runner_integration as integration


@pytest.fixture
def harness():
    value = integration.RunnerAccountingIntegrationTests(methodName="runTest")
    value.setUp()
    try:
        yield value
    finally:
        value.doCleanups()


def test_initial_request_drift_is_blocked_before_http_and_reservation(harness, monkeypatch):
    original = runner.request_body

    def changed_after_preflight(history, cell, cases):
        body = original(history, cell, cases)
        # The real preflight occurs before transport construction. Introduce
        # drift only at the actual dispatch boundary, after key substitution.
        if harness.instances:
            body["input"].append({"role": "user", "content": "unexpected drift"})
        return body

    monkeypatch.setattr(runner, "request_body", changed_after_preflight)
    result, report = harness.run_mode("success")
    assert harness.http_calls == []
    assert result["ledger"]["physical_attempts"] == 0
    assert report["coverage"]["unknown_n"] == 480
    assert not (harness.run / "REPAIR_STARTED.json").exists()


def test_one_shared_pacer_and_at_most_three_http_entries(harness, monkeypatch):
    original_pacer = pacing.SlidingWindowPacer
    original_http = httpx.MockTransport.handle_request
    pacers = []
    mutex = threading.Lock()
    active = maximum = 0

    def create_pacer(**kwargs):
        value = original_pacer(**kwargs)
        pacers.append(value)
        return value

    def observed_http(client, request):
        nonlocal active, maximum
        with mutex:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(0.01)  # Bounded overlap of synthetic HTTP callbacks.
            return original_http(client, request)
        finally:
            with mutex:
                active -= 1

    monkeypatch.setattr(pacing, "SlidingWindowPacer", create_pacer)
    monkeypatch.setattr(httpx.MockTransport, "handle_request", observed_http)
    result, _ = harness.run_mode("success")
    assert result["ledger"]["physical_attempts"] == 86
    assert len(pacers) == len(harness.instances) == 1
    assert harness.instances[0].pacer is pacers[0]
    assert 1 <= maximum <= 3 and active == 0
    assert [c["phase"] for c in harness.http_calls[:6]] == ["capability"] * 6
    assert all(c["phase"] != "capability" for c in harness.http_calls[6:])


def test_reservation_without_http_does_not_claim_repair_started(harness, monkeypatch):
    original = transport.OfficialTransport._complete_unchecked

    def fail_after_reservation(self, **kwargs):
        metadata = dict(self.local.metadata)
        if metadata["phase"] == "capability":
            return original(self, **kwargs)
        body = transport.make_body(**{k: kwargs[k] for k in (
            "model", "messages", "completion_kwargs", "temperature", "max_output_tokens")})
        request_hash = digest(body)
        self.journal.assert_dispatchable()
        atomic_json(self.run_dir / "requests" / (request_hash + ".json"), body)
        reservation = self.journal.reserve("offline-before-http/" + metadata["cell_id"],
            input_token_bound=transport.CONTEXT_LIMIT, max_output_tokens=transport.OUTPUT_LIMIT,
            request_hash=request_hash, metadata=metadata)
        self.local.active_reservation = reservation.reservation_id
        raise RuntimeError("synthetic failure between durable reservation and HTTP")

    monkeypatch.setattr(transport.OfficialTransport, "_complete_unchecked", fail_after_reservation)
    result, _ = harness.run_mode("success")
    assert len(harness.http_calls) == 6
    assert 7 <= result["ledger"]["physical_attempts"] <= 9
    status = runner.read_json(harness.run / "STATUS.json")
    assert status["research_requests_reserved"] >= 1
    assert not status["repair_started"]
    assert status["first_confirmed_research_request"] is None
    assert not (harness.run / "REPAIR_STARTED.json").exists()


def test_http_error_response_confirms_attempt_without_retry(harness, monkeypatch):
    original = httpx.MockTransport.handle_request
    research_ids = []
    mutex = threading.Lock()

    def error_response(client, request):
        metadata = harness.instances[0].local.metadata
        if metadata["phase"] == "capability":
            return original(client, request)
        with mutex:
            research_ids.append(metadata["cell_id"])
        return httpx.Response(429, json={"error": {"type": "tokens", "code": "rate_limit_exceeded"}})

    monkeypatch.setattr(httpx.MockTransport, "handle_request", error_response)
    result, _ = harness.run_mode("success")
    assert 1 <= len(research_ids) <= 3
    assert len(research_ids) == len(set(research_ids))
    assert result["research_responses"] == 0
    status = runner.read_json(harness.run / "STATUS.json")
    assert status["repair_started"]
    marker = runner.read_json(harness.run / "REPAIR_STARTED.json")
    proof = marker["first_confirmed_research_request"]
    response = runner.read_json(harness.root / proof["response_artifact"])
    assert response["http_status"] == 429
    assert runner.file_hash(harness.root / proof["response_artifact"]) == proof["response_sha256"]
    assert proof["reservation_event"]["metadata"]["phase"] in {"immediate", "sequential"}
