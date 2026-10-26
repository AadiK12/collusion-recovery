"""Additive, pre-reservation pacing for a prospectively audited new run.

The frozen OfficialTransport and its error/accounting semantics are unchanged.
This module never loads a credential, retries a call, edits a prompt, or decodes
opaque replay data. One shared pacer must cover every dispatcher using the same
allocation. Estimates are scheduling units, not provider-reconciled token usage.
"""
from collections import deque
from contextlib import contextmanager
import copy
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import threading
import time

from .common import MODEL, canonical
from .transport import DispatchStopped, OfficialTransport, make_body


QUEUED_BEFORE_PHYSICAL_ATTEMPT = "queuedbeforephysicalattempt"
MAX_WINDOW_UNITS = 1_000_000


class PacingStopped(DispatchStopped):
    """The delegate was not called and no physical attempt was reserved."""

    state = QUEUED_BEFORE_PHYSICAL_ATTEMPT

    def __init__(self, reason):
        super().__init__(self.state + ":" + reason)


def utf8_request_estimate(body):
    """Conservative fallback: all canonical JSON UTF-8 bytes plus output cap.

    Includes tools, protocol fields and opaque ciphertext, without decoding or
    removing any field. This intentionally overcounts ordinary text. A different
    audited estimator may be injected for a new release; provider usage never
    changes this estimate retrospectively or enables an automatic retry.
    """
    output = body.get("max_output_tokens")
    if type(output) is not int or output < 1:
        raise ValueError("a positive integer output cap is required")
    return len(canonical(body).encode("utf-8")) + output


def _finite_number(value, name, minimum, maximum):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not minimum <= value <= maximum):
        raise ValueError("invalid " + name)
    return float(value)


