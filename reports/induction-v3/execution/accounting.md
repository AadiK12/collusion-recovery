# V3 accounting handoff

Prepared and offline verified; this accounting task has issued no provider calls,
read no credentials, and created no empirical run. Only the execution owner may
dispatch. The implementation is `experiments/induction_v3/accounting.py`; its
offline tests are `tests/induction_v3/test_accounting.py`. Prior sources and
empirical artifacts were read without modification.

## Contract

The sole dispatcher retains the existing
`runs/empirical/.controlled-peer-recovery-program.lock` throughout the campaign.
One `ReservationJournal` owns that lock. A thread `RLock` serializes reservations,
settlements, journal appends, and manifest replacement. Each reservation is
flushed and fsynced before `reserve()` returns; the derived manifest is replaced
atomically, fsynced, and its directory fsynced before return. A durability failure
poisons the process-local journal and blocks further dispatch.

```python
from experiments.induction_v3.accounting import ProgramLock, ReservationJournal, verify_run

with ProgramLock(ROOT / "runs/empirical/.controlled-peer-recovery-program.lock") as lock:
    journal = ReservationJournal(
        run_dir,
        program_lock=lock,
        binding={"model": "gpt-5.6-luna", "run_id": run_id, "release_hash": release_hash},
        source_root=ROOT,
        source_inventory=source_hashes,
    )
    reservation = journal.reserve(
        logical_identity,
        input_token_bound=conservative_input_bound,
        max_output_tokens=64000,
        request_hash=request_sha256,
        metadata={"recipe": recipe_id, "dyad": dyad_id},
    )
    # Dispatch one physical request only after reserve() has returned.
    event = journal.settle(reservation.reservation_id, usage=normalized_provider_usage)
    # If event['outcome'] is unknown, do not label the response accepted.
    # In all timeout/ambiguous cases:
    # journal.settle(reservation.reservation_id, outcome="ambiguous_timeout")
    # Stop and retain the reservation; no automatic retry.
    audit = verify_run(run_dir, source_root=ROOT, program_lock=lock)
```

`verify_run()` is an independent read-only entry point. It checks the complete
hash chain, semantic replay, and exact journal/manifest agreement without opening
the mutating journal constructor. Source hashes and historical inventory are
also checked when their arguments are supplied. It explicitly reports open calls
that would require a dispatch stop on restart. A changing manifest during audit
fails closed. The constructor can recover a stale manifest from a valid journal;
the read-only verifier reports that disagreement instead of repairing it.

Every result is matched to its reservation ID, regardless of interleaving.
Duplicate IDs, duplicate logical identities, duplicate settlements, unknown
event fields, altered bindings/prices, broken hashes, and partially written
journals are rejected. Startup with an unsettled call durably stops new dispatch
and keeps its full reservation. There is no retry loop. Already in-flight
responses may still settle after another call has stopped dispatch.

Default enforced upper bounds are $100 campaign exposure, $250 lifetime exposure,
4,000 physical attempts, and three in-flight provider calls. These caps may be
lowered, never raised. Exposure includes both known estimated cost and every
outstanding reservation. The execution engine separately enforces at most three
dyads and causal per-dyad order.

Before every reservation the frozen sources and all historical manifests are
rechecked while the program lock remains held. Missing, conflicting, malformed,
running, or outstanding-reservation historical ledgers block dispatch. Historical
manifest hashes and costs are bound into the first journal event. A changed
history is not silently accepted on resume.

## Pricing and uncertainty

The configured target is exclusively `gpt-5.6-luna`, with the official source
`https://developers.openai.com/api/docs/models/gpt-5.6-luna`. The execution owner
must verify the current official source before launch; this module implements
the campaign's given rates:

| Token category | USD per million |
| --- | ---: |
| Ordinary input | 0.20 |
| Cached input | 0.02 |
| Cache writes | 0.25 |
| Output, including reasoning | 1.20 |

For input exceeding 272,000 tokens, all input categories receive a 2x multiplier
and all output a 1.5x multiplier for that request. Reservations price all bounded
input as cache writes, and include the entire bounded output including reasoning.
Reasoning is validated as a subset of output tokens, never added again.

If provider usage omits cache-write details, every noncached input token is
conservatively priced as a cache write and the normalized record carries
`cache_write_accounting: assumed_all_non_cached_write`. Explicit cache-write
counts use `provider_reported`. This avoids silently assuming missing detail is
zero. All settlement costs are list-price estimates from provider usage, not
invoice-confirmed amounts. Raw provider detail remains the transport's artifact
responsibility.

