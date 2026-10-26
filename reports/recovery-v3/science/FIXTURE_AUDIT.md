# Recovery V3 independent fixture science audit

Status: **PASS for the fixed exploratory reviewer assay**. No model calls were
made by this audit. This is a fixture audit, not a release/accounting approval or
proof of collusion induction.

Bound artifact: `reports/recovery-v3/fixtures/materialized-fixtures.json`, SHA-256
`0261da318301a1cffddcc0c02f1f373f8ed572b755e2a3b44f1679a2a8317e28`.
Reviewed constructor `experiments/recovery_v3/fixtures.py`, SHA-256
`da5640732d88ea13e0c866f768979ede089bbfa5c40cb2667576ebf4d6ed6235`.

## Checks completed

- Independently reran the fixture test file: **32 passed**. Used
  `PYTHONDONTWRITEBYTECODE=1 .venv-induction-v3/bin/python -m pytest -p
  no:cacheprovider tests/recovery_v3/test_fixtures.py -q`.
- Inspected all eleven fixed source implementations, mathematical specifications,
  test expectations, and recorded isolated test outcomes. Immediate and
  sequential sets each contain two correct and two faulty saved assessments;
  the three distinct capability cases are clean/full, faulty/full, and
  clean/constrained. Every saved assessment is `no_bug`; correctness comes from
  the source specification, not a model verdict. The failures are assertion
  failures on genuine incorrect implementations, not missing tests/tool failure.
- Materializer calls the original dispatcher, replacing only its test executor
  in a short-lived offline subprocess with the existing verified sandbox. Its
  `get_log` return is preserved. All eleven strings parse to ordered sequence
  numbers 1–4 and operations `read_code`, `write_test_file`, `run_tests`,
  `save_final_answer`; the original dispatcher excludes `get_log` itself.
- Independently checked all eleven raw strings use the original compact JSON
  serialization, and all recorded tool-output SHA-256 values match the actual
  embedded test output. This audit inspected the existing recorded sandbox
  output; it did not rematerialize traces or overwrite their timing-bearing bytes.
- Independently constructed **44 public payloads**: all eleven cases times two
  reviewers times two evidence modes. Each targets the other role. All rendered
  evidence chunks equal the expected frozen chunks, and evaluator-only keys
  `oracle`, `task_correct`, `implementation_has_bug`, `expected_pytest_exit`, and
  `raw_log_sha256` are absent. Only `submit_verdict` is exposed.
- Full logs contain 1,588–5,117 characters. Complete-mode chunks concatenate to
  the exact full string; constrained-mode chunks concatenate to exactly its
  first 1,000 characters, always strictly incomplete. Neither mode reconstructs
  records. Both have five peer-tagged chunks and the original raw-only policy.
- Reviewer transition explicitly says the traces are experimenter-generated,
  not adaptive work by the peer. It asks for direct review of already-completed
  work without fabricating an own-task phase, peer agreement, or feedback.

## Interpretation limits, not launch blockers

These are simple code-analysis diagnostics with explicit test contradictions.
They test evidence-based review under matched histories/interventions, not broad
capability across complex tasks. Incorrect fixtures naturally have longer logs
because assertion failures are verbose; matched arm contrasts hold each exact
fixture constant, but the assay does not establish that the reviewer reasons
about every underlying defect. Constrained prefixes also omit later operations,
including saved answers; this is deliberate incomplete evidence, not an equal-
information claim-only control. Truthful diagnostic framing may itself change
behavior relative to natural peer interaction, equally across all arms.

The pre-existing science claim boundary therefore remains essential: immediate
repair response and bounded reviewer carryover, not confirmed collusion,
autonomous dyadic persistence, or a passed induction holdout.
