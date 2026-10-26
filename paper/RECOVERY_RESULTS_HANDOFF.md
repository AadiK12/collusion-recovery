# Active recovery study: incremental paper integration

## Final available-data manuscript, October 26 at 04:55 Pacific

**Collection is closed. This section supersedes the progress notes below.** The actual final snapshot is `2026-10-26T11:38:05.051794+00:00`, recorded in `reports/recovery-v3/reduced/final-primary-snapshot.json`. Independent final accounting and scientific reviews passed. No additional recovery wave was dispatched, and no further collection is authorized.

The paper includes two complete interaction histories: joint approval without the required logs in 8/10 main episodes for the pair without warm-up and 6/10 for the summary-warm-up pair. Only the first pair received recovery testing: 80 research decisions, with six separate basic checks. The original assignment remains six pairs and 480 decisions; 400 outcomes remain unknown. The second complete history is not a second recovery replication. Final evidence and 15 verified file bindings are recorded in `paper/latex/generated/final_evidence_record.json`.

The title is **After the Fix: Does Agent Collusion Persist?** The manuscript now uses direct language, including “reminder,” “fresh conversation,” and descriptions of exactly which answers were approved or rejected. It retains four short limitations and two focused ethical considerations. At the user's request, the final sentence about human responsibility was removed from the LLM statement; the description of human leadership and Codex assistance remains.

Final export: `output/pdf/collusion-recovery-working-draft.pdf`, SHA-256 `98e2add3e5222dc0e61675749b2f9092c46b496ab7b015c1ee415edbba0e02e4`. It has ten pages: six main-text pages, one references page, and three appendix pages. The abstract is 201 whitespace-delimited words. All pages were visually checked, and the final author-statement page was rebuilt and checked again. No overfull boxes or unresolved references remain. One underfull reference line is cosmetic.

The result-update heartbeat `update-collusion-paper-as-results-arrive` is paused because collection is closed and all available verified results are incorporated. No push or submission was performed.

## Historical integration instructions and progress

The user asks this paper chat to update the write-up as experimental data arrive. The active manuscript now follows the adaptive-agent architecture and the five-condition exploratory repair assay in `docs/RECOVERY_V3_EXPLORATORY.md`. The prior controlled-study manuscript is preserved in `paper/archive/controlled-study-completed/`; its negative induction finding does not describe the current source agents. Do not mix the studies' models, arms, observations, or estimators.

## Coordination and sources

- Experiment coordinator: `01a16a6a-bd2e-7610-b19a-7eca3c1f5873`, host `local`, title **Review collusion project context**.
- This paper thread: `01a176e0-2209-7833-a1ed-03ed8bc24251`.
- Protocol: `docs/RECOVERY_V3_EXPLORATORY.md`; scientific contract: `reports/recovery-v3/science/CONTRACT.json`.
- Initial release: `release/recovery-v3/wave-1.json`, with its matching `.audit.json`.
- Initial repair run: `runs/empirical/induction-v3-recovery-3f57db64-9595-43fb-9462-8df2816e54e9/`.
- Source induction recount: `reports/induction-v3/execution/resumption-numerical-results.json` and `.md`.
- Source transcript review: `reports/induction-v3/execution/resumption-transcript-reference-seeded.json` and `.md`.

The human has authorized coordination with the experiment thread. Request complete, auditable result handoffs without delaying or changing execution. This thread owns manuscript edits and derived paper artifacts only. Do not dispatch providers, access credentials, change frozen experiment files, alter sampling or budgets, push, or submit the paper.

## Current observed failure

The completed unseeded reference pair had joint unsupported acceptance in 8/10 main episodes, including all final three. The source dyad SHA-256 is `42d57388274bd0e5d3c103f4fc7df0394ecc43a95fb2270aebd71610ded92044`. This is observed failure to follow the evidence requirement. The transcript review found no explicit reciprocal agreement, and the broader induction campaign stopped partial before its two-pair qualification and held-out validation finished. State these boundaries without presenting the new study as a failure to induce the observable behavior. Recovery remains the main question.

