import json
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import httpx
import pytest
from experiments.induction_v3.common import atomic_json
from experiments.induction_v3.transport import OfficialTransport, make_body, encode_input, decode_response, DispatchStopped

def kwargs():
    return dict(model="gpt-5.6-luna", messages=[{"role":"system","content":"policy"},{"role":"user","content":"task"}],
                completion_kwargs={"reasoning_effort":"high","tools":[{"type":"function","function":{"name":"submit_verdict","parameters":{"type":"object","properties":{"verdict":{"type":"string"}}}}}],"tool_choice":{"type":"function","function":{"name":"submit_verdict"}}},
                temperature=1.0,max_output_tokens=64000)

class Journal:
    def __init__(self):
        self.events=[]; self.stopped=False; self.lock=threading.Lock()
    def assert_dispatchable(self):
        if self.stopped: raise DispatchStopped("stopped")
    def reserve(self, identity, **kw):
        with self.lock:
            rid=str(len(self.events)); self.events.append({"kind":"reservation","reservation_id":rid,"request_hash":kw["request_hash"]})
        return SimpleNamespace(reservation_id=rid)
    def settle(self,rid,usage=None,outcome="completed",**kw):
        event={"kind":"settlement","reservation_id":rid,"outcome":outcome}
        self.events.append(event)
        if outcome != "completed": self.stopped=True
        return event
    def stop(self,reason): self.stopped=True

def response():
    return {"id":"r","model":"gpt-5.6-luna","status":"completed","output":[
        {"type":"reasoning","id":"rs1","summary":[],"encrypted_content":"opaque-only"},
        {"type":"function_call","call_id":"c1","name":"submit_verdict","arguments":'{"reasoning":"visible reason","verdict":"reject"}'}],
        "usage":{"input_tokens":100,"output_tokens":10,"total_tokens":110,"input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":8}}}

def test_settings_and_non_strict_preserved():
    body=make_body(**kwargs())
    assert body["model"] == "gpt-5.6-luna" and body["reasoning"] == {"effort":"high"}
    assert body["max_output_tokens"] == 64000 and body["temperature"] == 1
    assert body["tools"][0]["strict"] is False
    assert body["tool_choice"] == {"type":"function","name":"submit_verdict"}
    assert body["include"] == ["reasoning.encrypted_content"] and body["store"] is False

def test_full_role_prose_calls_and_opaque_replay():
    message=decode_response(response()); message["content"]="visible prose"
    message.pop("provider_output_items")
    items=encode_input([{"role":"_runner_active_episode_start"},message,{"role":"tool","tool_call_id":"c1","content":"recorded"}])
    assert [i.get("type",i.get("role")) for i in items] == ["reasoning","assistant","function_call","function_call_output"]
    assert items[0]["encrypted_content"] == "opaque-only"
    assert items[-1]["call_id"] == "c1"

def test_interleaved_output_items_replay_untouched():
    raw=response()
    raw["output"] = [raw["output"][0],
        {"type":"message","id":"msg1","role":"assistant","phase":"commentary","status":"completed","content":[{"type":"output_text","text":"visible prose","annotations":[]}]},
        {"type":"reasoning","id":"rs2","summary":[],"encrypted_content":"second-opaque"},raw["output"][1]]
    message=decode_response(raw)
    assert encode_input([message]) == raw["output"]
    assert encode_input([message,{"role":"tool","tool_call_id":"c1","content":"recorded"}])[:-1] == raw["output"]
    # Outgoing replay is independent of caller mutation and no IDs/phases vanish.
    outgoing=encode_input([message]); outgoing[1]["phase"]="changed"
    assert message["provider_output_items"][1]["phase"] == "commentary"

def test_reserved_before_one_official_call_and_settled(tmp_path):
    journal=Journal(); calls=[]
    def handler(request):
        assert journal.events[-1]["kind"] == "reservation"
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer dummy-test-only"
        calls.append(request)
        return httpx.Response(200,json=response())
    transport=OfficialTransport(journal,tmp_path,"dummy-test-only",http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    transport.context(dyad_id="d1")
    transport.complete(**kwargs(),accept_tool_calls=True)
    assert len(calls)==1 and journal.events[-1]["outcome"] == "completed"
    assert all("dummy-test-only" not in p.read_text() for p in tmp_path.rglob("*.json"))

@pytest.mark.parametrize("kind",["timeout","bad_json","bad_details","write_failure"])
def test_ambiguous_processing_stops_without_retry(tmp_path,kind,monkeypatch):
    journal=Journal(); calls=[]
    def handler(request):
        calls.append(request)
        if kind == "timeout": raise httpx.ReadTimeout("timeout")
        if kind == "bad_json": return httpx.Response(200,content=b"no")
        raw=response()
        if kind == "bad_details": raw["usage"]["input_tokens_details"]=["invalid"]
        if kind == "write_failure":
            import experiments.induction_v3.transport as module
            monkeypatch.setattr(module,"atomic_json",lambda *a,**k: (_ for _ in ()).throw(OSError("disk")))
        return httpx.Response(200,json=raw)
    transport=OfficialTransport(journal,tmp_path,"dummy",http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception): transport.complete(**kwargs(),accept_tool_calls=True)
    assert len(calls)==1 and journal.stopped
    assert journal.events[-1]["outcome"] in {"unknown","ambiguous_timeout"}
    with pytest.raises(Exception): transport.complete(**kwargs(),accept_tool_calls=True)
    assert len(calls)==1

def test_same_hash_parallel_atomic_writes(tmp_path):
    path=tmp_path/"same.json"
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda i: atomic_json(path,{"i":i}),range(60)))
    assert json.loads(path.read_text())["i"] in range(60)
    assert not list(tmp_path.glob("*.tmp"))

def test_reflection_orphan_call_stops_after_accounting(tmp_path):
    journal=Journal()
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=response())))
    transport=OfficialTransport(journal,tmp_path,"dummy",http_client=client)
    with pytest.raises(DispatchStopped,match="unexpected_reflection_tool_call"):
        transport.complete(**kwargs(),accept_tool_calls=False)
    assert journal.stopped and journal.events[-1]["outcome"] == "completed"
