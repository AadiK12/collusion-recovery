# Induction V3 execution interface

Status: CLI and schema implemented; paid dispatch is disabled until the exact frozen release passes independent audit. The capability gate then controls induction eligibility.

Runnable entrypoints from repository root:

```sh
.venv-induction-v3/bin/python -m experiments.induction_v3.cli prepare
.venv-induction-v3/bin/python -m experiments.induction_v3.cli run --release release/induction-v3/campaign.json
.venv-induction-v3/bin/python -m experiments.induction_v3.cli verify --run runs/empirical/RUN_ID
```

`prepare` is offline and freezes once. `run` is single-use and for the sole execution owner only; it refuses existing run directories and requires release/induction-v3/AUDIT.json bound to the release hash. `verify` is read-only and replays interleaved reservations by identity. The actual execution launch uses a clean subprocess environment and reads the already authorized credential only in the execution process.

Active prospective release: `release/induction-v3/campaign.json`, hash `c394415eb66b51134a3f82d47a37ddee1884e3fcb6099251d3b4da58d486ea39`. Earlier `frozen.json` and `frozen-final.json` were never dispatched and remain preserved as superseded preparation artifacts: finalized handoff freshness and independent pre-live replay/validation-stop findings required a new freeze. There is only one planned empirical campaign run.

Variant owners publish their existing READY.json handoff with an exact recipe path and SHA-256. The execution owner validates before freezing. JSON recipe v1:

```json
{
  "schema_version": 1,
  "recipe_id": "reference|warmup_raw|warmup_summary|seeded",
  "label": "human readable provenance",
  "main": {"episodes": 10, "policy": "raw_only", "message_char_limit": 200, "rounds": 5},
  "warmup": {"episodes": 0, "policy": "raw_only", "message_char_limit": null, "rounds": 5},
  "seed": null,
  "source_commit": "5afb2c595aa3eff6b9b829a032f4f117b36ee54d",
  "source_references": [],
  "deviations": []
}
```

Warm-ups use `episodes: 3`: raw warm-up uses `policy: raw_only`, `message_char_limit: null`; summary warm-up uses `policy: summary_allowed`, `message_char_limit: 200`. This corrects the initial provisional uncapped-summary description after checking paper Appendix H.2. Main policy always returns to raw-only, limit 200, five alternating rounds. Seeded uses no warm-up and one fixed `seed` object: `{ "kind": "synthetic_positive_control", "messages": [{ "role": "user|assistant", "content": "..." }], "provenance": "experimenter-authored, not observed behavior" }`. Source-compatible placement will be explicitly declared after source audit. Owner may supply per-agent seed messages if needed as `alice_messages` and `bob_messages` instead of common `messages`.

READY.json must list the recipe and all authored files with hashes, offline tests/evidence, source citations, deviations and status. This schema is provisional: document incompatibilities in the handoff; do not change shared engine files or load credentials. The dispatcher can adapt declarative metadata with a frozen explicit mapping before any variant calls.

Task schedules, model/effort/temperature/token limit, shared reward, budget, capability, and accounting belong to the execution release and cannot be overridden by recipes. All tracks share matched main tasks and independently maintained histories. Seeded is diagnostic only.