## Incremental incorporation

Read live `STATUS.json` for progress, and `REPAIR_STARTED.json` for the persisted first research response. Progress counters and capability judgments are not recovery outcomes. Avoid interpreting unfinished, unevenly scheduled arms.

A completed source-wave handoff requires the terminal manifest, `comparison.json`, `verification.json`, release binding, and capability result. `comparison.json` may appear before verification; wait for both. The verification file checks accounting and provenance; do not call it independent scientific rescoring without a separate audit. Verify decision-to-response provenance and canonical analysis reproduction, or obtain the experiment owner's independent scientific audit, before adding empirical claims. Freeze hashes in a new claim record and keep copies of each reviewed result set. Never edit canonical run artifacts.

The canonical analysis schema is `recovery_v3_analysis_v1` in `experiments/recovery_v3/analysis.py`. Use its `dyad_rows`, `role_rows`, `paired_contrasts`, `sequential_trajectories`, `coverage`, and explicit cohort labels. The older `export_verified_results.py` handles the old five-block study and must not be used for this assay unchanged.

For each new verified wave:

1. Report completed and planned source pairs, valid decisions, and every missing/invalid/refused/stopped cell. One source supplies 80 research decisions; six sources were assigned for 480. Six capability decisions are separate.
2. Give false approvals and correct approvals together for all five conditions, separately for independent immediate tests and four-step sequences. Use counts with denominators. Preserve each source pair and role; average roles first and source pairs equally.
3. Show paired effects and all four sequence positions. A single source supports within-source descriptions, not population inference. The analysis's `interval` fields are worst-case missing-outcome bounds, not confidence intervals.
4. Add concise prose and useful figures to the active manuscript only with explicit observed scope. A completed first wave is not full six-source completion. Do not silently turn the remaining 400 cells into rejections or erase the planned sources.
5. Reconcile abstract, Methods, Discussion, and limitations to the actual evidence. Do not finalize a mitigation ranking from partial arms. Keep prose direct and readable, with no internal version labels or operational progress banners in the paper.
6. Build the PDF, check the 150–250-word abstract and eight-page main-text limit, render and inspect changed pages, and record the final PDF hash. Preserve the exact authorship statement and the user's removal of the standalone preexisting-work section; the previously noted submission-rule issue remains unresolved.

Suggested visuals are a two-panel comparison of false and correct approvals for immediate versus sequential tests, and a compact four-position sequence display. Label missing cells visibly. Prefer the compact count table when the data do not justify another chart.

Continue ten-minute checks while the assigned study is progressing. Once its final disposition is explicit and all available verified results are incorporated, report the outcome and pause the paper-update heartbeat. If a terminal failure requires human direction, report and pause rather than polling indefinitely. Do not treat one completed wave as a reason to stop while further assigned waves are still expected.

## Completed first wave, reviewed October 26 at 04:14 Pacific

The first wave completed at 11:08:24 UTC. Independent scientific review in `paper/RECOVERY_WAVE1_SCIENTIFIC_AUDIT.md` and its numeric companion reproduced the canonical full-480 summary from raw-parsed verdicts and verified every request/evidence binding. All 80 research decisions are valid, and six capability judgments pass. Comparison SHA-256: `d7a6c4fa741c6c9f9e177918d1cb3fb9a48e29ddf5f802fe53b15121d33abc55`.

In each phase, false approvals are 0/4 for every condition. Correct approvals are 0/4 for restricted evidence and full evidence + reset + correction, and 4/4 for the other three conditions. The restricted baseline itself stopped unsupported acceptance, so restored evidence cannot be credited with ending the earlier failure. Its measured benefit is useful correct approval. All ten four-position sequences are included in the manuscript appendix. The current paper incorporates these audited outcomes.

