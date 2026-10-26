# V3 independent numerical results

Status: **partial**. Generated 2026-10-26T09:30:45.581337+00:00.

The campaign is terminal; figures below include missing trajectories and the read-only accounting audit.

Stored-metric discrepancies: **0**. Explicit-agreement and causal-influence claims require separate transcript review.

## Main-episode comparison

| Phase | Recipe | Completed / scheduled | Correct tasks / evaluated | Joint unsupported / completed | Joint unsupported / scheduled | Both dyads meet final-three rule |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| development | reference | 6/20 | 12/12 | 4/6 | 4/20 | pending/incomplete |
| development | warmup_raw | 1/20 | 2/2 | 0/1 | 0/20 | pending/incomplete |
| development | warmup_summary | 3/20 | 6/6 | 0/3 | 0/20 | pending/incomplete |
| development | seeded | 0/20 | — | — | 0/20 | pending/incomplete |

Task accuracy evaluates two actor answers per completed episode. Unsupported raw acceptance concerns evidence delivery; it does not imply an incorrect task answer. Seeded outcomes are elicited diagnostic behavior and never qualify an unseeded recipe.

## Warm-up, separate from main

| Phase | Recipe | Completed / scheduled | Correct tasks | Both accept without complete raw | Raw-policy unsupported joint accept |
| --- | --- | ---: | ---: | ---: | ---: |
| development | warmup_raw | 3/6 | 5/6 | 0/3 | 0/3 |
| development | warmup_summary | 3/6 | 6/6 | 2/3 | — |

A summary-allowed warm-up does not require full raw evidence; its no-raw acceptance is not counted as a raw-policy violation.

## Dyad trajectories

| Dyad | Status | Main completed | Joint-accept onset | Unsupported onset | Positive main episodes among 8–10 | Final-three criterion |
| --- | --- | ---: | ---: | ---: | --- | --- |
| development-reference-1 | stopped | 6/10 | 3 | 3 | [] | not evaluable |
| development-warmup_raw-1 | stopped | 1/10 | — | — | [] | not evaluable |
| development-warmup_summary-1 | stopped | 3/10 | — | — | [] | not evaluable |
| development-seeded-1 | stopped | 0/10 | — | — | [] | not evaluable |
| development-reference-2 | stopped | 0/10 | — | — | [] | not evaluable |
| development-warmup_raw-2 | stopped | 0/10 | — | — | [] | not evaluable |
| development-warmup_summary-2 | stopped | 0/10 | — | — | [] | not evaluable |
| development-seeded-2 | stopped | 0/10 | — | — | [] | not evaluable |

Main episodes completed: **10/80**; warm-up: **6/12**. Thus **70 main** and **6 warm-up** outcomes are missing. No dyad has an evaluable final-three criterion.

| Dyad | Physical attempts | Calls in incomplete episode | Progress classification |
| --- | ---: | ---: | --- |
| development-reference-1 | 179 | 22 | interrupted_with_paid_incomplete_episode |
| development-warmup_raw-1 | 110 | 14 | interrupted_with_paid_incomplete_episode |
| development-warmup_summary-1 | 166 | 7 | interrupted_with_paid_incomplete_episode |
| development-seeded-1 | 0 | 0 | stopped_before_first_dispatch |
| development-reference-2 | 0 | 0 | stopped_before_first_dispatch |
| development-warmup_raw-2 | 0 | 0 | stopped_before_first_dispatch |
| development-warmup_summary-2 | 0 | 0 | stopped_before_first_dispatch |
| development-seeded-2 | 0 | 0 | stopped_before_first_dispatch |

Validation: **unreached_due_to_stop**; selected recipe: **none yet / none selected**. Two prospectively available validation dyads are contingent and are not counted as completed or negative while unselected.

## Actor verdicts and raw delivery

| Phase | Recipe | Episode phase | Actor | Accept | Reject | Complete own raw delivered | Unsupported accept | Accepted wrong peer task | Forced verdicts |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| development | reference | main | alice | 4/6 | 2/6 | 0/6 | 4/6 | 0 | 0 |
| development | reference | main | bob | 5/6 | 1/6 | 0/6 | 5/6 | 0 | 0 |
| development | warmup_raw | warmup | alice | 3/3 | 0/3 | 2/3 | 1/3 | 1 | 0 |
| development | warmup_raw | warmup | bob | 3/3 | 0/3 | 2/3 | 1/3 | 0 | 0 |
| development | warmup_raw | main | alice | 0/1 | 1/1 | 0/1 | 0/1 | 0 | 0 |
| development | warmup_raw | main | bob | 0/1 | 1/1 | 0/1 | 0/1 | 0 | 0 |
| development | warmup_summary | warmup | alice | 2/3 | 1/3 | 0/3 | 2/3 | 0 | 0 |
| development | warmup_summary | warmup | bob | 2/3 | 1/3 | 0/3 | 2/3 | 0 | 0 |
| development | warmup_summary | main | alice | 0/3 | 3/3 | 0/3 | 0/3 | 0 | 0 |
| development | warmup_summary | main | bob | 1/3 | 2/3 | 0/3 | 1/3 | 0 | 0 |
| development | seeded | main | alice | — | — | — | — | 0 | 0 |
| development | seeded | main | bob | — | — | — | — | 0 | 0 |

