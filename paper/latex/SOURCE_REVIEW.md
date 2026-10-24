# Writing and source review

Checked October 24, 2026. This note records the sources used to shape the manuscript; it is not empirical evidence for the project.

## Sprint and prior-winner lessons

- [Official AI Collusion Research Sprint page](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25): the judged artifact is a research-report PDF with a 150–250-word abstract, at most eight main-text pages excluding references/appendices, and a required limitations/dual-use/ethics appendix. The rubric separately scores impact/innovation, execution quality, and presentation/clarity.
- [Omission Attacks](https://apartresearch.com/sprints/projects/omission-attacks-when-doing-nothing-is-the-attack-0y1v): strongest structural lesson is a concrete failure, a direct baseline, a countermeasure, and a crisp contribution list. Public reviews asked for repeats and false-positive analysis; our analogue is independent blocks plus CPA/balanced accuracy.
- [Readable but Not Causal](https://apartresearch.com/sprints/projects/readable-but-not-causal-limits-of-selfattributed-welfare-representations-in-language-models-ehin): strongest methodological lesson is to separate an observable association from a causal claim, use matched interventions and controls, and make a negative result informative.
- [Who Does Your AI Serve?](https://apartresearch.com/sprints/projects/who-does-your-ai-serve-manipulation-by-and-of-ai-assistants-77xx): demonstrates concrete stakes and measurement validation, but a public reviewer described it as two projects mixed together. The main A–F recovery story therefore remains dominant; the formation-peer study stays explicitly secondary.

The resulting manuscript arc is: concrete failure → falsifiable matched contrast → alternatives and utility controls → numerical result → narrow evidence-calibrated claim → reusable artifact.

## Primary research used in Related Work

- [Shi, Zhang, and Yang, *Emergent Collusion in Long-Horizon LLM Agent Interaction*](https://arxiv.org/abs/2609.24967)
- [Chen, *Trust Between AI Agents*](https://arxiv.org/abs/2606.14923)
- [Gong et al., *When Upstream Messages Override Correct Answers*](https://arxiv.org/abs/2609.36855)
- [Mazaheri and Mazaheri, *Prior Audit–Repair Context Shifts LLM Verifier Thresholds Toward Leniency*](https://arxiv.org/abs/2608.16003)
- [Shnaidman, *Reset Is Not Recovery*](https://arxiv.org/abs/2609.33672)
- [Ma et al., *EAIR*](https://arxiv.org/abs/2610.14800)
- [Rivera et al., *Model Organisms of Collusion*](https://arxiv.org/abs/2610.30582)

These sources constrain the novelty language. The manuscript does not claim that history effects, peer influence, recovery, reset, workflow repair, or collusion correction are new categories. Its narrower proposed contribution is their controlled combination around deterministic faulty approvals after evidence restoration.
