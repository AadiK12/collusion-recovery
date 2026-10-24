# Gate 1 to Gate 2 readiness boundary

> **Current boundary after the methodology amendment:** fresh main-only Gate 1 completed its 20-request capability calibration, passed the frozen threshold, and hard-stopped before smoke. The dedicated Gate 2 proposal and authorization schema are now generated, pass credential-free inspection, and match an independent deterministic rebuild and lineage audit. No Gate 2 authorization or run exists. Neither main release includes the separate formation-peer-v1 secondary study.

Status: **Gate 1 capability passed; Gate 2 proposal/schema generated and independently audited; still no-dispatch and experiment not started**.

The October 24 methodology pass first demonstrated the intended fail-closed behavior when the prior proposal no longer matched the amended protocol. The subsequent release pass reverified pricing, regenerated the proposal/schema, and passed credential-free inspection. After exact external authorization, the bounded release completed with terminal status `completed_gate1_passed_hard_stop`: 20/20 responses were schema-valid and audited, with 10/10 clean approvals and 9/10 faulty rejections. It used 20 attempts, 45,123 input tokens, 2,512 output/reasoning tokens, and `$0.1378975`. The report records zero preflight, smoke, or later-stage dispatches, `experiment_started: false`, and no authorization of a next stage.

The first hypothesis-bearing request is the block-0 smoke stage. Nothing in
the completed capability release sent one. The repository retains the retired v1.0
readiness projection, now implements a fresh calibration-only Gate 1 release,
and implements a separate v1.1 smoke-only Gate 2 controller. Each live release
requires its own external one-use authorization.

The selected [formation-peer-v1 secondary study](docs/FORMATION_PEER_CONTROLS.md), whose empirical inclusion remains conditional on a separate reviewed release,
has an isolated scripted implementation and mock validation only. It is not a
live controller, a new main A–F arm, or an extension of Gate 1/Gate 2 scope.
Main A–F, primary B−A, and the six evaluation-time peer cells remain unchanged.
The main-only Gate 1 release excluded this study and grants it no authority.

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

## Prior checked v1.1 Gate 1 snapshot — October 24, 2026

The dedicated `src/collusion_recovery/gate1_calibration.py` controller and
checked `configs/gate1_calibration_proposal.json` implemented the fresh
predecessor release. They schedule exactly 20 isolated `calibration/v1.1`
reviews, bind the sealed historical Stage 2 provenance, prevent reuse of
historical `calibration/v1` identities, consume a one-use authorization,
persist the calibration decision, and hard-stop before smoke.

At commit `0ddc34c`, `make test` passed 283/283 tests and credential-free
inspection returned `ready_awaiting_external_human_authorization` with
`experiment_started: false`. The inspection command was:

```bash
PYTHONPATH=src uv run --frozen python -m collusion_recovery.gate1_calibration inspect
```

That checked proposal bound the following exact prior state. Preserve these
values as dated evidence, not as a claim that the amended protocol has already
passed inspection:

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
owner-supplied authorization record. It is not itself authorization. At that
prior checked snapshot, no human authorization had been created, claimed, or
consumed and Gate 1 had made no provider call. The later completed release is
recorded in the next section.

## Completed post-amendment v1.1 Gate 1 release — October 24, 2026

