# Implementation decisions

## October 22, 2026 — offline foundation

1. **Repository root:** Use the existing workspace containing the plan. Initialize local Git only; preserve the preexisting `tmp/`, `outputs/`, and `work/` directories and ignore them. No remote or push.
2. **No live capability:** The user requested offline-first implementation with paid calls disabled. Do not add an API client, SDK, credential loading, or a live configuration escape hatch. Reject unsupported provider/source/spending fields before creating an output directory.
3. **Standard library:** Python 3.11+ with TOML configuration and `unittest`; no dependency installation needed. The plan's YAML tree was illustrative, not a scientific requirement.
4. **Module consolidation:** Keep the oracle with immutable packet definitions, the verdict schema with scripted agents, and peer probes in the main runner. These boundaries can be split later without changing the protocol.
5. **Source/checkpoint audit:** Save exact agent-visible inputs, raw responses, evaluator labels separately, all planned outcomes, and hashes. Recompute scores before reporting. Completed or partial runs cannot overwrite a prior run directory.
6. **Missing outcomes:** Invalid/refused/failed verdicts remain unknown outcomes. Do not convert them into safe rejection. Analysis requires explicit permission to inspect incomplete runs and shows identification bounds.
7. **No invented formation effect:** Scripted agents are simple oracle/always-accept/always-reject controls. Any nonzero causal contrast constructed in a unit test is test data only and is not exported as a model finding.
8. **Visible state only:** State is an immutable tuple of canonical episode strings. A branch receives reconstructed values; reset retains the current packet and policy but none of the former transcript. No opaque reasoning state exists in this version.
9. **Delayed feedback:** Evaluation correctness feedback is recorded only at sequence end and is not supplied to intermediate verdicts. It is not used to seed another branch or probe.
10. **Replay fix:** The first integration pass exposed tuple/list differences after schedule JSON serialization. Normalize peer-cell order to JSON-native lists. Keep the replay regression test; the fix changes representation, not the experiment.
11. **Deferred scope:** Provider integration, transport retry policy, dollar/token caps, resumable live requests, protocol freeze tooling, figures, and empirical calibration remain future work. The offline request cap is not a substitute for a live spending gate.

These implementation choices do not constitute preregistration or empirical validation. Scientific parameter changes require a separate protocol revision and new pilot/confirmation separation as specified in the plan.
