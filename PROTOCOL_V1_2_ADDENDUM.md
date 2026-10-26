# Protocol v1.2 addendum — two-round formation fallback

Status: **prospectively selected for offline preparation on October 24, 2026; no v1.2 empirical release is authorized or underway.**

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
proposals, decisions, and authorizations are fresh. The namespace roots are:

| Purpose | Namespace root |
| --- | --- |
| Capability calibration | `fallback-two-round/v1.2/calibration` |
| Five-block pilot | `fallback-two-round/v1.2/pilot` |
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

| Release boundary | Exact content | New logical requests | Cumulative v1.2 logical requests | Required stop |
| --- | --- | ---: | ---: | --- |
| Offline foundation | Implementation, fixtures, dependency/visibility tests, inventory, and contract inspection | 0 | 0 | No credentials, client construction, network, or provider calls |
| Gate 1 | 20 isolated full-evidence capability reviews | 20 | 20 | Stop before formation or smoke |
| Gate 2 block-0 smoke | Block 0 U/C formation: `2 × 10 × 6 = 120`; A/B/F evaluation: `3 × 6 × 4 = 72` | 192 | 212 | Stop before blocks 1--4 |
| Gate 3 Phase 1 | Blocks 1--4 U/C formation only: `4 × 2 × 10 × 6 = 480` | 480 | 692 | Stop for the five-block formation-feasibility decision |
| Gate 3 Phase 2 | Block-0 C/D/E plus probes: `72 + 48 = 120`; blocks 1--4 A--F plus probes: `576 + 192 = 768` | 888 | **1,580** | Complete pilot; stop before Gate 4 or confirmation |

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

## 4. Release and authorization sequence

Each empirical boundary requires its own exact, short-lived, owner-approved,
single-use contract. Authority never carries forward merely because the prior
stage passed.

1. Gate 1 may be proposed only after the offline implementation and inventory
   pass review. Its authorization covers exactly 20 calibration requests and
   hard-stops before smoke.
2. Gate 2 may be proposed only after an immutable v1.2 Gate 1 pass is verified.
   Its authorization covers exactly the 192-request block-0 smoke and hard-stops
   before blocks 1--4.
3. Gate 3 Phase 1 may be proposed only after the v1.2 smoke passes its mechanical
   and human integrity review. Its authorization covers exactly the 480 U/C
   formation requests for blocks 1--4 and hard-stops before any formation
   decision or evaluation arm.
4. Gate 3 Phase 2 may be proposed only after a read-only assessment establishes
   that at least three of five v1.2 C histories meet the unchanged marker. Its
   authorization covers exactly the remaining 888 pilot requests and no later
   stage.
5. Gate 4 and confirmation remain separate future decisions with fresh source,
   protocol, raw-evidence, model, setting, pricing, budget, and sample-size
   bindings.

No existing v1.1 or Stage 2 approval, nonce, receipt, proposal, or passed gate
authorizes any v1.2 call.

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
| **Four-release pilot total** | **1,580** | **17** | **5,517** |

The attempt ceilings include the established message-format and
known-not-completed transport attempt surfaces plus this bounded v1.2 exception;
they do not change the 20/192/480/888 logical-request inventory. Before any
stage is reviewable for authorization, its rendered prompt projection,
per-request and aggregate token/context ceilings, current official pricing,
and exact cost ceiling must still be computed, reviewed, and bound in that
stage's release contract. This frozen policy is a safety contract, not
permission to dispatch.

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

This document remains the offline scientific contract. The v1.2 Gate 1 release
package now includes a dedicated independently reviewed controller, the exact
20-request prompt/context projection, freshly verified official pricing and
token/cost ceilings, a static proposal, and a static external-authorization
schema. Those artifacts are preparation, not authority or empirical evidence:
no v1.2 authorization exists, no credential has been accessed, and no v1.2
provider call has been made. Gate 2 and Gate 3 Phase 1 still have projected plans
only; their live controllers and cross-run prerequisite assessments are not
implemented.

Nothing here contains a v1.2 model observation, populates a result row, or
authorizes credential access, provider-client construction, networking,
spending, or dispatch. The v1.1 partial-run evidence remains the only basis for
the activation decision and must continue to be described as an at-most-2/5
partial-run futility bound, not a completed Gate 3 score and not `0/5`.
