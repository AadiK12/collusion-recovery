# Exploratory recovery: immediate execution handoff assessment

2026-10-26. Prepared after the parent conveyed the human's explicit instruction to
run the exploratory repair experiment efficiently. No recovery provider request
has been issued. The exact scientific contract is pending from the parent.

## Available starting evidence

The first reference induction dyad is complete and immutable at
`runs/empirical/induction-v3-resumption-02c8e4c3-7cda-4aac-b058-7b4df6878ff6/dyads/development-reference-1.json`.
It has 8/10 joint unsupported-acceptance episodes, including all final three.
Independent review found zero explicit reciprocal agreements. Two incorrect
Alice answers were accepted by Bob. This supports exploratory recovery after
observed verification-policy failure, not recovery from confirmed collusion or
a passed held-out induction gate.

The raw and summary warm-up first dyads are unfinished but each has three
consecutive observed joint unsupported-acceptance episodes. The first seeded
episode completed with both agents rejecting. All assigned induction outcomes,
including unfinished and unstarted dyads, must remain in the final inventory.

## Hard dispatch obstacle

The live process is the single-use audited
`experiments.induction_v3.resumption run` dispatcher. It holds
`runs/empirical/.controlled-peer-recovery-program.lock` throughout all scheduled
development and conditional validation work. Its three worker slots are active.

The frozen runner has no signal handler, external stop request, pause file, or
checkpoint/handoff API. The journal's stop method is an in-process method;
another writer must not append to its active journal or bypass its lock. Pausing
the operating-system process would retain the lock and would not create a valid
handoff. Abrupt termination could lose the latest in-memory tool/history state.
The complete source design currently releases ownership only when the finite
run finishes or its own failure handling reaches a terminal result.

Therefore the supported safe route is to keep induction running while building
and auditing the additive recovery package. Recovery dispatch cannot begin
under the current architecture until the live process releases the shared lock.
At the observed pacing rate, that may take several hours. This is a dispatch
constraint, not a need for another user-approval round.

## Work that can proceed immediately

1. Build additive `experiments/recovery_v3/runner.py` and transport support, with
   tests under `tests/recovery_v3/runner*`; leave all frozen V3 files unchanged.
2. Implement the parent's forthcoming exact five-arm scientific contract:
   constrained reference and full-evidence correction × one-time history-reset.
   Preserve the distinction between immediate response and longitudinal
   persistence; useful recovery requires both appropriate acceptance and
   rejection, not universal rejection.
3. Freeze exact completed endpoint hashes, all-assigned source inventory,
   packet/arm/order/state schedules, and model settings prospectively. Document
   this as a new exploratory revision that does not satisfy or reinterpret the
   older explicit-agreement/validation prerequisites.
4. Add a separate repair allocation: at most $25 and 600 physical calls,
   including checks and ambiguities, with the existing $250 lifetime guardrail,
   the historical $0.6402 liability counted once, max-three paced concurrency,
   and no automatic ambiguous retry.
5. Pass offline scientific, accounting, transport, and isolation checks before
   the sole dispatcher acquires the lock and loads the already-authorized key.

The transport and isolated tool execution already exist and can be composed
without changing their frozen implementation. Scientific compilation and exact
accounting binding depend on the parent's contract and terminal induction
inventory. I will prepare and review those components in parallel now.
