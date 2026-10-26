# Recovery accounting interface: reuse the frozen liability ledger

Prepared 2026-10-26; revised to the parent's latest contract. Offline design
only: no source implementation, provider request, credential access, program
lock acquisition, or historical mutation. The current induction dispatcher is
still running. No handoff action is authorized by this note.

## Decision

Reuse `experiments.induction_v3.liability_accounting.LiabilityReservationJournal`
unchanged, with run ID `induction-v3-recovery-<uuid>` and distinct frozen recovery
protocol labels. The latest contract intentionally keeps recovery inside the
combined V3 $100/4,000-attempt limits, while also imposing a smaller recovery
allocation of at most $25/600 new physical attempts and the lifetime $250 limit.
An exact release may lower these limits. A new ledger/schema is unnecessary.

The earlier proposed separate recovery-family ledger is superseded. The old
class requires an `induction-v3-` prefix and treats all of its own schema as V3;
these constraints now match the requested accounting treatment. A directory
named only `recovery-v3-...` would fail. Scientific labels should distinguish
recovery without changing the frozen accounting schema or run-ID prefix.

## Existing API and precise limits

The frozen module already exports:

```python
historical_inventory(program_lock, *, excluded_run=None) -> list[dict]
historical_totals(rows) -> dict
inventory_hash(rows) -> str
liability_anchor() -> dict

LiabilityReservationJournal(
    run_dir, *, program_lock, binding, source_root, source_inventory,
    expected_history_hash,
    campaign_limit_usd=None, lifetime_limit_usd="250",
    max_attempts=None, max_in_flight=3,
)

verify_run(run_dir, *, source_root=None, program_lock=None) -> dict
```

Pass explicit local limits; omission would allocate the larger remaining V3
capacity. After a verified handoff, the sole dispatcher prepares under the
existing program lock using decimal arithmetic:

```python
history = historical_inventory(lock, excluded_run=new_run_dir)
totals = historical_totals(history)

# For the first and only run in this frozen recovery allocation:
local_dollars = min(Decimal("25"),
                    Decimal(totals["remaining_v3_usd"]),
                    Decimal(totals["remaining_lifetime_usd"]))
local_attempts = min(600, totals["remaining_v3_attempts"])
# Block preparation/dispatch if either remaining limit is nonpositive.

journal = LiabilityReservationJournal(
    new_run_dir,
    program_lock=lock,
    binding=frozen_recovery_binding,
    source_root=ROOT,
    source_inventory=frozen_sources,
    expected_history_hash=inventory_hash(history),
    campaign_limit_usd=str(local_dollars),
    lifetime_limit_usd="250",
    max_attempts=local_attempts,
    max_in_flight=3,
)
```

The prepared release must bind that exact history hash and those limits; pass
its expected hash at actual construction rather than silently substituting a
new inventory after review. `binding` must have the exact directory `run_id`
and `model="gpt-5.6-luna"`. Add frozen `protocol`, `recovery_campaign_id`, release
hash, scientific-contract hash, and endpoint/provenance hashes as campaign
metadata. Both original accounting modules and all imported recovery source
must be in the frozen source inventory.

Here `campaign_limit_usd` and `max_attempts` are local to the newly created
journal. The existing constructor and each reservation independently enforce
historical-plus-local V3 exposure at most $100, V3 attempts at most 4,000, and
all-history lifetime exposure at most $250. Its thread RLock, durable append
and fsync before dispatch, hash chain, atomic manifest, interleaved settlements,
concurrency limit, source/history checks, and restart-stop behavior are reused.

## Sequential-run and liability precautions

One fixed recovery run ID is simplest. Starting another directory does not
automatically preserve the smaller $25/600 recovery allocation. If a separately
reviewed successor is needed, subtract **all prior recovery runs' local consumed
plus local reserved dollars and physical attempts** before taking the minima
above. Identify that fixed allocation by its bound recovery campaign ID and
release lineage, not by a newly invented ID. Derive those labels from each
journal's binding event: the history summary rows do not include arbitrary
campaign metadata. The old cumulative V3/lifetime checks alone do not prevent
resetting a $25 local allowance by creating another run.

The sole admissible historical unknown remains reservation
`ebb929c67f6b4506ba3f5f091572c78e` in
`induction-v3-continuation-97f9c0b4-0bcb-4125-97f9-431caee260e4`, at **$0.6402**.
The frozen class validates its exact manifest, journal, request, response, and
event hashes. It remains reserved in combined V3 and lifetime exposure, counted
once, and does not consume this new journal's local $25 allocation. Do not
release it, relabel it consumed, or additionally sum its mirror in successor
manifests. Global reports sum each run's local `ledger.consumed` and local
`ledger.reserved`, not `external_liability` or combined/historical totals.

Any other historical unresolved charge blocks this constructor. A new 429,
timeout, or missing/invalid usage keeps the full local reservation and stops
new dispatch; the old allowlist does not extend to it. Already active calls
may settle, and no automatic retry is permitted. Unknown usage never becomes
zero merely because a new directory is proposed.

Preserve the frozen conservative pricing and token bound. At 1,050,000 input
plus 64,000 output, reservation is $0.6402 per call and $1.9206 for three active
calls. Rate-pacing estimates do not reduce the financial bound. Every new
physical call, including setup/check calls, counts toward the local allowance.
Copied history and replayed old responses are provenance, not new billable
attempts. Give each new physical attempt a fresh recovery run/arm/episode/actor/
call identity; the ledger also rejects historical identity reuse.

## Handoff and review

The inspected induction manifest still said `running`, even when its local
reserved amount was zero between paced calls. That is not terminal ownership
transfer. Do not modify the active run, acquire or bypass its lock, interrupt an
active call, or create a new empirical directory in its changing inventory.
Offline recovery preparation belongs outside `runs/empirical` until handoff.
The parent is separately investigating a safe quiescent handoff; this note
neither implements nor authorizes one.

Before recovery creates its run, induction must have a verified terminal
accounting state and release the shared program lock. The sole dispatcher must
then recheck all historical manifests and journals under that same lock. The
existing scanner rejects `created`, `running`, or `executing` historical runs
and every unresolved charge except the exact allowlisted $0.6402. Bind the
terminal inventory and explicit local caps in the exact reviewed release;
construction must reject subsequent history drift. Hold the lock throughout
recovery dispatch.

Use the existing `verify_run(new_run_dir, source_root=ROOT)` for read-only
semantic journal replay, exact manifest reconciliation, frozen-source checks,
and immutable carry-lineage validation. An optional already-held `program_lock`
adds a check against the complete current inventory. The verifier does not
implicitly acquire a lock, repair files, load credentials, or make requests.
When auditing an older run after later runs exist, omit the lock argument to
validate its frozen lineage; the all-current-history comparison intentionally
rejects an enlarged inventory.

Offline dispatcher tests should prove explicit local caps, cumulative minima,
exactly-once liability, new ambiguity handling, and any successor-allocation
calculation using isolated temporary locks. Existing ledger tests need not be
duplicated. Final independent clearance must bind the exact recovery release
and terminal history rather than this interface proposal.
