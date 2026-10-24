# Gate 1 to Gate 2 readiness boundary

> **Current boundary:** the checked fresh v1.1 Gate 1 proposal is ready for external human authorization; it has not executed. The dedicated Gate 2 controller is implemented, but no Gate 2 proposal or authorization schema may be generated until empirical Gate 1 passes. The historical v1.0 projection below remains audit evidence only and cannot authorize either release.

Status: **Gate 1 `ready_awaiting_external_human_authorization`; Gate 2 blocked pending an empirical passed Gate 1 decision; experiment not started**.

The first hypothesis-bearing request is the block-0 smoke stage. Nothing in
this preparation pass sent one. The repository retains the retired v1.0
readiness projection, now implements a fresh calibration-only Gate 1 release,
and implements a separate v1.1 smoke-only Gate 2 controller. Each live release
requires its own external one-use authorization.

## What was verified

The local Stage 2 evidence is bound to:

- authorization package hash
  `14971740751c8285597692c7f3c954c4ab10146af00a0bbc86e389f6cf4dfdff`;
- execution report hash
  `2c46fa0dccb7c7d51a06a7eb602c7243f342f1e66209cddc25abfb22e640a834`;
- journal tail
  `2b2e4f521a3712ce9868b38673225186dba66e402f10bb6e71bedaef630a7bdd`
  at 47 events;
- the exact saved config, plan, implementation, dispatch authorization,
  transport, and returned model identity;
- passing transport/schema and calibration gates; and
- zero smoke reservations or results.

The tracked self-hashed `configs/stage2_consumed_release.json` additionally
binds the package/report hashes, journal tail, 47 events, and 22 consumed
attempts. The Stage 2 command and generic paid-authorization validator both
fail closed for this run id before credential or provider-client access, even
when the ignored run directory is absent on a fresh clone.

The verifier rejects a changed report, config, plan, journal record, model
identity, gate result, authorization binding, or stop-before-smoke claim.

## Historical v1.0 exact smoke projection

The no-dispatch projection contains:

| Item | Value |
|---|---:|
| Logical requests | 152 |
| Message requests | 76 |
| Verdict requests | 76 |
| Permitted overlength-message retry shapes | 76 |
| Maximum billable attempt shapes | 228 |
| Permitted known-not-completed transport retries | 304 |
| Maximum new smoke dispatch attempts | 532 |
| Stage 2 attempts already consumed | 22 |
| Maximum cumulative attempts if the same ledger continues | 554 |
| Conservative input-bound sum across all shapes | 70,266,369 |
| Full output-token allowance across all shapes | 933,888 |
| Shapes whose conservative input bound exceeds 272K | 134 |
| Shapes exceeding the Stage 2 per-request input cap | 220 |
| Longest conservative input bound | 582,302 |
| Longest request plus output allowance | 586,398 of 1,050,000 context tokens |

The longest projection is the initial block-0 arm-A evaluation verdict for
Alice in episode 5. Its prompt hash is
`30d6e26ddaae72949cb7c7c292bc0fc482c706e5de6a7059e9d3f533afda4843`
and its wire-payload hash is
`0b4b349aed5c959c005d4e372c585affc457da644d1d1de561556f9fb1900e5f`.

