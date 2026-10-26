import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace
import pytest

from experiments.induction_v3 import resumption as r, engine
from experiments.induction_v3.common import ROOT
from experiments.induction_v3.upstream import load


SOURCE = ROOT / "runs/empirical/induction-v3-continuation-97f9c0b4-0bcb-4125-97f9-431caee260e4"


def fixtures():
    release = json.loads((SOURCE / "release.json").read_text())
    proofs = {p["dyad_id"]: p for path in r.REPORTS.glob("replay-final-*.json")
              for p in [json.loads(path.read_text())]}
    return release, proofs


def test_plan_keeps_exact_eight_and_three_interrupted_paths():
    release, proofs = fixtures()
    plan = r.source_plan(release, SOURCE, proofs)
    assert len(plan) == 8
    assert sum(p["replay_required"] for p in plan) == 3
    assert sum(p["completed_prefix_episodes"] for p in plan) == 16
    assert not next(p for p in plan if p["dyad_id"] == "development-seeded-1")["replay_required"]
    with pytest.raises(ValueError, match="proof"):
        r.source_plan(release, SOURCE, {k: v for k, v in proofs.items() if "reference" not in k})
    wrong = copy.deepcopy(proofs); wrong["development-reference-1"]["episode"] += 1
    with pytest.raises(ValueError, match="proof"):
        r.source_plan(release, SOURCE, wrong)


def test_resume_carries_prior_six_and_runs_only_remaining_four(tmp_path, monkeypatch):
    load()
    release, proofs = fixtures()
    recipe = next(x for x in release["recipes"] if x["recipe_id"] == "reference")
    schedule = release["schedules"]["development"][0]
    release.update(plan=r.source_plan(release, SOURCE, proofs), run_directory=str(tmp_path),
                   source_run=str(SOURCE), run_id="test-resumption",
                   replay_proofs={k: {"path": str(r.REPORTS / ("replay-final-" + k.removeprefix("development-") + ".json"))} for k in proofs})
    prior = json.loads((SOURCE / "dyads/development-reference-1.json").read_text())
    contexts, replayed, executed = [], [], []
    class Transport:
        def context(self, **metadata): contexts.append(metadata)
        @contextmanager
        def episode_replay(self, run, dyad, proof, *, histories):
            assert histories == {a: prior["episodes"][-1]["agents"][a]["messages"] for a in ("alice", "bob")}
            replayed.append(proof["episode"])
            yield
    def episode(**kwargs):
        executed.append((kwargs["config"].episode_index, kwargs["pair"]))
        return {"agents": {}, "simulated": kwargs["config"].episode_index}
    monkeypatch.setattr(engine, "metrics", lambda value: {"joint_unsupported_acceptance": False})
    result = r.run_dyad(recipe, schedule, phase="development", release=release, transport=Transport(),
                        episode_module=SimpleNamespace(run_one_episode=episode))
    assert result["status"] == "completed"
    assert result["episodes"][:6] == prior["episodes"]
    assert [i for i, p in executed] == [6, 7, 8, 9]
    assert [p for i, p in executed] == schedule["main"][6:]
    assert replayed == [7]
    assert all(c["resume_run_id"] == "test-resumption" for c in contexts)
    assert result["carried_prefix"]["new_observations"] is False
    assert json.loads((SOURCE / "dyads/development-reference-1.json").read_text()) == prior


def test_failed_replay_does_not_append_a_new_episode(tmp_path, monkeypatch):
    load()
    release, proofs = fixtures()
    recipe = next(x for x in release["recipes"] if x["recipe_id"] == "warmup_summary")
    schedule = release["schedules"]["development"][0]
    identity = "development-warmup_summary-1"
    prior = json.loads((SOURCE / "dyads" / (identity + ".json")).read_text())
    release.update(plan=r.source_plan(release, SOURCE, proofs), run_directory=str(tmp_path),
                   source_run=str(SOURCE), run_id="test-resumption",
                   replay_proofs={identity: {"path": str(r.REPORTS / "replay-final-warmup_summary-1.json")}})
    class Transport:
        def context(self, **metadata): pass
        @contextmanager
        def episode_replay(self, *args, **kwargs):
            raise ValueError("mismatch")
            yield
    def unexpected(**kwargs): raise AssertionError("must not execute episode")
    with pytest.raises(ValueError, match="mismatch"):
        r.run_dyad(recipe, schedule, phase="development", release=release, transport=Transport(),
                   episode_module=SimpleNamespace(run_one_episode=unexpected))
    result = json.loads((tmp_path / "dyads" / (identity + ".json")).read_text())
    assert result["status"] == "stopped" and result["episodes"] == prior["episodes"]
    assert result["incomplete_histories"] == {a: prior["episodes"][-1]["agents"][a]["messages"] for a in ("alice", "bob")}


def test_warmup_schedule_is_carried_in_order_and_seed_has_no_warmup():
    release, _ = fixtures(); schedule = release["schedules"]["development"][0]
    for recipe in release["recipes"]:
        jobs = r.episode_jobs(recipe, schedule)
        count = recipe["warmup"]["episodes"]
        assert jobs[:count] == [("warmup", p) for p in schedule["warmup"]][:count]
        assert jobs[count:] == [("main", p) for p in schedule["main"]]


def test_frozen_tokenizer_loads_offline_and_rejects_vocabulary_drift(monkeypatch):
    assert r.load_encoding().name == "o200k_base"
    monkeypatch.setattr(r, "TOKENIZER_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="vocabulary drift"):
        r.load_encoding()
