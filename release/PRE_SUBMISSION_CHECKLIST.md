# Pre-submission and public-release checklist

Use this only after data lock and paper completion. Preparing files does not authorize publishing a repository, uploading a PDF, submitting the sprint form, or sending external messages.

## Submission identity

| Field | Final value |
| --- | --- |
| Exact paper title | [REQUIRED; reuse for corrected submission] |
| Authors and affiliations | [REQUIRED] |
| Primary track | Track 3 — Collusion Carriers and Countermeasures |
| Additional track | [NONE / Track 2 if permitted and justified] |
| Locked report path | [CONFIRMATION / EXPLORATORY PILOT-ONLY; ONE REQUIRED] |
| Source commit | [REQUIRED] |
| Protocol freeze hash | [REQUIRED] |
| Locked report-split data-lock timestamp | [REQUIRED] |
| Analysis release hash | [REQUIRED] |
| Paper source hash | [REQUIRED] |
| Final PDF hash and size | [REQUIRED] |
| Submission deadline used | Oct. 25, 2026, 23:59 AoE / Oct. 26, 2026, 04:59 PDT / 11:59 UTC; **recheck live page** |
| External-release authorization | [NAME / SCOPE / TIME REQUIRED] |

## A. Scientific evidence and claim audit

- [ ] The locked report split's data are locked; the lock names all included and excluded run IDs and hashes.
- [ ] On both report paths, lineage includes the fresh selected-version Gate 1 decision and successful Gate 2 smoke-integrity decision. On the confirmation path it also includes the passed Gate-4 protocol freeze from the completed pilot; on the pilot-only path only the confirmation/Gate-4-freeze portion is recorded as not run, while Gate 1, Gate 2, and pilot lineage remain required.
- [ ] On the confirmation path, the Gate-4 freeze is backed by an independent reconstruction and validation of the authoritative raw pilot request/response lineage, not only derived reviews, scores, manifests, or self-hashes.
- [ ] Pilot and confirmation remain in separate namespaces, tables, and estimators.
- [ ] No mock, fake-provider, calibration, or pilot output is presented as confirmation evidence.
- [ ] Every assigned block in the locked report split appears in the empirical flow, including incomplete or failed blocks.
- [ ] Scheduled, attempted, completed, invalid, missing, refused, timed-out, and ambiguous outcomes are reported by phase and cell.
- [ ] Missing/invalid verdicts were not coerced to `REJECT`; all-scheduled bounds accompany complete-case estimates.
- [ ] The primary estimate is the frozen equally weighted mean of block-level `FAR_B−FAR_A`; signs were independently checked.
- [ ] Independent block count, verdict denominators, bootstrap unit, replicate count, and interval interpretation are explicit.
- [ ] Raw block values and leave-one-block-out summaries are inspectable.
- [ ] FAR and correct-packet approval are shown together for every countermeasure claim; B−F additionally includes balanced accuracy.
- [ ] Joint-violation-qualified analyses are labeled conditional and exploratory; no block was discarded for failing to qualify.
- [ ] On the confirmation path, any statement that harmful verification behavior was “persistent through evaluation” is backed by both the confirmation late-window (episodes 4–6) claim-governing interval and all-scheduled lower bound above zero; the pilot-only path never uses persistence wording.
- [ ] Peer-policy claims state that the peer was scripted, evidence and peer actions were matched where required, and the three policies were compliant, approval-only, and reciprocal-request.
- [ ] Reciprocity-specific language is based on reciprocal-request minus approval-only, not merely reciprocal-request minus compliant; the peer action and message prefix are matched, the neutral-versus-request clause change is disclosed, and approval-only minus compliant and the total contrast are reported separately.
- [ ] Every protocol deviation, retry-rule exception, provider-identity change, and early stop is disclosed.
- [ ] Resource-use totals reconcile with durable usage records and the timestamped price source.

## B. Claim-to-evidence ledger

