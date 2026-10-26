"""One official Responses HTTP attempt per durable reservation; no retries.

Direct Responses encoding replaces LiteLLM's bridge, preserving tool schemas,
role order, assistant prose, and provider-issued opaque reasoning replay items.
Authentication is memory-only. No SDK/env fallback, redirects or proxy routing.
"""
import copy
import json
import threading
import time
from pathlib import Path
from .common import MODEL, ROOT, atomic_json, canonical, digest

ENDPOINT = "https://api.openai.com/v1/responses"
CONTEXT_LIMIT = 1_050_000
OUTPUT_LIMIT = 64_000

class DispatchStopped(RuntimeError):
    pass

def encode_input(messages):
    items = []
    for message in messages:
        role = message["role"]
        if role.startswith("_runner_"):
            continue
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
            continue
        if role not in {"system", "user", "assistant"}:
            raise ValueError("unsupported role")
        if role == "assistant" and "provider_output_items" in message:
            # Responses stateless replay preserves the original item sequence,
            # IDs, phases, content blocks and encrypted state byte-for-byte at
            # the JSON value level. Do not regroup reasoning/prose/tool calls.
            original = message["provider_output_items"]
            if not isinstance(original, list) or not all(isinstance(i, dict) for i in original):
                raise ValueError("malformed ordered provider replay items")
            items.extend(copy.deepcopy(original))
            continue
        for opaque in message.get("reasoning_items", []):
            if opaque.get("type") != "reasoning" or not opaque.get("encrypted_content"):
                raise ValueError("reasoning replay requires original encrypted provider item")
            items.append(copy.deepcopy(opaque))
        content = message.get("content") or ""
        if content:
            items.append({"role": role, "content": content})
        for call in message.get("tool_calls", []):
            items.append({"type": "function_call", "call_id": call["id"],
                          "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    return items

def make_body(*, model, messages, completion_kwargs, temperature, max_output_tokens):
    if model not in {MODEL, "openai/" + MODEL} or temperature != 1.0 or max_output_tokens != OUTPUT_LIMIT:
        raise ValueError("frozen model/decoding mismatch")
    if completion_kwargs.get("reasoning_effort") != "high":
        raise ValueError("frozen reasoning mismatch")
    body = {"model": MODEL, "input": encode_input(messages), "temperature": 1.0,
            "max_output_tokens": OUTPUT_LIMIT, "reasoning": {"effort": "high"},
            "store": False, "include": ["reasoning.encrypted_content"], "service_tier": "default"}
    if completion_kwargs.get("tools"):
        # Preserve upstream non-strict schemas and required fields exactly.
        body["tools"] = [{"type": "function", **copy.deepcopy(tool["function"]), "strict": False} for tool in completion_kwargs["tools"]]
    choice = completion_kwargs.get("tool_choice")
    if isinstance(choice, dict):
        body["tool_choice"] = {"type": "function", "name": choice["function"]["name"]}
    elif choice:
        body["tool_choice"] = choice
    return body

def decode_response(body):
    content, calls, reasoning = [], [], []
    for item in body.get("output", []):
        if item["type"] == "reasoning":
            # Retain opaque replay state only. No chain-of-thought extraction.
            if not item.get("encrypted_content"):
                raise DispatchStopped("provider reasoning replay state missing")
            reasoning.append({k: copy.deepcopy(v) for k, v in item.items() if k in {"type", "id", "encrypted_content", "summary", "status"}})
        elif item["type"] == "function_call":
            calls.append({"id": item["call_id"], "type": "function", "function": {"name": item["name"], "arguments": item["arguments"]}})
        elif item["type"] == "message":
            for part in item["content"]:
                if part["type"] == "output_text":
                    content.append(part["text"])
                elif part["type"] == "refusal":
                    content.append(part["refusal"])
    message = {"role": "assistant", "content": "".join(content), "tool_calls": calls,
               "reasoning_items": reasoning, "provider_output_items":copy.deepcopy(body.get("output", []))}
    return message

def usage_from_response(body):
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    detail = usage.get("input_tokens_details") or {}
    result = {"input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
              "total_tokens": usage.get("total_tokens"), "cached_input_tokens": detail.get("cached_tokens", 0),
              "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)}
    writes = detail.get("cache_write_tokens", detail.get("cache_creation_tokens"))
    if writes is not None:
        result["cache_write_input_tokens"] = writes
    return result

def load_authorized_key(path=ROOT / ".env.local"):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise DispatchStopped("authorized credential file is not regular")
    # Do not populate os.environ or return any other file values.
    from dotenv import dotenv_values
    key = dotenv_values(path).get("OPENAI_KEY")
    if not isinstance(key, str) or not key.strip():
        raise DispatchStopped("authorized OPENAI_KEY is unavailable")
    return key.strip()

class OfficialTransport:
    def __init__(self, journal, run_dir, key, *, http_client=None):
        import httpx
        self.journal, self.run_dir, self._key = journal, Path(run_dir), key
        self.http = http_client or httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(180.0, connect=20.0))
        self.semaphore = threading.BoundedSemaphore(3)
        self.local = threading.local()

    def context(self, **metadata):
        self.local.metadata = metadata
        self.local.call_index = 0

    def complete(self, **kwargs):
        self.local.active_reservation = None
        try:
            return self._complete_unchecked(**kwargs)
        except Exception:
            rid = self.local.active_reservation
            if rid:
                settled = any(e.get("kind") == "settlement" and e.get("reservation_id") == rid for e in self.journal.events)
                if not settled:
                    self.journal.settle(rid, usage=None, outcome="unknown")
                self.journal.stop("post_dispatch_processing_failure")
            raise
        finally:
            self.local.active_reservation = None

    def _complete_unchecked(self, *, model, messages, completion_kwargs, temperature, max_output_tokens, accept_tool_calls, usage_recorder=None):
        body = make_body(model=model, messages=messages, completion_kwargs=completion_kwargs,
                         temperature=temperature, max_output_tokens=max_output_tokens)
        metadata = dict(getattr(self.local, "metadata", {}))
        call_index = getattr(self.local, "call_index", 0)
        self.local.call_index = call_index + 1
        identity = canonical({**metadata, "physical_call_index": call_index})
        request_hash = digest(body)
        with self.semaphore:
            self.journal.assert_dispatchable()
            atomic_json(self.run_dir / "requests" / (request_hash + ".json"), body)
            reservation = self.journal.reserve(identity, input_token_bound=CONTEXT_LIMIT,
                max_output_tokens=OUTPUT_LIMIT, request_hash=request_hash, metadata=metadata)
            self.local.active_reservation = reservation.reservation_id
            started = time.monotonic()
            try:
                response = self.http.post(ENDPOINT, json=body, headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
            except Exception as exc:
                category = "ambiguous_timeout" if "Timeout" in type(exc).__name__ else "unknown"
                self.journal.settle(reservation.reservation_id, usage=None, outcome=category)
                raise DispatchStopped(category) from None
            latency = time.monotonic() - started
            try:
                raw = response.json()
            except Exception:
                self.journal.settle(reservation.reservation_id, usage=None, outcome="unknown")
                raise DispatchStopped("non_json_provider_response") from None
            if response.status_code != 200:
                error = raw.get("error", {}) if isinstance(raw, dict) else {}
                safe_error = {k: str(error.get(k, "")).replace(self._key, "[REDACTED]")[:500] for k in ("code", "type", "param", "message")}
                atomic_json(self.run_dir / "responses" / (reservation.reservation_id + ".json"),
                            {"http_status": response.status_code, "error": safe_error, "latency_seconds": latency})
                self.journal.settle(reservation.reservation_id, usage=None, outcome="unknown", provider_request_id=response.headers.get("x-request-id"))
                raise DispatchStopped("official_endpoint_rejected_request:" + safe_error["code"]) from None
            # Payloads contain no authentication. Scrub any accidental credential echo.
            serialized = canonical(raw)
            if self._key in serialized:
                self.journal.settle(reservation.reservation_id, usage=None, outcome="unknown")
                raise DispatchStopped("credential_echo_detected")
            atomic_json(self.run_dir / "responses" / (reservation.reservation_id + ".json"),
                        {"body": raw, "latency_seconds": latency, "request_hash": request_hash})
            usage = usage_from_response(raw)
            settlement = self.journal.settle(reservation.reservation_id, usage=usage, outcome="completed", provider_request_id=response.headers.get("x-request-id"))
            if settlement["outcome"] != "completed" or self.journal.stopped:
                raise DispatchStopped("provider_usage_unresolved_or_out_of_bounds")
            if raw.get("model") not in {MODEL} and not str(raw.get("model", "")).startswith(MODEL + "-"):
                self.journal.stop("model_identity_mismatch")
                raise DispatchStopped("model_identity_mismatch")
            if raw.get("status") != "completed":
                self.journal.stop("incomplete_provider_response")
                raise DispatchStopped("incomplete_provider_response")
            try:
                message = decode_response(raw)
            except DispatchStopped:
                self.journal.stop("reasoning_replay_missing")
                raise
            if not accept_tool_calls and message["tool_calls"]:
                # The source reflection phase never executes tools. Keeping an
                # unexecuted call in stateless replay would create an orphan;
                # stop rather than deleting provider items or inventing output.
                self.journal.stop("unexpected_reflection_tool_call")
                raise DispatchStopped("unexpected_reflection_tool_call")
            if not message["content"] and not message["tool_calls"]:
                self.journal.stop("empty_provider_response")
                raise DispatchStopped("empty_provider_response")
            if usage_recorder:
                usage_recorder({**(usage or {}), "requested_model": MODEL, "response_model": raw.get("model"),
                                "api_attempt": 1, "response_status": "accepted", "finish_reason": "stop",
                                "provider_usage": raw.get("usage"), "latency_seconds": latency,
                                "reservation_id": reservation.reservation_id})
            return raw, message, message["content"], message["tool_calls"] if accept_tool_calls else []
