# Bounded capability diagnostic: accounting review

## Actual final-release review: accounting cleared

At **2026-10-26 09:02:23 UTC**, the actual additive implementation in
`experiments/induction_v3/diagnostic.py` was reviewed against the exact prepared
`release/induction-v3/diagnostic-final.json`. **No accounting blocker was found
for this frozen diagnostic.** This clearance covers the budget, attempt, lock,
and historical-reconciliation implementation; it is not a substitute for the
separate scientific and provider-safety audit.

| Bound artifact | Hash |
| --- | --- |
| Canonical release hash | `46eea1163c01ed10f59a7f8c4c099c92932945b1047490baffbc013b656c0a67` |
| Release file SHA-256 | `f9b47be554bf993532a6faa0c93852d742c9ab26c987defd3e225181cdddc7e8` |
| `diagnostic.py` SHA-256 | `8d7e752960b960c9a4a7728296d8bc5784e5e870d1a0432ca3098c2c9b4d92c0` |
| `test_diagnostic.py` SHA-256 | `0a38f6ddc0cd806d1078b34297c8bcb78fd0ba2659deb25e58c5a2b685a992db` |

The review called `validate(..., require_audit=False)` to check the release's
self-hash, frozen source inventory, and isolated runtime versions without
pretending that the final independent audit had already been issued. It then
acquired the existing shared program lock and independently confirmed:

- Current full historical inventory equals the release's frozen inventory.
- `prior_v3_totals(history)` equals the frozen prior-V3 object exactly.
- `remaining_limits(prior, diagnostic=True)` equals the frozen limits exactly:
  $2 local exposure and 12 local physical attempts.
- The first V3 run still passes read-only semantic replay and source checking.
- The new diagnostic run directory does not yet exist, and 12 cells are frozen.

The actual `run()` revalidates inside the retained lock, re-reads historical
manifests, and refuses either inventory or prior-V3 mismatch before constructing
the journal or loading credentials. Its journal receives the frozen $2/12
limits, `max_in_flight=1`, and the existing constructor's $250 lifetime default.
The release independently records the same $250 lifetime and one-call limits.
The immutable journal binding includes the release hash, campaign ID, and full
prior-V3 object. The journal itself rechecks history and source hashes before
each reservation. Therefore changes to prior spend, attempts, membership, or
manifest hashes between preparation and launch block this exact release.

The observed full historical total remains $47.40771165. Prior V3 remains
$0.00067505 and one attempt. The diagnostic's maximum aggregate V3 exposure is
therefore $2.00067505 and its maximum aggregate V3 attempt count is 13. The
single-use directory check prevents redispatch of this release.

Offline verification:
`.venv-induction-v3/bin/python -m pytest -q tests/induction_v3/test_diagnostic.py tests/induction_v3/test_accounting.py`
completed with **33 tests and four subtests passed**. No credential or provider
call was used, and the lock was released after the read-only check.

Two interpretation limits remain explicit. `prior_v3_totals()` selects the
`induction-v3-` run-ID prefix; this correctly includes every actual V3 member in
this frozen inventory, but future V3 runs must retain that convention or expand
the selector prospectively. Also, `aggregate_v3_cost_usd` in result/status output
is consumed estimated cost; on an ambiguous failure it excludes the separately
reported outstanding `ledger.reserved.cost_usd`. Aggregate exposure must include
that reservation. Enforcement already includes it, and a successor cannot start
while any historical reservation remains open.

## Initial constructor review

Read-only review of `CAPABILITY_DIAGNOSTIC_SCOPE.md` and the existing
`ReservationJournal`. No source, prior run, release, audit, or credential file was
modified; no provider call was made.

At 2026-10-26 08:56:53 UTC, while holding the existing shared program lock, the
review reconciled all eight historical empirical manifests and independently
replayed the first V3 run. The lock was released after inspection.

| Quantity | Verified value |
| --- | ---: |
| Lifetime consumed estimate | $47.40771165 |
| Lifetime outstanding reservations | $0 |
| Prior V3 consumed estimate | $0.00067505 |
| Prior V3 physical attempts | 1 |
| Remaining aggregate V3 operating capacity | $99.99932495 |
| Remaining aggregate V3 physical attempts | 3,999 |
| Remaining lifetime capacity | $202.59228835 |

Prior V3 run:
`induction-v3-b2522673-f58a-4d9e-8be0-ae08b511db53`.
Its manifest SHA-256 was
`f2289770ef5fd6d897e667f648f04e21126aa4b2625b2cbf47f857ced13329c4`.
Its seven-event journal, manifest, and frozen source inventory reconciled. The run
remains stopped after one failed capability judgment, with no induction dyad.

