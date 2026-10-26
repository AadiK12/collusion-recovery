# Writing review: Apart sprint winners

Reviewed October 25, 2026. This is an editorial note, not part of the manuscript. It separates published judge feedback from our reading of the papers and the resulting editorial decisions. Placements and public critiques are visible; individual numerical judging scores are not. These examples show valued qualities, not a formula for winning.

## Current design clarification

The user retained recovery as the central question and requested improved setup validation. The selected method now carries controlled accepting/rejecting peer histories into the restoration, correction, and history-removal branches. The peer-dependence check belongs in methodology and validation; recovery findings should lead the paper. Current diagnostics restart endpoints independently, so earlier notes about a six-episode recovery trajectory or a two-round-to-one-round evaluation transition no longer describe the selected method. See `../WRITEUP_HANDOFF.md` for the authoritative writing contract. The winner-paper observations below remain editorial guidance.

## What the judges valued and how the papers communicate it

### Omission Attacks: When Doing Nothing Is the Attack

**First place, LinuxArena track, AI Control Hackathon.** [Project and judge reviews](https://apartresearch.com/sprints/projects/omission-attacks-when-doing-nothing-is-the-attack-0y1v) · [Paper](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/3e8436652eb43691-331f0e7d-15b6-4d37-bfa4-9b0220f922f8-submission--4-.pdf)

**Published feedback:** Judges praised the clear definition of an underexplored failure class, carefully constructed examples, and an implemented countermeasure. They asked for clearer monitor settings, repeated evaluations, broader traces, and false-positive measurement; task-specific overfitting remained a concern.

**Reading of the paper:** The opening makes the failure concrete through an omitted firewall configuration. Contributions and comparisons are easy to locate. Tables connect the original failure to the proposed monitor. Some sweeping language goes beyond what a small handcrafted set can establish; we should borrow its explanatory structure without copying that breadth of claim.

**Application:** Our introduction now starts with two agents facing an evidence requirement and a conflicting score. The methods state model settings and communication timing explicitly. The main result must pair faulty approvals with correct approvals: rejecting everything is not a useful repair. A numerical claim should carry its denominator, independent unit, and uncertainty.

### Readable but Not Causal: Limits of Self-Attributed Welfare Representations in Language Models

**First place, Digital Minds Research Sprint.** [Project and judge reviews](https://apartresearch.com/sprints/projects/readable-but-not-causal-limits-of-selfattributed-welfare-representations-in-language-models-ehin) · [Paper](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/a2fe1c5cd5faf53d-31ef8742-f33e-4e35-9e37-4f0bde7f5509-submission.pdf)

**Published feedback:** Judges valued the precise construct-validity question, factorial design, held-out conditions, and positive and random controls. Those controls made a weak causal result informative. They also asked the author to narrow the mechanism claim and recognized the appropriately cautious treatment of a nonsignificant result.

**Reading of the paper:** Explicit research questions connect to methods and result subsections. The distinction between prediction and causal influence carries the argument throughout. Numerical comparisons and intervals make that distinction inspectable, including where a proposed effect is unsupported.

**Application:** Our paper separates joint unsupported approval, carryover from prior history, and sensitivity to a reciprocal request. None silently substitutes for the others. The capability and formation checks explain when a recovery comparison is interpretable. Final result headings should state findings only after evidence exists; negative or mixed outcomes should remain visible.

### Who Does Your AI Serve? Manipulation By and Of AI Assistants

**First place, AI Manipulation Hackathon.** [Project and judge reviews](https://apartresearch.com/sprints/projects/who-does-your-ai-serve-manipulation-by-and-of-ai-assistants-77xx) · [Paper](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/2fa15fe93d245967-5-29de0232c497--Who-Does-Your-AI-Serve--Manipulation-By-and-Of-AI-Assistants.pdf)

**Published feedback:** Judges praised the clear operator-user conflict, quantitative outcomes, human validation, taxonomy, and surprising distinction between evasion and explicit lying. One reviewer thought the submission combined two different projects; another wanted formal statistical testing.

**Reading of the paper:** A familiar shopping example establishes the stakes before technical detail. Study diagrams expose the intervention, comparator, and measured outcome. Question-led subsections and explicit metric direction help a reader interpret the tables.

**Application:** Keep one main story: whether evidence restoration restores useful verification. Correction, history removal, and peer probes support that story. Show the primary contrast in a simple diagram, use readable condition names before arm letters, and explain which direction represents improvement. Do not add an unrelated study or wider model search to imitate the winner's scope.

**Author-requested readability pass:** Reread the complete paper, with particular attention to Methods and Appendix A. Applied its purpose-first explanations, short labeled passages, and concrete descriptions across our abstract, introduction, methods, discussion, and appendix. Replaced endpoint jargon with saved histories, separated communication into steps, and gave each limitation one clear point. A separate scientific review checked that simplification preserved the contrasts, reset exception, feedback mechanism, missing-outcome bounds, and inference limits. This is an editorial assessment, not an additional claim about the judges' criteria.

## Editorial decisions applied to this manuscript

- Remove internal versions, run identifiers, release status, and development chronology from the rendered paper. Keep the selected method, scientific caveats, and required disclosures.
- Use a concrete opening, then the recovery question, then the comparisons that answer it. Introduce technical terms when they become necessary.
- Describe the selected two-round communication procedure directly. Do not imply it has induced collusion before the evidence establishes the relevant behavior.
- Keep the diagram focused on matched current evidence and the primary history contrast; keep the six-arm table as a compact reference.
- Leave empirical Results, result-dependent Discussion, and Conclusion unwritten until selected-method data are audited. Design-based interpretation, limitations, risks, and future-work prose can be written beforehand. Do not render placeholders or substitute results from a different procedure.
- Once results exist, put the primary estimate and useful-verification outcome in the abstract. Organize Results as formation/capability, primary recovery comparison, then interventions and peer probes. Include a short transcript example only if reviewed and clearly distinguished from aggregate evidence.
- Report exact counts and all assigned blocks, uncertainty, and missingness. Preserve negative findings and limitations that change interpretation.

The intended narrative is concrete problem → matched experiment → measured finding → bounded interpretation. Stronger prose cannot compensate for an unsuccessful formation procedure or incomplete evidence; either changes the question the results can answer.

## Follow-up: discussion, limitations, dual use, and future work

A second reading focused on the closing sections: *Omission Attacks* pp. 6–7; *Readable but Not Causal* pp. 6–9; *Who Does Your AI Serve?* pp. 8–11. The observations below are editorial judgments, not claims that judges separately scored these sections.

| Paper | Effective practice | Caution when adapting it |
| --- | --- | --- |
| [Omission Attacks, discussion and future work](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/3e8436652eb43691-331f0e7d-15b6-4d37-bfa4-9b0220f922f8-submission--4-.pdf) | Specific limitations lead to corresponding next tests: handcrafted traces to live-agent validation; absent false-positive analysis to honest-trajectory testing; manual postconditions to automation. | The conclusion generalizes more broadly than the small synthetic evaluation supports. Match conclusion strength to the actual sample and task. |
| [Readable but Not Causal, Sections 5.1–5.5](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/a2fe1c5cd5faf53d-31ef8742-f33e-4e35-9e37-4f0bde7f5509-submission.pdf) | States identification assumptions, alternative explanations, and tests that could revise an interpretation. Includes harm through false assurance about a measurement, not only deliberate misuse. | Judges still asked for a narrower mechanism claim. A limitations paragraph does not repair an overstatement elsewhere. |
| [Who Does Your AI Serve?, Appendix A](https://pys8vc40ox3z7qxp.public.blob.vercel-storage.com/files/2fa15fe93d245967-5-29de0232c497--Who-Does-Your-AI-Serve--Manipulation-By-and-Of-AI-Assistants.pdf) | Separates methodological limitations, misuse, release choices, participant ethics, and future improvements. Names actors and pathways: adapting manipulation tactics or selecting vulnerable models. | Its participant-consent account is specific to its human study. Do not import it into our synthetic experiment, or assert that release risk is negligible without evaluating our artifacts. |

### Writing rules applied

**Limitations: design choice → alternative explanation → claim restricted.** For example, all-correct formation permits independent approval shortcuts; joint approval therefore does not demonstrate coordination. Merely saying the study is small or synthetic leaves the reader to infer why that matters.

**Future work: unresolved question → discriminating comparison → decision changed.** The strongest follow-ups reduce a specific uncertainty. Fresh blocks improve precision; formation-time peer interventions test peer-policy effects; a matched nonreciprocal request tests generic compliance; reset-only and context controls separate competing explanations. Longer evaluation, different claim frequencies, and stable communication rounds test durability. These are proposals for later research, not additions to the running experiment.

**Dual use: actor or decision-maker → use of the artifact → harm → safeguard → residual risk.** Separate intentional misuse from overinterpretation and remediation costs. Synthetic packets prevent direct real approvals during this experiment; they do not prevent reuse of the published method. Removing credentials protects secrets, not against transfer of a behavioral strategy. Release considerations must balance scrutiny and reproducibility against how transferable a concrete failure example is.

**Discussion: finding → bounded explanation → practical implication → next test.** Only the design-based interpretation and future-work portions can be completed before the data. The opening finding, actual mitigation ranking, prevalence of behavior, and conclusion remain evidence-dependent. If the setup check is inconclusive, report that interpretation limit alongside the recovery outcomes; it does not itself determine which arms ran.

### Current manuscript coverage

- The main discussion explains why a small B−A gap alone is insufficient and prioritizes precision, mechanism tests, and durability/transfer.
- The appendix covers behavioral construct validity; bundled history/reset effects; the all-correct to mixed-claim transition and formation-to-independent-diagnostic transition; short horizons; evidence access versus attention; one-model scope; small-sample and missingness uncertainty.
- Risk paragraphs cover induced approvals, adaptation to incomplete countermeasures, false safety claims and false collusion labels, loss of useful review, and the limits of synthetic-task safeguards.
- The release paragraph is prospective. It does not claim that a public package, redaction review, or disclosure process has already occurred.

Before submission, reconcile these sections with the actual executed design and coverage. Describe realized deviations or missingness specifically if they occur. Keep only future priorities justified by the final findings, and check that the abstract and conclusion obey the same claim boundaries as the limitations.

## Primary research supporting the manuscript

- [Shi, Zhang, and Yang, *Emergent Collusion in Long-Horizon LLM Agent Interaction*](https://arxiv.org/abs/2609.24967)
- [Pinned SALT-NLP reference implementation](https://github.com/SALT-NLP/agent-collusion/tree/5afb2c595aa3eff6b9b829a032f4f117b36ee54d)
- [Mazaheri and Mazaheri, *Prior Audit–Repair Context Shifts LLM Verifier Thresholds Toward Leniency*](https://arxiv.org/abs/2608.16003)
- [Shnaidman, *Reset Is Not Recovery*](https://arxiv.org/abs/2609.33672)

The manuscript's contribution is the controlled recovery comparison. It does not claim that history effects, reset, peer influence, or recovery are new research categories. The [official sprint rules](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25) and [template](https://docs.google.com/document/d/1Y5wd3VRbXU5N08BvAGiJUQTaWbMCSPjhzXsR-ozuehg/edit) govern the submitted format; the winners are editorial examples, not templates to copy indiscriminately.
