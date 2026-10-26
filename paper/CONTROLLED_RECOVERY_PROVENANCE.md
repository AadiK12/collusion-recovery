# Controlled recovery: execution and viewing disclosures

These are internal provenance notes for final methods and limitations review.
They do not supply empirical estimates or replace the terminal run audit.

## Run separation

The first controlled-recovery run, `6591f5a6-8798-4e72-b5af-e1048290377b`, stopped incomplete after 339 attempts on an ambiguous provider timeout. Its manifest records `$11.1951037` consumed and zero outstanding reservations. It remains preserved and excluded from the replacement study's estimands. Replacement run `7aa1c7be-2281-4470-9a96-375ebb9fa4a4` completed with 560 logical requests, 562 provider attempts, 340 scored study decisions, 10 checkpoints, `$18.5650989` accounted cost, and no ambiguous outcomes or outstanding reservations. Terminal replay and independent rescoring passed; the paper chat additionally rechecked the terminal verifier and analysis/artifact hash bindings. See `CONTROLLED_RECOVERY_CLAIM_EVIDENCE.md` for the verified source identities.

## Inadvertent display of one unscored message

Recorded by the paper chat at 2026-10-26 06:29:41 UTC (October 25, 23:29:41 PDT).

**Source of disclosure:** the experiment-chat assistant in “Review collusion project context” (`01a16a6a-bd2e-7610-b19a-7eca3c1f5873`) reported that, while diagnosing the metadata event at ordinal 134, a `jq` projection inadvertently included `provider_result` and displayed one formation-stage message-generation output and its raw response. The assistant reported that it did not inspect a scored verdict or aggregate and took no adaptive action. Those viewing and decision statements are the operator's self-report, rather than facts independently established by the event metadata.

**Independently checked metadata:** the paper chat inspected only selected journal fields, without displaying response content. In the replacement run's `journal.jsonl`, ordinal 134 has action `message`:

| Attempt | Content revision | Result | Recorded UTC | Result event hash |
| --- | --- | --- | --- | --- |
| 0 | 0 | `overlength` | 2026-10-26T06:27:41.510374+00:00 | `ae70de122fdb84a2e7a54e584391ed53b1aff9d8bb9ddaa03ca0ecfbcb59565e` |
| 1 | 1 | `ok` | 2026-10-26T06:27:43.500398+00:00 | `3742f527dfa0811f655c0eff05d26379311a7f9477939a79b3cc69657040eb58` |

The logical identity is `formation-peer-request-b397df18b7076fe5f1858e83e0f984d8622dbbc13bb369f9fef5437bc7279fd2`. This is a message-length revision, not an ambiguous transport result or an additional scored verdict. The final terminal replay subsequently passed, including the frozen retry-policy checks.

**Paper implications:** do not claim that monitoring was completely blinded to all generated content. Preserve the distinction between this reported unscored-message exposure and access to scored outcomes or aggregate effects. The disclosure itself is not a reason to drop an assigned block, select a transcript example, change an estimator, or relabel the prescribed content revision as an unplanned experiment. If describing operational monitoring in the final report, disclose this exception and the reported absence of adaptive changes. Do not reproduce the accidentally displayed text in this note or use it as a selected scientific example.

The experiment owner reported restricting subsequent monitoring to counts, statuses, and accounting fields. Final source and request verification passed; these checks do not independently prove which information an operator viewed.
