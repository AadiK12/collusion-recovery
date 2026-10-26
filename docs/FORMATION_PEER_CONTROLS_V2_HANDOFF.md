# Experiment-chat handoff: two-round formation controls

Prepared 2026-10-25 PDT. This package contains no empirical model results or live runner.

## Integration scope

Copy or cherry-pick only the five files below into the experiment checkout. All existing tracked files are unchanged; in particular no main controller, release, protocol, prompt, or original one-round control was edited. Recompute source bindings in the destination checkout because its main implementation is newer than this worktree.

| File | SHA-256 |
| --- | --- |
| `docs/FORMATION_PEER_CONTROLS_V2.md` | `3913e707ee5c53a55e7fb5460bb180bc580c9bee0892ad38d90bbf52419f661c` |
| `experiments/formation_peer_controls_v2.py` | `f59ab1acc936609aeffb4747a4a8ef530cdf03767b4773b8f6b0b777a17e4244` |
| `experiments/formation_peer_analysis_v2.py` | `7d44a201a96ae00a3a0c4d90266d9cc7cc073e60d3252ba7f11b249fc1d57e7e` |
| `tests/test_formation_peer_controls_v2.py` | `a6736739ed20ddac3f7365a92e37569120d2a63b87b5c989af564fb85c8b5046` |
| `tests/test_formation_peer_analysis_v2.py` | `b2b004564383a7d1e4a36f58f5e47ce39f5f7f3477a1ad6266c3b5379ec63383` |

## Verified offline results

- 46/46 formation-control tests pass (25 new two-round tests and 21 one-round regression tests), with networking disabled in mock execution.
- 15/15 existing two-round protocol tests pass. Python compilation passed.
- Five-block dry run completed 380 scripted requests and 180 focal reviews; 10 immutable checkpoints; zero paid calls. All 180 verdicts were valid.
- Offline plan hash: `3bd087f8e80ba0a0dc54b375fec2094f39c6eee60771f86709c202154c0534e9`.
- Offline source hash: `71b7b1c7d21564ae5f887003a8df346df7175ffa175730ffc35e5b80e74575b0`.
- These hashes bind the offline example only. The empirical run needs its own namespace, identity, source binding, and provider projection.

## Live adapter interface

`StudyConfig`, `build_plan`, and `build_block_design` provide the exact schedule and fixtures. `formation_request` consumes immutable history and completed `round_1` / `round_2` dictionaries; each completed round contains `own_message`, `peer_message`, and `own_message_attempts`. Use the exact neutral peer message supplied by the protocol. The request factory enforces the main two-round visibility frontier. `append_formation_episode` reveals the scripted peer verdict and truthful score only after the focal verdict. `recovery_request` constructs independent full-evidence diagnostics from the frozen endpoint.

The mock store and CLI deliberately do not accept a live provider. Reuse the scientific constructors in the experiment-owned audited provider controller. Independently validate inputs and rescore raw outputs. The current analysis entry point accepts only mock provenance; implement a separate audited empirical ingestion wrapper with the same fixed schedule, estimands, denominators, and missingness rules rather than relabeling artifacts.

The fixed addition is 380 nominal requests, 200 potential overlength retries, and 180 focal decisions across five paired blocks. Transport retry ceilings and cost/token bounds must be derived by the owner. Do not select blocks or run this only when the main formation marker passes. It is a separate estimate of peer-policy/feedback influence, not an extension of the main B−A sample.

## Timing and budget

Owner reports a safe sequential path: main run reaches terminal state, reconcile ledger, then bind this study to remaining shared funds. Do not delay main execution to merge these files into its already reviewed scientific lineage. The user has asked to prioritize these controls; prepare the live integration while main execution proceeds where possible. Conservative observed-throughput estimate: 20–45 minutes for the control, plus audits and writing. Official deadline: October 26 04:59 PDT.

User program ceiling remains $250. The owner reports conservative prior project spend of $14.549712. Resolve the exact parent-budget interpretation in the concrete release; do not create an additional $250 allowance.

## Paper integration contract

Describe the implemented methodology without internal version labels. Report this as a distinct paired controlled-peer study, with all five block effects and immediate diagnostic outcomes. A supported difference establishes assigned peer-policy/feedback influence in this setting. It does not isolate reciprocal intent, establish mediation of the main recovery effect, or exclude independent learning alongside social influence. Inspect the already selected main live/live transcripts separately for reciprocal proposals, responses, and compatible behavior. Keep scripted peer text out of spontaneous-agreement evidence. An inconclusive control means retain that uncertainty and narrow the collusion claim; an unrun or incomplete control is not a completed result.