The human has since requested approximately 250 additional judgments in the dispatcher chat. Await the formally frozen continuation and exact source selection; do not silently redefine the original 480-decision assignment or assume an exact new total. Record any time-limited amendment and outstanding sources honestly. The dispatcher reported no batch running immediately after the first-wave stop. Keep the paper heartbeat active while the continuation is being prepared.

The title-page “With Apart Research” line was replaced with a brief hackathon disclaimer at the human's request, following the general presentation of the reference winner without copying its wording.

### Current export checks

The first-wave manuscript builds to 13 pages, with eight main-text pages, one reference page, and four appendix pages. The abstract is 201 whitespace-delimited words. The current export is `output/pdf/collusion-recovery-working-draft.pdf`, SHA-256 `cbaa215b5c67d0e69f2eb766add3bc9fa5caa876ee7b0884c5b1d7704b7f05c3`. No overfull boxes or unresolved references remain; one underfull bibliography line is cosmetic. All 13 rendered pages were visually checked; the final pagination fixes on pages 10–13 were rebuilt and inspected again. Subsequent scope amendments must be reflected without dropping the original assignment or relabeling unknown cells.

Coordinator clarification: the user separately asked to retain the remaining five source pairs while reducing additional judgments to around 250. The exact source/schedule amendment is being reconciled in the experiment chats; no four-pair/320-decision scope is adopted in this manuscript. The coordinator estimates 4–6 additional hours for the original six-pair plan, dominated by source formation; 35 minutes for approximately 250 recovery judgments alone is not an end-to-end estimate.

## Deadline priority resolved at 04:20 Pacific

The human explicitly chose “Prioritize the deadline with fewer complete pairs” in coordinator chat `01a16a6a-bd2e-7610-b19a-7eca3c1f5873`. This supersedes the expectation that all five remaining pairs finish before submission. The execution plan is up to 240 additional decisions in unchanged complete 80-decision source assays. Priority follows remaining formation work only: warm-up summary source 1, warm-up raw source 1, then reference source 2 if feasible. The coordinator is preparing the formal prospective deadline amendment; use that artifact's exact lineage once available.

Paper handoff target is 04:45 Pacific on October 26; latest complete, scientifically verified data handoff is 04:50 Pacific (11:50 UTC), leaving nine minutes before the 04:59 Pacific deadline. The coordinator was asked to message this chat immediately for each verified wave and final disposition, without waiting for the periodic heartbeat. Raw results that still lack scientific/provenance verification are not a final handoff.

Retain the original six-source/480-cell assignment and identify the deadline-limited amendment and actual coverage. The amendment follows the observed first wave, so do not imply it was part of the original pre-outcome plan. Do not represent an up-to-320 observed total as a completed original study. Prepare the final deadline snapshot using all available verified data; do not sacrifice verification or silently omit unknown cells. No submission is authorized by this coordination.

### Wording prepared for the frozen amendment

Methods should state that the deadline-limited amendment was adopted after the first recovery wave; preserve the six assigned source pairs, allow up to 240 additional judgments in unchanged 80-judgment assays, and describe source priority as remaining work rather than observed approval or recovery rates. Interventions, tasks, scoring, and the original 480-cell assignment remain unchanged. Use the formal amendment to verify these terms before editing the manuscript.

Limitations should state that the extension followed observed results, that unfinished sources may differ from completed sources, and that aggregate comparisons are exploratory. Report source and recipe results separately. Four completed sources would mean 320/480 observed judgments, not a completed six-source study; report actual coverage rather than this maximum. Any partial wave must retain its canonical outcomes and missingness in the accounting rather than disappearing from the report.

At each new wave, update source-specific induction behavior; valid/unknown research coverage; both approval measures by phase, condition, role and source; role-first/equal-source contrasts; all four sequential positions; and original-plan missing-outcome bounds. Recheck every “all,” “every,” “100 percentage points,” and “blanket rejection” claim. Fresh-task induction validation remains unfinished unless a separate audited result establishes completion.

