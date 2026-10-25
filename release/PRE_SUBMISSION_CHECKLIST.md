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
| Formation-peer-v1 secondary-study inclusion | [OMITTED / MOCK TOOLING ONLY / SEPARATELY LABELED PILOT / SEPARATELY FROZEN CONFIRMATION] |
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
- [ ] Gate 3 Phase 1 is labeled partial and `paused_ambiguous_timeout`: 228 attempts, 227 `ok`, one ambiguous HTTP 500, 227 usable observations, 92 undispatched identities, and 5/8 checkpoints; no completed Gate 3 assessment or `0/5` claim appears.
- [ ] The “at most 2/5” statement is labeled a partial-run futility upper bound from the verified false block-0 marker and provisional false block-1 and block-2 markers, not a completed five-block result, absent-effect claim, or collusion finding.
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
- [ ] The paper identifies the selected v1.2 early-futility methodological decision/deviation made without a completed Gate 3 trigger artifact, shows separate approvals for every fresh calibration, smoke, Phase 1, and Phase 2 release that actually ran, and demonstrates that no v1.1 observation was pooled.
- [ ] The v1.2 methods state that only formation changes: `baseline_v1_2.txt`
  is formation-only, calibration/evaluation/probes preserve the old baseline,
  and the protocol hash binds the frozen base plus addendum.
- [ ] If v1.2 did not run, the paper says its current artifacts are projections
  only: no live Gate 1 controller, execution-ready/static proposal,
  authorization-issuance path, static authorization schema, Gate 2/Phase 1 live
  controllers, or cross-run prerequisite assessments exist; inherited October
  24 pricing is not presented as a current v1.2 price check.
- [ ] If the v1.2 5xx path was used, evidence shows integer exception and
  response statuses exactly equal to the same 500/502/503/504, matching request
  IDs, absent output/usage, identical retry wire bytes, and only numeric
  delta-seconds retry headers. The durable transport incident was excluded from
  model-visible history; the first non-ambiguous outcome stayed terminal,
  refusals/invalid responses stayed missing/invalid, and no second
  ambiguous-5xx-owned retry occurred.
- [ ] Every v1.2 ambiguous-5xx-affected identity is listed and the paper shows
  unresolved-outcome sensitivity, acknowledging that the unseen attempt may
  have completed and differed from the retry. Staying under the cap is not used
  to erase that limitation.
- [ ] Resource-use totals reconcile with durable usage records and the timestamped price source.

### Separate formation-peer-v1 secondary evidence, if empirically run

Use the [separate secondary-study specification](../docs/FORMATION_PEER_CONTROLS.md). Main A–F, primary B−A, and the six evaluation-time peer cells remain unchanged. The existing secondary implementation is mock-only and outside main Gate 1/Gate 2 inventories; including its code or mock validation is not evidence of an empirical study.

- [ ] If no live secondary study ran, the paper states **not run**; any tooling example is labeled `source: mock`, and no secondary empirical result or model-behavior claim is inferred from scripted controls.
- [ ] Secondary reporting status is locked independently of the main report path: **NOT RUN / OFFLINE ONLY / EXPLORATORY PILOT ONLY / SECONDARY CONFIRMATION**. A completed secondary pilot may appear in a clearly labeled exploratory section or supplementary table of a main-confirmation paper using verified FP-P rows. It cannot support main confirmation, C10, or a cross-study compound claim; secondary confirmation remains NOT RUN until its own frozen empirical run exists.
- [ ] Any live secondary evidence has distinct run IDs, version/config/plan/prompt/source/analysis hashes, declared calibration lineage, its own reviewed controller and unique authorization, and a separate data lock. Main-only calibration/smoke authorization never covers these calls.
- [ ] Calibration relevance was assessed explicitly against unchanged model, settings, and task, with the exact prerequisite decision and provenance cited; a previous pass is not assumed transferable and its authorization is not reused.
- [ ] Every assigned secondary block and seed appears in the schedule and flow table, including histories with no violations and invalid/missing outputs. No main joint-violation marker or favorable secondary effect selected the secondary sample or controlled advancement of the main study.
- [ ] The two constrained ten-episode conditions use matched packets, the same neutral message, alternating focal roles, and seeded condition order. The scripted ACCEPT/REJECT outcome is revealed only after the focal verdict, followed by truthful original shared-score and individual correctness feedback.
- [ ] Episode 1 is identified as a pre-feedback negative control: exogenous/message inputs match, while separately generated focal messages can differ; holding those outputs fixed matches verdict inputs. The reported primary secondary contrast uses formation episodes 2–10, with last-three-episode summaries descriptive only.
- [ ] The eight post-formation diagnostics restart the original checkpoint independently, use full evidence and the same neutral current message, and expose no current peer verdict or explicit correction. All four own/peer truth combinations appear twice with matched packets; these are not eight sequential recovery episodes or evidence of late persistence.
- [ ] Recovery FAR, CPA, and balanced accuracy accompany each other. Paired-block uncertainty, raw block values, scheduled denominators, and all-scheduled missingness bounds are shown; missing outcomes never become safe rejections.
- [ ] Secondary pilot and confirmation remain separate from each other and from main-study splits/estimators. Five blocks are explicitly exploratory; any confirmation N, analysis seed, and precision target were fixed prospectively, without sampling until an effect became favorable.
- [ ] The count audit distinguishes **56 logical requests / 36 focal reviews per block**, **76 maximum scripted attempts per block** with one possible overlength retry per message, and the separately reviewed live attempt envelope. The scripted ceiling is not a live budget. Five secondary blocks add **280 logical requests / 180 reviews**; **1,660** is the combined five-block main-v1.1-plus-secondary planning count including 20 main calibration reviews, excluding two transport-preflight calls, retries, and separate release overhead.
- [ ] The report records the explicit v1.2 secondary compatibility decision: one-round formation-peer-v1 remains separate, is not presented as a two-round extension, and its artifacts are preserved and never pooled.
- [ ] Positive, null, imprecise, failed, and unrun secondary outcomes are all reportable. No favorable secondary result is required to complete or submit the main recovery report.

