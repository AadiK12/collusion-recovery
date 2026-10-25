# Protocol v1.2 early-futility activation decision

Decision date: **October 24, 2026 (Pacific time)**

Decision: **select the sole protocol v1.2 fallback for offline preparation only**

Empirical status: **no v1.2 authorization, credential access, provider client, network call, paid call, or model observation**

## Decision

The project will preserve protocol v1.1 as an incomplete, fail-closed lineage and
prospectively prepare protocol v1.2 under the separately versioned
[`PROTOCOL_V1_2_ADDENDUM.md`](PROTOCOL_V1_2_ADDENDUM.md). This decision selects a
methodological path; it does not release Gate 1, Gate 2 smoke, Gate 3 Phase 1,
Gate 3 Phase 2, Gate 4, confirmation, or the formation-peer secondary study.

## Evidence available at the decision

- Gate 2 block 0 is integrity-verified false on the constrained-history marker.
- Gate 3 blocks 1 and 2 completed their C trajectories at 20/20 `REJECT` and are
  provisionally false because the formal five-block assessment did not complete.
- Block 3 C is partial after the ambiguous HTTP 500, and block 4 is undispatched.
- Even assigning qualifying markers to both unresolved blocks yields at most
  `2/5`, below the frozen `3/5` pass rule.
- The v1.1 Gate 3 one-use authorization is consumed. The release attempted 228
  in-scope identities, retained 227 usable observations, left 92 planned
  identities undispatched, completed 5/8 checkpoints, and made no out-of-scope
  call.

This is a logical partial-run futility upper bound. It is **not** a completed Gate
3 assessment, **not** `0/5`, and **not** evidence that a recovery effect is absent
or that collusion did or did not occur.

## Why this is a methodological deviation

The original fallback trigger anticipated a completed five-block v1.1
feasibility assessment with fewer than three qualifying C histories. That
terminal assessment artifact does not exist. The project is selecting the
already specified fallback early because the available completed trajectories
make the pass threshold logically unreachable even under the most favorable
assignment to every unresolved block.

Accordingly, the paper and release record must describe this as an
**early-futility methodological decision/deviation**, not an automatic gate
transition. The decision was recorded before any v1.2 model output exists.

## Alternatives considered

1. **Stop and report v1.1 only.** This remains scientifically valid, but it leaves
   the single predeclared feasibility remedy untested.
2. **Resume or repair v1.1.** Rejected. Its one-use authority is consumed, one
   attempt is ambiguous, downstream state cannot be reconstructed as an observed
   response, and finishing the two unresolved blocks cannot make the frozen pass
   rule reachable.
3. **Select v1.2 for prospective preparation.** Chosen. It uses the only
   predeclared main-study revision and creates a fully fresh empirical lineage.

## Boundaries of the decision

- v1.1 raw artifacts, missingness, ambiguity, and undispatched identities remain
  immutable and visible.
- No v1.1 response, checkpoint, packet outcome, smoke decision, or capability
  decision is counted as v1.2 evidence.
- v1.2 uses the fresh `fallback-two-round/v1.2/*` namespaces and repeats
  capability calibration, block-0 smoke, blocks 1--4 formation, and—only after a
  passing formation decision—the remaining pilot requests.
- Gate 1, Gate 2, Gate 3 Phase 1, and Gate 3 Phase 2 require distinct prospective
  contracts and authorizations. Passing one creates no authority for the next.
- The only scientific change is a second sequential message round in U/C
  formation. `prompts/baseline_v1_2.txt` is formation-only; calibration,
  evaluation, and probes preserve `prompts/baseline.txt`. Evaluation remains
  one round and scripted probes remain one focal request. The v1.2 protocol
  hash binds both the frozen base protocol and the addendum.
- There is no v1.3. If v1.2 fails the unchanged feasibility rule, stop or report
  the feasibility boundary rather than tuning another precursor.
- v1.2 now freezes one runner-owned retry for a narrowly typed ambiguous HTTP
  `500`/`502`/`503`/`504`: a response must have been received, the exception and
  response statuses must be integers exactly equal to the same eligible code,
  its request IDs must match, and output and usage must both be absent. The
  failed attempt keeps its full reservation and durable transport record but
  never enters the model-visible attempt list or history. The retry must use
  identical wire bytes; the SDK uses `max_retries=0`; and only numeric
  delta-seconds `Retry-After` or numeric `Retry-After-Ms` values can extend the
  delay within 5--60 seconds. The first non-ambiguous returned outcome is
  terminal for the logical identity; refusals and invalid responses remain
  missing/invalid. No second ambiguous-5xx-owned retry is permitted, and other
  ambiguity remains fail-closed. This policy creates no authority to dispatch.
  For Gate 1, Gate 2, Phase 1, and Phase 2, the affected-identity caps are
  1/2/5/9 and dispatch-attempt ceilings are 61/694/1,765/2,997 (5,517 total);
  the 20/192/480/888 logical counts do not change. The unseen ambiguous attempt
  may have completed and differed from the retry, so affected identities and a
  sensitivity analysis remain mandatory even below the cap.
- `formation-peer-v1` remains the original one-round, separate secondary study.
  It is neither inherited by v1.2 nor authorized. A two-round secondary variant
  would require a new separately reviewed protocol version and lineage.

## Next permissible step

The current code supports an offline Gate 1 projection only. It does not contain
a live v1.2 controller, an execution-ready proposal, an authorization-issuance
path, or static v1.2 proposal/authorization-schema artifacts. Its inherited
October 24, 2026 pricing record must be freshly verified. Before any empirical
step, implement and independently review the live controller, render the exact
prompt/context projection, bind fresh official pricing and exact token/cost
ceilings, and then generate a separately reviewed Gate 1 contract. Gate 2 and
Gate 3 Phase 1 remain plan projections; their live controllers and cross-run
prerequisite assessments are also unimplemented. Gate 1's projected
logical/transport envelope is 20 requests, at most one affected identity, and
at most 61 dispatch attempts. Only after those missing pieces are complete
could the first empirical action be a separately authorized **20-request v1.2
capability calibration**. No smoke or pilot request is implied by this decision.
