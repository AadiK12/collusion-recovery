# Additive historical-liability accounting

This is offline implementation evidence. It does not authorize provider calls or resolve the old charge uncertainty. The original accounting module, verifiers, frozen run, source releases, and audit artifacts remain unchanged.

`experiments/induction_v3/liability_accounting.py` adds `LiabilityReservationJournal`, schema `induction_v3_liability_accounting_v1`. It inherits the original serialized reservation, settlement, fsync-before-dispatch, hash-chain, atomic-manifest, restart-stop, and ambiguous-usage behavior. Its overrides bind and enforce separately accounted historical consumed amounts and historical outstanding liabilities.

The sole historical exception is reservation `ebb929c67f6b4506ba3f5f091572c78e` in `induction-v3-continuation-97f9c0b4-0bcb-4125-97f9-431caee260e4`. Its original manifest, complete journal, journal tail, reservation event, request body/hash, and HTTP 429 response are fixed to exact hashes in `liability_anchor()`. The old outcome remains `unknown`; $0.6402 remains reserved. The exception cannot cover any other unresolved historical request. A new unknown outcome stops the new journal and prevents an ordinary later successor from importing that new liability.

## Public interface

```python
from experiments.induction_v3.liability_accounting import (
    LiabilityReservationJournal, historical_inventory, historical_totals,
    inventory_hash, liability_anchor, verify_run,
)

# Caller already holds the existing shared ProgramLock.
history = historical_inventory(lock, excluded_run=run_dir)
totals = historical_totals(history)
prepared_history_hash = inventory_hash(history)

journal = LiabilityReservationJournal(
    run_dir,
    program_lock=lock,
    binding={"run_id": run_dir.name, "model": "gpt-5.6-luna", "release_hash": prepared_release_hash},
    source_root=ROOT,
    source_inventory=frozen_sources,
    expected_history_hash=prepared_history_hash,
    campaign_limit_usd="98.26023724",
    lifetime_limit_usd="250",
    max_attempts=3532,
    max_in_flight=3,
)

# Read-only, no implicit lock acquisition or repair.
audit = verify_run(run_dir, source_root=ROOT, program_lock=lock)
```

Both `accounting.py` and `liability_accounting.py` must appear in `frozen_sources`. Omitting local campaign/attempt limits derives the remaining combined V3 capacity; explicit smaller limits are accepted. The prepared history hash is required. The constructor rereads all history under the held lock and rejects drift. Each dispatch rechecks the frozen source and exact historical manifest/journal/request/response fingerprints, after the constructor's full semantic history audit.

The journal accepts only a fresh physical identity. Identities already reserved by historical V3 journals cannot be silently reserved again. Resumed dispatch must distinguish an explicitly authorized new physical call from the frozen failed call and bind the replay/provenance design separately.

## Exact baseline and enforcement

The read-only scan of ten original manifests produced inventory hash `8b7c58307c2eeff560272c7e7052fb3401c44e3fb3521561fd1c58f512ebfdd5`. No real program lock was acquired for this report; preparation must recompute and compare this inventory under the held lock.

| Quantity | Baseline |
| --- | ---: |
| V3 consumed | $1.09956276 |
| V3 reserved | $0.6402 |
| V3 exposure | $1.73976276 |
| V3 physical attempts | 468 |
| Remaining V3 exposure capacity | $98.26023724 |
| Remaining V3 attempts | 3532 |
| Lifetime consumed | $48.50659936 |
| Lifetime reserved | $0.6402 |
| Lifetime exposure | $49.14679936 |
| Remaining lifetime exposure capacity | $200.85320064 |

The atomic reservation check enforces local exposure and attempt limits, combined V3 consumed plus reserved exposure at most $100, combined V3 attempts at most 4,000, lifetime consumed plus reserved exposure at most $250, and at most three new calls in flight. The old reservation is counted once in external history. Local `consumed` and `reserved` remain new-run quantities; the snapshot also exposes `historical_reserved_cost_usd`, `combined_v3_reserved_cost_usd`, `combined_v3_exposure_usd`, and lifetime exposure. A later clean successor can import the same old liability exactly once without double counting a prior successor's external baseline.

The read-only verifier replays every new journal event through these capacity and settlement semantics, exactly reconciles the manifest, and verifies frozen historical lineage and the immutable exception. Supplying an already-held lock additionally checks the complete current historical inventory; omitting it never claims that a full current-history scan occurred under lock.

## Offline validation

15 focused tests passed in 18.160 seconds. All tests use copied frozen artifacts and temporary empirical roots, temporary source inventories, and temporary program locks. They cover exact baseline arithmetic, unchanged original rejection behavior, interleaved settlement, read-only semantic replay, unfinished-call restart, future unknown rejection, clean successor liability accounting without duplication, concurrent cap enforcement, three-call concurrency, lifetime exposure including the retained reserve, impossible cap overrides, attempts after proven noncompletion, historical identity reuse rejection, history/source/response drift, and rehashed semantic tampering.

The combined original-accounting and liability-accounting suite also passed: 39 tests in 17.690 seconds.

No provider request, credential read, old ledger mutation, or release of the $0.6402 liability occurred.
