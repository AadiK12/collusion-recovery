# Experiment launch status

Snapshot: 2026-10-26 03:59 UTC / 2026-10-25 20:59 PDT

## Frozen release scope

- Scientific question: whether independent verification recovers after a controlled accepting-peer history when evidence, correction, or prior-history visibility changes.
- Live release: `controlled-peer-recovery-v1`; five paired blocks; 20 capability-calibration verdicts followed, only on a passing gate, by the fixed 540-request hypothesis-bearing schedule.
- Nominal scientific inventory: 560 logical requests, 200 message identities, 360 verdict identities, 340 hypothesis-bearing scored decisions, and 10 formation checkpoints.
- Calibration gate: 20/20 schema-valid, at least 9/10 correct approvals, and at least 9/10 faulty rejections. Failure hard-stops before the study.
- Shared lifetime program ceiling: `$250.000000`; verified historical spend: `$14.549712`; maximum available remainder for this release: `$235.450288`.
- Execution shape: one sequential local process, one in-flight provider call, no block selection, no arm expansion, no confirmation run, and no original 1,580-request extension.

No API key has been read and no provider request has been dispatched for this release.

At the 21:01 PDT red-team snapshot, the concrete failures were:

1. `source_inventory()` references the not-yet-created `experiments/controlled_peer_recovery_empirical_analysis.py`, so an ordinary projection currently stops before release creation.
2. The offline transport's calibration parser assumes only one prose line before the JSON state. The actual prompt has two; the first unpatched fake run therefore stopped at calibration ordinal 0 with `JSONDecodeError` after one simulated request.
3. The independent verifier still rejects new `transport_binding`, `gate`, and `retry_schedule` events; calls the scientific replay with the old signature; and expects old completion statuses and artifact types. A completed or stopped new run therefore cannot yet return verified evidence.
4. The public empirical function still has the legacy `run_formation_peer_control_v2` name.
5. After those migrations, a fresh-live ordering mismatch remained: the runner verifies the pristine store before provider construction, but the verifier required a `transport_binding` that is written only when the controller starts. Without a narrow pristine-prefix exception or earlier durable planned binding, every first launch would stop before key access. A credential-sentinel test must exercise this exact path.

The known-not-completed retry off-by-one identified earlier is fixed in the latest working file. Items 1--4 are being rechecked after patches; item 5 is the newest launch blocker. The normal, unpatched path must pass before dispatch.

## Launch-critical checklist

| State | Exact requirement before first dispatch | Evidence or failure location | Owner |
| --- | --- | --- | --- |
| PASS | Frozen scientific schedule and analysis contract | `docs/CONTROLLED_PEER_RECOVERY.md`, `docs/CONTROLLED_PEER_RECOVERY_HANDOFF.md`, `experiments/controlled_peer_recovery.py`, `experiments/controlled_peer_recovery_analysis.py`; commit `49c2459`; 14/14 focused tests passed; offline scientific mock produced 540 requests, 340 reviews, and 10 checkpoints | Experiment owner |
| IN PROGRESS | Finish recovery-specific live adapter migration | `experiments/controlled_peer_recovery_live.py` still contains legacy runner/status names and its independent verifier is not yet fully rebound to calibration, gate, recovery artifacts, and all 560 identities | Live-controller owner |
| IN PROGRESS | Enforce the exact retry and attempt ceilings without an off-by-one or unverifiable stopped run | Adapter execution and verifier must agree on two known-not-completed retries per logical identity, one message revision, six ambiguous retry allowances, 766 maximum billable shapes, and 1,886 maximum physical attempts | Live-controller owner + red-team reviewer |
| IN PROGRESS | Make first claim and resume safe before credential access | Empirical runner must hold the program and run locks, re-read run state and the historical budget under lock, distinguish first-claim freshness from exact receipt-backed resume, repair only journal-derived manifest state, and verify any interrupted prefix before reading `.env.local` | Live-controller owner |
| IN PROGRESS | Prove the held original extension cannot consume this release's budget concurrently | Program-wide lock and live historical-manifest revalidation must cover reconciliation through terminal stop; original 1,580-request release remains non-authorizable and unlaunched | Live-controller owner |
| NOT RUN | Pass the minimum adversarial launch tests | Focused tests must cover capability failure before request 21, attempt/retry ceilings, exact receipt resume, manifest repair/fail-closed behavior, budget drift, and held-extension isolation | Live-controller owner + red-team reviewer |
| NOT RUN | Pass a complete production-shaped offline fake execution | Exact live controller and durable store must terminal-verify 560 logical requests, 340 study reviews, 10 checkpoints, correct gate order, raw request/wire/result replay, and zero paid calls | Live-controller owner |
| IN PROGRESS | Generate and validate the no-dispatch release package twice | Fresh run ID/namespace; deterministic plan, source, shape, pricing, transport, retry, budget-reconciliation, projection, proposal, and authorization hashes. The exact projection now succeeds with 560 logical requests, 760 base billable shapes, 766 maximum billable shapes, and 1,886 maximum physical attempts. Its deliberately loose aggregate reservation envelope is `$989.304938`, above the fixed remainder, so completion is not guaranteed; sequential execution must reconcile actual usage after every call and hard-stop at `$235.450288` rather than raise the ceiling. | Experiment owner |
| PENDING | Independent read-only reconciliation of the frozen package | The designated artifact-review chat checks exact paths, hashes, counts, ceiling, gate, provider binding, and absence of prior dispatch | Independent reviewer |
| IN PROGRESS | Bind the existing human scope authorization honestly to the immutable release | The latest direct human instruction authorizes launching the fixed 20+540 study within the existing ceiling. No direct human instruction requires personal review of final hashes; that was an agent-authored runtime choice and is not an additional human gate. The controller must preserve the human scope instruction verbatim, independently prove that the proposal implements exactly that scope and ceiling, and bind the resulting immutable hashes in a one-use machine receipt without claiming the human personally reviewed unseen hashes. | Experiment owner |

## Deferred until after data collection

- Additional general-purpose concurrency machinery beyond the single-process program lock.
- Broader fault-injection permutations not needed to establish the frozen sequential path.
- Optional documentation polish and unrelated regression suites.
- Any confirmation, extension, or redesign.

These items are not permitted to delay calibration once every launch-critical row above passes.
