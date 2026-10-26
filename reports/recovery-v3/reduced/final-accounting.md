# Final available-data accounting

**Passed.** The primary available-data snapshot closed collection at **11:38:05 UTC**. At independent verification on 2026-10-26T11:41:06.775220+00:00, all **13 existing empirical runs were terminal**. The proposed recovery wave 2 never started: its observed-rate feasibility check rejected admission at **11:37:20 UTC**, before its ledger/key path, and its assigned run directory is absent. There are no wave-2 calls, charges, or reservations. This verifies the actual early freeze, not a backdated 11:50 snapshot.

The dispatcher's separate locked verification at **11:39:34 UTC** agrees exactly with this independent recount. All bindings in [final-primary-snapshot.json](final-primary-snapshot.json) were checked; [final-locked-verification.json](final-locked-verification.json) records the dispatcher's lock-held check. This reviewer did not acquire that lock.

| Scope | Consumed | Retained liability | Total exposure | Attempts / observations |
|---|---:|---:|---:|---|
| All empirical history | $51.71680945 | $0.6402 | $52.35700945 | 13 terminal runs |
| Combined V3 | $4.30977285 | $0.6402 | $4.94997285 | 993 attempts; 992 completed usage records |
| Recovery wave 1 | $0.69186867 | $0 | $0.69186867 | 6 capability + 80 research calls |
| New source completion | $0.16245751 | $0 | $0.16245751 | 10 new calls; one completed main episode |
| Proposed recovery wave 2 | $0 | $0 | $0 | Not admitted; zero calls |

The summary-1 source now has its original three warm-up and ten main episodes. Its final episode references **23 calls: 13 previously paid calls replayed exactly and 10 new calls**, each new reservation matched once to raw provider usage. Twelve completed episode prefixes were carried unchanged. Source completion counts against V3/lifetime, not the recovery envelope. It ran from 2026-10-26T11:30:34.957518+00:00 to 2026-10-26T11:33:10.885113+00:00: 155.928 seconds including initial cooldown; new dispatch through final settlement took 96.271 seconds.

Recovery evidence therefore remains **80 valid research judgments from one independent source dyad**, plus the separate six-case capability gate. The original **480-cell denominator has 400 unknown cells**. Completing a formation source adds no recovery judgment. No missing judgment is treated as rejection, and the unstarted 80-cell wave is not a second completed pair.

The original HTTP429 unknown reservation remains held **once**, at $0.6402, unchanged and separate from consumed usage. Remaining headroom is **$95.05002715 and 3,007 V3 attempts**, **$197.64299055 lifetime**, and **$24.30813133 / 514 calls** in recovery's cumulative envelope. These balances are descriptive, not new dispatch authorization.

I independently recalculated all **992 completed V3 usage records** from raw response usage and frozen prices, including cache writes and reasoning without double counting; the sum exactly matches $4.30977285. All V3 journal chains and reservation/settlement links reconcile, the final source's semantic replay and bound sources pass, and all thirteen local manifest balances reconcile. Pre-V3 costs retain their historical tariffs. Inventory hash: `2de942fbbd3b1abc3d3a310b4648f1e49fa3cf709b6cfc6aae0495974a75fd84`. No mismatches were found.

Evidence hashes, per-run balances, all ten new-call price calculations, source timestamps, and the non-admission receipt are in [final-accounting.json](final-accounting.json). No provider requests, credential reads, real program lock, or existing-file changes occurred in this review. The reviewer authored some reusable accounting/successor code; arithmetic was independently recomputed, but this is not a claim of independent code authorship. Costs are estimates from provider usage and frozen prices, not invoice reconciliation.