Pricing was rechecked at `2026-10-23T21:44:27-07:00` against the official
[GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
That page lists $2/M ordinary input, $0.10/M cached input, $2.50/M cache
writes, and $10/M output, with prompts above 272K actual input tokens charged
at 2x input/cache rates and 1.5x output for the full request.

Applying those multipliers to the conservative bound of every projected
shape gives:

- `$267.684700` using the ordinary uncached-input rate; and
- `$331.585075` using the highest input/cache-write rate, which is the safer
  reservation ceiling.

These deliberately pessimistic numbers apply to all 228 potentially billable
content shapes, assume every optional message-content retry happens, every
prompt reaches its maximal synthetic bound, and every request uses its full
output allowance. The reusable budget ledger applies the >272K input/cache
2x and output 1.5x multipliers during both reservation and actual-usage
reconciliation. The 304 transport retries count toward the dispatch-attempt
cap but reconcile to zero tokens/dollars only when non-completion is proven.
These are **not** expected-spend estimates and do not authorize spending.

## Why the historical Stage 2 release cannot execute smoke

Stage 2 was correctly created as a one-shot release whose immutable config
ends at `calibration`. Its config hash is embedded in its immutable plan and
its checked-in consumed receipt makes the authorization non-replayable even
without the ignored run directory. The same config also caps requests at 67,
total input at 211,932, total output at 94,208, and per-request input at
9,353. The smoke proposal exceeds those limits.

The existing renewal validator permits only monotonic releases *within* the
immutable config. It therefore rejects either a `smoke` stage renewal or an
expanded limit set. Rewriting the Stage 2 config would change its hash and
break exact resume. Bypassing either check would weaken the provenance and
spending controls, so this pass did not add such a bypass.

The separate human artifact expected at
`runs/empirical/stage2-2026-10-23/gate2_human_authorization.json` is absent by
design. The readiness tool refuses to accept or act on a file at that path.

## Current Gate 1 release boundary

The dedicated `src/collusion_recovery/gate1_calibration.py` controller and
checked `configs/gate1_calibration_proposal.json` now implement the fresh
predecessor release. They schedule exactly 20 isolated `calibration/v1.1`
reviews, bind the sealed historical Stage 2 provenance, prevent reuse of
historical `calibration/v1` identities, consume a one-use authorization,
persist the calibration decision, and hard-stop before smoke.

Credential-free inspection returns
`ready_awaiting_external_human_authorization` with `experiment_started: false`:

```bash
PYTHONPATH=src uv run --frozen python -m collusion_recovery.gate1_calibration inspect
```

The checked proposal binds:

| Item | Value |
| --- | --- |
| Proposal hash | `a9a8e9a4b548afc8bd35de928498a4c6a60d7ed01e93be287bfe2cfed9fed3d7` |
| Implementation hash | `8b78157f5e14d6ca96f4bf6e2c6d0dab1415a0bc13a62e86d4ee66ee83115040` |
| Protocol hash | `ac54293fb2d7b077fc116b427669a6e635f6ad23f2a96f8af51b743207d723d1` |
| Logical requests | 20 |
| Maximum dispatch attempts | 60 |
| Maximum total input tokens | 184,387 |
| Maximum total output/reasoning tokens | 81,920 |
| Per-request input/output limits | 9,331 / 4,096 |
| Maximum cost / concurrency | `$1.280168` / 1 |

`release/GATE1_HUMAN_AUTHORIZATION_SCHEMA.json` specifies the external
owner-supplied authorization record. It is not itself authorization. No human
authorization has been created, claimed, or consumed, so Gate 1 has not made a
provider call.

## Dedicated Gate 2 controller

`src/collusion_recovery/gate2_successor.py` is reconciled to the current v1.1
design. It is restricted to exactly 152 block-0 smoke requests and at most 532
dispatch attempts, binds the empirical passed Gate 1 decision plus historical
Stage 2 provenance, rejects calibration/preflight replay, writes an immutable
smoke report, and hard-stops before any later formation, arm, probe, or
confirmation request.

The smoke report does not unlock later work. A separate no-dispatch
`record-smoke-decision` step requires a fresh external owner review, recomputes
the exact 152-result mechanical assessment, binds the Gate 1 lineage, smoke
report, and journal tail, and records one immutable smoke-integrity decision.
That decision authorizes no provider call by itself.

There is deliberately no checked `configs/gate2_smoke_proposal.json` or Gate 2
authorization schema yet. Both must be generated only after empirical Gate 1
passes so they bind the durable fresh-calibration decision rather than a
hypothetical result. They must then be independently reviewed and receive a
different unique, non-reusable external human authorization with exact
billable-attempt, input-token, output/reasoning-token, dollar, concurrency, and
context ceilings. The historical v1.0 numbers above do not become v1.1
authorization merely because the logical-request and maximum-attempt counts
are also 152 and 532.

The legacy `python -m collusion_recovery gate2-readiness` command remains
retired and always fails closed. The ignored `gate2_readiness.json` remains
historical evidence only; do not overwrite, delete, or reuse it.

## Remaining gates

The current correct state is Gate 1 authorization-ready, Gate 2 blocked pending
a passed fresh Gate 1 decision, and `experiment_started: false`. After an empirical
Gate 1 pass, the sequence is:

1. generate and independently review the source-bound Gate 2 proposal/schema;
2. obtain its separate external human authorization;
3. run only the block-0 smoke and hard-stop;
4. record the external evidence-bound smoke-integrity decision; and
5. only then consider a separately authorized later-pilot release.

A successful smoke still does not unlock confirmation. The five-block pilot
must complete, and `pilot_freeze_assessment` must first be hardened to
reconstruct and validate the authoritative raw pilot request/response trail.
Only then may the human Gate-4 decision and immutable protocol freeze be used
to prepare a fresh confirmation release.

If the sole v1.2 fallback is activated later, it requires fresh v1.2-capable
Gate 1 and Gate 2 implementation review, proposals, decisions, caps, and
separate authorizations. No v1.1 decision or proposal carries forward.
