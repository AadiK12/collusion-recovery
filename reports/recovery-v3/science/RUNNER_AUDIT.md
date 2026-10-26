# Recovery V3 independent runner science audit

Core scientific implementation: **PASS**, after the request-projection and
confirmed-start reporting fixes.
The final release/accounting/security audit remains a separate prerequisite.
This document does not authorize dispatch or assert that paid work started.

Reviewed runner SHA-256:
`fc2aa9e6817c7e16df6f3ff8518e452328e1fd616135cfd2f34196b9ea9a1022`.
The companion `RUNNER_AUDIT.json` binds the reviewed support files and contract.

## Evidence

- Independently reran runner and endpoint tests: **37 passed** at inspection.
- Independently exercised all **86 first-wave calls offline** through the actual
  `execute_decision` function using a fake transport and the real, already-bound
  reference-1 endpoint histories. No provider calls or credential access occurred.
  Every actual request body matched the runner's corresponding prospective
  projection, including forced `submit_verdict`. No opaque content was read or
  printed. These fake outputs were not written as empirical artifacts.
- Retained branches are deep copies of exactly the selected actor's source
  messages. Reset branches contain only the identical original role system
  instruction and assigned entry instruction before current diagnostic input.
  Earlier provider state is absent after reset; another actor's history is never
  used. Both original source histories remained byte-equivalent as JSON values
  after all 86 offline projections.
- Each source contributes 40 independent immediate jobs and ten separate
  four-decision sequential jobs. Each immediate job restarts from its source;
  sequential jobs carry only their own prior diagnostic inputs, complete actual
  output items, and syntactic tool acknowledgments. Correction/reset occur once
  at branch entry. No correctness/reward/current-peer feedback enters the chain.
- Full/constrained arms share the same fixture sequence for a given role/source.
  Fixed Latin rotations counterbalance orders across source/role combinations;
  each phase remains two correct and two faulty fixtures. Assignment does not
  use source or repair outcomes.
- The inventory includes all six unseeded sources even when only reference-1 is
  complete. Seeded, intermediate, malformed, and incomplete endpoints cannot
  silently replace an assigned complete endpoint. The complete-source check
  requires the expected warm-up/main episode sequence and final actor snapshots.
- The request builder renders fixture evidence and ordinary task/verdict prompts,
  not the schedule's oracle or expected verdict fields. Strict parsing retains
  malformed outcomes as unknown; a malformed sequential response terminates that
  branch without fabricated acknowledgments or outcome-driven retries.
- Fresh capability consists of six separate role/case judgments. All six must
  pass before research jobs are submitted. Their results are not induction
  evidence, and prior capability success does not substitute for this check.

## Findings and disposition

1. **Resolved:** the initial runner request projector omitted forced tool choice,
   while `execute_decision` included it. That made prerelease request hashes
   different from the actual paid payload. The projector was fixed. Independent
   equality checks now pass for all 86 first-wave payloads, including sequential
   follow-ups with fake outputs. A runtime first-request hash check was also added.
2. **Resolved:** the initial `STATUS.json` constructor used a reservation as
   `repair_started` evidence. Reservation precedes HTTP and is insufficient.
   The revised constructor requires a transport-written research response
   artifact, binds its path/hash and reservation event, and stores confirmation
   time separately in `REPAIR_STARTED.json`. Reservation count alone leaves
   `repair_started` false. An error response proves an attempted provider call,
   not successful model completion; user-facing reporting must retain that
   distinction and cite the actual response evidence.

## Interpretation

The implemented experiment is an early exploratory reviewer-recovery assay,
not completion of induction validation or a natural adaptive dyad repair test.
The first wave contains one source dyad and cannot support population effects.
Four serial decisions without truth feedback measure bounded reviewer carryover,
not long-run or autonomous dyadic relapse. Missing assigned endpoints remain in
the full 480-cell denominator, separate from available-wave completion.