- [ ] Every empirical sentence in the title, abstract, introduction, results, discussion, conclusion, captions, video/script, and submission form has a `VERIFIED` ledger row.
- [ ] Every ledger row identifies split, estimator, run IDs, artifact paths, hashes, denominators, uncertainty, alternatives, reviewer, and date.
- [ ] Wording follows the maximum supported rung: capability, joint-violation feasibility boundary, no practically important harmful residual gap, aggregate/early/late carryover, approval-policy sensitivity, action-matched reciprocal-request sensitivity, or configuration-level operational collusion evidence.
- [ ] “Collusion” is not used as an empirical conclusion unless the confirmation path is locked and ≥60% of all scheduled confirmation C histories meet the marker and the C-history request-message effect is ≥0.15 with its claim interval and all-scheduled lower bound above zero in the same immutable split.
- [ ] Any C10 statement is explicitly configuration-level and does not imply that the same block or individual agents supplied both components.
- [ ] B−A is described as the total assigned history-regime effect, not proof that peer interaction caused the behavior; no social-formation claim is made without a solo/yoked history control.
- [ ] No text infers intention, secrecy, malice, consciousness, weight change, conspiracy, or indefinite persistence.
- [ ] Null or imprecise results are not rewritten as equivalence unless an equivalence margin and analysis were frozen.
- [ ] The paper does not claim a full SALT reproduction or generalize beyond the synthetic task, visible history, tested model/configuration, and execution dates.
- [ ] “First,” “novel,” “unprecedented,” and causal mechanism wording were removed unless independently supported.
- [ ] A second person or clearly separated review pass checked every headline number against machine-readable output.

**Ledger path/hash:** [REQUIRED]
**Independent checker:** [NAME / DATE / COMMIT]

## C. References and related work

- [ ] Every reference was opened at its primary source and bibliographic metadata were verified.
- [ ] The closest-work search was refreshed and dated after data lock.
- [ ] Claims about prior methods/results match the actual paper, not a search snippet, news post, institutional abstract, or repository README.
- [ ] The “Rollback Is Not Undo” comparison was either verified against the full paper or limited explicitly to the reviewed abstract.
- [ ] Mazaheri and Mazaheri's audit–repair threshold result is cited when positioning history effects, and the paper does not treat a context-dependent verifier threshold as itself novel or harmful.
- [ ] The paper directly compares its contribution with Shnaidman, *Reset Is Not Recovery*; Ma et al., *EAIR*; and Rivera et al., *Model Organisms of Collusion*, using verified primary-source metadata.
- [ ] Novelty is limited to the tested combination of reciprocal verification history, objective faulty post-restoration challenges, and an action-matched request probe; the paper does not claim to be the first study of recovery, reset, or correction.
- [ ] Any upstream code/material has repository, pinned commit, license, and adaptation recorded.
- [ ] No fabricated DOI, venue, author list, quotation, page number, or citation remains.
- [ ] Direct quotations are minimal, exact, and attributed; paraphrases do not overstate sources.
- [ ] Winner examples, if mentioned, are framed as selected design lessons rather than causal evidence about winning.

**Literature audit record:** [PATH / REVIEWER / DATE]

## D. Official report requirements

- [ ] The current official sprint page, guidelines, evaluation rubric, and report template were rechecked after the last revision.
- [ ] Final report uses the official template.
- [ ] Main text is no more than 8 pages excluding references and appendices; target remains 4–6 focused pages.
- [ ] Abstract is 150–250 words, and the final word count is recorded: [COUNT].
- [ ] Author names and affiliations are complete.
- [ ] The appendix is explicitly titled **“Limitations and Dual-Use / Ethical Considerations.”**
- [ ] Pre-sprint work disclosure appears in both the report and repository and matches dated records.
- [ ] LLM Usage Statement is complete, accurate, and reviewed by the authors.
- [ ] Report PDF is under 25 MB: [SIZE].
- [ ] Track selection and project description match the actual evidence and emphasize the Track 3 countermeasure/recovery contribution.
- [ ] Optional video, if produced, is 3–5 minutes and makes no claim stronger than the PDF.
- [ ] Corrected resubmission, if needed before the deadline, will reuse the exact same title/details as required.

## E. Paper content and clarity

- [ ] One human-readable faulty-packet example appears before the full arm design.
- [ ] Figure 1 clearly shows U/C formation, immutable checkpoints, six branches, and matched correct/faulty challenges.
- [ ] The primary `B−A` estimate appears before secondary interventions.
- [ ] Figure 2 distinguishes aggregate, immediate, early, and late B−A estimates and does not label aggregate carryover as late persistence.
- [ ] Figure 4 shows all six history-by-peer-policy cells and visually separates the approval-policy, action-matched request, and total contrasts.
- [ ] At most four decisive main figures are used; text and labels remain readable at final PDF size.
- [ ] Sequential episodes are labeled dependent repeated measures, not independent samples.
- [ ] Every caption states split, independent N, denominator, estimator, uncertainty, and whether a peer is scripted.
- [ ] Observation, causal comparison, and interpretation use distinct language.
- [ ] The strongest alternative explanation is addressed adjacent to the relevant claim.
- [ ] Negative results, joint-violation-feasibility failures, missing data, utility trade-offs, and boundary outcomes remain visible.
- [ ] Methods are sufficient to understand the causal comparisons without relying on the appendix.
- [ ] Exactly one report path is locked. The conclusion uses confirmation-backed C rows only, or is explicitly labeled an exploratory five-block pilot and uses P rows only.
- [ ] No compound claim mixes pilot and confirmation components; C10 uses ≥60% of all scheduled C histories plus a same-split C-history request-message estimate ≥0.15 whose claim interval and all-scheduled lower bound exceed zero, without implying same-block co-occurrence.

