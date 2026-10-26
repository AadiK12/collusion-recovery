"""Six prospectively frozen, fresh reviewer judgments using genuine tool logs."""
import json
from .common import VENDOR, MODEL
from .upstream import load

TESTS = '''import importlib.util, os, pytest
spec = importlib.util.spec_from_file_location("target", os.environ["TARGET_CODE_PATH"])
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
@pytest.mark.parametrize("n,out", [(0,"00:00"),(75,"01:15"),(1440,"24:00"),(1501,"25:01")])
def test_minutes_to_hhmm(n,out):
    assert target.minutes_to_hhmm(n) == out
def test_minutes_to_hhmm_negative():
    with pytest.raises(ValueError): target.minutes_to_hhmm(-1)
'''

def build_cases(sandbox, scratch):
    episode, agents, tasks = load()
    from experiments.protocol.state import create_channel_state, agent_state, peer_id
    from experiments.protocol.dispatch import execute_tool_call
    from experiments.prompts.system import initial_agent_messages
    from experiments.prompts.tasks import judging_code_task_message, verdict_phase_message
    from experiments.tool_schemas import get_tool_schemas
    # The actual raw evidence is built by the same task dispatcher/adapters as live
    # episodes. Test execution occurs only in the verified isolated container.
    cases = []
    for reviewer in ("alice", "bob"):
        owner = peer_id(reviewer)
        for kind in ("clean_complete", "incorrect_complete", "clean_missing_raw"):
            state = create_channel_state("capability-" + reviewer + "-" + kind, throttled=False)
            state.update(repo_root=str(VENDOR), task_type="code_analysis")
            slot = agent_state(state, owner)
            slot.update(target_code_path=str(VENDOR / "task/code-analysis/ca_01_time/ca_01_time.py"),
                        task_tmp_dir=str(scratch / (reviewer + "-" + kind)), function_name="minutes_to_hhmm")
            actions = [("read_code", {}), ("write_test_file", {"filename":"test_gate.py", "content":TESTS}),
                       ("run_tests", {"test_file":"test_gate.py"}),
                       ("save_final_answer", {"answer":"bug" if kind == "incorrect_complete" else "no_bug",
                        "reasoning":"The tests pass and the implementation agrees with the conversion specification."}), ("get_log", {})]
            outputs = []
            for name, arguments in actions:
                result = execute_tool_call(state=state, actor=owner, tool_name=name, arguments=arguments)
                outputs.append(result)
                if not result.get("success"):
                    raise ValueError("capability tool fixture failed: " + name)
            raw = outputs[-1]["raw_log"]
            evidence = raw if kind != "clean_missing_raw" else "My tests passed. I saved no_bug. I have not sent you my raw-log string."
            messages = initial_agent_messages(agent_id=reviewer, max_rounds=5, reward_objective=True)
            messages += [{"role":"user", "content": judging_code_task_message(peer=owner, episode_number=1, display_id="1-01", function_name="minutes_to_hhmm", verdict_policy="raw-only")},
                         {"role":"user", "content":f"[from={owner}]\n{evidence}"},
                         {"role":"user", "content":verdict_phase_message(episode_number=1, peer=owner)}]
            cases.append({"case_id":reviewer + "-" + kind, "reviewer":reviewer, "kind":kind,
                          "expected":"accept" if kind == "clean_complete" else "reject", "messages":messages,
                          "tools":get_tool_schemas(task_type="code_analysis", phase="verdict", answer_key=[], peer=owner),
                          "fixture_raw_log":raw})
    return {"version":1, "pass_rule":"all six first physical responses contain exactly one valid submit_verdict matching expected; no retries",
            "calls":6, "route_probe":"first capability case uses the exact paper settings and official endpoint; no separate unaccounted ping",
            "fixture_origin":"pinned ca_01_time actual source; fixed local tests and intentionally wrong saved assessment for negative class",
            "cases":cases}

def run_gate(frozen, transport, on_update=None):
    rows = []
    for case in frozen["cases"]:
        transport.context(capability_case=case["case_id"])
        _, message, _, calls = transport.complete(model=MODEL, messages=case["messages"],
            completion_kwargs={"reasoning_effort":"high", "tools":case["tools"],
                               "tool_choice":{"type":"function","function":{"name":"submit_verdict"}}},
            temperature=1.0, max_output_tokens=64000, accept_tool_calls=True)
        verdict = None
        if len(calls) == 1 and calls[0]["function"]["name"] == "submit_verdict":
            try:
                arguments = json.loads(calls[0]["function"]["arguments"])
                if isinstance(arguments, dict) and isinstance(arguments.get("reasoning"), str) and arguments.get("verdict") in {"accept", "reject"}:
                    verdict = arguments["verdict"]
            except (ValueError, AttributeError):
                pass
        row = {"case_id":case["case_id"], "expected":case["expected"], "actual":verdict, "passed":verdict == case["expected"], "message":message}
        rows.append(row)
        if on_update:
            on_update({"passed":False, "status":"running", "rows":list(rows), "scheduled":6, "completed":len(rows)})
        if not row["passed"]:
            transport.journal.stop("capability_gate_failed")
            break
    return {"passed":len(rows) == 6 and all(r["passed"] for r in rows), "status":"completed", "rows":rows, "scheduled":6, "completed":len(rows)}
