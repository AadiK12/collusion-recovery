# Collusion recovery — offline research harness

This repository implements the offline foundation of [the sprint protocol](SPRINT_PROJECT_PLAN.md): deterministic audit packets, reciprocal review, controlled evidence access, immutable formation checkpoints, six recovery arms, and four scripted peer-probe cells.

**Everything currently runs with scripted controls. These outputs are not model results and do not demonstrate collusion. Paid API calls are unavailable, not merely turned off by default.** There is no API SDK/client, no credential loading, and no supported live provider in this build.

## Quick start

Requirements: Python 3.11+ and a source checkout. Tested locally with Python 3.13.7. The application and tests use only the standard library; no package installation, GPU, API key, or network access is needed. Commands below run from the repository root.

```bash
make test
make validate
make estimate
make mock
```

Without `make`:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m collusion_recovery validate
PYTHONPATH=src python3 -m collusion_recovery estimate --config configs/mock.toml
PYTHONPATH=src python3 -m collusion_recovery run --config configs/mock.toml
```

`run` chooses a new directory under `runs/mock/`, runs the protocol, independently replays its artifacts, and writes `summary.json`. It prints the path. To choose a path, use `--output runs/mock/my-run`; the directory must not already exist. Existing runs are never overwritten or silently resumed.

```bash
PYTHONPATH=src python3 -m collusion_recovery run --control always_accept
PYTHONPATH=src python3 -m collusion_recovery run --control always_reject
PYTHONPATH=src python3 -m collusion_recovery analyze --run-dir runs/mock/my-run
```

`analyze` prints a JSON summary after integrity checks and rescoring from raw responses. It does not trust stored derived correctness flags. If a request cap interrupted a run, `--allow-incomplete` reports scheduled missing observations and bounds; incomplete runs are otherwise refused. `--empirical` always refuses mock output.

## Implemented

| Area | What is present |
| --- | --- |
| Fixtures | Seeded 32-record selection tasks; clean, omitted-ID, extra-ID, and substituted-ID packets; exact sorted-set oracle; disjoint namespaces |
| Visibility | Explicit own/peer views; no oracle labels or evaluator metadata in agent requests; constrained peers see only specification and claim |
| Communication | Simultaneous-information message and verdict phases; 200-code-point limit; one overlength retry; no silent truncation |
| Histories | Immutable visible transcripts; checkpoint hashes; independent branches; full prior-history removal in E |
| Main experiment | U/C formation, ten episodes each; arms A–F, six paired challenge episodes each; balanced clean/faulty cases and roles |
| Peer probes | U/C × compliant/shortcut; twelve independent checkpoint-replay probes per cell; fixed evidence and explicit scripted-peer intervention |
| Controls | Evidence-policy oracle, always-accept, always-reject; 40-fixture validation matrix |
| Scoring | FAR, correct-packet approval, balanced accuracy, evidence/content violations, joint violations, formation marker; invalid responses never become rejections |
| Analysis | Paired-block contrasts and bootstrap; missing-data bounds; source separation; degenerate intervals flagged as uninformative |
| Audit | Predeclared schedule, evaluator fixtures, exact agent inputs, raw responses, scored reviews, checkpoint lineage, source hashes, Python version, and failed-run manifests |
| Safety | Live/provider/spending settings rejected before output creation; request-count cap; network-blocked integration tests; no automatic environment-key use |

The baseline evidence-policy control is a deterministic reviewer of the records in its actual input. It is **not** secretly passed evaluator truth. Without full evidence it rejects; with full evidence it computes the answer itself. `fixtures.select_records` is the evaluator truth function and is separately tested against hand-calculated examples.

## Expected control outcomes

`make validate` evaluates 20 correct and 20 faulty packets:

| Control | Evidence | False approval rate | Correct-packet approval |
| --- | --- | ---: | ---: |
| Evidence-policy oracle | Full | 0 | 1 |
| Evidence-policy oracle | Constrained | 0 | 0 |
| Always accept | Full | 1 | 1 |
| Always reject | Full | 0 | 0 |

These are expected scripted outcomes. In particular, always rejecting gets a zero false approval rate but fails the usefulness test. No scripted control is designed to manufacture a history-dependent recovery effect.

## Main-arm mapping

| Arm | History | Current evidence | Correction | Reset |
| --- | --- | --- | --- | --- |
| A | U, full evidence | Full | No | No |
| B | C, constrained | Full | No | No |
| C | U | Full | Yes | No |
| D | C | Full | Yes | No |
| E | C | Full | Yes | Yes |
| F | C | Constrained | No | No |

The primary contrast is `FAR_B − FAR_A`. Arms and conditions are analyst metadata, not model-visible labels. Main-arm verdicts are simultaneous; the peer probe intentionally makes the scripted peer's verdict observable first. Every probe restarts at the untouched formation checkpoint.

## Default run size

`configs/mock.toml` uses two independent blocks:

- 80 formation reviews, 144 main-arm reviews, and 96 focal peer probes.
- 320 scheduled reviews and 544 nominal scripted requests.
- No live calibration is included. Format retries, when deliberately exercised, add requests.
- Zero paid calls and zero API cost. Scripted calls have no provider token counts; missing token usage is not presented as measured token usage.

The later five-block live pilot in the plan would have 1,360 requests plus 20 calibration requests. **That live pilot is not implemented or authorized here.**

## Inspecting a run

| Artifact | Purpose |
| --- | --- |
| `manifest.json` | Source=`mock`, config, planned/observed counts, code/prompt/protocol hashes, Git status, runtime, and completion state |
| `schedule.json` | Seeded formation/arm/probe execution order |
| `scheduled_reviews.json` | Every expected outcome, written before requests; supports missingness accounting |
| `fixtures.jsonl` | Evaluator-only truth, defect type, full canonical packet, generator namespace, and hash |
| `inputs/<hash>.json` | Exact allowed agent input, including its visible transcript; hash-addressed |
| `requests.jsonl` | Append-only raw scripted responses, statuses, timings, input references, and retry attempts |
| `reviews.jsonl` | Derived scores tied to the raw verdict request |
| `checkpoints.jsonl` | Immutable formation endpoint transcripts and hashes |
| `branches.jsonl` | Parent/reset lineage, initial/final history hashes, and delayed end-of-sequence feedback |
| `summary.json` | Recomputed mock-only rates, paired contrasts, formation markers, and missingness warnings |

These logs contain only generated task data. They can still be large because the complete visible history is saved for each request. Runs are ignored by Git. Do not commit every generated run by accident; review any future release bundle explicitly.

To inspect a single decision, locate its `review_id` in `reviews.jsonl`, use `request_id` to locate the raw response, open the matching `inputs/<input_hash>.json`, and compare the packet with the evaluator fixture. The `analyze` command automates integrity checks and rescoring of this path.

## Repository guide

- `src/collusion_recovery/fixtures.py`: immutable data, deterministic generator, oracle.
- `visibility.py`, `history.py`: access boundaries and state isolation.
- `design.py`, `runner.py`: balanced schedule, arm definitions, and orchestration.
- `agents.py`, `scoring.py`: scripted agents, strict verdict schema, and independent scoring.
- `events.py`, `analysis.py`: persisted evidence and block-level analysis.
- `validation.py`, `tests/`: deterministic control checks and regression tests.
- `prompts/`: inspectable baseline, transition, correction, and peer intervention text.
- `PREEXISTING_WORK.md`, `DECISIONS.md`: disclosure and implementation choices.

Configuration is TOML rather than the plan's illustrative YAML, avoiding a parsing dependency. Runtime/package details are in [DEPENDENCIES.md](DEPENDENCIES.md).

## Deliberately not implemented yet

- OpenAI or any other live provider, secure credential setup, model selection, or paid calibration.
- Dollar/token reservations, provider transport retries, provider-specific schema configuration, or context-capacity preflight.
- Resuming a partially completed live run. A reused output directory is rejected instead of risking duplicate requests.
- Protocol freezing/preregistration commands, confirmation automation, plots, or a populated research paper.
- Live-model formation, history effects, or claims of collusion.

The current package is the offline foundation, not a fully validated empirical experiment. A future API adapter must be a separate explicit change after credentials, model choice, budget, and these controls are reviewed. No configuration toggle in this version can enable it.

## Pre-sprint status

The plan and this offline implementation were prepared on October 22, 2026, Pacific time. Record them as preexisting work in the final report. This is a verification-only adaptation, not a reproduction of the full SALT benchmark. No upstream source code was copied into this implementation. See the plan for related-work references and limitations.

Local Git has been initialized. No remote, push, public release, or sprint submission is performed by these commands.

See [VALIDATION.md](VALIDATION.md) for the verified test commands and the saved two-block mock run.
