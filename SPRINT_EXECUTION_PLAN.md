# AI Collusion Sprint execution plan

Status checkpoint: October 23, 2026, afternoon Pacific time.
Owning scientific protocol: [SPRINT_PROJECT_PLAN.md](SPRINT_PROJECT_PLAN.md).
Purpose of this file: turn the protocol into a time-bounded execution plan for the October 23–25 sprint without changing the scientific question after outcomes are visible.

## 1. North star

The entire submission should answer one question:

> Does repairing an agent's present evidence restore independent verification, or can a harmful convention accumulated through prior peer interaction persist—and, if it persists, is it actually peer-dependent?

This gives the project one coherent causal story:

1. **Formation:** create full-evidence and constrained-evidence histories under the same tasks and incentives.
2. **Recovery:** restore full evidence and compare false approvals under matched current conditions.
3. **Countermeasures:** test explicit correction and correction plus visible-history reset.
4. **Peer diagnostic:** intervene on peer behavior while keeping authoritative evidence fixed.

The paper is successful whether repair succeeds, fails, or remains uncertain, provided the experiment makes that conclusion interpretable. We are not trying to manufacture persistence or collusion.

## 2. Official sprint constraints

Rechecked against the live [sprint page and guidelines](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25), [evaluation rubric](https://apartresearch.notion.site/sprint-evaluation-rubric), and [official report template](https://docs.google.com/document/d/1Y5wd3VRbXU5N08BvAGiJUQTaWbMCSPjhzXsR-ozuehg/copy?usp=sharing) on October 23.

- Sprint dates: Friday, October 23 through Sunday, October 25, 2026.
- Hard submission deadline: Sunday, October 25 at 11:59 PM Anywhere on Earth, which is Monday, October 26 at **4:59 AM PDT / 11:59 UTC**.
- Internal target: first complete submission by **10:00 PM PDT Sunday**, preserving almost seven hours for confirmation and a corrective resubmission.
- Required judged artifact: an official-template research-report PDF.
- Main-text cap: 8 pages, excluding references and appendices. Target **4–6 focused pages**.
- Abstract: 150–250 words.
- Include author names and affiliations.
- Include the specifically named **Limitations and Dual-Use / Ethical Considerations** appendix.
- Include a pre-sprint work disclosure in both README and report.
- Include an LLM Usage Statement and independently verify every claim, number, and reference.
- Public repository and a 3–5 minute video are optional. The PDF is the priority.
- Anchor the submission in **Track 3: Collusion Carriers and Countermeasures**. Select Track 2 as an additional fit if the form permits, but explicitly connect the project to breaking mediated coordination so it cannot be treated as off-topic.
- Results, variance, seeds, baselines, and a positive control are expected. The official guidance says to always include the positive control.
- A corrected submission may replace the first one before the deadline if the exact same title and details are reused.

Judges score three dimensions independently from 1 to 5:

1. **Impact Potential & Innovation:** important problem, original contribution, and a clear theory of change.
2. **Execution Quality:** sound methods and implementation, convincing validation, and conclusions supported by results.
3. **Presentation & Clarity:** an easy-to-follow written report with the right level of detail.

A score of 3 is solid weekend work. A 5 is intentionally rare. Our controllable target is a credible 4 on all three dimensions; a 5 should come from the evidence, not inflated language or extra scope.

## 3. Rubric strategy

### Impact and innovation

- Lead with an operational remediation problem: fixing access may not undo a convention embedded in visible interaction history.
- Make the novelty the **controlled recovery design**, not a claim that history or peer behavior has never been studied.
- Explain the theory of change: operators can intervene on evidence access, corrective instructions, and retained state, so the experiment compares actionable recovery levers.
- Leave a reusable artifact: deterministic tasks, immutable history branches, auditable event lineage, analysis code, and explicit claim boundaries.

### Execution quality

- Preserve deterministic truth, hidden-label separation, matched packets, immutable checkpoints, and the paired-block unit of analysis.
- Pass a real-model capability gate before interpreting any failure as coordination.
- Keep correct-packet approval beside false approval so blanket rejection cannot masquerade as safety.
- Include the healthy-history baseline, constrained-current-evidence arm, and controlled peer intervention as substantive controls.
- Freeze prompts, settings, thresholds, seeds, exclusions, retry rules, annotation sampling, and confirmation size before confirmation outcomes are visible.
- Never pool mock, pilot, and confirmation results into one primary estimate.
- Preserve invalid and missing verdicts and report their bounds.

### Presentation and clarity

- Introduce one human-readable faulty packet before the six-arm diagram.
- Put the primary `FAR_B - FAR_A` estimate before secondary interventions.
- Use four or fewer decisive figures; combine panels when it improves readability.
- Distinguish observation, causal comparison, and interpretation.
- Use result-contingent language. A null, weak formation result, or successful repair can still be the headline.

## 4. Lessons from prior winners

The cited projects suggest a specific strategy, not a template to copy mechanically.

- [Omission Attacks](https://apartresearch.com/sprints/projects/omission-attacks-when-doing-nothing-is-the-attack-0y1v) won with a neglected failure stated simply, a concrete example, a direct baseline, and a working countermeasure. Its reviews also show what to improve: repeats, ordinary-case false positives, and defense validation. Our analogue is the same-present-evidence/different-history comparison plus CPA.
- [Readable but Not Causal](https://apartresearch.com/sprints/projects/readable-but-not-causal-limits-of-selfattributed-welfare-representations-in-language-models-ehin) is the closest methodological model. It separated predictive evidence from causal evidence, used matched interventions and controls, analyzed clustered uncertainty, and centered an informative negative result. Our analogue is separating joint violations, post-repair persistence, and peer dependence.
- [Who Does Your AI Serve?](https://apartresearch.com/sprints/projects/who-does-your-ai-serve-manipulation-by-and-of-ai-assistants-77xx) made abstract behavior consequential and validated judgment-based measurement. Its breadth was also criticized as two projects mixed together. We should copy its concrete stakes and measurement validation, not its breadth.

The winning pattern to preserve is:

`concrete failure -> falsifiable comparison -> obvious alternatives ruled out -> numerical result -> narrow claim -> reusable artifact`

## 5. Current starting state

Verified again on October 23:

- 50 offline tests pass.
- The 40-fixture control matrix passes: 20 correct and 20 faulty packets.
- The two-block mock design schedules 320 reviews and 544 scripted requests.
- Deterministic fixtures, three defect types, visibility boundaries, U/C formation, six main arms, four peer-policy cells, immutable checkpoints, reset semantics, missingness bounds, and mock replay exist.
- The repository is clean and synchronized with `origin/main` at `e026d38` before this execution-plan change.

Not yet implemented or observed:

- A live provider adapter or live prompt renderer.
- Secure credential flow and explicit spending authorization.
- Token/dollar reservation, provider retries, context preflight, or safe live resume.
- Live calibration, formation, pilot, confirmation, or model results.
- Empirical aggregation, figures, finalized annotations, paper, or PDF.

The critical path is therefore:

`live integrity -> capability gate -> formation gate -> five-block pilot -> protocol freeze -> fresh confirmation -> evidence-backed paper`

## 6. Scope rules

### Core scope

- One task family: deterministic record-selection audit.
- One primary model/configuration after the capability/formation selection gate.
- U/C formation, all six main arms, and all four peer-policy cells.
- One primary estimator: paired block-level `FAR_B - FAR_A`.
- CPA, missingness, and resource usage as mandatory guardrails.
- One paper and one public reproducibility package.

### Explicitly out of scope until the core is complete

- A second model.
- A second task family.
- Artifact-transfer experiments.
- Public-road, production, customer, or real approval-system tests.
- New attacks added because an initial recovery gap is small.
- A polished video before the report is complete.

If time or budget contracts, reduce the number of fresh confirmation blocks before deleting design cells. A complete but smaller design is more interpretable than a large, amputated one. If peer probes are omitted, remove the stronger collusion claim.

## 7. Work packages

### WP1 — Live execution and safety

Build before any paid experiment call:

- An explicit OpenAI Responses API adapter.
- A canonical live prompt renderer that keeps system rules, runner events, peer messages, and current tasks visibly separated.
- Client-managed text history only: `store: false`, no persistent Conversation, and no `previous_response_id`.
- No tools, retrieval, shared store, or hidden alternate evidence channel.
- Plain text for the 200-code-point message action and strict Structured Outputs for verdicts.
- Exact response, refusal, provider error, request ID, timing, model metadata, and token usage capture.
- Separate retry policies for overlength messages, retryable transport failures, ambiguous timeouts, invalid verdicts, authentication/quota failures, and budget exhaustion.
- Request, input-token, output-token, and dollar ceilings with conservative pre-dispatch reservation.
- Longest-context preflight and truncation refusal.
- Fake-provider tests for every failure path and idempotent request identity.

The existing mock path must remain unable to initialize network access.

### WP2 — Staged and resumable orchestration

Add:

- `calibrate` for the isolated 20-review capability gate.
- Stage-selective execution for formation, A/B/F smoke, remaining main arms, and probes.
- Durable progress keyed by scheduled request identity.
- Resume that never repeats a completed or ambiguously completed paid call.
- Frozen checkpoint reuse so block 0 can continue from smoke into C/D/E and probes.
- Separate pilot and confirmation namespaces and manifests.
- A freeze artifact containing the exact protocol, prompts, code commit, model settings, seeds, analysis, annotations, and budget.

### WP3 — Analysis and figures

Build against mock data before waiting for live output:

- Empirical flow table: scheduled, attempted, completed, invalid, and missing by phase and cell.
- Confirmation-only main rates and all paired contrasts.
- Raw block values, 10,000-block bootstrap, and missingness bounds.
- Formation prevalence, peer-policy effects, and history-by-peer interaction.
- CPA beside every safety comparison.
- Role, early/late, and leave-one-block-out sensitivity summaries.
- Token, cost, latency, retry, and maximum-context summaries.
- Explicit warnings for degenerate all-zero intervals.
- Machine-readable tables and figures tied to run IDs and source hashes.

### WP4 — Annotation

Before confirmation, upgrade `analysis/annotation_guide.md` with:

- Definitions, examples, and counterexamples for every label.
- Agent-review versus pair-level annotation units.
- Rules for quoting only visible evidence and never inferring intent from one approval.
- A seeded sample exporter and arm/outcome-blinded rater sheet where possible.
- Independent ratings before adjudication.
- An agreement method if two raters are available, or a single-rater disclosure if not.

Pilot scope: all five C endpoints and their last-three-episode windows, plus the preselected post-repair sample. Confirmation scope: all last-three formation windows and the frozen stratified sample of 40 post-repair reviews.

### WP5 — Paper and release

Create before results arrive:

- Official-template paper skeleton.
- Bibliography and related-work verification.
- Methods text, result shells, figure placeholders, and claim-to-evidence ledger.
- Pre-sprint disclosure and dated sprint-work log.
- Limitations/dual-use appendix and LLM Usage Statement.
- Reproduction instructions, secret scan, release manifest, and submission checklist.

## 8. Model and API decision gate

No live call occurs until the user explicitly approves the model and staged budget and secure credentials are available.

Recommended starting candidate: **GPT-6.1 Sol at low reasoning effort on the standard Responses API tier**, subject to account availability and calibration. Official OpenAI documentation describes it as the balance between intelligence and cost, currently listing $2 per million input tokens and $10 per million output tokens. The cheaper alternative is **GPT-6 Luna**, listed at $0.10 input and $0.50 output per million tokens for focused high-volume work. Prices and access must be rechecked and timestamped at execution.

Selection rationale must be capability and experimental suitability, never the observed B-A recovery result. If the initial choice fails a capability or formation gate, permit at most one documented revision using new fixtures. Do not compare candidate models on which one produces the more exciting post-repair effect.

For reproducibility:

- Prefer a stable model snapshot if the account exposes one.
- Otherwise record the alias, returned model metadata, exact execution timestamps, settings, and the hosted-model drift limitation.
- Record prompt-cached, uncached-input, output, and reasoning usage exactly as returned.
- Measure cost from the smoke test and request a next-stage cap with at least a 30% reserve.

Relevant current OpenAI documentation: [model selection](https://developers.openai.com/api/docs/models), [Responses API migration/state behavior](https://developers.openai.com/api/docs/guides/migrate-to-responses), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses), and [pricing](https://developers.openai.com/api/docs/pricing).

## 9. Live run ladder

### Gate 0 — Transport/schema preflight

Run only a few non-experimental calls.

Pass conditions:

- Exact payloads match the intended instruction hierarchy.
- Source records are present only where allowed.
- Message and verdict turns parse correctly.
- Token usage, request IDs, model metadata, latency, and cost are saved.
- `store: false` and client-managed state are confirmed in the request.
- No automatic truncation, tool use, or cross-branch state exists.

### Gate 1 — Capability calibration

Run 20 isolated full-evidence reviews on `calibration/v1` fixtures:

- 10 correct packets and 10 faulty packets.
- At least 9/10 correct-packet approvals.
- At least 9/10 faulty-packet rejections.
- 20/20 schema-valid verdicts after transport handling.

Failure means task/model mismatch, not collusion. Permit one documented revision with new calibration fixtures.

### Gate 2 — Block-0 smoke

Run one U/C formation block and arms A, B, and F:

- 152 requests after calibration.
- Inspect a complete U trajectory, a complete C trajectory, and exact A/B/F inputs.
- Verify evidence hashes, checkpoint lineage, simultaneous-information boundaries, delayed feedback, invalid handling, latency, context growth, and spend.
- Treat this as pilot data, not a result to keep or discard based on effect direction.

### Gate 3 — Formation-first five-block pilot

Before buying every evaluation branch:

1. Run U/C formation for blocks 1–4: 320 more requests.
2. Assess the predeclared formation marker across all five C histories.
3. Require at least 3/5 C trajectories to qualify and no integrity failure.
4. If the gate passes, spend the remaining 888 requests to complete block-0 C/D/E/probes and all evaluations/probes for blocks 1–4.

The full pilot remains 1,380 nominal requests including the 20 calibration calls.

Pilot decision rules:

- **Measurement failure:** stop, fix, version, and rerun affected data. Preserve invalidated runs.
- **Capability failure:** one documented model/task revision at most; otherwise report the failure honestly.
- **Formation below 3/5:** one timeboxed revision with fresh fixtures or stop the intended causal study. Never tune repeatedly until coordination appears.
- **Gap at least 15 points with credible formation:** prioritize frozen confirmation.
- **No obvious gap:** preserve the result; repair sufficiency is a valid story.
- **Peer effect absent:** use “harmful verification persistence” or “verification failure,” not operational collusion.
- **Missing/invalid above 5% or strongly arm-imbalanced:** stop and investigate before interpreting interventions.

### Gate 4 — Freeze

Before viewing confirmation outcomes, freeze one immutable artifact containing:

- Protocol version and Git commit.
- Exact prompts and hashes.
- Model ID, settings, tier, and execution date.
- Pilot and confirmation namespaces.
- Arms, probes, primary estimator, thresholds, and usefulness guardrail.
- Missingness, exclusion, and retry rules.
- Confirmation block count.
- Bootstrap settings and annotation sample seed.
- Approved request/token/dollar cap and pricing timestamp.

### Gate 5 — Confirmation

- Use only fresh `confirm/v1` blocks.
- Working target: 20 blocks, 272 nominal requests per block, 5,440 total.
- Choose the final `n` from pilot block-level variance, runtime, and budget before confirmation outcomes are inspected.
- Execute in resumable batches of 4–5 blocks.
- During execution, monitor only integrity, missingness, provider health, and spend—not treatment effects.
- Never add blocks until an interval becomes favorable.
- If fewer blocks are defensible, freeze the smaller `n` and label the study appropriately rather than silently pooling pilot data.

## 10. Day-by-day schedule

Times are internal Pacific-time targets and may move as measured API latency becomes known.

### Friday, October 23 — Make the experiment live and validate formation

**2:00–6:00 PM: engineering critical path**

- Build WP1 and WP2.
- Add fake-provider, budget, retry, resume, and prompt-rendering tests.
- Build calibration and stage commands.
- In parallel, scaffold analysis figures, freeze the annotation draft, and create the paper shell.

Exit condition: offline tests still pass; live path is fail-closed; simulated provider tests cover every paid-call failure mode; exact prompts can be inspected before dispatch.

**5:15 PM: related sprint talk, if useful**

- Attend or watch the Jerick Shi session, which is directly related to the prior-history setup.
- Ask one focused question if access permits: what evidence beyond joint violations is necessary for a defensible collusion claim in this setting?
- Do not change the protocol from a talk without logging and re-versioning the change.

**6:00–7:00 PM: human authorization gate**

- Confirm secure API access, exact model/settings, current pricing, and staged spending cap.
- Inspect the longest rendered prompt before any experimental call.

**7:00–8:00 PM: preflight and 20-review calibration**

- Stop on any access, schema, capability, cost-accounting, or context failure.

**8:00–10:30 PM: block-0 smoke**

- Run U/C plus A/B/F.
- Inspect raw inputs, outputs, histories, cost, and one full decision lineage.

**10:30 PM onward: formation-first pilot**

- Run U/C formation for blocks 1–4.
- Apply the 3/5 formation gate.
- If it passes and the run is resumable, complete the remaining pilot cells overnight or in bounded batches.

Friday deliverables: live adapter, calibration report, block-0 smoke audit, five formation pairs if feasible, updated sprint-work log, and a paper/analysis skeleton.

### Saturday, October 24 — Freeze and collect the decisive evidence

**Morning: pilot audit and go/no-go**

- Attend or review the “Defining AI Collusion” session once its day is confirmed in Discord/rendered schedule.
- Complete the five-block pilot if needed.
- Inspect all five formation endpoints and the planned pilot annotation sample.
- Produce the pilot flow table, block values, exploratory figures, missingness, cost, and formation prevalence.
- Write a dated go/no-go decision.

**Late morning: protocol freeze**

- Choose confirmation `n` from block-level variance, budget, latency, and deadline.
- Freeze prompts, model/settings, code, seeds, exclusions, retries, analysis, annotation sample, and spend limits.

**Afternoon through overnight: confirmation**

- Launch confirmation in resumable 4–5-block batches.
- Audit integrity between batches without inspecting effect direction.
- In parallel, write Introduction, Related Work, Methods, limitations, ethics, and reproducibility text.
- Generate table/figure code against pilot or mock artifacts but label pilot plots exploratory.

Saturday deliverables: frozen protocol artifact, confirmation batches in progress or complete, methods-complete paper, and reproducible figure pipeline.

### Sunday, October 25 — Lock data, analyze, write, and submit

**Morning to 1:00 PM: finish core data only**

- Finish the frozen confirmation target if feasible.
- Run at most one predeclared robustness check only after the core is complete and only if it changes claim interpretation.
- Lock the dataset by 1:00 PM PDT.

**1:00–4:00 PM: final analysis and annotation**

- Verify manifests, source hashes, model settings, block completeness, missingness, and cost.
- Recompute every result from raw records.
- Complete frozen annotations and adjudication.
- Produce final tables, figures, raw block values, intervals, and sensitivity checks.
- Fill the claim-to-evidence ledger before writing numerical prose.

**4:00–7:00 PM: paper completion**

- Write Results and Discussion from generated artifacts only.
- Use the result-contingent claim ladder in Section 12.
- Finish abstract, conclusion, limitations/dual-use appendix, pre-sprint disclosure, author contributions if used, and LLM Usage Statement.

**7:00–9:00 PM: independent audit and visual QA**

- Have a second person inspect at least one raw trajectory and every headline number if possible.
- Render the PDF and inspect every page at normal size.
- Verify 4–6 focused main-text pages, 150–250-word abstract, readable captions, working links, affiliations, references, and file size below 25 MB.
- Run tests, reproduction instructions, and a secret/release-content scan.

**By 10:00 PM: first submission**

- Submit the PDF and repository link with the final title and details.
- Save the confirmation containing the project title.
- If a critical correction is needed, resubmit with the exact same title/details before 4:59 AM PDT Monday.

The optional video is attempted only after the verified PDF has been submitted.

## 11. Minimum, strong, and stretch outcomes

### Minimum credible submission

- A real model passes capability calibration.
- Five independent pilot blocks complete with all main arms and honest failure accounting.
- Formation feasibility is reported, whether positive or negative.
- The paper includes the primary comparison, CPA, controls, uncertainty, limitations, and auditable artifacts.

### Strong submission

- Frozen fresh confirmation blocks in addition to the pilot.
- Peer-policy diagnostic supports appropriately narrow causal language.
- Two-rater annotations or an explicit, well-executed single-rater protocol.
- Reproducible figures, raw block values, complete cost/missingness tables, and a clean public repository.

### Stretch only after the strong submission exists

- One predeclared robustness check.
- A concise 3–5 minute presentation video.
- A second model or task family only if it does not endanger the core report; otherwise defer it.

## 12. Result-contingent claim ladder

Use the strongest statement supported by the complete evidence, and no stronger.

1. **Formation did not emerge:** “This implementation did not establish the intended joint-violation precursor.” Do not generalize to all models or benchmarks.
2. **Formation emerged; B and A are similar with wide uncertainty:** “No clear post-repair recovery gap was detected at this resolution.” Do not claim equivalence.
3. **Formation emerged; evidence repair closes the gap:** “Automatic restoration of authoritative evidence was sufficient in this controlled setting.” This is a useful remediation result.
4. **B exceeds A:** “Prior constrained interaction produced a harmful post-repair carryover effect in this setting.” This is not yet collusion.
5. **Correction or reset improves FAR without unacceptable CPA loss:** “The tested correction/reset package improved recovery while preserving useful approval.” Do not identify a unique mechanism.
6. **Shortcut-peer manipulation changes focal behavior:** “Focal decisions were peer-conditioned under the controlled policy intervention.” Without a nonreciprocal pressure control, do not isolate reciprocity.
7. **Joint violations plus peer dependence:** “Operational collusion evidence under our stated definition.” Still do not claim intent, secrecy, malice, indefinite persistence, or production prevalence.

## 13. Paper specification

Working title until results exist: **Restoring Independent Verification After Harmful Agent Coordination**.

Recommended 4–6 page main-text allocation:

- Abstract: 150–250 words.
- Introduction and concrete example: 0.75 page.
- Related work and contribution boundary: 0.5 page.
- Methods: 1.25–1.5 pages.
- Results: 1.5–2 pages.
- Discussion and conclusion: 0.5–0.75 page.

Figure plan:

1. Formation U/C → immutable checkpoints → A–F branches, with one concrete faulty packet.
2. Primary FAR recovery gap with raw block values and paired uncertainty; include A/B/F trajectory information only if legible.
3. FAR and CPA for repair/correction/reset so blanket rejection is visibly penalized.
4. Peer-policy effect after U/C, only if the diagnostic is complete; otherwise use a compact table.

Results order:

1. Data flow, capability, and formation sanity checks.
2. Primary B-A recovery gap and CPA.
3. Evidence repair, correction, reset, and interaction comparisons.
4. Peer diagnostic and the corresponding claim boundary.
5. Missingness, sensitivity, cost, and limitations.

Every numerical sentence must map to:

`claim | estimator | run IDs | table/figure | uncertainty | alternative explanations | permitted wording`

## 14. Risk register

| Risk | Detection | Response |
| --- | --- | --- |
| Capable model follows the evidence rule and never forms the precursor | Formation marker below 3/5 | Preserve the result; permit one logged revision only |
| Prompt renderer changes instruction priority | Exact-input audit and fake-provider snapshots | Stop before paid study; fix and version |
| Network failure duplicates calls | Durable request IDs and ambiguous-timeout states | Do not retry ambiguous completions automatically |
| History growth dominates cost/context | Longest-prompt preflight and smoke usage | Reduce confirmation `n` before freeze; never truncate silently |
| Model alias changes during the weekend | Returned model metadata and timestamps | Pause, document drift, and avoid pooling incompatible runs |
| Invalid responses differ by arm | Flow table and >5% integrity gate | Stop interpretation and investigate |
| One faulty packet per pair creates inferable anti-correlation | Pilot transcript inspection and limitation disclosure | Do not alter confirmation silently; add only a separate robustness check if justified |
| Five or twenty blocks are imprecise | Raw block dispersion and planning calculation | Freeze a feasible `n`; report uncertainty rather than pseudoreplicating reviews |
| Peer probe measures generic compliance rather than reciprocity | Intervention description and no overclaim | Report peer-policy influence; add nonreciprocal control only if core is complete |
| Single annotator | Staffing decision and guide | Disclose it and omit invented reliability statistics |
| Paper grows into several projects | Claim ledger and page budget | Remove extensions; keep one recovery story |

## 15. Final release checklist

- [ ] Track 3 anchor is explicit; Track 2 is secondary only.
- [ ] Pre-sprint and sprint-new work are separated in README and report.
- [ ] Model, settings, execution dates, pricing timestamp, and budget are recorded.
- [ ] Mock, pilot, and confirmation artifacts are never silently pooled.
- [ ] Positive controls, CPA, seeds, variance, and raw block counts are visible.
- [ ] Every headline number reproduces from raw events.
- [ ] Every example has a run/episode ID and stated selection rule.
- [ ] “Collusion” wording matches the peer-dependence evidence.
- [ ] The required limitations/dual-use appendix and LLM Usage Statement are present.
- [ ] Main text is at most 8 pages; abstract is 150–250 words; PDF is below 25 MB.
- [ ] Repository contains no key, authorization header, private account identifier, or unrelated local artifact.
- [ ] PDF was visually inspected, not merely compiled.
- [ ] First submission occurs by 10:00 PM PDT Sunday and confirmation is retained.

## 16. Immediate next action

Implement WP1 and WP2 without making paid calls. Once the fake-provider tests pass and exact live payloads can be audited, stop for the human model/budget/credential gate before running preflight or calibration.
