# Preexisting work disclosure

Prepared October 22, 2026 (America/Los_Angeles), before the sprint's listed October 23 start.

## Prepared before the sprint

- Literature exploration, hypothesis selection, and `SPRINT_PROJECT_PLAN.md`.
- Offline Python fixture generator and deterministic oracle.
- Agent-visible evidence boundaries, history checkpoint/branch/reset implementation, and prompts.
- Six main evaluation arms, four scripted peer-probe cells, and deterministic controls.
- Event recording, replay/rescoring, block-level analysis, missingness accounting, and tests.
- Repository documentation and mock-only local smoke runs.

All experiment outputs produced so far are scripted mock controls. They are not observations of OpenAI or any other model's behavior. No paid inference was performed.

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

No real-model calibration, live model experiment, paid inference, pilot result, confirmation run, or empirical paper result had been produced at this checkpoint. No API credential was created, read, or stored. Add subsequent work as dated entries; do not retroactively describe the pre-sprint preparation above as sprint work.
