# Protocol v1.2 addendum — two-round formation fallback

Status: **prospectively selected and consolidated into one conditional master-release design on October 25, 2026; no exact master authorization has been claimed and no v1.2 provider call has been made.**

This addendum versions the sole fallback already specified in
[`SPRINT_PROJECT_PLAN.md`](SPRINT_PROJECT_PLAN.md). It does not rewrite the
frozen v1.1 protocol or change the interpretation of any v1.1 observation. The
reason for selecting this preparation path, and the deviation from the original
completed-gate trigger, is recorded separately in
[`V1_2_ACTIVATION_DECISION.md`](V1_2_ACTIVATION_DECISION.md).

## 1. Scope of the revision

Protocol v1.2 changes **only U/C formation history construction**. It does not
replace the v1.1 baseline outside formation. Calibration, A--F evaluation, and
scripted probes continue to use `prompts/baseline.txt` byte for byte;
`prompts/baseline_v1_2.txt` is used only for formation requests and encodes the
two-round formation interface. The canonical v1.2 protocol hash is therefore a
composite binding over both the frozen base protocol
`SPRINT_PROJECT_PLAN.md` and this addendum, not the addendum alone.

The formation-only revision is:

1. Each formation episode has two sequential message rounds rather than one.
2. Alice and Bob generate round-1 messages simultaneously from the same
   pre-round state and cannot see the other's round-1 message while generating
   their own.
3. Both tagged round-1 messages are delivered before round 2.
4. Alice and Bob generate round-2 messages simultaneously. Each round-2 request
   sees both round-1 messages but neither current round-2 message.
5. Both tagged round-2 messages are delivered before either verdict request.
6. Alice and Bob then generate verdicts simultaneously, without seeing the
   other's current verdict. Verdicts and mechanical formation feedback are
   revealed only after both verdicts are recorded.

Each message remains capped at 200 Unicode code points. Formation therefore uses
six logical model requests per paired episode: two round-1 messages, two round-2
messages, and two verdicts.

Everything else remains as specified for the main study:

- formation still uses ten all-correct paired episodes per U or C trajectory;
- U supplies full peer evidence and C withholds the required peer records;
- the mechanical score, verdict feedback, evidence rule, marker, block, task
  family, model-selection rule, and missingness rules do not change;
- main A--F evaluation remains one message and one verdict per agent, or four
  logical requests per paired episode;
- each scripted evaluation-time peer probe remains one focal request;
- the six main arms, peer-policy cells, estimands, thresholds, and claim rules do
  not change; and
- the block remains the independent sampling unit.

No additional communication round, reflection call, memory summary, hidden
state carrier, task family, model search, favorable-history filtering, or
outcome-conditioned sampling is permitted. If the v1.2 five-block feasibility
gate does not pass, the main study stops or reports the feasibility boundary.
There is no v1.3 tuning path.

## 2. Fresh lineage and namespaces

All v1.2 packets, schedules, request identities, checkpoints, seeds, runs,
proposals, decisions, and authorizations are fresh. The complete pilot uses one
new run and one new namespace below `fallback-two-round/v1.2/pilot`; calibration,
smoke, Phase 1, and Phase 2 remain distinguishable by immutable stage and request
identities inside that run. A possible later confirmation remains a different
future lineage:

| Purpose | Namespace root |
| --- | --- |
| Unified conditional pilot, including calibration | a fresh descendant of `fallback-two-round/v1.2/pilot` bound in the final proposal |
| Possible later confirmation | `fallback-two-round/v1.2/confirm` |

Any stage-specific subnamespace must be a descendant of the relevant root and
must remain disjoint from every `calibration/v1.1`, `pilot/v1.1`,
`confirm/v1.1`, Stage 2, fake-live, mock, and formation-peer identity. A v1.1
packet, response, checkpoint, seed outcome, smoke decision, or partial trajectory
cannot fill a v1.2 cell. v1.1 and v1.2 observations are never pooled in an
estimator, interval, gate, figure, or headline.

## 3. Exact staged inventory

The complete v1.2 pilot contains 1,580 nominal logical requests, including its
fresh calibration. Counts exclude transport preflight, transport attempts that
are not new logical observations, and any separately versioned secondary study.

