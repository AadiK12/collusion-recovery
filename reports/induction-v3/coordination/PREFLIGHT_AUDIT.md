# Independent pre-implementation audit

Observed 2026-10-26 08:16:39 UTC by a separate read-only audit agent.
This is a point-in-time audit, not permission to skip dispatch-time checks.

Seven historical manifests reconcile to $47.4070366 accounted consumption:

| Run | Accounted USD |
| --- | ---: |
| Stage 2 | 0.1500220 |
| Gate 1 | 0.1378975 |
| Gate 2 | 7.4959330 |
| Gate 3 paused ambiguous timeout | 6.7658595 |
| Controlled failed run | 11.1951037 |
| Controlled completed run | 18.5650989 |
| Exploratory V2 | 3.0971220 |

All seven manifests reported zero outstanding requests, token reservations, and
cost reservations. Remaining under the existing $250 program guardrail was
$202.5929634. This is local reported/conservative accounting, not a reconciled
provider invoice. No empirical Python writer or program-lock holder was observed.

V2 independently replayed 192/192 results with valid source snapshots and journal.
All 28 current frozen sources of the completed controlled run still matched;
562 reservations matched 562 results, with none outstanding. The original held
1,580-request extension remains absent and source-disabled.

## Implementation cautions

- `BudgetLedger` supports more than one in-flight reservation, but is not
  thread-safe. Serialize every mutation and journal append in one coordinator.
- Existing controlled and V2 run verifiers require adjacent reservation/result
  events. A concurrent campaign needs a new identity-matching replay verifier;
  do not change those frozen old verifiers.
- The original pinned SALT `experiments/protocol/code_tests.py` strips provider
  environment variables but executes generated pytest via an ordinary subprocess
  at the repository root. It does not protect `.env.local` from file reads and
  does not prevent network access. Do not run it unmodified on this host.
- Docker CLI exists, but the daemon was unavailable. `sandbox-exec` also exists;
  neither fact proves isolation. Require tested no-network, task-only files,
  secret isolation, resource/output bounds, and process-tree timeouts.
- Reproduce alternating same-round delivery, private task resources, actual
  tool-derived logs, and phase barriers. Parent/source audit is not a substitute
  for tests of the new implementation.

Evidence entrypoints: `experiments/controlled_peer_recovery_live.py` historical
inventory/reconciliation and journal verifier; `src/collusion_recovery/budget.py`;
`experiments/exploratory_collusion_v2_live.py`; frozen release and run manifests.

Original execution source:
https://raw.githubusercontent.com/SALT-NLP/agent-collusion/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/experiments/protocol/code_tests.py

No new model calls were issued by the parent or this audit agent. The sole live
dispatcher is identified in `release/induction-v3-coordination.json`.