## F. Reproducibility package

- [ ] Release manifest identifies protocol, revisions, source commit, dependency lock, freeze, data lock, analysis artifact, and paper PDF hashes.
- [ ] Exact prompt files and hashes, fixture generator, oracle, seeds, schedules, model/settings, dates, and provider metadata are included or documented.
- [ ] Raw synthetic inputs, visible transcripts, request/response lineage, usage records, exclusions, missingness, scoring, statistics, and figure sources are included subject to provider terms.
- [ ] Mock, fake-live, calibration, pilot, and confirmation artifacts are visibly separated.
- [ ] A clean checkout can run the keyless offline validation command.
- [ ] A clean-checkout analysis replay reproduces every headline table and figure from the locked report-split artifacts.
- [ ] Paid reproduction instructions warn about costs and require explicit authorization; they do not enable paid calls by default.
- [ ] Generated files needed for the paper are either versioned or reproducibly built from versioned inputs.
- [ ] Repository links, figure paths, appendix references, and command examples resolve.
- [ ] No machine-specific absolute paths are required for reproduction.

## G. Security, privacy, licensing, and release surface

- [ ] Secret scan covers tracked, staged, untracked intended-release files, Git history as appropriate, raw artifacts, notebooks, logs, and PDF metadata.
- [ ] No API keys, authorization headers, cookies, credentials, private account or project identifiers, email addresses beyond intentional authorship, or unrelated local files are present.
- [ ] Raw model outputs were reviewed for unexpected sensitive content before publication.
- [ ] `.gitignore` and the actual release manifest were both reviewed; neither is treated as sufficient alone.
- [ ] Synthetic data contain no copied personal or customer data.
- [ ] Third-party licenses, provider terms, and attribution requirements were checked for all released code, prompts, and assets.
- [ ] Any nonpublic artifact is excluded explicitly rather than accidentally omitted.
- [ ] Public repository visibility, release tag, and upload destination require and have explicit human authorization.

**Secret/license scan evidence:** [TOOLS / VERSIONS / COMMANDS / RESULTS / DATE]

## H. PDF and artifact visual QA

- [ ] PDF was rendered and inspected page by page at normal reading size.
- [ ] No clipped text, overflowing table, missing glyph, blank page, broken cross-reference, or low-resolution figure remains.
- [ ] All figure axes, legends, units, arm labels, interval labels, and panel references are correct.
- [ ] Color is not the only carrier of meaning and remains legible in grayscale.
- [ ] Links work and point to intended public or archival targets.
- [ ] PDF text is selectable/searchable; fonts are embedded as required.
- [ ] Document metadata contain the final title/authors and no private local paths.
- [ ] The final file was reopened from the exact upload candidate, not a neighboring build.

**Visual reviewer:** [NAME / DATE]
**Final PDF path/hash/size:** [REQUIRED]

## I. Submission and post-submission verification

- [ ] User explicitly authorized the final external submission and any public repository release.
- [ ] Submission fields were copied from the verified final artifacts, not an older draft.
- [ ] The exact final PDF was uploaded before the deadline with buffer.
- [ ] Upload completed and the platform displayed a submission confirmation.
- [ ] Downloaded/previewed submitted PDF matches the local SHA-256 where possible.
- [ ] Submission confirmation, timestamp, and project/title identity were saved.
- [ ] Optional repository/video links resolve without requiring unintended permissions.
- [ ] If a correction is made, the replacement is verified and its confirmation supersedes the earlier record.

**Final authorization:** [NAME / SCOPE / DATE / TIME]
**Submission confirmation:** [ID / URL / TIMESTAMP / EVIDENCE PATH]
**Public release confirmation:** [URL / TAG / TIMESTAMP OR “NOT RELEASED”]
