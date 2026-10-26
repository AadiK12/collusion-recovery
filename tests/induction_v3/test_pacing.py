"""Offline tests: synthetic keys, mock HTTP, temporary journals, no provider."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

import httpx
import pytest

from experiments.induction_v3.accounting import ProgramLock, ReservationJournal, verify_run
from experiments.induction_v3.common import MODEL, atomic_json, canonical
from experiments.induction_v3.pacing import (
    KnownReplayTokenEstimator, PacedOfficialTransport, PacingStopped,
    QUEUED_BEFORE_PHYSICAL_ATTEMPT, SlidingWindowPacer, utf8_request_estimate,
)
from experiments.induction_v3.transport import DispatchStopped, make_body


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []
        self.hook = None

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert 0 < seconds <= 1
        self.sleeps.append(seconds)
        self.now += seconds
        if self.hook:
            self.hook()


def request():
    return dict(model=MODEL, messages=[{"role": "user", "content": "visible task"}],
                completion_kwargs={"reasoning_effort": "high"},
                temperature=1.0, max_output_tokens=64000)


def response(identity="reasoning-one", ciphertext="opaque-test-ciphertext", output_tokens=20):
    return {"model": MODEL, "status": "completed", "output": [
        {"type": "reasoning", "id": identity, "summary": [], "encrypted_content": ciphertext},
        {"type": "message", "id": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "visible response"}]}],
        "usage": {"input_tokens": 100, "output_tokens": output_tokens,
                  "total_tokens": 100 + output_tokens,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 10}}}


@pytest.fixture
def journal(tmp_path):
    empirical = tmp_path / "runs/empirical"
    empirical.mkdir(parents=True)
    source = tmp_path / "frozen.txt"
    source.write_text("offline frozen fixture")
    with ProgramLock(empirical / ".controlled-peer-recovery-program.lock") as lock:
        value = ReservationJournal(empirical / "offline-pacing", program_lock=lock,
            source_root=tmp_path, binding={"model": MODEL, "run_id": "offline-pacing"},
            source_inventory={"frozen.txt": hashlib.sha256(source.read_bytes()).hexdigest()})
        yield value


def transport(journal, pacer, handler, on_wait=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PacedOfficialTransport(journal, journal.path, "offline-only-synthetic-key",
                                  http_client=client, pacer=pacer, on_wait=on_wait)


def test_utf8_fallback_counts_entire_body_and_output_without_mutation():
    body = make_body(**request())
    body["input"].append({"type": "reasoning", "encrypted_content": "opaque-é"})
    frozen = copy.deepcopy(body)
    assert utf8_request_estimate(body) == len(canonical(body).encode("utf-8")) + 64000
    assert body == frozen


@pytest.mark.parametrize("kwargs", [
    {"capacity": 1000001}, {"capacity": True}, {"max_in_flight": 4},
    {"window_seconds": 59}, {"window_seconds": float("nan")},
    {"max_wait_seconds": float("inf")}, {"initial_delay_seconds": -1},
])
def test_invalid_or_excessive_pacing_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        SlidingWindowPacer(**kwargs)


def test_credit_survives_long_active_call_and_full_window_after_completion(journal):
    clock = Clock()
    pacer = SlidingWindowPacer(capacity=100, estimator=lambda body: 60,
        initial_delay_seconds=0, clock=clock, sleep=clock.sleep)
    notices = []
    with pacer.admission({}, journal):
        clock.now = 200
        assert pacer.snapshot()["charged_estimated_units"] == 60
        assert pacer.snapshot()["active"] == 1
    with pacer.admission({}, journal, on_wait=notices.append) as result:
        assert clock.now == 260
        assert result["waited_seconds"] == 60
    assert len(notices) == 1
    assert notices[0]["state"] == QUEUED_BEFORE_PHYSICAL_ATTEMPT
    assert journal.snapshot()["physical_attempts"] == 0


@pytest.mark.parametrize("reason", ["stop", "timeout", "oversized"])
def test_queued_failure_creates_no_physical_attempt_or_request_file(journal, reason):
    clock = Clock()
    pacer = SlidingWindowPacer(initial_delay_seconds=60, max_wait_seconds=2,
        estimator=lambda body: 1000001 if reason == "oversized" else 100,
        clock=clock, sleep=clock.sleep)
    if reason == "stop":
        clock.hook = lambda: journal.stop("offline_stop")
    http_calls = []
    value = transport(journal, pacer, lambda req: http_calls.append(req))
    with pytest.raises(PacingStopped, match="queuedbeforephysicalattempt"):
        value.complete(**request(), accept_tool_calls=False)
    assert journal.snapshot()["physical_attempts"] == 0
    assert journal.snapshot()["reserved"]["requests"] == 0
    assert not list(journal.path.glob("requests/*.json"))
    assert http_calls == []
    assert pacer.snapshot()["queued"] == pacer.snapshot()["active"] == 0


def test_body_is_frozen_before_queue_callback_and_original_return_is_preserved(journal):
    clock = Clock()
    pacer = SlidingWindowPacer(initial_delay_seconds=1, clock=clock, sleep=clock.sleep)
    args = request()
    expected = make_body(**copy.deepcopy(args))
    sent = []

    def on_wait(event):
        assert event["state"] == QUEUED_BEFORE_PHYSICAL_ATTEMPT
        assert journal.snapshot()["physical_attempts"] == 0
        args["messages"][0]["content"] = "changed after queueing"

    def handler(req):
        assert journal.snapshot()["physical_attempts"] == 1
        sent.append(json.loads(req.content))
        return httpx.Response(200, json=response())

    value = transport(journal, pacer, handler, on_wait)
    result = value.complete(**args, accept_tool_calls=False)
    assert len(result) == 4 and result[2] == "visible response" and result[3] == []
    assert sent == [expected]
    assert journal.snapshot()["physical_attempts"] == 1
    assert journal.snapshot()["reserved"]["requests"] == 0
    assert verify_run(journal.path, source_root=journal.source_root)["journal_valid"]


def test_four_workers_never_reserve_more_than_three_active_calls(journal):
    pacer = SlidingWindowPacer(initial_delay_seconds=0, estimator=lambda body: 1)
    release = threading.Event()
    three_entered = threading.Event()
    queued = threading.Event()
    mutex = threading.Lock()
    entered = 0
    active = 0
    maximum = 0

    def handler(req):
        nonlocal entered, active, maximum
        with mutex:
            entered += 1
            active += 1
            maximum = max(maximum, active)
            if entered == 3:
                three_entered.set()
        assert release.wait(3)
        with mutex:
            active -= 1
        return httpx.Response(200, json=response())

    value = transport(journal, pacer, handler, lambda event: queued.set())

    def call(index):
        value.context(offline_worker=index)
        return value.complete(**request(), accept_tool_calls=False)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(call, i) for i in range(4)]
        assert three_entered.wait(3) and queued.wait(3)
        assert journal.snapshot()["physical_attempts"] == 3
        release.set()
        assert all(f.result()[2] == "visible response" for f in futures)
    assert entered == 4 and maximum == 3


def test_429_retains_unknown_reservation_and_never_retries(journal):
    clock = Clock()
    pacer = SlidingWindowPacer(initial_delay_seconds=0, clock=clock, sleep=clock.sleep)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded",
                             "type": "rate_limit_error", "message": "offline rejection"}})

    value = transport(journal, pacer, handler)
    with pytest.raises(DispatchStopped):
        value.complete(**request(), accept_tool_calls=False)
    with pytest.raises(Exception):
        value.complete(**request(), accept_tool_calls=False)
    assert len(calls) == journal.snapshot()["physical_attempts"] == 1
    assert journal.snapshot()["reserved"]["requests"] == 1
    assert journal.stopped
    assert verify_run(journal.path, source_root=journal.source_root)["journal_valid"]


class RecordingEncoding:
    name = "o200k_base"

    def __init__(self):
        self.text = None

    def encode(self, text, *, disallowed_special):
        assert disallowed_special == ()
        self.text = text
        return [0] * len(text)


def replay_body(raw):
    body = make_body(**request())
    body["input"].extend(copy.deepcopy(raw["output"]))
    return body


def test_known_replay_counts_usage_allowance_and_preserves_outgoing_ciphertext():
    encoding = RecordingEncoding()
    estimator = KnownReplayTokenEstimator(encoding=encoding)
    raw = response(output_tokens=40)
    estimator.observe(raw)
    body = replay_body(raw)
    body["input"].insert(0, copy.deepcopy(raw["output"][0]))
    original = copy.deepcopy(body)
    detail = estimator.estimate_breakdown(body)
    assert detail["opaque_output_token_allowance"] == 80
    assert detail["opaque_items"] == 2
    assert "opaque-test-ciphertext" not in encoding.text
    assert "visible task" in encoding.text and "visible response" in encoding.text
    assert detail["estimated_units"] == 70400
    assert body == original


@pytest.mark.parametrize("mutation", ["unknown", "changed_ciphertext", "changed_usage"])
def test_unknown_or_conflicting_replay_fails_before_dispatch(journal, mutation):
    encoding = RecordingEncoding()
    estimator = KnownReplayTokenEstimator(encoding=encoding)
    raw = response()
    if mutation != "unknown":
        estimator.observe(raw)
    if mutation == "changed_ciphertext":
        raw["output"][0]["encrypted_content"] = "changed-ciphertext"
    if mutation == "changed_usage":
        raw["usage"]["output_tokens"] += 1
        with pytest.raises(PacingStopped, match="conflicting"):
            estimator.observe(raw)
        return
    args = request()
    args["messages"].append({"role": "assistant", "provider_output_items": raw["output"]})
    value = transport(journal, SlidingWindowPacer(estimator=estimator, initial_delay_seconds=0),
                      lambda req: pytest.fail("unexpected physical dispatch"))
    with pytest.raises(PacingStopped, match="unknown"):
        value.complete(**args, accept_tool_calls=False)
    assert journal.snapshot()["physical_attempts"] == 0


def test_archived_response_requires_exact_hash_and_regular_path(tmp_path):
    path = tmp_path / "response.json"
    atomic_json(path, {"body": response()})
    binding = {"response.json": hashlib.sha256(path.read_bytes()).hexdigest()}
    estimator = KnownReplayTokenEstimator.from_archives(binding, root=tmp_path,
                                                       encoding=RecordingEncoding())
    assert estimator(replay_body(response())) == 70400
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="hash"):
        KnownReplayTokenEstimator.from_archives(binding, root=tmp_path, encoding=RecordingEncoding())
    link = tmp_path / "symlink.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="regular"):
        KnownReplayTokenEstimator.from_archives({"symlink.json": hashlib.sha256(path.read_bytes()).hexdigest()},
                                                root=tmp_path, encoding=RecordingEncoding())


def test_successful_response_is_observed_before_future_replay(journal):
    estimator = KnownReplayTokenEstimator(encoding=RecordingEncoding())
    clock = Clock()
    pacer = SlidingWindowPacer(estimator=estimator, initial_delay_seconds=0,
                              clock=clock, sleep=clock.sleep)
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=response())

    value = transport(journal, pacer, handler)
    first = value.complete(**request(), accept_tool_calls=False)
    args = request()
    args["messages"].append(first[1])
    value.complete(**args, accept_tool_calls=False)
    assert bodies[1]["input"][1:] == response()["output"]
    assert journal.snapshot()["physical_attempts"] == 2


def test_post_response_replay_conflict_stops_after_completed_accounting(journal):
    estimator = KnownReplayTokenEstimator(encoding=RecordingEncoding())
    estimator.observe(response())
    pacer = SlidingWindowPacer(estimator=estimator, initial_delay_seconds=0)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=response(ciphertext="conflicting-opaque-value"))

    value = transport(journal, pacer, handler)
    with pytest.raises(PacingStopped, match="conflicting"):
        value.complete(**request(), accept_tool_calls=False)
    assert journal.stopped and journal.stop_reason == "pacing_replay_observation_failed"
    assert len(calls) == journal.snapshot()["physical_attempts"] == 1
    assert journal.snapshot()["consumed"]["requests"] == 1
    assert journal.snapshot()["reserved"]["requests"] == 0
    assert pacer.snapshot()["active"] == 0
    assert pacer.snapshot()["charged_estimated_units"] == 70400
