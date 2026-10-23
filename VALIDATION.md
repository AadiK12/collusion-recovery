# Offline validation record

Date: October 22, 2026, America/Los_Angeles. Runtime: Python 3.13.7.

**These checks validate infrastructure and deterministic controls, not empirical model behavior.** No live model calls or paid API calls were made.

## Verified commands and outcomes

| Check | Outcome |
| --- | --- |
| `make test` | 50 tests passed |
| `make validate` | All expected control rates recovered on 40 packets: 20 correct, 20 faulty |
| `make estimate` | Default two-block design: 320 reviews, 544 nominal scripted requests |
| `PYTHONPATH=src python3 -m collusion_recovery run --config configs/mock.toml --output runs/mock/offline-policy-verified` | Completed with 320 reviews, 544 requests, no invalid/missing outcomes, and zero paid calls |
| Independent `analyze_run` replay | Recomputed summary exactly matched the persisted JSON summary |
| `PYTHONPATH=src python3 -m compileall -q src tests` | Passed |
| Git ignore checks | Environment-secret paths, generated runs, and the preexisting temporary reference files are ignored |
| Git remotes | None configured; no push or commit performed |

## Safety and methodology regression coverage

- Deterministic ground truth including threshold boundaries and all three defect types over 100 seed cases.
- Claim-list length does not trivially encode the defect label in matched generator cases.
- Hidden evaluator metadata and full peer evidence do not enter constrained views.
- Packet views and branch histories cannot mutate their sources or siblings.
- Correction preserves current evidence; reset removes old history and then accumulates only new evaluation history.
- Current messages and verdicts are not exposed before their simultaneous-information boundaries.
- Overlength messages receive one retry; failed messages are not truncated or leaked to the peer.
- Invalid, refused, and missing verdicts remain unknown rather than becoming safe rejections.
- All main arms share the same challenge packets; peer probes preserve authoritative evidence.
- Block-level contrasts, sign conventions, degenerate bootstrap handling, and missingness bounds are tested.
- Mixed/mock-to-empirical analysis is rejected, corrupted artifacts are detected, existing run directories cannot be overwritten, and request caps produce explicit failed manifests.
- End-to-end runs for all three controls execute with socket/HTTP entry points patched to fail if used.
- Live/provider/spending settings are rejected before output creation, and an environment credential marker cannot enable paid execution.

## Inspectable local artifacts

The verified example is in `runs/mock/offline-policy-verified/`, including the manifest, input snapshots, raw responses, evaluator fixtures, checkpoints, scored reviews, and `summary.json`. Generated runs are intentionally untracked.

The earlier `runs/mock/offline-policy-smoke/` directory is retained as an initial debugging artifact: its first replay exposed the schedule tuple/list representation mismatch documented in `DECISIONS.md`. Use the `offline-policy-verified` run for the validated example.

## What remains unverified

Actual model capability, natural formation of joint violations, post-repair persistence, correction/reset effectiveness, and real peer-policy influence have not been tested. API integration, spending controls for real inference, transport retries, live resumption, and empirical confirmation remain separate future milestones.
