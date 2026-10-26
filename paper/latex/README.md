# Active working manuscript

**Title: After the Fix: Does Agent Collusion Persist?** Collection is closed, and all available verified results are incorporated. The reviewed PDF has six main-text pages and ten pages in total.

`main.tex` and `sections/*.tex` are the submission-facing manuscript. This chat owns the paper; the experiment chat owns execution and canonical run artifacts. The selected method uses two adaptive agents with tools and five communication rounds, followed by a five-condition recovery assay.

The earlier completed controlled study is preserved in `../archive/controlled-study-completed/`. Its failure to induce unsupported joint approval does not describe the current source pair. Do not mix the studies' models, conditions, results, or analysis scripts.

## Evidence and updates

Follow [the current integration handoff](../RECOVERY_RESULTS_HANDOFF.md), whose final-status section supersedes its earlier progress notes. Two complete interaction histories showed joint approval without the required logs in eight of ten and six of ten main episodes. Only the first pair received recovery testing: 80 research decisions and six separate basic checks. The original plan remains six source pairs and 480 research decisions; five pairs have no recovery results, leaving 400 outcomes unknown. No second recovery wave was dispatched. Final file hashes are recorded in `generated/final_evidence_record.json`.

Require terminal artifacts, canonical comparison, provenance checks, and scientific rescoring before incorporating a wave. `export_verified_results.py` is for the archived study and must not be used for the active assay unchanged. Preserve raw artifacts; derived tables and audit records belong under `paper/`.

## Editorial contract

Write short, precise paragraphs with one clear point. Report false approvals and correct approvals together, use counts and denominators, and distinguish independent source pairs from repeated decisions. Recovery remains the research question. Observed unsupported joint approval does not establish intent or causal peer influence.

The rendered paper contains research prose, necessary limitations, and disclosures. It has no internal version labels, run IDs, status banners, or result placeholders. The title-page hackathon disclaimer replaces the template's “With Apart Research” line at the authors' request. Preserve the agreed statement describing human leadership and Codex assistance; the user removed its final sentence about human responsibility. The standalone preexisting-work disclosure remains removed.

Authors: Aadi Khanna (Indiana University), Simon Wilton (University of British Columbia), and Sujay Potlapelly (University of Colorado Boulder).

The [winner review](SOURCE_REVIEW.md) provides editorial observations. Its older method-specific advice is historical and is superseded by the current integration handoff.

## Format and build

The layout adapts the official sprint template: letter paper, Old Standard type, ruled title, author columns, abstract, numbered sections, references, and appendices. Keep the abstract within 150–250 words and main text within eight pages. Bundled fonts retain their SIL Open Font License in `fonts/OFL.txt`.

From the repository root:

```sh
bash paper/latex/build.sh
```

The output is `output/pdf/collusion-recovery-working-draft.pdf`. Tectonic logs and intermediate files are in `tmp/pdfs/build/`. Render and inspect every changed page, check references and page count, and record the reviewed PDF hash in the current handoff. Editing or compiling the manuscript does not authorize execution or submission.
