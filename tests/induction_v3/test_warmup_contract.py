"""Offline checks against pinned source; never import a runner or provider package."""

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import runpy
from typing import Any
import unittest


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports/induction-v3/warmup"
VARIANT = ROOT / "experiments/induction_v3/variants/warmup"
SOURCE = ROOT / "vendor/salt_v3"
CONTRACT = runpy.run_path(str(VARIANT / "contract.py"))


def read_json(path):
    return json.loads(path.read_text())


def load_source_functions(path, names, namespace):
    """Compile only audited pure definitions, leaving all vendor imports unexecuted."""
    parsed = ast.parse((SOURCE / path).read_text())
    nodes = [node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError("Pinned source definition missing")
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    exec(compile(module, str(SOURCE / path), "exec"), namespace)
    return namespace


def render_template(value, replacements):
    if isinstance(value, str):
        for key, replacement in replacements.items():
            value = value.replace("{{" + key + "}}", str(replacement))
        return value
    if isinstance(value, list):
        return [render_template(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: render_template(item, replacements) for key, item in value.items()}
    return value


class WarmupContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Gate source-backed tests on public pinned bytes, not merely filenames.
        cls.source_index = read_json(REPORT / "SOURCE_INDEX.json")
        for item in cls.source_index["files"]:
            contents = (ROOT / item["path"]).read_bytes()
            blob = b"blob " + str(len(contents)).encode() + b"\0" + contents
            if hashlib.sha1(blob).hexdigest() != item["git_blob_sha1"]:
                raise AssertionError("Pinned source mismatch: " + item["path"])
        cls.recipes = {name: read_json(VARIANT / (name + ".json")) for name in ("warmup_raw", "warmup_summary")}
        cls.policies = load_source_functions(
            "experiments/prompts/policies.py",
            {"_verdict_basis_text", "verdict_policy_block", "verdict_policy_section"},
            {"BLOCK_HEADING_PREFIX": "###", "VERDICT_POLICY_HEADING": "Verdict policy"},
        )
        # These two pinned modules import only standard-library code.
        cls.spans = runpy.run_path(str(SOURCE / "experiments/episode_schedules.py"))
        cls.messages = runpy.run_path(str(SOURCE / "experiments/memory/messages.py"))
        cls.memory = load_source_functions(
            "experiments/memory/cross_episode.py",
            {"_first_remembered_index", "_retire_active_episode", "_trim_completed_episode", "set_cross_episode_memory_from_results"},
            {**cls.messages, "CROSS_EPISODE_MEMORY_SCOPES": ("feedback-and-reflection", "communication-onward", "full-history")},
        )
        cls.messaging = load_source_functions(
            "experiments/protocol/messaging.py", {"_send_message", "_reject_message"},
            {"Any": Any, "agent_state": lambda state, actor: state[actor],
             "peer_id": lambda actor: "bob" if actor == "alice" else "alice",
             "error_string": lambda kind, detail: kind + ": " + detail,
             "_log_event": lambda state, event: state["events"].append(deepcopy(event))},
        )
        cls.fixture = read_json(REPORT / "SYNTHETIC_FIXTURE.json")

    def transition_fixture(self, recipe_id):
        recipe = self.recipes[recipe_id]
        old = {agent: [deepcopy(self.fixture["system"])] for agent in ("alice", "bob")}
        warm_policy = recipe["warmup"]["policy"].replace("_", "-")
        for number in range(1, 4):
            for agent, peer in (("alice", "Bob"), ("bob", "Alice")):
                old[agent].append(self.messages["active_episode_start_message"]())
                old[agent].extend(render_template(self.fixture["episode_template"], {
                    "AGENT": agent.upper(), "EPISODE": number,
                    "POLICY_SECTION": self.policies["verdict_policy_section"](warm_policy, peer),
                }))
            before_retention = {agent: deepcopy(self.messages["model_visible_messages"](history)) for agent, history in old.items()}
            self.memory["set_cross_episode_memory_from_results"](old, scope="full-history", episode_id=str(number), max_memory_episodes_by_agent={"alice": -1, "bob": -1})
            for agent in old:
                self.assertEqual(self.messages["model_visible_messages"](old[agent]), before_retention[agent])
        before = {agent: deepcopy(self.messages["model_visible_messages"](history)) for agent, history in old.items()}
        after = deepcopy(before)
        policy = {}
        for agent, peer in (("alice", "Bob"), ("bob", "Alice")):
            policy[agent] = self.policies["verdict_policy_section"]("raw-only", peer)
            replacements = {"AGENT": agent.upper(), "POLICY_SECTION": policy[agent]}
            after[agent].append(render_template(self.fixture["main_task_template"], replacements))
            after[agent].append(render_template(self.fixture["main_communication_template"], replacements))
        return before, after, policy

    def test_recipe_plans_equal_published_source_spans(self):
        mapping = read_json(REPORT / "CONTRACT.json")["source_mapping"]
        for name, recipe in self.recipes.items():
            with self.subTest(recipe=name):
                plan = CONTRACT["phase_schedule"](recipe)
                source = mapping[name]
                policies = self.spans["expand_verdict_policy_spans"](source["verdict_policy_spans"], 13, source["verdict_policy"])
                throttles = self.spans["expand_throttle_spans"](source["throttle_spans"], 13)
                self.assertEqual([episode["policy"].replace("_", "-") for episode in plan], policies)
                self.assertEqual([episode["throttled"] for episode in plan], [state == "throttled" for state in throttles])
                self.assertEqual(plan[2]["phase"], "warmup")
                self.assertEqual(plan[3]["main_episode"], 1)
                self.assertTrue(all(item["policy"] == "raw_only" and item["message_char_limit"] == 200 for item in plan[3:]))

    def test_detects_original_interface_summary_cap_error(self):
        recipe = deepcopy(self.recipes["warmup_summary"])
        recipe["warmup"]["message_char_limit"] = None
        with self.assertRaises(ValueError):
            CONTRACT["validate_recipe"](recipe)

    def test_rejects_policy_leak_seed_or_release_overrides(self):
        for mutate in (
            lambda recipe: recipe["main"].update(policy="summary_allowed"),
            lambda recipe: recipe.update(seed={"kind": "synthetic"}),
            lambda recipe: recipe.update(model="different-model"),
            lambda recipe: recipe["warmup"].update(episodes=4),
        ):
            recipe = deepcopy(self.recipes["warmup_summary"])
            mutate(recipe)
            with self.assertRaises(ValueError):
                CONTRACT["validate_recipe"](recipe)

    def test_two_independent_dyads_and_main_only_denominator(self):
        contract = read_json(REPORT / "CONTRACT.json")
        self.assertEqual(contract["development_dyads_per_recipe"], 2)
        for recipe in self.recipes.values():
            scored = [item for item in CONTRACT["phase_schedule"](recipe) if item["include_in_main_metrics"]]
            self.assertEqual([item["main_episode"] for item in scored], list(range(1, 11)))
            self.assertEqual([item["absolute_episode"] for item in scored[-3:]], [11, 12, 13])
            self.assertEqual(2 * len(scored), 20)

    def test_pinned_main_tasks_match_extended_prefix_schedule(self):
        for repeat in (1, 2):
            filename = f"rep{repeat:03d}_sampled_manifest.json"
            main = read_json(SOURCE / "task/task_sequences_50x10" / filename)["pairs"]
            extended = read_json(SOURCE / "task/task_sequences_50x3+50x10" / filename)["pairs"]
            # Pinned manifests contain ordered objects with two task IDs each.
            self.assertEqual(len(extended), 13)
            self.assertEqual(extended[3:], main)
            warm_ids = {task for pair in extended[:3] for task in pair["tasks"]}
            main_ids = {task for pair in main for task in pair["tasks"]}
            self.assertFalse(warm_ids & main_ids)

    def test_original_raw_policy_restored_with_full_history_and_replay(self):
        for name in self.recipes:
            before, after, policy = self.transition_fixture(name)
            CONTRACT["assert_main_transition"](before, after, current_policy_text=policy)
            for agent in before:
                self.assertEqual(json.dumps(before[agent]), json.dumps(after[agent][:len(before[agent])]))
                self.assertEqual(sum("opaque_replay_fixture" in message for message in before[agent]), 3)
            if name == "warmup_summary":
                self.assertEqual(sum("Your verdict can be based on" in (m.get("content") or "") for m in after["alice"]), 3)
                self.assertNotIn("Your verdict can be based on", after["alice"][-1]["content"])

    def test_transition_rejects_history_loss_rewrite_role_change_or_new_system(self):
        for defect in ("drop", "rewrite", "role", "replay", "system", "stale_policy"):
            before, after, policy = self.transition_fixture("warmup_summary")
            if defect == "drop":
                after["alice"].pop(1)
            elif defect == "rewrite":
                after["alice"][4]["content"] = policy["alice"]
            elif defect == "role":
                after["alice"][1]["role"] = "assistant"
            elif defect == "replay":
                after["alice"][8].pop("opaque_replay_fixture")
            elif defect == "system":
                after["alice"].insert(-1, {"role": "system", "content": "Reset"})
            else:
                after["alice"][-1]["content"] = self.policies["verdict_policy_section"]("summary-allowed", "Bob")
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                CONTRACT["assert_main_transition"](before, after, current_policy_text=policy)

    def test_fixture_histories_are_private_and_dyads_independent(self):
        before, after, policy = self.transition_fixture("warmup_summary")
        independent, _, _ = self.transition_fixture("warmup_summary")
        before["alice"][8]["opaque_replay_fixture"]["payload"] = "mutated"
        self.assertNotEqual(before, independent)
        self.assertNotIn("BOB_PRIVATE_FIXTURE", json.dumps(after["alice"]))
        self.assertNotIn("ALICE_PRIVATE_FIXTURE", json.dumps(after["bob"]))
        self.assertFalse(self.fixture["empirical"])

    def test_pinned_delivery_uses_character_cap_not_truncation(self):
        for name, recipe in self.recipes.items():
            for episode in (CONTRACT["phase_schedule"](recipe)[0], CONTRACT["phase_schedule"](recipe)[3]):
                state = {"throttled": episode["throttled"], "char_limit": 200, "round": 0,
                         "alice": {"delivered": False, "inbox": []}, "bob": {"delivered": False, "inbox": []}, "events": []}
                oversized = "🙂" * 201
                result = self.messaging["_send_message"](state, "alice", oversized)
                if episode["throttled"]:
                    self.assertFalse(result["success"])
                    self.assertEqual(state["bob"]["inbox"], [])
                    self.assertEqual(state["events"][-1]["content"], oversized)
                    result = self.messaging["_send_message"](state, "alice", "🙂" * 200)
                    self.assertTrue(result["success"])
                    self.assertEqual(result["chars_sent"], 200)
                else:
                    self.assertTrue(result["success"])
                    self.assertEqual(state["bob"]["inbox"][0]["content"], oversized)
                self.assertFalse(self.messaging["_send_message"](state, "alice", "second delivery")["success"])


if __name__ == "__main__":
    unittest.main()
