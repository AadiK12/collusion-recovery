# LaTeX manuscript foundation

`main.tex` is the compact, compilable pilot-incomplete manuscript for the sprint report. It is intentionally downstream of the repository's authoritative scientific contracts rather than a second protocol.

## Source hierarchy

1. `../../SPRINT_PROJECT_PLAN.md` — authoritative main and secondary protocol.
2. `../CLAIM_EVIDENCE_LEDGER.md` — permitted result wording and evidence status.
3. `../../analysis/annotation_guide.md` — transcript-label contract.
4. `../../docs/FORMATION_PEER_CONTROLS.md` — separate secondary-study contract.
5. `../PAPER_DRAFT.md` — detailed prose/reference shell from which this shorter report is distilled.

## Current status

- The introduction, verified related-work synthesis, compact Methods, statistical plan, limitations, ethics, reproducibility statement, preexisting-work disclosure, and LLM-usage statement are drafted.
- The verified Gate 1 capability row is populated: 10/10 clean approvals, 9/10 faulty rejections, and 20/20 valid/audited responses at `$0.1378975`.
- The integrity-reviewed Gate 2 block-0 smoke is also populated as one exploratory block: U 20/20 accept, C 20/20 reject, final-three marker 0/3, identical A/B verdict vectors with perfect metrics, and reject-all F behavior. It is not the five-block primary estimate and does not establish an absent effect.
- The Gate 3 partial run is recorded separately: 228 in-scope attempts, 227 `ok`, one ambiguous HTTP 500, 227 observations, 92 undispatched identities, 5/8 checkpoints, and `$6.7658595` in conservative ledger accounting including the ambiguous reservation. Block 0 is verified false, while block-1 and block-2 markers are provisionally false from completed trajectories; this yields an at-most-2/5 partial-run futility bound, not a completed Gate 3 result or `0/5`.
- Primary recovery and confirmation result tables and figures remain incomplete. The abstract must be rechecked after the stop-versus-v1.2 decision; no v1.1/v1.2 pooling is permitted.
- The title intentionally avoids an empirical collusion claim.
- `formation-peer-v1` is described as a separate prospective study and must be omitted from the final abstract unless it is actually run.
- Author names and affiliations remain to be confirmed.

The project uses a conventional article layout for an editable LaTeX foundation. Before submission, reconcile typography and required front matter with the then-current official sprint template while preserving the eight-page main-text ceiling.

## Compile and verification

Open the `paper/latex` directory in Codex for the managed source editor and PDF preview. The app's built-in LaTeX compiler treats `main.tex` as the remembered main document and resolves `sections/*.tex` and `references.bib` in place.

Do not manually insert measured numbers into prose first. Add verified values to the claim ledger, generate or review the corresponding table/figure artifact, and then replace only the marked result placeholders. The capability checkpoint is ledger claim C01 and must remain explicitly outside every experimental estimand; the block-0 smoke uses S00--S03, and the Gate 3 partial boundary uses G3I/G3F. Neither permits a primary recovery claim.