Missing or invalid total input/output usage, ambiguous timeout, or unknown
completion retains the full reservation and stops dispatch globally. Proven
noncompletion can release dollars only with null usage and an explicit safe
`noncompletion_evidence` identifier; it still counts as an attempted physical
request. The transport currently conservatively uses unknown for HTTP failures.
Provider usage exceeding a reserved bound is charged in full and stops dispatch;
the ledger never clips actual reported usage to make a cap appear satisfied.

## Historical locked check

At **2026-10-26 08:31:18 UTC** (**01:31:18 America/Los_Angeles**), this task acquired
the existing shared program lock, inspected every empirical manifest, and then
released the lock. It found seven runs, **$47.4070366 consumed**, **$0 reserved**,
and **$202.5929634 lifetime remainder**. This is an observed snapshot, not launch
authority; the actual dispatcher must repeat the check under its retained lock.

| Historical run | Consumed USD | Manifest SHA-256 |
| --- | ---: | --- |
| `controlled-peer-recovery-v1-6591f5a6-8798-4e72-b5af-e1048290377b` | 11.1951037 | `bc81cbe5a909f2c1980f1cc7e4ab8391c5dac98532be9a22ebb7f4fde620862f` |
| `controlled-peer-recovery-v1-7aa1c7be-2281-4470-9a96-375ebb9fa4a4` | 18.5650989 | `8876b4f6c1785b5815c7cc9a1f328942295c68883a559cabc97b3b0a918ca0d4` |
| `exploratory-collusion-v2-development-2cb522de-9852-4b75-a448-86d88620f39d` | 3.0971220 | `9ad033af80030fc21a3aa929a7952b07de7b703f4401fb8a9cd97e95b3427389` |
| `gate1-calibration-v1.1-1b61ea2d-e609-4c67-a649-b889722dba9b` | 0.1378975 | `51ada83aa4279a57f723be2c5e7cab180199ba99725b0cf53279ff27015201d9` |
| `gate2-smoke-v1.1-6abffb64-f71b-4868-89cb-28b0aff0c7e3` | 7.4959330 | `67f8dafe28536f99ba7e29f700b0fb561c933702b58b5eebc6472a3e6c31a1e4` |
| `gate3-formation-phase1-v1.1-3e0d7744-45d7-4db5-a36a-a46b634c0b98` | 6.7658595 | `a75511ee7eb1d7746049f845e4a1b7bba7b483d20947f4b7d0e4d83eb0934725` |
| `stage2-2026-10-23` | 0.1500220 | `2136c720fb00a4f7baeb77b7aeb41161c2a8db5e34028e9c219adc26cdf130c1` |

The V2 row remains its original separate $3.0971220 study. No V2 outcome is
relabeled by this implementation. The historical Gate-3 manifest remains
`paused_ambiguous_timeout`; its previously consumed conservative accounting is
included without changing that status or retroactively making the attempt free.

## Verification

`python3 -m unittest discover -s tests/induction_v3 -p test_accounting.py -v`

Twenty-four tests passed. They cover interleaved result order, reserve/settle replay,
thread competition at the concurrency and dollar limits, lifetime spend,
attempt counts, open reservations on restart, unknown usage and timeouts,
manifest lag, source/history drift, rehashed schema and binding tampering,
truncated journals, truthful over-bound usage, cache writes and the long-context
threshold, fsync-before-return and durable-write failures, lock ownership,
conservative missing cache details, and a nonmutating standalone verifier.

Four additional transport/ledger integration cases use an injected fake HTTP
client and a nonfunctional sentinel credential, with no environment or network
access. They verify conservative settlement when cache-write detail is omitted,
no accepted result with missing usage, a durable stop/reservation after malformed
postdispatch usage, and a durable stop/reservation after a response artifact
write failure. The sentinel credential does not appear in any generated
artifact. Earlier review findings about missing-write pricing and unhandled
postdispatch failures were addressed and these cases passed against the updated
transport.

The tests are entirely synthetic and do not establish model availability,
scientific fidelity, task isolation, or induction. The execution owner still
must integrate transport behavior, freeze the finite schedule and source hashes,
complete the independent campaign audit, and pass capability controls before
induction.