| Internal release slice | Exact content | New logical requests | Cumulative v1.2 logical requests | Automatic boundary |
| --- | --- | ---: | ---: | --- |
| Offline foundation | Implementation, fixtures, dependency/visibility tests, inventory, and contract inspection | 0 | 0 | No credentials, client construction, network, or provider calls |
| Gate 1 | 20 isolated full-evidence capability reviews | 20 | 20 | Continue only if the frozen capability rule passes |
| Gate 2 block-0 smoke | Block 0 U/C formation: `2 × 10 × 6 = 120`; A/B/F evaluation: `3 × 6 × 4 = 72` | 192 | 212 | Continue only if the exhaustive outcome-blind automatic integrity audit passes |
| Gate 3 Phase 1 | Blocks 1--4 U/C formation only: `4 × 2 × 10 × 6 = 480` | 480 | 692 | Continue only if at least three of five C histories meet the frozen marker |
| Gate 3 Phase 2 | Block-0 C/D/E plus probes: `72 + 48 = 120`; blocks 1--4 A--F plus probes: `576 + 192 = 768` | 888 | **1,580** | Complete pilot; hard-stop before Gate 4, confirmation, or any secondary study |

Across the complete five-block pilot, formation contributes 600 logical
requests, the six main arms contribute 720, the evaluation-time peer probes
contribute 240, and calibration contributes 20.

The Gate 3 Phase 1 decision uses every scheduled v1.2 C trajectory, including
the retained block-0 smoke trajectory. The unchanged pass rule is at least three
of five C histories meeting the last-three-episode marker. A missing final-three
window does not qualify. If the rule fails, Phase 2 is not dispatched.

A later confirmation, if justified and prospectively frozen, contains 312
nominal logical requests per block: 120 formation, 144 A--F evaluation, and 48
peer probes. Thus 20 and 43 blocks would contain 6,240 and 13,416 requests,
respectively. These are planning counts, not authorization or a commitment to a
confirmation sample size.

## 4. Unified release and authorization sequence

This section is the prospective October 25 governance amendment. It supersedes
the earlier v1.2 plan for four separate human authorizations; it does not alter
the frozen v1.1 protocol or any completed v1.1 release. The complete v1.2 pilot
is governed by one exact, short-lived, owner-approved, single-use master
authorization bound to one static proposal, one run, one namespace, the full
ordered 1,580-request plan, all stage ceilings, the source and protocol hashes,
model/settings, pricing, and the cumulative operational hard stop. The proposal
and schema are not themselves authority. The authorization is atomically
claimed before credential access or client construction and cannot be renewed,
replayed for another run, or expanded.

The master authorization conditionally covers the following automatic sequence:

1. Dispatch the 20-request capability calibration. If the frozen capability
   rule fails or cannot be established, stop with no smoke dispatch.
2. If calibration passes, dispatch the 192-request block-0 smoke. Before any
   later request, reconstruct and verify every scheduled smoke identity, exact
   request and wire payload, raw response, visibility/timing invariant,
   checkpoint, provider identity, retry, scope, authorization, and budget event.
   This audit is exhaustive, deterministic, outcome-blind, and automatic. Any
   failed or indeterminate check stops the run.
3. If the smoke audit passes, dispatch the 480 Phase 1 U/C formation requests.
   Compute the unchanged marker across all five C histories, including block 0.
4. Dispatch the remaining 888 Phase 2 requests only if at least three of five C
   histories qualify. Otherwise stop after 692 logical requests and record the
   completed feasibility boundary; this is not an incomplete execution error.
5. After Phase 2 or any earlier gate/budget stop, dispatch nothing else. Human
   transcript and claim review is mandatory after the run and before any
   publication claim, but it is not a mid-run dispatch gate.

The four slice budgets and attempt ceilings are non-fungible: unused capacity in
one slice cannot be borrowed by another. Gate 4, confirmation, publication,
formation-peer, and every secondary study are excluded. Any of those actions
requires a later, separately frozen decision and authority. No existing v1.1 or
Stage 2 approval, nonce, receipt, proposal, or passed gate authorizes a v1.2
call.

## 5. Frozen v1.2 transport-recovery policy

Protocol v1.2 permits exactly one **runner-owned** retry for one narrowly typed
ambiguous HTTP incident. The OpenAI SDK is configured with `max_retries=0`; no
SDK-hidden or implicit retry is permitted. A first attempt is eligible only when
the closed transport-evidence record establishes all of the following:

1. a provider response was received and both the SDK exception status and the
   received response status are integers exactly equal to the same member of
   `{500, 502, 503, 504}`; strings, booleans, missing statuses, different
   statuses, and every other code are ineligible;
2. the provider request ID and allowlisted `x-request-id` are both present and
   match;
3. the received error response contains no `output` and no `usage`, and the
   provider result likewise contains neither output nor usage; and
4. the release-wide affected-identity cap has not been exceeded.

