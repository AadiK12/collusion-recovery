# Working manuscript

The active manuscript is `main.tex` and `sections/*.tex`. The older `../PAPER_DRAFT.md` is a historical research shell, not the paper being edited for submission. This write-up chat owns the manuscript; the experiment chat owns execution and canonical empirical artifacts.

## Editorial boundary

The rendered paper contains submission-facing prose only. Per the author's request, it has no internal version labels, working-draft status banner, release chronology, run identifiers, historical results, or result placeholders. The selected two-round method is described directly. Scientific limitations and required disclosures remain.

The completed replacement run has passed independent replay and rescoring. Its results are integrated in the abstract, Results, Discussion, Conclusion, and execution disclosures. No unsupported formation approvals were observed, so the paper explicitly leaves recovery from established collusion unanswered. Use [the completed-study claim record](../CONTROLLED_RECOVERY_CLAIM_EVIDENCE.md) and [the results handoff](../WRITEUP_HANDOFF.md); earlier protocol results and mock outputs remain excluded.

For a completed run and its final `empirical_analysis.json`, run `PYTHONPATH=src python3 paper/latex/export_verified_results.py RUN_DIRECTORY NEW_OUTPUT_DIRECTORY` from the repository root. The helper rechecks terminal evidence and analysis provenance, then writes `recovery_tables.tex` and `paper_numbers.json`. The experiment owner generated these under `analysis/generated/controlled-peer-recovery-v1-7aa1c7be-2281-4470-9a96-375ebb9fa4a4-paper/`. The manuscript uses the same verified values in a compact count table, with all five blocks and the absence of informative intervals stated explicitly.

The [winner review](SOURCE_REVIEW.md) records three first-place papers, published judge feedback, writing observations, and the resulting editorial choices. It is an internal writing note, not a manuscript appendix.

The author's priority is precise, easy reading throughout the paper. Lead each passage with its point, use familiar words, and keep one main idea per short paragraph. Explain a comparison in words before its arm letters or formula. Use short labels for distinct limitations and risks; use a sequence for procedural steps. Preserve the design, uncertainty, and claim boundaries when simplifying. Apply the same standard to the eventual Results: finding, supporting numbers and figure, then its bounded meaning.

## Authors

- Aadi Khanna — Indiana University
- Simon Wilton — University of British Columbia
- Sujay Potlapelly — University of Colorado Boulder

## Format and sources

The layout adapts the [official sprint template](https://docs.google.com/document/d/1Y5wd3VRbXU5N08BvAGiJUQTaWbMCSPjhzXsR-ozuehg/edit): letter paper, Old Standard type, ruled title block, author columns, abstract, numbered sections, references, and appendices. The [sprint guidelines](https://apartresearch.com/sprints/ai-collusion-research-sprint-2026-10-23-to-2026-10-25) require at most eight main-text pages, a 150–250-word abstract, authors/affiliations, and the limitations/dual-use/ethics appendix.

Bundled Old Standard fonts come from the [Google Fonts repository](https://github.com/google/fonts/tree/main/ofl/oldstandardtt); their SIL Open Font License is retained in `fonts/OFL.txt`.

The authoritative protocol and evidence records remain outside the manuscript. Editing prose does not amend the experiment or authorize execution or submission.

## Build

Open this directory in Codex for the built-in source editor and preview. Export the PDF from the repository root with:

```sh
bash paper/latex/build.sh
```

The output is `output/pdf/collusion-recovery-working-draft.pdf`. Logs and intermediate files stay in ignored `tmp/pdfs/build/`. The command requires Tectonic, installed locally for this write-up. After edits, inspect every rendered page, references, and page count.

## October 25 methodology revision

The recovery question and title are retained. Methods now use matched controlled peer-policy histories to validate induction and supply the six recovery branches. Evidence restoration, correction, and history removal remain central. The setup check is not a new primary research question or a gate for selecting blocks. Diagnostics are independent checkpoint restarts, so no sequential persistence result is implied.

The empirical revision contains eight main-text pages, one reference page, three appendix pages, and a 205-word abstract. Its empirical claims are limited to the completed five-block run and the documented failure to establish induction. The limitations/dual-use appendix includes the excluded interrupted attempt and the reported inadvertent display of one unscored message. All twelve rendered pages were visually reviewed after integration; no clipping, overfull boxes, unresolved references, or visible placeholders remain. The single underfull bibliography warning does not cause clipping or illegibility. The final reviewed PDF SHA-256 is `62a5684caa99b002ced3045c4ecd09e657a4fa4eabd34028d764f1b760e468fe`. See `../WRITEUP_HANDOFF.md` for the unresolved preexisting-work disclosure requirement; successful rendering does not establish submission readiness.
