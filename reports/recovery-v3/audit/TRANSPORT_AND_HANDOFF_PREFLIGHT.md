# Recovery V3 transport and handoff preflight

Status: **planning audit only; new recovery release not yet reviewed or approved by this audit**.

This read-only audit inspected the V3 source and live artifacts on 2026-10-26.
No credentials were loaded, provider calls made, production code changed, or
historical artifacts rewritten. The new early exploratory repair study must not
be described as successful completion of the original induction-validation gate.

## Current handoff

The active induction run is
`induction-v3-resumption-02c8e4c3-7cda-4aac-b058-7b4df6878ff6`.
During inspection its manifest was still `running`, with 312 local physical
attempts, zero local in-flight calls at that instant, and no stop reason.
Zero in-flight is not a terminal handoff: queued work can still dispatch.

The historical unresolved reservation remains **$0.6402** and is separately
carried in lifetime exposure. It must not be marked settled, subtracted, or
replayed merely because the original response was HTTP 429.

Freeze recovery historical inventory only after the sole dispatcher's clean
terminal handoff. Then acquire the same program lock, semantically verify every
historical manifest/journal, verify the selected complete endpoint bytes, and
freeze release hashes. If the ledger still says running, do not synthesize a
terminal state from observed process absence.

## Reusable interfaces

- `induction_v3.transport.encode_input` replays complete ordered
  `provider_output_items` by deep copy, including IDs, phases, tool calls and
  encrypted reasoning. Do not flatten these to visible prose or regroup them.
- `induction_v3.pacing.PacedOfficialTransport` freezes request inputs before
  waiting, then delegates to the original one-attempt official transport.
- One `SlidingWindowPacer` shared by every worker supports at most three active
  requests and at most 1,000,000 conservative estimated units per window. It
  retains active charge through completion plus a full 60-second window.
- `KnownReplayTokenEstimator.from_archives` must receive hash-bound originating
  response artifacts for every opaque item in every endpoint. Carried histories
  require both original continuation and resumption archives. Unknown or changed
  opaque IDs fail before reservation. The estimator is scheduling-only and does
  not replace the full spend reservation.
- `LiabilityReservationJournal` preserves the old unresolved liability and
  enforces local, combined V3, and lifetime ceilings under the shared program
  lock. Every physical call receives its durable reservation before HTTP.
- `episode_runner.run_one_episode` takes `agent_messages` by reference and mutates
  it through task solving, communication, verdict, feedback and reflection.
  Clone the persisted complete endpoint independently for every branch.

## Integration hazards

1. **Ledger namespace is not generic.** The liability journal requires run IDs
   beginning `induction-v3-`, and classifies them inside the existing $100/4000
   aggregate V3 cap. The lowest-risk reuse is an explicitly labelled
   `induction-v3-recovery-*` run with the new tighter $25/600 local limits,
   while preserving the aggregate cap and $250 lifetime cap. A separate
   `recovery-v3-*` accounting namespace needs a reviewed additive implementation.
   The older base journal cannot replace this journal because it rejects the
   unresolved historical reserve.
2. **Adapter installation is global.** `engine.install_adapters` monkeypatches
   upstream modules and wraps `_prepare_agent_task`; install once per process,
   not inside every parallel branch. Do not run the induction runner and recovery
   runner concurrently in the same process or under different locks.
3. **Branches must not share mutable history.** Load immutable endpoint JSON,
   deep-copy each actor list for each arm, hash before branching, and verify the
   source bytes remain unchanged after all branches. No partial-episode
   `incomplete_histories` can masquerade as a complete endpoint.
4. **Scratch paths must be branch-specific.** Include endpoint, arm, replicate,
   and episode in `temp_run_id` and metadata. The sandbox adapter retains the
   upstream scratch directory basename; collisions can mix generated files.
5. **Evidence restoration changes scoring provenance.** Existing
   `engine.metrics` measures full raw delivery only from peer channel transcript
   strings. Direct restoration of raw logs to a judge would be wrongly counted
   as unsupported unless the recovery scorer checks an explicit delivery record
   and matches the actual authoritative bytes. Do not simply reuse that metric
   for an out-of-band restoration arm.
6. **Budget covers reservations, not only cheap typical outputs.** A single
   original full-context reservation is $0.6402. Concurrent reservations and the
   held historical liability count toward limits before admission; a $25 local
   cap/600 attempt ceiling is a maximum, not a promise to complete 600 calls.
7. **Stop semantics must remain immediate.** Any new ambiguous provider error
   retains its own reservation and stops queued dispatch. Siblings already in
   flight may settle; no automatic retry or silently skipped arm is allowed.
8. **Release inventory must include new mechanisms.** Freeze runner, intervention
   hooks, metrics, schedules, endpoint hashes, transport/pacer/accounting code,
   tokenizer/runtime versions, sandbox identity, and all selected archive hashes.
   An old induction audit does not cover new recovery hooks.

## Required offline assertions before dispatch

1. Tamper any endpoint, intervention text, schedule, source hash, archive,
   liability anchor or release hash: fail before credential access/reservation.
2. Acquire the existing program lock in another process: recovery cannot start.
3. Start four workers: at most three physical calls, one shared window, and no
   separate per-worker token allowance.
4. Block queued admission then stop ledger: no extra request artifact, attempt,
   reservation or HTTP call from the queued task.
5. Check exact JSON equality of all prefix provider replay items across cloned
   arms; insert only the planned intervention at its prescribed phase.
6. Mutate an arm's nested assistant/provider output item and history after clone:
   neither another arm nor source endpoint changes.
7. Run matched branches: independent scratch roots and unique physical identities;
   identical task schedule/orientation except explicitly planned manipulation.
8. For restoration, verify delivered raw bytes equal authoritative peer logs,
   reach only the proper reviewer, and are counted as complete evidence;
   for controls verify no restoration artifact enters messages or tools.
9. New 429/timeout: exactly one new attempt, old $0.6402 remains, new reserve
   remains, all queued dispatch stops, no retry.
10. At a local/lifetime/aggregate cap boundary, reject reservation before HTTP.
11. Recompute final ledger from interleaved event IDs; source lineage stays
    unchanged; actual dispatched repair calls, not a prepared file, determine
    `repair_started`.

Existing offline test patterns are in `tests/induction_v3/test_transport.py`,
`test_pacing.py`, `test_resumption_accounting.py` and
`test_resumption_security.py`. These tests were inspected, not rerun by this
planning audit; new recovery assertions remain required.

## Efficiency conclusion

Parallelize independent endpoint-arm jobs in one bounded dispatcher. Keep each
dyad's conversation/episode sequence serial because its next request depends on
the preceding response. Three independent workers hide individual request and
sandbox latency, but more agent chats do not multiply the shared token quota.
With long retained histories the token window, not worker count, becomes the
bottleneck. Separate coding/review agents help offline preparation; they must not
each launch their own paid loop.

Official references inspected:

- [Reasoning model state and ordered output replay](https://developers.openai.com/api/docs/guides/reasoning)
- [Organization/project rate limits and token budgeting](https://developers.openai.com/api/docs/guides/rate-limits)

The local 1M-window/three-call settings are frozen project operating limits,
not a claim about the account's guaranteed provider capacity.
