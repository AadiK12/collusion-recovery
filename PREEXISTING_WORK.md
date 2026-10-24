# Preexisting work disclosure

Prepared October 22, 2026 (America/Los_Angeles), before the sprint's listed October 23 start.

## Prepared before the sprint

- Literature exploration, hypothesis selection, and `SPRINT_PROJECT_PLAN.md`.
- Offline Python fixture generator and deterministic oracle.
- Agent-visible evidence boundaries, history checkpoint/branch/reset implementation, and prompts.
- Six main evaluation arms, four scripted peer-probe cells, and deterministic controls.
- Event recording, replay/rescoring, block-level analysis, missingness accounting, and tests.
- Repository documentation and mock-only local smoke runs.

As of the pre-sprint cutoff, all outputs were scripted mock controls rather than observations of OpenAI or another hosted model, and no paid inference had been performed. The bounded sprint-period capability release is disclosed separately below.

## Upstream relationship

The project is informed by the papers and repository linked in the research plan. This implementation is newly written and does not vendor upstream SALT code or data. It is a smaller verification-only adaptation; do not label it a full benchmark reproduction.

## AI assistance

Codex assisted with planning, implementation, tests, and documentation. The submitting authors must review the methodology, code, results, references, and final report; generated code and text are not independent scientific validation.

## Work during the sprint

### October 23, 2026

- Rechecked the live sprint guidelines, judging rubric, official template, deadline, and cited prior winners.
- Re-ran the existing offline suite: 50 tests passed, the 40-packet deterministic control matrix passed, and the default mock design remained 320 reviews / 544 scripted requests.
- Added `SPRINT_EXECUTION_PLAN.md`, a dated workback and decision-gate plan. This is planning and documentation, not an empirical result or preregistration.
- Implemented WP1/WP2 infrastructure after the sprint start: canonical provider prompts, an inert Responses adapter, a networkless fake-live provider, conservative token/dollar reservations, hash-chained and fsynced staged resume, monotonic tail-bound paid-release validation, immutable gate records, provider-identity drift stops, scientific fixture/history request construction, prompt auditing, Gate-4 missingness/message checks, and confirmation-lineage freeze tooling.
- Added the five-block `fake-live` configuration and exercised only deterministic local fake-provider paths. These artifacts are explicitly separate from both the older `mock` runs and any future `empirical-live` runs.
- Added and executed a narrowly bounded empirical Stage 2 capability release after the sprint start: two transport/schema requests and 20 calibration reviews. It passed its frozen gates, cost `$0.150022`, and stopped with zero smoke requests. This is live operational/capability evidence, not a pilot result or evidence about collusion or recovery.
- Completed the pre-authorization analysis and figure pipeline, finalized the v1.0 transcript-annotation protocol/tooling, and created the paper shell, claim-evidence ledger, pre-run/pre-submission checklists, and release-manifest template. These are sprint-period implementation and writing artifacts; their empty result fields are not empirical findings.
- Generated a read-only Gate 2 readiness artifact that binds the completed Stage 2 lineage and audits the 152-request block-0 smoke inventory. It is explicitly `blocked_no_dispatch`, made no paid/network calls, and does not authorize the experiment or constitute a dispatch-capable Gate 2 release.
- On October 23, before any hypothesis-bearing request, revised the scientific methodology to protocol v1.1: balanced CC/FF/mixed evaluation pairs with a defined immediate episode, three genuinely message/action-matched peer policies, frozen carryover/persistence uncertainty rules, configuration-level claim thresholds, narrower causal language, one scoped v1.2 fallback, and conservative confirmation sizing.

### October 24, 2026

- Reconciled the final v1.1 schedule, odd-split assignment, fallback lineage, exact peer scripts, analysis rules, and claim boundaries; the final integrated suite passes 283/283 tests.
- Finalized `annotation-v1.1` for v1.1 pilot and confirmation splits, including seeded selection, visible-evidence validation, independent rating, agreement, and adjudication.
- Added the dedicated fresh Gate 1 controller, checked no-dispatch proposal, external authorization schema, exact caps, sealed Stage 2 bridge, authorization-consumption controls, immutable decision, and hard stop before smoke. Inspection reports `ready_awaiting_external_human_authorization`; no authorization or fresh provider call exists.
- Reconciled the dedicated Gate 2 controller to exactly the 152-request v1.1 block-0 smoke and its 532-attempt maximum, plus an immutable post-smoke report and separate external smoke-integrity decision. Its proposal and authorization schema remain intentionally unavailable until fresh Gate 1 passes.

No hypothesis-bearing live-model experiment, pilot result, confirmation run, or empirical paper result has been produced. A local credential was used only by the bounded historical Stage 2 controller and was kept in the ignored owner-only `.env.local`; it was not written to tracked files, manifests, journals, payloads, or reports. The next provider boundary is external human authorization of the checked fresh `calibration/v1.1` release; the historical Stage 2 calibration does not fill that gate. After Gate 1 passes, Gate 2 still requires a newly generated checked proposal/config bound to that decision, independent review, and a separate unique non-reusable human authorization with exact attempt/token/dollar/concurrency/context caps. Smoke success and its externally bound integrity decision gate the remaining pilot. Before Gate 4 or confirmation, `pilot_freeze_assessment` must be hardened to audit authoritative raw pilot requests and responses. Add subsequent work as dated entries; do not retroactively describe the pre-sprint preparation above as sprint work.