The proposal and authorization schema were regenerated after the canonical
protocol reached its final amended bytes. Pricing was reverified at
`2026-10-24T14:02:52-07:00` against the official
[GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
whose Standard rates remain $2/M ordinary input, $0.10/M cached input,
$2.50/M cache writes, and $10/M output. Credential-free inspection first
returned `ready_awaiting_external_human_authorization`. The exact one-use
authorization was then consumed by the calibration-only release.

| Item | Value |
| --- | --- |
| Proposal hash | `bac585908d48427d9048f4cc5677b42e5c1892f4065fb123ed8388e4a1bd9ed4` |
| Implementation hash | `019fb0f4637e4e85c7cb70540c3697f37125569bbe82194dcad2244854b08279` |
| Protocol hash | `e6d1d8bc78b7d802e9e65506e2b3f13b4ff541d3f9d57f3117f1f306f57a41bf` |
| Pricing contract hash | `13975d13621c9e0636e89234b11658212fb368abd17a23170619d24e288b98bc` |
| Protocol bridge hash | `c7b573e46c655e3761bf83b5c3d4d596868a2c450ebc07a123d4c0a8655326c9` |
| Logical requests | 20 |
| Maximum dispatch attempts | 60 |
| Maximum total input tokens | 184,387 |
| Maximum total output/reasoning tokens | 81,920 |
| Per-request input/output limits | 9,331 / 4,096 |
| Maximum cost / concurrency | `$1.280168` / 1 |

Retain the prior snapshot as provenance. Any later edit to the bound protocol
or implementation must again fail closed and require fresh proposal/schema
generation and review. The schema is not authorization and the consumed Gate 1
authorization cannot be reused for Gate 2.

The run ID is `1b61ea2d-e609-4c67-a649-b889722dba9b`, and its durable run
directory is `runs/empirical/gate1-calibration-v1.1-1b61ea2d-e609-4c67-a649-b889722dba9b`; its embedded
report hash is
`6a5ca98d79b27be5ff71aa59d157cbd9cc236218098171d52f2d0511099321d8`.
The immutable assessment records 20/20 schema-valid reviews, 20/20 verified
request payloads, 20/20 verified wire payloads, 20/20 complete provider audits,
10/10 clean approvals, and 9/10 faulty rejections. The run consumed 20 attempts,
45,123 input tokens, 2,512 output/reasoning tokens, and `$0.1378975`. Its status
is `completed_gate1_passed_hard_stop`, with zero preflight, smoke, and
later-stage dispatches, `experiment_started: false`, and
`next_stage_authorized: false`. This supports only the narrow capability-gate
claim; it is not formation, recovery, peer-influence, or collusion evidence.

## Dedicated Gate 2 controller

At the prior checked checkpoint, `src/collusion_recovery/gate2_successor.py`
was reconciled to the main v1.1 design. Its main-only scope is exactly 152
block-0 smoke requests and at most 532 dispatch attempts. The controller binds
the empirical passed Gate 1 decision plus historical
Stage 2 provenance, rejects calibration/preflight replay, writes an immutable
smoke report, and hard-stops before any later formation, arm, probe, or
confirmation request.

The controller's reviewed scope excludes every formation-peer-v1 request.
Adding the secondary methodology to documentation does not add requests to
this inventory or grant permission to dispatch them. Any eventual main smoke
proposal must bind the actual amended protocol after the fresh Gate 1 pass;
the prior controller validation is not a replacement for that inspection.

The smoke report does not unlock later work. A separate no-dispatch
`record-smoke-decision` step requires a fresh external owner review, recomputes
the exact 152-result mechanical assessment, binds the Gate 1 lineage, smoke
report, and journal tail, and records one immutable smoke-integrity decision.
That decision authorizes no provider call by itself.

The checked `configs/gate2_smoke_proposal.json` and
`release/GATE2_HUMAN_AUTHORIZATION_SCHEMA.json` are now generated from the
durable fresh-calibration decision. Credential-free inspection returns
`ready_awaiting_external_human_authorization` with zero credential access,
network calls, or paid calls and `experiment_started: false`.

| Item | Current v1.1 value |
| --- | ---: |
| Proposal hash | `371e6e01b216bb62c1f2a7e77d40e85317585fcc44ff425534b8a14e1a9e3a27` |
| Successor run ID | `6abffb64-f71b-4868-89cb-28b0aff0c7e3` |
| Logical requests | 152 |
| Message / verdict identities | 76 / 76 |
| Potentially billable content shapes | 228 |
| Maximum dispatch attempts | 532 |
| Maximum total input tokens | 70,316,277 |
| Maximum total output/reasoning tokens | 933,888 |
| Per-request input/output limits | 582,556 / 4,096 |
| Maximum cost / concurrency | `$331.805585` / 1 |

The cost is the strict maximal-shape reservation ceiling, not expected spend.
An independent audit confirmed that both artifacts canonically match a fresh
offline rebuild, their Stage 2 and Gate 1 lineage, exact caps, schema constants,
and no-dispatch boundary. They still require a different unique, non-reusable
external human authorization before dispatch. The historical v1.0
numbers do not become v1.1 authorization merely because the logical-request and
maximum-attempt counts are also 152 and 532.

The legacy `python -m collusion_recovery gate2-readiness` command remains
retired and always fails closed. The ignored `gate2_readiness.json` remains
historical evidence only; do not overwrite, delete, or reuse it.

## Remaining gates

The current correct state is a passed fresh Gate 1 capability decision, an
independently audited Gate 2 proposal/schema, no Gate 2 authorization or run,
and `experiment_started: false`. The main-only sequence is now:

1. obtain a separate external human authorization for the exact reviewed caps;
2. run only the block-0 smoke and hard-stop;
3. record the external evidence-bound smoke-integrity decision; and
4. only then consider a separately authorized later-pilot release.

A successful smoke still does not unlock confirmation. The five-block pilot
must complete, and `pilot_freeze_assessment` must first be hardened to
reconstruct and validate the authoritative raw pilot request/response trail.
Only then may the human Gate-4 decision and immutable protocol freeze be used
to prepare a fresh confirmation release.

If the sole v1.2 fallback is activated later, it requires fresh v1.2-capable
Gate 1 and Gate 2 implementation review, proposals, decisions, caps, and
separate authorizations. No v1.1 decision or proposal carries forward.

## Separate formation-peer-v1 readiness boundary

The current command is a no-network scripted rehearsal with `source: mock`,
separate plan/source hashes, and artifacts that the main analyzer rejects. It
has no live-provider entry point or resume path. Successful mock validation
does not establish empirical readiness or authorize spending.

Each secondary block assigns the same focal role to two matched constrained
ten-episode histories: a scripted always-ACCEPT peer versus an always-REJECT
peer, both emitting the same neutral message. The current peer verdict is
revealed only after the focal verdict; the original shared score and individual
correctness feedback remain truthful. From each immutable history endpoint,
eight independent full-evidence single-verdict diagnostics use the same
neutral current message and no current peer verdict or correction. Each
diagnostic restarts its checkpoint, with the four own/peer truth combinations
represented twice. These are immediate diagnostics, not a sequential
eight-episode persistence test.

| Secondary scope | Per block | Five blocks |
| --- | ---: | ---: |
| Nominal logical requests | **56** | **280** |
| Scored focal reviews | **36** | **180** |
| Maximum scripted attempts with one optional overlength retry per message | **76** | **380** |

The 76-attempt number is a **scripted-only ceiling**, not a live dispatch or
transport-retry budget. A prospective live implementation must derive its own
full request/attempt, token/context, dollar, and retry envelope. The combined
main v1.1 five-block pilot plus five secondary blocks is **1,660 logical
requests**, including the main 20 calibration reviews and excluding the two
transport-preflight calls, retries, and separate release overhead. This is
planning arithmetic, not an executable combined release or authorization.

Before any live secondary request, a separate review must bind its run IDs,
config, protocol/version, exact plan, prompt/source/analysis hashes, every
assigned block seed, counterbalanced focal roles, seeded condition order,
split labels, prospective N/stopping rules, and its own unique authorization.
Earlier calibration may be relevant only if the model/settings/task are
unchanged and the prerequisite evidence and lineage are explicitly reviewed;
neither applicability nor reuse of an authorization is assumed. Consumed
Stage 2 authority and main-only Gate 1/Gate 2 authority cannot cover this study.

Every assigned secondary block is reported, including no-violation and
invalid/missing histories. A favorable secondary effect is not required for
main-study advancement. Secondary pilot and confirmation are separate, the
paired block is the independent unit, and samples are not enlarged until an
effect becomes favorable. Report formation episodes 2–10, the episode-1
pre-feedback negative control, and recovery FAR/CPA/balanced accuracy with
their own uncertainty and all-scheduled bounds.

The estimand is the total assigned peer-policy effect, including its truthful
reward/feedback pathway and subsequent transcript changes. It does not isolate
a pure peer-action effect, private intent, spontaneous negotiation, or
mediation of main B−A. Independent recovery diagnostics do not support late
persistence claims. The one-round secondary design is not automatically
compatible with the main v1.2 fallback: preserve its artifacts and make an
explicit prospective compatibility/version decision before further secondary
dispatch if that fallback activates.
