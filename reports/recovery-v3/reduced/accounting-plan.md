# Read-only budget and timing plan

At 04:23 PDT on 26 October, the 04:59 deadline was 36 minutes away. Exact allocation of the requested approximately250 judgments across the remainingfive pairs is pending. The earlier240-judgment/four-combined-pair plan is not an approved amendment, and `reduced_analysis.py` remains an unbound draft.

The twelve existing runs consume $51.55435194 lifetime and retain the original $0.6402 liability once: exposure $52.19455194, leaving $197.80544806. V3 has983 physical attempts, $4.14731534 consumed and $0.6402 held, leaving3017 attempts and $95.21248466. Recovery wave1 used86 calls and $0.69186867, leaving514 calls and $24.30813133 within its cumulative600/$25 envelope. These are sums of local run balances, not repeated historical mirrors. Read-only inventory hash: `95b2ef4de29905c2b03d1665a20998e8da4915f973926731343270c70d4be38e`.

The proposed source-completion cap is2000 calls/$25, further limited by the V3/lifetime remainder and reserving250 V3 attempt slots for recovery. Source completion counts against V3/lifetime but does not consume the recovery envelope. The dispatcher must recheck exact history and limits under its program lock; this review has not acquired that lock and is not release clearance.

Timing is the binding practical constraint. The last induction run dispatched429 calls in58m47s, or7.30calls/minute; its last20minutes averaged6.65/minute. Recovery wave1 dispatched86 calls in11m02s, or7.79/minute. Those spans exclude each fresh run's one-minute initial cooldown and preparation.

Common source completion requires15 main episodes: reference2 ten, raw1 four, summary1 one. Based on completed episodes by recipe, this represents approximately347 new calls after exactly replaying37 already paid partial calls (raw1 twenty-four; summary1 thirteen). The recipe-specific observed min/max range gives311–384 new calls. At prior induction throughput, the mean is about48 active minutes. Allfive remaining sources require35 main episodes **plus six warmup episodes**, approximately997 new calls or137 active minutes at that rate. These are workload projections, not guarantees; retained-history growth and task-specific tool use can alter them.

The250 recovery judgments alone project32 active minutes at wave1 throughput. Consequently neither common15-source completion plus250 judgments nor allfive-source completion plus250 judgments has a credible04:59 finish based on observed pace. Preserve an honest partial result if the deadline governs collection; do not present unfinished source histories or scheduled judgments as completed observations.

Evidence: local reservation/settlement timestamps in the resumption and wave1 journals, local resumption dyad `llm_usage` counts, and the twelve immutable manifests. No provider, credentials, generated code, program lock, or frozen file was touched.