class KnownReplayTokenEstimator:
    """Approximate rate pacing with known opaque replay allowances.

    The caller supplies an already-loaded o200k_base encoding. This module never
    fetches tokenizer data. Only a local counting copy omits encrypted_content;
    ciphertext is never decoded or interpreted. Each retained reasoning item
    instead contributes its originating response's entire output token usage.
    Unknown IDs or changed ciphertext fail before dispatch. A fixed 10% headroom
    applies to max(visible JSON tokens + allowances, max_output_tokens).

    This is not the spend reservation bound or a guarantee of future server-side
    rate accounting. An exact new release must bind the estimator, its archived
    response hashes, tokenizer version, and offline calibration evidence.
    """

    def __init__(self, *, encoding):
        if (importlib.metadata.version("tiktoken") != "0.14.0"
                or getattr(encoding, "name", None) != "o200k_base"):
            raise ValueError("the audited tiktoken 0.14.0 o200k_base encoding is required")
        self.encoding = encoding
        self._known = {}
        self._mutex = threading.Lock()
        self.archive_bindings = {}

    @classmethod
    def from_archives(cls, bindings, *, root, encoding):
        """Seed only from exact-hash, regular cached response JSON files."""
        estimator = cls(encoding=encoding)
        root = Path(root).resolve()
        for name, expected in sorted(bindings.items()):
            if not isinstance(name, str):
                raise ValueError("invalid archived response path")
            relative = Path(name)
            if (relative.is_absolute()
                    or ".." in relative.parts or not relative.parts
                    or not isinstance(expected, str) or len(expected) != 64):
                raise ValueError("invalid archived response binding")
            path = root / relative
            if (not path.resolve().is_relative_to(root) or not path.is_file()
                    or any(root.joinpath(*relative.parts[:i]).is_symlink()
                           for i in range(1, len(relative.parts) + 1))):
                raise ValueError("archived response must be a regular root-contained file")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("archived response hash mismatch")
            wrapper = json.loads(data)
            estimator.observe(wrapper["body"])
            estimator.archive_bindings[name] = expected
        return estimator

    def observe(self, raw):
        """Register visible output usage and hashes, never opaque plaintext."""
        if (not isinstance(raw, dict) or raw.get("status") != "completed"
                or not (raw.get("model") == MODEL
                        or str(raw.get("model", "")).startswith(MODEL + "-"))):
            raise PacingStopped("invalid_replay_response")
        usage = raw.get("usage")
        output = usage.get("output_tokens") if isinstance(usage, dict) else None
        if type(output) is not int or not 0 <= output <= 64000:
            raise PacingStopped("invalid_replay_output_usage")
        items = raw.get("output")
        if not isinstance(items, list):
            raise PacingStopped("invalid_replay_output_items")
        pending = {}
        for item in items:
            if not isinstance(item, dict):
                raise PacingStopped("invalid_replay_output_item")
            if item.get("type") != "reasoning":
                continue
            identity, ciphertext = item.get("id"), item.get("encrypted_content")
            if not isinstance(identity, str) or not identity or not isinstance(ciphertext, str) or not ciphertext:
                raise PacingStopped("invalid_opaque_replay_identity")
            record = (hashlib.sha256(ciphertext.encode("utf-8")).hexdigest(), output)
            if identity in pending and pending[identity] != record:
                raise PacingStopped("conflicting_opaque_replay_identity")
            pending[identity] = record
        with self._mutex:
            if any(identity in self._known and self._known[identity] != record
                   for identity, record in pending.items()):
                raise PacingStopped("conflicting_opaque_replay_identity")
            self._known.update(pending)

    def estimate_breakdown(self, body):
        local = copy.deepcopy(body)
        output = local.get("max_output_tokens")
        if type(output) is not int or output < 1:
            raise PacingStopped("invalid_output_cap")
        items = local.get("input")
        if not isinstance(items, list):
            raise PacingStopped("invalid_request_input")
        allowance = 0
        opaque_items = 0
        with self._mutex:
            for item in items:
                if not isinstance(item, dict):
                    raise PacingStopped("invalid_request_input_item")
                if item.get("type") != "reasoning":
                    continue
                identity, ciphertext = item.get("id"), item.get("encrypted_content")
                if not isinstance(identity, str) or not isinstance(ciphertext, str):
                    raise PacingStopped("unknown_opaque_replay_item")
                record = self._known.get(identity)
                if record is None or hashlib.sha256(ciphertext.encode("utf-8")).hexdigest() != record[0]:
                    raise PacingStopped("unknown_or_changed_opaque_replay_item")
                allowance += record[1]
                opaque_items += 1
                del item["encrypted_content"]
        # Treat special-token-looking user text as ordinary text, not tokenizer
        # instructions. All other request fields are retained in the count copy.
        visible = len(self.encoding.encode(canonical(local), disallowed_special=()))
        return {"visible_json_tokens": visible, "opaque_output_token_allowance": allowance,
                "opaque_items": opaque_items,
                "estimated_input_tokens": (11 * (visible + allowance) + 9) // 10,
                "estimated_units": (11 * max(output, visible + allowance) + 9) // 10}

    def __call__(self, body):
        return self.estimate_breakdown(body)["estimated_units"]


class SlidingWindowPacer:
    """FIFO admission, bounded waits, and at most three active delegate calls.

    Active estimates remain charged until their delegate returns, then for a
    whole window. Consequently an arbitrarily delayed dispatch cannot outlive a
    credit charged at admission and cause an uncounted burst. This is stricter
    than a window measured only at request start. Cancellation never refunds a
    credit after admission; an unsuccessful attempt still occupies its window.

    A fresh instance starts with a 60-second cooldown, avoiding an empty-window
    reset immediately after another dispatcher. The calling release must bind
    any nondefault settings and estimator. clock/sleep support offline tests.
    """

    def __init__(self, *, capacity=MAX_WINDOW_UNITS, window_seconds=60,
                 max_in_flight=3, max_wait_seconds=300, initial_delay_seconds=60,
                 estimator=utf8_request_estimate, clock=time.monotonic,
                 sleep=time.sleep):
        if type(capacity) is not int or not 1 <= capacity <= MAX_WINDOW_UNITS:
            raise ValueError("pacing capacity exceeds the fixed upper bound")
        if type(max_in_flight) is not int or not 1 <= max_in_flight <= 3:
            raise ValueError("pacing concurrency exceeds the fixed upper bound")
        self.capacity = capacity
        self.window_seconds = _finite_number(window_seconds, "window", 60, 3600)
        self.max_in_flight = max_in_flight
        self.max_wait_seconds = _finite_number(max_wait_seconds, "maximum wait", 0, 3600)
        initial_delay = _finite_number(initial_delay_seconds, "initial delay", 0, 3600)
        if not callable(estimator) or not callable(clock) or not callable(sleep):
            raise ValueError("estimator, clock and sleep must be callable")
        self.estimator, self._clock, self._sleep = estimator, clock, sleep
        self._mutex = threading.Lock()
        self._waiting = deque()
        self._active = {}
        self._recent = deque()
        self._last_time = -math.inf
        self._not_before = self._now() + initial_delay

    def _now(self):
        now = self._clock()
        if (isinstance(now, bool) or not isinstance(now, (int, float))
                or not math.isfinite(now) or now < self._last_time):
            raise PacingStopped("invalid_monotonic_clock")
        self._last_time = now
        return now

    def _prune(self, now):
        while self._recent and self._recent[0][0] + self.window_seconds <= now:
            self._recent.popleft()

    def snapshot(self):
        with self._mutex:
            now = self._now()
            self._prune(now)
            return {
                "state": QUEUED_BEFORE_PHYSICAL_ATTEMPT if self._waiting else "idle_or_in_flight",
                "queued": len(self._waiting), "active": len(self._active),
                "charged_estimated_units": sum(self._active.values()) + sum(v for _, v in self._recent),
                "capacity": self.capacity, "window_seconds": self.window_seconds,
                "initial_cooldown_remaining_seconds": max(0, self._not_before - now),
            }

    @contextmanager
    def admission(self, body, journal, *, on_wait=None):
        """Yield only after admission; every wait precedes delegate reservation.

        on_wait receives safe pacing metadata once, when a request first queues.
        It does not replace the transport's return value or add prompt fields.
        """
        units = self.estimator(copy.deepcopy(body))
        if type(units) is not int or units < 1:
            raise PacingStopped("invalid_request_estimate")
        if units > self.capacity:
            raise PacingStopped("request_exceeds_window_capacity")
        journal.assert_dispatchable()
        ticket = object()
        admitted = False
        notified = False
        with self._mutex:
            started = self._now()
            deadline = started + self.max_wait_seconds
            self._waiting.append(ticket)
        try:
            while True:
                if journal.stopped:
                    raise PacingStopped("journal_stopped")
                with self._mutex:
                    now = self._now()
                    self._prune(now)
                    charged = sum(self._active.values()) + sum(v for _, v in self._recent)
                    available = (self._waiting[0] is ticket and now >= self._not_before
                                 and len(self._active) < self.max_in_flight
                                 and charged + units <= self.capacity)
                    if available:
                        # Recheck frozen sources, ledger ownership and stop state
                        # immediately before delegating to the original transport.
                        journal.assert_dispatchable()
                        self._waiting.popleft()
                        self._active[ticket] = units
                        admitted = True
                        break
                    if now >= deadline:
                        raise PacingStopped("queue_wait_limit")
                    delay = min(1.0, deadline - now)
                    if now < self._not_before:
                        delay = min(delay, self._not_before - now)
                    elif self._recent:
                        delay = min(delay, self._recent[0][0] + self.window_seconds - now)
                if not notified and on_wait is not None:
                    on_wait({"state": QUEUED_BEFORE_PHYSICAL_ATTEMPT,
                             "estimated_units": units,
                             "max_wait_seconds": self.max_wait_seconds})
                    notified = True
                # No application lock is held during sleep; stop is checked at
                # least once per second, including while another call is active.
                self._sleep(delay)
            yield {"estimated_units": units, "waited_seconds": now - started}
        finally:
            with self._mutex:
                if admitted:
                    # Retaining credit through completion also covers ambiguous
                    # errors and exceptions before any observable response.
                    completed = self._now()
                    self._recent.append((completed, self._active.pop(ticket)))
                elif ticket in self._waiting:
                    self._waiting.remove(ticket)


class PacedOfficialTransport(OfficialTransport):
    """Unchanged official transport behind one shared pre-reservation pacer."""

    def __init__(self, journal, run_dir, key, *, http_client=None, pacer=None,
                 on_wait=None):
        super().__init__(journal, run_dir, key, http_client=http_client)
        self.pacer = pacer if pacer is not None else SlidingWindowPacer()
        self.on_wait = on_wait

    def complete(self, *, model, messages, completion_kwargs, temperature,
                 max_output_tokens, accept_tool_calls, usage_recorder=None):
        # Freeze the exact outgoing arguments before the wait. Other threads or
        # callbacks cannot alter the body after its scheduling estimate is taken.
        request = dict(model=model, messages=copy.deepcopy(messages),
                       completion_kwargs=copy.deepcopy(completion_kwargs),
                       temperature=temperature, max_output_tokens=max_output_tokens)
        body = make_body(**request)
        with self.pacer.admission(body, self.journal, on_wait=self.on_wait):
            result = super().complete(**request, accept_tool_calls=accept_tool_calls,
                                      usage_recorder=usage_recorder)
            observe = getattr(self.pacer.estimator, "observe", None)
            if observe is not None:
                try:
                    observe(result[0])
                except Exception:
                    self.journal.stop("pacing_replay_observation_failed")
                    raise
            return result