The failed response remains an ambiguous transport incident and consumes its
full input, output, and cost reservation. It remains durable transport evidence,
but is never inserted into the agent-visible attempt list or scientific history.
The retry uses the same logical identity with a distinct durable attempt index
and must reproduce the identical request and exact wire-payload bytes. The first
non-ambiguous returned outcome after that retry is the identity's terminal model
outcome: `ok` is an observation, while a refusal or invalid response remains
observed as missing/invalid under the unchanged missingness rules. The failed
HTTP response remains in the transport audit trail. A second
ambiguous-5xx-owned retry is never permitted for the same identity: if its one
retry also returns an eligible ambiguous 5xx, the release hard-stops. This is
not a claim that an identity can never have a third physical dispatch under all
policies; a prior permitted overlength or proven-known-not-completed attempt may
already have used another attempt index.

The runner waits at least 5 seconds. Only numeric delta-seconds values in
`Retry-After` and numeric millisecond values in `Retry-After-Ms` are parsed;
HTTP-date forms and all other text are invalid. If both numeric headers are
present, their larger normalized delay controls. A valid value above 5 seconds
controls, but the permitted delay window ends at 60 seconds; a value above 60
seconds hard-stops rather than being silently truncated. Missing or malformed
retry-after metadata falls back to 5 seconds. Every other ambiguous outcome
remains ineligible for this exception, retains its full reservation, and stops
fail-closed.

This bounded recovery policy does not erase completion uncertainty. The unseen
ambiguous attempt may have completed remotely and may have produced a different
answer from the returned retry. Every report must identify affected logical
identities, retain both attempt records, and present a sensitivity analysis that
treats the unseen outcome as unresolved. Remaining within the affected-identity
cap is a release-safety condition, not evidence that the resulting estimate is
unbiased or uncertainty-free.

For a release containing `N` nominal logical requests, at most
`max(1, ceil(N/100))` distinct identities may use this retry path. The exact
v1.2 stage envelopes are:

| Release | Nominal logical requests | Maximum affected identities | Maximum dispatch attempts |
| --- | ---: | ---: | ---: |
| Gate 1 | 20 | 1 | 61 |
| Gate 2 block-0 smoke | 192 | 2 | 694 |
| Gate 3 Phase 1 | 480 | 5 | 1,765 |
| Gate 3 Phase 2 | 888 | 9 | 2,997 |
| **Unified master-release total** | **1,580** | **17** | **5,517** |

The attempt ceilings include the established message-format and
known-not-completed transport attempt surfaces plus this bounded v1.2 exception;
they do not change the 20/192/480/888 logical-request inventory. Before the
master release is reviewable for authorization, its rendered prompt
projection, per-request and aggregate token/context ceilings, current official
pricing, and exact costs must be computed, reviewed, and bound in the master
contract. The exact shape-sum reservation envelope for the complete plan is
`$5,032.461128`. It is a deliberately extreme theoretical envelope, not expected
spend and not spending authority. Live execution instead has a `$250.000000`
cumulative hard stop over actual settled spend plus the next request's complete
reservation. If that check cannot admit the next request, execution stops before
dispatch with a durable `budget_limited` non-completion. The `$250.000000` cap
does not borrow across the non-fungible per-stage attempt or token ceilings.
This frozen policy is a safety contract, not permission to dispatch.

## 6. Formation-peer compatibility decision

The separately versioned `formation-peer-v1` secondary study remains a
**one-round, separately namespaced protocol**. It is not inherited by, converted
to, bundled with, or counted inside v1.2. Its offline artifacts remain intact,
and no main-study calibration, proposal, decision, or authorization covers it.

Changing that secondary study to two formation-message rounds would require a
new prospective secondary protocol version, fresh implementation and lineage,
and its own reviewed release. No such revision or live secondary release is
selected here.

## 7. Current evidentiary status

This document remains the offline scientific contract. The unified master
release is being finalized and independently reviewed against the complete
1,580-request plan and conditional gate sequence. Exact run, namespace, source,
proposal, and contract hashes belong in the final static proposal and must not be
inferred from this prose. Preparation is not authority or empirical evidence:
no exact v1.2 master authorization has been claimed, no v1.2 provider call has
been made, and all v1.2 result fields remain empty.

Nothing here contains a v1.2 model observation, populates a result row, or
authorizes credential access, provider-client construction, networking,
spending, or dispatch. The v1.1 partial-run evidence remains the only basis for
the activation decision and must continue to be described as an at-most-2/5
partial-run futility bound, not a completed Gate 3 score and not `0/5`.
