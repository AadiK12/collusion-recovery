# Formation-control coordination

Snapshot: 2026-10-26 03:48 UTC / 2026-10-25 20:48 PDT. Submission deadline: 2026-10-26 04:59 PDT.

## Current main-run state

- At 2026-10-25 20:28 PDT, the human prospectively reprioritized a controlled-peer implementation to run first. Subsequent clarification preserves **recovery** as the paper's central scientific question: controlled peer policy changes and validates how the problematic history is induced; it does not replace recovery as the endpoint.
- The superseding prospective design is integrated in commit `49c2459` (source handoff commit `0566010`): five controlled-history blocks, each with 60 two-round formation requests and 48 independent recovery diagnostics across A--F, for 540 hypothesis-bearing requests, plus a required fresh 20-request capability calibration (560 total). The exact offline schedule and analysis passed 14/14 focused tests in this checkout; live source is not yet frozen.
- The protocol-v1.2 live/live A--F recovery pilot is held as a separate optional extension, subject to time and verified post-control budget.
- The recovery implementation remains a frozen 1,580-request scope in 20 / 192 / 480 / 888 conditional slices, run ID `8c2d9470-8a24-4ee6-98ce-0aeabc439fe0`; it is being preserved rather than relabeled or pooled.
- Controller, release, and exhaustive smoke-audit implementation is still in final offline review. The first-call terminal-verification gap has been repaired and independently re-reviewed; the latest focused controller result is 45/45. The final release blocker is a real on-disk 1,580-request fake-controller rehearsal with crash/resume and terminal verification.
- No final v1.2 proposal, authorization schema, human authorization, receipt, run directory, credential access, SDK client, network call, paid call, or model observation exists. The reprioritization is entirely prospective; **zero paid original-study calls have started**.
- The annotation packet support is integrated in commit `f9cf8d4`; the remaining controller/release work is uncommitted and source hashes are not yet frozen.

## Safe integration boundary

The controlled-formation recovery study must remain separately identified. It must not change, pool with, or reuse the live/live A--F request identities, histories, checkpoints, gates, run ID, namespace, analysis population, or authorization receipt. Its precursor 380-request scientific package is integrated in commit `f7bbaed` (source handoff commit `a53e8c3`) and remains an audit record; it is superseded for live execution by the prospective 540-plus-20 schedule now being prepared.

Each block forms two matched focal histories under `peer_accept` and `peer_reject` with identical packets and neutral peer messages, then evaluates eight independent matched verdict diagnostics in each of six arms: A reject-peer/full/neutral; B accept-peer/full/neutral; C reject-peer/full/correction; D accept-peer/full/correction; E accept-peer/full/correction/history removed; F accept-peer/constrained/neutral. Formation episodes 2--10 provide the peer-dependence identification check. B--A is the controlled peer-policy-history contrast under full current evidence; B--F tests evidence restoration, D--B and C--A identify correction effects, and E--D tests history removal added to correction.

The control estimates the total effect of assigned peer policy plus truthful feedback. It does not by itself identify reciprocity. Any added matched control must be frozen in the control protocol before its own projection and authorization; it must not be inserted into the main pilot.

Provider execution should be sequential unless a shared aggregate ledger is implemented and audited. The revised deadline path is:

1. Finish the generic live-adapter mechanics while the exact 540-request controlled-history recovery schedule and 20-request capability calibration are frozen offline.
2. Rebind, project, test, freeze, audit, and authorize the resulting 560-request maximum release without outcome-informed changes.
3. Run it under its own run ID, namespace, proposal, authorization, receipt, ledger, artifacts, and analysis outputs.
4. Reconcile its actual spend against the historical program ledger.
5. Only if time and verified remaining budget permit, separately decide whether to authorize and run the preserved 1,580-request A--F recovery extension.

This preserves lineage and makes the shared ceiling enforceable without concurrent-ledger races. There is no pending original-study spend to reserve. The optional recovery extension receives no authority from the primary-study release.

## Control integration state

- The precursor 380-request design and offline implementation are frozen in `docs/FORMATION_PEER_CONTROLS_V2.md` and `experiments/formation_peer_controls_v2.py`; they will not be relabeled as the amended empirical schedule.
- The final offline scientific contract is `controlled-peer-recovery-v1`, documented in `docs/CONTROLLED_PEER_RECOVERY.md` and implemented by `experiments/controlled_peer_recovery.py` plus `experiments/controlled_peer_recovery_analysis.py`.
- Its five-block network-free mock completed 540 requests, 340 scored reviews, and 10 checkpoints with zero invalid verdicts or paid calls. The new 14/14 tests cover exact inventory and dependencies, matched inputs, correction/reset differences, evidence assignment, independent checkpoint restarts, missingness, bounded retries, and raw overflow retention.
- The offline plan/source hashes are examples only and will not be reused as empirical identities.
- Remaining work is to rebind the audited live adapter to the integrated constructors; add and gate the 20 calibration requests; derive the provider token/cost/attempt projection including all permitted retries; freeze a fresh run ID and namespace; finish durable-resume and independent empirical-ingestion tests; audit the exact package; and bind it to the conservative available program remainder.
- Expected sequence: adapter rebinding and offline E2E now; exact source/projection freeze, artifact audit, and hash-bound human acknowledgement next; then provider execution followed by integrity/analysis/paper handoff. The original recovery rehearsal is complete and does not block this work. Live start is blocked on adapter completion, projection, audit, and authorization.

## Budget state

- Upcoming recovery-extension ledger: `$0.000000` spent and no pending dispatch; it is held.
- Conservatively counting all recorded prior empirical runs against a lifetime-project $250 ceiling, tracked spend is `$14.549712` and the remaining amount is `$235.450288`. This includes Stage 2 (`$0.1500220`), Gate 1 (`$0.1378975`), Gate 2 (`$7.4959330`), and the stopped Gate 3 partial run (`$6.7658595`).
- The primary control release must bind a source cap no greater than the conservatively verified `$235.450288` remainder. After it terminates, any optional recovery extension must be rebound to the newly verified remainder; neither release may claim an additional $250 allowance.

## Observed empirical throughput

Durable journal dispatch windows from the prior empirical runs are:

| Run | Attempts | Provider window | Throughput | Recorded cost |
|---|---:|---:|---:|---:|
| Gate 1 calibration | 20 | 61.36 s | 19.6 attempts/min | $0.1378975 |
| Gate 2 smoke | 153 | 375.11 s | 24.5 attempts/min | $7.4959330 |
| Gate 3 partial formation | 228 | 473.84 s | 28.9 attempts/min | $6.7658595 |
| Combined | 401 | 910.31 s | 26.4 attempts/min | $14.3996900 |

These are provider-dispatch windows, not wall-clock project time, and later long-context requests may be slower. At the observed aggregate rate, 1,580 main requests are about 60 minutes of provider time and 380 control requests about 14 minutes; use a conservative 1--3 hours for the main run and 20--45 minutes for the control, plus projection, audits, retries, and packaging.

## Authorization boundary

The user's direction makes this controlled-history recovery implementation first and requests no new spending ceiling, while preserving recovery as the central question. It fixes the scientific inventory at 540 hypothesis-bearing requests plus a required fresh 20-request calibration, but does not yet bind the live run identity, provider projection, retry/attempt ceilings, remaining-budget allocation, or final source hashes. Once those are concrete and audited, the release needs its own exact hash-bound authorization. It receives at most the conservative `$235.450288` remainder under the existing shared $250 program ceiling, never an additional $250 allowance. The held 1,580-request extension would require a later separate exact authorization against whatever remains.
