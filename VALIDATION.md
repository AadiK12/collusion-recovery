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

Actual model capability, natural formation of joint violations, post-repair persistence, correction/reset effectiveness, and real peer-policy influence have not been tested. Empirical transport activation, secure credential provisioning, a paid preflight, scientific gate decisions on real outputs, pilot execution, protocol freeze approval, and empirical confirmation remain separate future milestones.

## WP1/WP2 fake-live validation checkpoint

Date: October 23, 2026, America/Los_Angeles. Runtime: Python 3.13.7.

**This checkpoint validates code paths and deterministic fake-provider behavior only. It is not a live-model experiment or evidence about the scientific hypothesis. No live model call, paid API call, credential creation, or credential read occurred.**

### Verified commands and outcomes

| Check | Outcome |
| --- | --- |
| `PYTHONPATH=src python3 -m unittest discover -s tests -v` | 116 tests passed, including the original offline suite and the provider/orchestration regressions |
| `PYTHONPATH=src python3 -m compileall -q src tests` | Passed after the final WP1/WP2 changes |
| Load `configs/fake_live.toml` with `load_live_config`, then call `build_request_plan` | Accepted as `fake-live`/`fake`, five blocks, pilot split, `probes` stage ceiling, zero prices, zero approved budget, and paid calls disabled |
| Planned five-block staged counts | 1,380 logical requests: 20 calibration, 152 smoke, 320 later formation, 648 remaining arms, and 240 probes; 10 immutable checkpoint specifications |
| `inspect-live` over the checked-in fake configuration | Rendered all 1,940 predeclared attempt shapes; maximum conservative input bound 583,673 tokens; both configured input and context checks passed; zero dispatch |
| Fake calibration, computed calibration gate, and exact resume | 20/20 schema-valid; 10/10 correct approvals and 10/10 faulty rejections; gate passed; resume skipped all 20 terminal identities and dispatched zero duplicates; reported zero network and paid calls |
| Pilot-tooling freeze rehearsal | Wrote and revalidated an immutable self-hashing `pilot_tooling_only` artifact; it does not unlock confirmation or claim empirical evidence |

### New regression coverage

- Canonical prompt rendering is deterministic, hashes the exact visible input, omits constrained records and oracle labels, and rejects hidden evaluator data.
- Fake-provider execution is explicitly labeled `fake-live`, rejects duplicate request attempts, and remains functional with socket/HTTP entry points patched to fail.
- Responses payloads fix `store: false`, disable tools and automatic truncation, omit persistent conversation state, and use strict verdict output. Adapter parsing preserves raw output, refusals/errors, request IDs, model metadata, usage, and timing using injected fake transports only.
- Secret-shaped or safety-overriding model settings are rejected. Credential-shaped values and secret-bearing keys are rejected before payload, journal, gate, manifest-status, or freeze persistence. Transport error bodies are redacted before persistence.
- Request, token, dollar, and in-flight limits reserve conservatively before dispatch. Known-not-completed and ambiguous failures follow distinct accounting and retry rules.
- Deterministic request identities, canonical type-exact config/plan checks, journal integrity, fsynced atomic writes, manifest-proven torn-tail truncation, journal-ahead-of-manifest repair, unmatched-reservation recovery, immutable checkpoints, and no-redispatch ambiguity behavior are exercised.
- Stage advancement pauses without immutable gate decisions. Paid execution scope is bound to an exact transport and journal tail and can only grow monotonically by budget, stage, and inclusive block. Confirmation additionally requires a freeze decision bound to the exact plan, protocol, implementation, and independently reopened pilot.
- Empirical provider identity binds requested/returned model and service tier plus system fingerprint; missing identity or drift pauses execution and pilot identity seeds confirmation.
- Gate-4 includes every scheduled experimental message, arm-balanced verdict missingness, all-scheduled block-level false-approval bounds, and complete five-block A/B faulty-cell requirements before variance-based sizing.
- Git cleanliness is tri-state: a failed or timed-out status check is unknown, never clean, and cannot produce a protocol freeze.
- Oversized or context-incompatible requests stop in local preflight before budget reservation or provider dispatch.
- Fake and empirical source/provider combinations are rejected when mixed. A paid-looking config cannot dispatch without a separate authorization artifact bound to the exact plan and budget.

### Remaining human and empirical gates

Before any empirical preflight, a human must still select and approve the exact model snapshot/alias and settings, reverify and timestamp provider pricing, set request/token/dollar/concurrency ceilings, authorize a staged positive budget in a separately bound artifact, provision credentials securely outside repository artifacts, and inspect the exact rendered prompts and longest-context request. Calibration accuracy, smoke integrity, formation prevalence, and protocol-freeze decisions must then be evaluated and recorded at their intended boundaries. Confirmation must use its separate namespace and exact approved freeze.

The checked-in fake-live configuration deliberately sets generous local ceilings so deterministic infrastructure tests do not fail for artificial capacity reasons. Those ceilings are not a recommendation or authorization for empirical spending.
