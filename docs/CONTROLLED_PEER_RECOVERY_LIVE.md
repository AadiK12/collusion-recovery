# Controlled-history recovery live execution

This document covers the separate audited live shell in
`experiments/controlled_peer_recovery_live.py`. The scientific design remains
frozen in `experiments/controlled_peer_recovery.py` and
`docs/CONTROLLED_PEER_RECOVERY.md`.

## Fixed release scope

- Protocol: `controlled-peer-recovery-v1`.
- Fresh capability calibration: 20 independent full-evidence verdicts.
- Automatic gate: 20/20 schema-valid, at least 9/10 correct-packet approvals,
  and at least 9/10 faulty-packet rejections.
- Hypothesis study after a passing gate: five paired blocks, 540 requests.
- Total: 560 logical requests, comprising 200 message identities and 360
  verdict identities; 340 study reviews and 10 immutable formation endpoints.
- Retry ceilings: 200 possible message revisions, 1,120 known-not-completed
  retry slots, six ambiguous-retry-authorized identities, 766 maximum billable
  shapes, and 1,886 maximum physical attempts.
- No third formation condition, sample expansion, confirmation study, or
  original 1,580-request extension.

The release uses the verified lifetime-program remainder after historical
spend, never a second allocation. The cap is currently `$235.450288` under the
existing `$250.000000` ceiling. The deliberately conservative all-shapes token
envelope can exceed that operational cap. Completion is therefore not
guaranteed: the ledger hard-stops before a request that cannot be reserved.

## Safety boundary

`prepare`, projection, validation, replay verification, and `offline-fake`
never read a credential or make an API call. Empirical execution:

1. takes the program lock and the run-specific lock;
2. revalidates the complete historical manifest inventory and proves the
   separate 1,580-request extension remains absent and unclaimed;
3. reads owner-only direct-user scope evidence and packages a machine binding
   to the exact proposal, scope, cap, source, transport, retry, and provider
   hashes;
4. claims the authorization into a one-use owner-only receipt;
5. creates or reloads the run and independently replays its durable prefix;
6. only then reads `OPENAI_KEY` from the ignored owner-only `.env.local` and
   constructs the sealed official SDK client.

The human evidence is persisted verbatim in `human_scope_evidence.json`.
Final artifact hashes are added by the machine and do not imply that the human
personally reviewed those hashes. An existing exact receipt can resume after
the packaging freshness window without consuming authority again.

Every paid dispatch has a pre-dispatch reservation. Raw provider requests,
rendered prompts, wire payloads, provider responses, usage, timing, provider
identity, retry schedules, and the journal hash chain are durable. An
interrupted in-flight reservation is conservatively charged as ambiguous and
is never redispatched. A seventh observed ambiguous identity hard-stops without
authorizing an extra retry. Any ambiguous identity suppresses directional
claims in empirical analysis and produces a downstream-contamination report.

## Commands

Use a fresh lowercase UUIDv4 and the exact matching namespace
`controlled-peer-recovery/v1/<run-id>`.

```bash
PYTHONPATH=src:. .venv/bin/python -m experiments.controlled_peer_recovery_live prepare \
  --run-id <uuidv4> \
  --seed-namespace controlled-peer-recovery/v1/<uuidv4> \
  --output <new-release-directory>
```

The owner then packages already-given direct-user scope evidence according to
`human_authorization_schema.json`; the adapter does not fabricate that file.
After independent review, empirical execution or resume is:

```bash
PYTHONPATH=src:. .venv/bin/python -m experiments.controlled_peer_recovery_live run \
  --proposal <release-directory>/proposal.json \
  --authorization <owner-only-external-authorization.json>
```

Verification is independent of execution:

```bash
PYTHONPATH=src:. .venv/bin/python -m experiments.controlled_peer_recovery_live verify \
  --run-directory runs/empirical/controlled-peer-recovery-v1-<uuidv4>
```

Empirical analysis accepts only a complete verified empirical run and always
uses exactly 10,000 paired-block bootstrap replicates:

```python
from experiments.controlled_peer_recovery_empirical_analysis import (
    analyze_empirical_control_run,
)

analysis = analyze_empirical_control_run("runs/empirical/<run-directory>")
```

The deterministic offline path is deliberately barred from the empirical run
tree and cannot carry a receipt, human authorization, paid-call count, or
empirical label.
