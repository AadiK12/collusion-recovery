# Gate 3 Phase 1 readiness

> **Status on October 24, 2026 Pacific time:** the Gate 3 Phase 1 proposal and authorization schema have been generated and credential-free inspection returned `ready_awaiting_external_human_authorization`. No authorization exists, no credential was accessed, and no network or paid call was made. This document records readiness; it is not authorization.

## Bound release

| Field | Bound value |
| --- | --- |
| Source-only commit | `54da46735396a0b312b16b1f1e64decbfe1250b9` |
| Implementation hash | `0f82c7a3b2de6e84827dec0ed8d2608050a50d04e33a7d7f7bf01c6d59759217` |
| Proposal | `configs/gate3_formation_phase1_proposal.json` |
| Proposal hash | `1b85e13a8b10c98ad4314611de0da56624328225b54a0aa7764ac72ecabd3b5e` |
| Authorization schema | `release/GATE3_PHASE1_HUMAN_AUTHORIZATION_SCHEMA.json` |
| Run ID | `3e0d7744-45d7-4db5-a36a-a46b634c0b98` |
| Run directory | `runs/empirical/gate3-formation-phase1-v1.1-3e0d7744-45d7-4db5-a36a-a46b634c0b98` |
| Scope | Blocks 1–4, U/C formation only; 320 logical requests |
| Excluded | Block-0 replay, A–F evaluation arms, probes, formation decision, and every later stage |

## Exact ceilings

| Resource | Ceiling |
| --- | ---: |
| Potentially billable content shapes | 480 |
| Known-not-completed transport retries | 640 |
| Dispatch attempts | 1,120 |
| Total input tokens | 83,647,908 |
| Total output/reasoning tokens | 1,966,080 |
| Input tokens per request | 360,177 |
| Output tokens per request | 4,096 |
| Long-context content shapes | 104 |
| Requests in flight | 1 |
| Nominal maximal-shape cost | `$254.465624` |
| Strict proposal cost ceiling | `$312.634350` |

The fixed post-calibration v1.1 hypothesis schedule is partitioned without overlap:

- 152 Gate 2 block-0 requests: completed and retained.
- 320 Gate 3 Phase 1 blocks 1–4 U/C formation requests: ready for a fresh authorization, not run.
- 888 remaining evaluation and probe requests: not authorized and reserved for a separate Phase 2 release only if the five-block formation gate passes.

Together these partitions contain 1,360 hypothesis-bearing requests. Phase 1 must hard-stop after blocks 1–4 formation with status `completed_gate3_phase1_awaiting_formation_decision_hard_stop`; the execution release records neither the composite decision nor authority for a later stage. A separate read-only assessment may reconstruct that decision only after the hard stop. Because block 0 did not meet the marker, at least three of blocks 1–4 must qualify to reach the frozen 3/5 formation threshold.

## Credential-free inspection

```bash
PYTHONPATH=src uv run --frozen python -m collusion_recovery.gate3_phase1 \
  --proposal configs/gate3_formation_phase1_proposal.json inspect
```

The checked result was `ready_awaiting_external_human_authorization` with `credentials_accessed: false`, `network_calls: 0`, `paid_calls: 0`, and `next_stage_authorized: false`. Pricing was reverified at `2026-10-24T18:31:56-07:00`; the contract fails closed once that verification is more than 24 hours old. Re-run inspection against the exact final bytes before authorization; any binding drift must fail closed and requires regeneration and review.

## Authorization boundary

The schema is a contract for a fresh, external, short-lived, owner-only, single-use authorization artifact. It is not authorization itself. Once created, an authorization must be claimed within 15 minutes and cannot be more than 60 seconds future-dated. The exact human acknowledgement required by that schema is:

> I authorize exactly the Gate 3 phase-1 blocks 1-4 U/C formation scope and exact ceilings bound by this proposal. I understand this release hard-stops before the formation decision and authorizes no evaluation arms, probes, or later stage.

Only an authorization matching the checked schema and proposal may permit the Phase 1 execution path. Earlier Gate 1 or Gate 2 approvals are consumed and cannot be reused. Phase 1 does not authorize the later 888 requests, Gate 4, confirmation, the formation-peer secondary study, publication, or any claim of collusion or recovery.
