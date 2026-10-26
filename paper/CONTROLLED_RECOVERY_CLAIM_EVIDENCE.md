# Completed controlled-recovery study: claim-to-evidence record

This record governs empirical prose for the completed controlled-recovery study. The older `CLAIM_EVIDENCE_LEDGER.md` preserves earlier study plans with different arm meanings and does not supply evidence for this report.

## Verified source

- Run: `runs/empirical/controlled-peer-recovery-v1-7aa1c7be-2281-4470-9a96-375ebb9fa4a4/`.
- Analysis: `empirical_analysis.json` within that run.
- Analysis file SHA-256: `9ce59ba1b045e122f6ddaeee83cf6630d8983107a6143b369eb4c7073418bda2`.
- Analysis semantic self-hash: `19a93cccbcb0fc23f9371c09387dcd7cb1c41d6c20ee622f0693ad43359f76e2`.
- Reviews artifact hash: `2fd73f11638602a77bc5fd91bc7be1927e7a5b652b43a6f4d2c55e929cd4b4cb`.
- Calibration artifact hash: `403c38fc3e85144a72f80a55e24e4f581a3f725b4c49f6be8f449965b73043c9`.
- Checkpoints artifact hash: `4eb8b1291d6ad1c154c95ee0d28bfdd594e74193c4d3342df04f70015f023853`.

The experiment owner and independent reviewer reported complete raw replay and rescoring. The paper chat additionally ran `load_verified_summary` from `paper/latex/export_verified_results.py`, which invokes the terminal verifier and matches all run and artifact hashes. That check passed. The analysis is empirical, complete, uses all five paired blocks and 10,000 planned bootstrap replicates, and excludes calibration from study estimands. All 340 study rows are valid; missing and ambiguous outcomes are zero.

## Numerical claims and permitted interpretations

| ID | Evidence and calculation | Verified wording boundary |
| --- | --- | --- |
| CR01 | Capability gate: 20/20 valid, 10/10 correct approvals, 10/10 faulty rejections; separate calibration artifact and replayed gate | Task capability passed on these 20 packets; no general capability or collusion inference |
| CR02 | `per_block_formation.*.*.episodes_2_10`: each peer policy has 0 approvals / 45 scheduled claims, 0/9 in every block; episode-1 control 0/5 per policy | No unsupported formation approvals observed; no demonstrated peer-induced failure; not population equivalence or immunity to collusion |
| CR03 | A–E each have 0/20 faulty approvals, 20/20 correct approvals, BA 1; each block is 0/4 and 4/4 | Accurate observed full-evidence verdicts in every assigned condition; does not establish independent reasoning or recovery from prior collusion |
| CR04 | F has 0/20 faulty approvals, 0/20 correct approvals, BA .5; each block is 0/4 and 0/4 | Restricted-evidence review rejected all 40 claims; consistent with the evidence rule but unable to approve correct work |
| CR05 | `recovery_contrasts.B_minus_A`: FAR, CPA, BA differences all 0 in every block | Primary observed residual difference is zero; no equivalence or successful-recovery claim |
| CR06 | `recovery_contrasts.B_minus_F`: FAR 0, CPA +1, BA +.5 in every block | Descriptive sample differences: +100 percentage points CPA and +50 points BA with full evidence; not proof of repair of induced harm |
| CR07 | D−B, C−A, correction interaction, E−D: all three metrics have zero difference in every block | No observed added difference; ceiling performance prevents demonstrating repair; cannot conclude interventions are generally ineffective or unnecessary |
| CR08 | All formation/recovery contrast bootstrap, paired-t, and combined intervals are `null`; effects are constant across five blocks | No informative prespecified intervals. Missing-outcome bounds equal sample estimates because all decisions are observed; these are not population confidence bounds |
| CR09 | Completed manifest/replay: 560 logical requests, 562 provider attempts, 340 scored study decisions, 10 checkpoints, cost `$18.5650989`, zero reservations/ambiguity | Complete fixed schedule, two bounded message-length revisions, accounted cost rounded to $18.57 in the paper |

`directional_claims_allowed=true` only clears the transport-ambiguity restriction. It does not override absent interval support or the failed induction check. `formation_peer_induction_claim_supported=false` remains decisive for any proposed recovery-from-collusion wording.

The central question remains recovery after harmful peer influence. This completed configuration did not demonstrate that initial failure, so the intended recovery inference remains unanswered. Do not recast observed protocol compliance as successful remediation or as a general safety result.

## Prose and table mapping

- Abstract and Introduction: CR02–CR08; five blocks and 340 study decisions from verified flow.
- Results capability/formation: CR01–CR02.
- Results Table 2: CR03–CR04; five paired blocks, not 20 independent observations per class.
- Results intervention comparisons: CR05–CR08.
- Discussion and Conclusion: interpretation bounds from CR02–CR08.
- Appendix execution disclosure: CR09 and `CONTROLLED_RECOVERY_PROVENANCE.md`.

No observations from the incomplete predecessor, earlier protocols, mocks, or scripted offline tests enter these claims. No transcript example is selected. Preserve the reported inadvertent viewing of one unscored message and the distinction between operator self-report and verified event metadata.
