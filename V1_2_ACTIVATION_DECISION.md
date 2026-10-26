# Protocol v1.2 early-futility activation decision

Decision date: **October 24, 2026 (Pacific time)**

Decision: **select the sole protocol v1.2 fallback and prepare one conditional master release**

Empirical status: **no exact v1.2 master authorization has been claimed and no v1.2 network call, paid call, or model observation exists**

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
- v1.2 uses one fresh run and one fresh descendant of
  `fallback-two-round/v1.2/pilot`. It repeats capability calibration, block-0
  smoke, blocks 1--4 formation, and—only after a passing formation
  decision—the remaining pilot requests.
- The October 25 prospective governance amendment replaces the earlier plan for
  four distinct v1.2 human authorizations with one exact, short-lived,
  owner-approved, single-use master authorization. The four internal slices
  retain independent non-fungible ceilings and automatic fail-closed gates.
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

## Unified prospective governance amendment

The four-stage authorization wording above and in earlier readiness snapshots is
superseded for v1.2 only. It remains historically correct for the executed v1.1
lineage. The unified v1.2 contract binds one ordered `20 + 192 + 480 + 888 =
1,580` logical-request plan under one run and namespace. Maximum dispatch
attempts are non-fungible by slice: `61 + 694 + 1,765 + 2,997 = 5,517`.

After calibration, the controller may release smoke only on the frozen
capability pass. After smoke, it may release Phase 1 only when an exhaustive,
deterministic, outcome-blind automatic audit verifies all 192 smoke identities,
payloads, raw evidence, timing/visibility, checkpoints, transport, provider,
scope, authorization, and budget invariants. After Phase 1, it may release the
888 Phase 2 requests only when at least three of five C histories satisfy the
frozen marker. A failed or indeterminate gate stops all later dispatch. A marker
failure after 692 requests is the completed feasibility result for this pilot,
not permission to tune or continue.

The exact shape-sum reservation envelope is `$5,032.461128`; it is a theoretical
worst-case projection, not expected spend and not spending authority. The live
contract instead enforces a `$250.000000` cumulative hard stop over actual
settled spend plus the next request's complete reservation. If the next request
cannot fit, the run stops before dispatch as `budget_limited` and may remain
scientifically incomplete.

Human transcript and claim audit is mandatory after the run and before
publication, but it is not an intermediate dispatch gate. The master release
excludes Gate 4, confirmation, publication, the formation-peer study, and every
secondary study. No v1.2 call has yet been made.

## Next permissible step

Finalize and independently verify the unified controller and static master
proposal, including exact source/protocol/proposal hashes. Then create the one
fresh, short-lived, external, single-use authorization bound to that exact
package. Until that exact authorization is claimed, credential access and every
v1.2 dispatch remain prohibited. Proposal generation, this decision, and prior
v1.1 approvals create no live authority.