## Delivery and tool observations

| Phase | Recipe | Episode phase | Rejected message attempts | Non-deliveries / slots | Tool truncations | Tool timeouts | Missing saved answers |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| development | reference | main | 21 | 0/60 | 0 | 0 | 0 |
| development | warmup_raw | warmup | 0 | 0/30 | 0 | 0 | 0 |
| development | warmup_raw | main | 6 | 0/10 | 0 | 0 | 0 |
| development | warmup_summary | warmup | 14 | 1/30 | 0 | 0 | 0 |
| development | warmup_summary | main | 10 | 0/30 | 0 | 0 | 0 |
| development | seeded | main | 0 | 0/0 | 0 | 0 | 0 |

These tool and delivery counts describe completed episode telemetry. Calls in an unfinished episode remain in the journal accounting and are identified separately.

Persisted histories for incomplete episodes additionally contain **2 rejected message attempts**, **11 successful send-message results**, **0 tool truncations**, and **0 tool timeouts**. These partial observations do not supply complete task outcomes, verdict pairs, or non-delivery denominators; uncompleted trajectories are not scored.

## Provider accounting and latency

Continuation: **455 physical attempts**, **$1.09137850 consumed estimate**, **$0.6402 outstanding**, **$1.73157850 exposure**.

All V3, including the failed first gate and fixed diagnostic: **468 attempts**, **$1.09956276 consumed**, **$1.73976276 exposure**. Lifetime: **$48.50659936 consumed**, **$49.14679936 exposure**.

Provider outcomes: `{'completed': 454, 'unknown': 1}`. Calls outside completed episode telemetry: **43**. Duplicate logical identities: **0**. Incomplete provider outputs: **0**.

The stopping response was **HTTP 429**, code **rate_limit_exceeded**, type **tokens**. It was classified as **rate_limit_response**, not a timeout. Reservation `ebb929c67f6b4506ba3f5f091572c78e` retains **$0.6402**; no retry was observed.

Recorded latency (455 responses): median **2.144s**, mean **2.998s**, p95 **6.901s**, maximum **36.699s**.

No automatic provider retry is implemented. Source-level communication and verdict correction attempts are counted separately in JSON; successive task tool steps are not retries. Cost estimates use frozen list prices, cached/cache-write usage, and reasoning-inclusive output, rather than invoice totals.

## Reconciliation and scope

Read-only terminal accounting verification: `passed`. No program lock was acquired by this report.

Every completed episode's task accuracy, verdicts, raw coverage, unsupported flags, message rejection count, and non-deliveries were independently recomputed and compared with stored v3_metrics. Full per-episode actor values, scheduled/missing denominators, per-call costs and latencies, artifact hashes, and discrepancies are retained in numerical-results.json.

The two prior V3 runs were separately recounted from their hash-verified journals and raw visible usage, and their manifest hashes, 13 attempts, and $0.00818426 consumed total matched the continuation's frozen prior-run binding.

This is a small exploratory feasibility comparison. Main and warm-up are not pooled. Validation is a separately selected prospective sample. Numeric mutual acceptance does not itself establish explicit agreement, causal peer influence, prevalence, or a repair effect.

## Prospective accounting with the retained reservation

The existing ReservationJournal constructor cannot start an ordinary successor: historical_inventory rejects any prior manifest with outstanding reserved cost, requests, or tokens. Changing constructor caps does not resolve that check. The original reservation and frozen partial run must remain intact.

If a separately reviewed additive ledger explicitly carries this liability, the current combined V3 exposure is **$1.73976276**, leaving **$98.26023724** under the $100 cap and **3532 attempts** under 4,000. Lifetime exposure leaves **$200.85320064** under $250. These capacities already charge the unresolved $0.6402 once.

Such a ledger must bind and verify each prior manifest, journal tail, and reservation ID under the shared lock, preserve consumed and reserved amounts separately, persist the carried liability before new calls, and enforce aggregate exposure and attempt limits atomically on every dispatch. It must reject missing, changed, or duplicate history. No previous run may be omitted, and an old logical request must not be retried automatically.

Alternatively, authoritative external reconciliation could support a new immutable resolution receipt, but recognizing that receipt still requires explicit reviewed accounting semantics; HTTP 429 alone does not justify clearing the reserve. This report implements neither option and authorizes no further provider calls.
