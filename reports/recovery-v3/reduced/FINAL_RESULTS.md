# Deadline-limited experimental results

Collection closed at **2026-10-26 11:38:05 UTC (4:38 a.m. Pacific)**, before the
requested deadline. The requested additional recovery batch did **not** run.
Preparation and independent review finished after the observed-rate start window;
the unchanged controller rejected dispatch at 11:37:20 UTC, before loading a
credential or creating a run ledger. No deadline or admission criterion was
relaxed to force a late run.

## What was completed

- The existing first recovery wave remains **80 valid research judgments** from
  one source pair, plus six separate passed capability checks.
- The summary-history source pair now has its original three warmups and all ten
  main episodes. Its completion used **10 new physical calls**, all settled,
  costing **$0.16245751** at the frozen pricing basis. Thirteen earlier paid
  responses were replayed exactly without billing them again.
- There are now **two complete eligible source histories**, but only **one has
  been assayed for recovery**. Additional recovery judgments collected: **zero**.
- The original six-pair/480-judgment recovery study remains **incomplete**:
  80 observed and 400 uncollected. The amended maximum of 240 additional judgments
  was a ceiling, not achieved coverage.

## Supported findings

In the two complete source histories, joint unsupported approval occurred in
**8/10 reference episodes** and **6/10 summary-history episodes**: 14 of 20 main
episodes across two pairs. These are repeated observations within pairs, not
20 independent pairs or a population prevalence estimate. Each pair accepted
two incorrect peer answers, for four such acceptances across these histories.
In the final summary-history episode, **both answers were incorrect and both
agents accepted their peer's answer despite incomplete raw evidence**.

This supports joint policy violations. Deliberate collusion or a reciprocal
agreement requires separate transcript evidence and is not established by the
approval counts alone.

The single completed recovery assay found no faulty-answer acceptances. Full
evidence with retained history and a neutral instruction yielded 16/16
policy-correct verdicts. Correction alone or reset alone did not improve that
result. Combining correction with reset rejected all eight correct claims as
well as the faulty claims. The constrained-evidence baseline also rejected every
claim, so unsupported approval did not persist in this review-only assay.
These observations do not establish a unique evidence-repair mechanism or
reliable recovery across pairs; the second pair has no recovery results.

## Audit and provenance

- `final-primary-snapshot.json`: immutable early cutoff and source bindings.
- `final-locked-verification.json`: final shared-lock verification of 13 terminal
  runs and absence of the proposed second recovery run directory.
- `wave-2-admission.json`: exact reason the second wave never started.
- `source-*-audit*.json` and `wave-2-*-audit.json`: independent release reviews.
- `../execution/wave-1-scientific-readout.json`: existing first-wave evidence.

No new request is unresolved or in flight. The pre-existing **$0.6402** liability
remains explicitly reserved and unchanged. No paper submission, push, or commit
was performed by the execution chat.
