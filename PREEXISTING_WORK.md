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

To be completed with dated entries: any model calibration, live experiments, protocol amendments, confirmation runs, analyses, and paper writing performed during the sprint. Do not retroactively describe the above preparation as work begun during the event.