## Exact constructor limits

The existing implementation needs no modification. In the retained shared lock,
recheck all prior runs and prospectively freeze the diagnostic's finite schedule,
source inventory, release hash, previous V3 membership, costs, and attempts. The
constructor's campaign cap is per run, so compute its aggregate-aware limit
before constructing it:

```python
local_dollar_cap = min(
    Decimal("2"),
    Decimal("100") - prior_v3_exposure,
    Decimal("250") - all_historical_exposure,
)
local_attempt_cap = min(12, 4000 - prior_v3_physical_attempts)
# Fail closed before construction if either remaining capacity is nonpositive.

journal = ReservationJournal(
    NEW_RUN_DIR,                    # Must not already exist.
    program_lock=lock,
    binding={
        "model": "gpt-5.6-luna",
        "run_id": NEW_RUN_ID,
        "release_hash": DIAGNOSTIC_RELEASE_HASH,
        "campaign_id": "induction-v3-20261026",
        "run_kind": "capability_diagnostic",
        "aggregate_campaign_limit_usd": "100",
        "aggregate_max_physical_attempts": 4000,
        "prior_v3_exposure_usd": str(prior_v3_exposure),
        "prior_v3_physical_attempts": prior_v3_physical_attempts,
        "prior_v3_runs": frozen_prior_v3_inventory,
    },
    source_root=ROOT,
    source_inventory=DIAGNOSTIC_FROZEN_SOURCE_HASHES,
    campaign_limit_usd=str(local_dollar_cap),   # Currently "2".
    lifetime_limit_usd="250",                 # Ceiling, not remaining balance.
    max_attempts=local_attempt_cap,            # Currently 12.
    max_in_flight=1,                          # Actual diagnostic is serial.
)
```

The new constructor automatically inventories every previous empirical manifest
under the lock, including the first V3 run. Its lifetime accounting therefore
includes $47.40771165 before the first new reservation. A $2 local cap and 12
local attempts imply at most **$2.00067505 V3 exposure and 13 V3 attempts** after
this diagnostic, comfortably within the aggregate bounds. These are upper
bounds, not forecasts or a promise that every scheduled call can complete.

Membership must include every earlier V3 run, including unsuccessful capability
checks, diagnostics, and interrupted attempts. Current V3 manifests use
`artifact_type: induction_v3_accounting_v1`; the first run predates an explicit
campaign-family field, so include it by the verified run ID and manifest hash.
Freeze every member's manifest hash, estimated consumed/reserved cost, and
physical-attempt count. Do not identify members only by a successful outcome or
recipe label. Each later authorized V3 run must recompute the aggregate remainder.

## Pitfalls that matter

- Binding metadata records the aggregate calculation but does not itself enforce
  it. The release builder/validator must independently derive the local limits
  from the locked prior inventory. Never use the defaults of $100 and 4,000 for
  every successor run.
- Do not reopen the stopped first run or alter its limits. Its immutable binding
  must remain unchanged; this continuation is a fresh diagnostic run and release.
- `verify_run(old_run, program_lock=lock)` compares that old run's originally
  frozen historical inventory with the complete current inventory. Once a later
  run exists, this intentionally strict comparison can fail because of the later
  run. Audit prior immutable journals with `verify_run(old_run, source_root=ROOT)`
  while the outer lock is held, and separately reconcile the current inventory
  through `historical_inventory(lock)`. Use source roots matching each frozen
  inventory; never change prior source files to make an audit pass.
- The historical scanner blocks a new run if any previous run has an unresolved
  reservation. Do not discard or zero an ambiguous charge to enable continuation.
- At the frozen full-context input bound of 1,050,000 and 64,000 output tokens,
  each reservation is $0.6402. Three simultaneous reservations total $1.9206.
  Actual settled exposure may permit the remaining fixed calls; the runtime cap
  can still stop a partially completed diagnostic. Report all predeclared cells,
  including any calls blocked by budget, and do not stop at a favorable verdict.
- After execution, read-only replay must reconcile the new journal and manifest;
  then sum its exposure and attempts with the immutable prior V3 inventory.
  All costs remain estimates from usage and conservative frozen list pricing.

No accounting blocker was found for the proposed bounded diagnostic. This review
does not establish the model's capability or authorize relabeling the failed
gate; those remain governed by the separately frozen diagnostic design.
