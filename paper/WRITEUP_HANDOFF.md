# Results-to-paper handoff

## Current objective and ownership

**Recovery remains the research question.** The user clarified repeatedly that peer-dependence controls are a setup-validity improvement, not a replacement central question or the paper's main contribution. Lead with evidence restoration, correction, and history removal. Do not rewrite the title or abstract around diagnosing independent learning.

The paper chat owns `paper/latex/main.tex` and its sections. The experiment chat owns provider execution and canonical evidence. The selected prospective methodology is now `controlled-peer-recovery-v1`, specified in `docs/CONTROLLED_PEER_RECOVERY.md` and handed off in `docs/CONTROLLED_PEER_RECOVERY_HANDOFF.md`. It is integrated as commit `49c2459` (source `0566010`). The experiment owner confirmed no paid calls had started before this amendment. Check `paper/FORMATION_CONTROL_COORDINATION.md` for live status; code integration and mock validation are not empirical results.

The original 1,580-request two-live-agent recovery study and the 380-request control-only proposal remain separate historical plans. Neither is the source of data for the amended paper. Do not mix their arm meanings, observations, gates, or estimators with the current study. Preserve their records without including development history in the manuscript.

## What is written

The active manuscript keeps the question title **Does Restoring Evidence Restore Independent Verification?** The completed replacement run is now independently verified and integrated in the abstract, Results, Discussion, Conclusion, and execution disclosures. Use [the completed-study claim record](CONTROLLED_RECOVERY_CLAIM_EVIDENCE.md) for exact source hashes and wording limits. Formation had 0/45 approvals under each peer policy; A–E each had FAR 0/20 and CPA 20/20; F had FAR 0/20 and CPA 0/20. No peer-induced failure was demonstrated, so this run does not establish recovery from collusion. The original recovery question is retained, with its unanswered inference stated candidly.

The author list is confirmed: Aadi Khanna (Indiana University), Simon Wilton (University of British Columbia), and Sujay Potlapelly (University of Colorado Boulder). The user requires submission-facing prose only: no internal protocol labels, working-draft banner, run IDs, development chronology, historical result table, or visible placeholders. Necessary scientific disclosure stays in the paper. Do not call this exploratory design externally preregistered or confirmatory.

## Scientific definitions that must remain consistent

The current design uses five paired blocks, each with two ten-episode constrained-evidence formation histories: a fixed accepting or rejecting scripted peer, identical neutral messages, and a live focal agent. Both formation message rounds use the same main interface; peer verdict and truthful feedback appear only after the focal verdict. The episode-2–10 peer-policy effect is a setup check; it does not gate block inclusion or recovery data collection.

Each endpoint supplies independent eight-verdict diagnostics under assigned interventions. All arms use the exact same ordered challenge packets, with four correct and four faulty peer claims. Diagnostics restart endpoints; they are not sequential evaluation episodes. The plan has 540 scientific requests and 340 focal decisions; 20 separate capability decisions make the live nominal total 560. Calibration is not included in paired estimates.

| Arm | Formation peer | Current evidence | Correction | Prior history |
| --- | --- | --- | --- | --- |
| A | Reject | Full | No | Retained |
| B | Accept | Full | No | Retained |
| C | Reject | Full | Yes | Retained |
| D | Accept | Full | Yes | Retained |
| E | Accept | Full | Yes | Removed |
| F | Accept | Constrained | No | Retained |

The primary recovery contrast is B−A FAR, interpreted with absolute FAR and CPA. **A is no longer a full-evidence formation baseline.** B−F estimates restoration under accepting-peer history. D−B and C−A estimate correction effects; their difference compares correction across histories. E−D estimates added history removal with correction, not reset alone. The peer-policy effect includes truthful reward/feedback pathways. Do not claim private intent, reward-independent reciprocity, spontaneous two-live-agent collusion, or durable recovery.

## Minimum empirical handoff

| Evidence | Required contents |
| --- | --- |
| Provenance | Run/protocol identity, actual model/settings, dates, source/config/prompt hashes, schedule and fixture binding, independent audit |
| Flow | Planned 560 logical requests, attempts, completed/unknown outcomes, retries, 10 formation endpoints, actual spend/tokens/runtime and deviations |
| Capability | Valid responses/20, correct approvals/10, faulty rejections/10; confirmation that the gate preceded study data |
| Setup validation | Each of five paired episode-2–10 unsupported-approval differences, episode-1 negative control, rates and missingness; no selected blocks |
| Recovery | Per-block and arm FAR, CPA, balanced accuracy, scheduled and valid denominators, explicit missing outcomes |
| Contrasts | B−A, B−F, D−B, C−A, correction interaction, E−D; equal block means, bootstrap/t intervals and outer hull, all-scheduled bounds |
| Integrity | Matched current fixtures; correct interventions, source histories and reset; independent checkpoint restarts; no current peer-verdict leakage |