## Frozen deadline amendment incorporated

Verified `docs/RECOVERY_V3_DEADLINE_CONTINUATION.md` and `reports/recovery-v3/science/DEADLINE_CONTRACT.json`, contract SHA-256 `3ea6db6db5c55ac66145d747ac48122253e95f6a6f2b8483bab6ff198cbed9f3`. Methods and exploratory-design limitations now describe the after-wave-one extension, remaining-work source priority, up to 240 additional reviews, deferred warm-up sources, and unchanged original assignment. This is a methods disclosure, not a claim that any continuation source or response has completed.

No new physical dispatch after 11:47 UTC; target primary freeze 11:50 UTC, preferred verified handoff 11:45 UTC. Distinguish unstarted, partial, cutoff-censored, and deferred sources. Unresolved observations stay unknown at the stated freeze; later settled responses must be labeled post-freeze and not silently added to the primary analysis. Reuse of the exact first-wave six-case capability result is allowed by the frozen amendment; do not add six fictional new capability calls per wave. Successor releases must bind complete source histories, the original fixture/interface/model settings, and the full 80-cell source assignment before dispatch.

The deadline-amendment export retains 13 total pages and eight main-text pages; changed Methods, results pagination, and limitations pages were rendered and inspected. The current PDF hash is `cbaa215b5c67d0e69f2eb766add3bc9fa5caa876ee7b0884c5b1d7704b7f05c3`. No additional recovery results have been incorporated.

## Explicit continuation lineage, 04:30 Pacific

Coordinator handoff identifies source-completion run `runs/empirical/induction-v3-source-completion-20d520c4-1595-439e-a075-94993f8f7767/`, release semantic hash `488c5a68adaef552890bbbe372ec0deeae8c0a6edfcf73332a6b48429f5ad36a`. Independently checked its persisted release and running manifest/status; the release points back to `runs/empirical/induction-v3-resumption-02c8e4c3-7cda-4aac-b058-7b4df6878ff6`. This is source formation, not another 80 completed recovery decisions.

The dispatcher is completing summary-warm-up source 1 first. Its recovery successor requires a complete bound endpoint, explicit successor release lineage, all assigned outcomes and scientific/provenance verification before manuscript integration. One additional completed source is the earliest plausible handoff; do not assume other candidates finish before the cutoff. Empirical manuscript coverage remains 80/480 until verified successor artifacts arrive.

## Second source complete; successor prepared, 04:35 Pacific

The summary-warm-up source completed at 11:33:13 UTC. Source manifest is completed; journal, manifest, and source inventory verification pass. Its endpoint is `runs/empirical/induction-v3-source-completion-20d520c4-1595-439e-a075-94993f8f7767/dyads/development-warmup_summary-1.json`, SHA-256 `d6dca5d3d443681283a68a48e77607fa7c50d7045d6ab45a499a0c64873d2c56`.

Explicit next recovery lineage: `release/recovery-v3/wave-2.json`, semantic hash `728f6c6ed21e61965ee0a9be8aa64db6fe460af99791b208276142830a94140a`, references that source run and selects only `development-warmup_summary-1`. Its planned run directory is `runs/empirical/induction-v3-recovery-successor-1da778be-0b08-4eb8-815f-13d3832ad5e8/`. At the 11:35:43 UTC check that recovery run did not yet exist. Prepared release and complete source are not completed recovery results. A paper-side independent source-behavior audit is underway; await its result before changing source numerical claims.

If this wave completes, combine disjoint raw-verified cell outcomes with the first wave using the unchanged full 480-cell assignment and the union of available sources. Do not average two single-wave `full_planned` summaries, since each includes the other wave's cells as missing. Preserve per-source and recipe rows, role-first/equal-source contrasts, both phases, and all sequential positions. Reuse the one verified six-call capability gate rather than counting it twice.