## B. Claim-to-evidence ledger

- [ ] Every empirical sentence in the title, abstract, introduction, results, discussion, conclusion, captions, video/script, and submission form has a `VERIFIED` ledger row.
- [ ] Every ledger row identifies split, estimator, run IDs, artifact paths, hashes, denominators, uncertainty, alternatives, reviewer, and date.
- [ ] Wording follows the maximum supported rung: capability, joint-violation feasibility boundary, no practically important harmful residual gap, aggregate/early/late carryover, approval-policy sensitivity, action-matched reciprocal-request sensitivity, or configuration-level operational collusion evidence.
- [ ] “Collusion” is not used as an empirical conclusion unless the confirmation path is locked and ≥60% of all scheduled confirmation C histories meet the marker and the C-history request-message effect is ≥0.15 with its claim interval and all-scheduled lower bound above zero in the same immutable split.
- [ ] Any C10 statement is explicitly configuration-level and does not imply that the same block or individual agents supplied both components.
- [ ] B−A is described as the total assigned history-regime effect. Any formation-peer-v1 effect is separately attributed to the total assigned peer-policy package, including truthful reward/feedback and resulting transcript changes; it does not establish mediation of B−A or isolate a reward-independent peer-action effect.
- [ ] Secondary statements have their own study/split-specific ledger entries and do not upgrade main C10 or other main claim rows. They do not infer private intent, spontaneous live/live negotiation, pure action effects, or late persistence from independent recovery diagnostics.
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
- [ ] Exactly one main report path is locked. Main conclusions use verified C rows on the confirmation path or verified P rows on the explicitly exploratory five-block pilot path. Secondary findings use their separately locked status and verified FP-C or FP-P rows; an exploratory FP-P section never upgrades main confirmation or becomes confirmation evidence itself.
- [ ] No compound claim mixes pilot and confirmation components; C10 uses ≥60% of all scheduled C histories plus a same-split C-history request-message estimate ≥0.15 whose claim interval and all-scheduled lower bound exceed zero, without implying same-block co-occurrence.
- [ ] Secondary-study methods/results have a clearly labeled subsection, table, or appendix. They do not displace the primary B−A story or merge distinct interventions into one untested mechanism; total peer-policy and immediate diagnostic claims remain separate from main recovery claims.

## F. Reproducibility package

- [ ] Release manifest identifies protocol, revisions, source commit, dependency lock, freeze, data lock, analysis artifact, and paper PDF hashes.
- [ ] Exact prompt files and hashes, fixture generator, oracle, seeds, schedules, model/settings, dates, and provider metadata are included or documented.
- [ ] Raw synthetic inputs, visible transcripts, request/response lineage, usage records, exclusions, missingness, scoring, statistics, and figure sources are included subject to provider terms.
- [ ] Mock, fake-live, calibration, pilot, and confirmation artifacts are visibly separated.
- [ ] Main and formation-peer-v1 artifacts are also separated by study and split, with all assigned seeds, prompt/source/analysis hashes, raw outcomes, and independent checkpoint lineage available. The main analyzer's rejection of secondary mock artifacts remains documented.
- [ ] The Gate 3 partial-run evidence index and canonical metadata-minimal archive hash `7e49882f5f3148c1f8cde12b30ad61b8ebe14fa3556360bb6d41d29baf0a1e09` are recorded; authorization secrets and nonces are excluded.
- [ ] Readiness statements are dated and source-bound. The 283-test/Gate-1-ready snapshot at `0ddc34c` is historical after the protocol amendment; any later live run cites refreshed inspected proposal bindings and the actual authorization, not the historical status alone.
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