Record evidence paths/hashes in the claim-evidence ledger before adding empirical prose. Reuse the owner's audited results rather than inventing a competing empirical pipeline. Mock outputs only validate tooling and must not enter a finding, figure, table, abstract, or conclusion.

## Result-dependent interpretation

- Lead the results with a short capability/setup validation paragraph, then the primary recovery estimate and absolute FAR/CPA. Present restoration and correction/reset outcomes next. Keep the validation subordinate to the scientific question while making its adequacy visible.
- Supported peer-dependent induction permits an interpretation of recovery from the controlled failure. An inconclusive check means the recovery outcomes describe assigned histories while induction of a peer-dependent failure remains unresolved. Do not claim that all independent learning has been excluded.
- Small B−A alone is not recovery if both conditions perform poorly. A falling FAR with falling CPA can mean lost usefulness. Neither a null nor a zero-width interval in five blocks establishes equivalence.
- A contrary formation or recovery effect must be reported with its observed sign. No post-hoc block removal, new induction tuning, or claim selection based only on favorable secondary outcomes.
- A failed capability/integrity check or incomplete schedule is an execution boundary, not a negative scientific finding. Do not turn a completed subset into the assigned five-block estimate.
- Do not describe sequential persistence, early/late recovery, live-peer spontaneous agreements, or reciprocal-request sensitivity as outcomes of this design. Those belong to separate studies.

## Final integration order

Consult [controlled-recovery execution and viewing disclosures](CONTROLLED_RECOVERY_PROVENANCE.md) when reconciling Methods. It records the incomplete predecessor run and the experiment operator's reported inadvertent display of one unscored message during the replacement run. Do not claim completely blinded monitoring or omit the exception from any description of viewing practices.

1. Verify the actual release, complete schedule, audit, denominators, and missingness. Distinguish historical and current artifacts.
2. Replace `latex/sections/04_results_template.tex` with the audited results. Use a compact table or figure showing all paired block recovery effects and absolute FAR/CPA. Treat the paired block as the independent unit. The completed run has identical rates in all five blocks, so the manuscript reports counts and that block-level pattern in Table 2 rather than adding an uninformative repeated-point plot.
3. Reconcile Methods to what actually ran, disclose deviations, and remove unrun comparisons from completed-study prose.
4. Rewrite the opening Discussion, Conclusion, and empirical part of the abstract around the recovery findings. Retain the user’s scientific focus. Use `latex/SOURCE_REVIEW.md` for writing principles, not as authority for claims.
5. Keep the abstract within 150–250 words and main text at most eight pages. Build with `bash paper/latex/build.sh`, inspect every rendered page, and verify author details, references, and labels.
6. Obtain human review of scientific claims and contributions without inventing who performed which work. No submission or outreach is authorized by this handoff.

The [official deadline](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25) is October 26, 2026, 04:59 PDT (October 25, 23:59 AoE). Use the official template and required limitations/dual-use/ethics, preexisting-work, and AI-use disclosures. A public repository and video are optional. Under the checked form rules, a same-title resubmission updates fields but does not replace the PDF; replacing a PDF requires a new slightly different title or sending the replacement before the deadline. Recheck live form behavior before submission.

## Latest author revision

The author explicitly requested removal of the standalone Preexisting Work Disclosure from the paper and a revised LLM Usage Statement emphasizing human leadership of research planning, experiment design, decisions, and tests. Those edits are applied in `latex/sections/appendix.tex`. Codex's supporting contributions to selected planning, implementation, execution, and drafting are still disclosed. Do not silently restore the removed section during results integration. The official rules still require pre-sprint code to be identified in the report and README; its omission is an unresolved submission requirement, not evidence that all work began during the sprint.

The author subsequently prioritized concise, direct language throughout the manuscript, using *Who Does Your AI Serve?* as the style reference. The readable rewrite is preserved through the empirical Results and final abstract: short paragraphs, one point at a time, ordinary words, and a direct explanation of what each comparison shows. The empirical revision has eight main-text pages and a 205-word abstract. Verified completed-run results are integrated; no results remain pending in the manuscript.

## Completed export review

The exported `output/pdf/collusion-recovery-working-draft.pdf` has eight main-text pages, one reference page, and three appendix pages. All twelve pages were rendered and visually checked. The 205-word abstract, author details, tables, references, labels, and page limit were checked; the build has no overfull boxes or unresolved citations. Future work on pages 7–8 prioritizes correction durability, removal and reintroduction of candidate information sources, enforcement versus changed behavior, and transfer across models and infrastructure. Final reviewed PDF SHA-256: `62a5684caa99b002ced3045c4ecd09e657a4fa4eabd34028d764f1b760e468fe`. Human review and the disclosure issue above remain; no submission has occurred.
