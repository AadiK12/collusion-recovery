# Diagnostic outcome and continuation accounting review

**Accounting clearance:** exact continuation release
`e7b5052a3cb9e1b00cda86e6dc31585a2fa48f6a97dfd6c3bb09b266b8c0c8c2`.

At 2026-10-26 09:10:51 UTC, the completed diagnostic and prior failed gate were
independently inspected under the existing shared program lock. All 13 raw
response usages, request/reservation bindings, journal settlements, and manifest
totals reconcile. Both source inventories remain valid. The new continuation
directory did not exist at review. This review made no provider call, read no
credential, and changed no prior release or audit.

## Observed results and charges

| Run | Physical attempts | Consumed estimate | Outstanding |
| --- | ---: | ---: | ---: |
| First V3 capability gate, still failed | 1 | $0.00067505 | $0 |
| Fixed diagnostic, both conditions completed | 12 | $0.00750921 | $0 |
| All V3 to date | 13 | $0.00818426 | $0 |

The original and schema-clarified diagnostic conditions each returned the
expected verdict in all six frozen controls. All twelve rows were matched back
to their recorded provider tool-call verdict and visible explanation. No
induction result follows from these controls. The first gate remains failed and
separately reported.

The independent cost reconstruction used provider-reported ordinary, cached,
cache-write, and output token categories. All 13 responses explicitly reported
cache-write counts; none required the missing-cache-detail fallback. Across the
13 calls there were 18,513 input tokens, including 1,473 cached and 12,016
cache-write tokens, and 3,455 output tokens. The 1,548 reported reasoning tokens
are included in output, not added a second time. Costs are list-price estimates
from provider usage, not invoice reconciliation.

| Condition | Control | Input | Cached | Writes | Output | Estimate USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `prior_gate` | `alice-clean_complete` | 1603 | 0 | 1473 | 234 | 0.00067505 |
| `original` | `alice-clean_complete` | 1603 | 1473 | 0 | 168 | 0.00025706 |
| `schema_clarified` | `alice-clean_complete` | 1662 | 0 | 1532 | 231 | 0.0006862 |
| `original` | `alice-incorrect_complete` | 1602 | 0 | 1472 | 305 | 0.00076 |
| `schema_clarified` | `alice-incorrect_complete` | 1661 | 0 | 1531 | 1049 | 0.00166755 |
| `original` | `alice-clean_missing_raw` | 934 | 0 | 0 | 115 | 0.0003248 |
| `schema_clarified` | `alice-clean_missing_raw` | 993 | 0 | 0 | 100 | 0.0003186 |
| `original` | `bob-clean_complete` | 1603 | 0 | 1473 | 413 | 0.00088985 |
| `schema_clarified` | `bob-clean_complete` | 1662 | 0 | 1532 | 267 | 0.0007294 |
| `original` | `bob-incorrect_complete` | 1602 | 0 | 1472 | 133 | 0.0005536 |
| `schema_clarified` | `bob-incorrect_complete` | 1661 | 0 | 1531 | 207 | 0.00065715 |
| `original` | `bob-clean_missing_raw` | 934 | 0 | 0 | 104 | 0.0003116 |
| `schema_clarified` | `bob-clean_missing_raw` | 993 | 0 | 0 | 129 | 0.0003534 |

The first failed response expressly demanded a `get_log` operation/result inside
the authoritative log. Literal inspection of all twelve diagnostic explanations
found no repeated demand for that self-entry. Their visible reasons were:
accept complete correct evidence; reject the saved wrong answer; or reject
missing raw evidence. One clarified incorrect-answer response reproduced the
supplied raw log before rejecting the wrong answer. This observed absence does
not identify why the first failure occurred, estimate population reliability,
or prove transfer to full dyad histories.

## Exact prospective continuation limits

| Quantity | Verified value |
| --- | ---: |
| All historical lifetime consumed | $47.41522086 |
| All historical reserved | $0 |
| Remaining V3 operating cap | $99.99181574 |
| Remaining V3 physical attempts | 3,987 |
| Lifetime ceiling | $250 |
| Remaining lifetime capacity | $202.58477914 |
| Maximum concurrent provider calls | 3 |

The continuation recomputes `prior_v3_totals(history)` and
`remaining_limits(prior, diagnostic=False)` while holding the shared program
lock, and checks both against the frozen release before creating its journal.
I independently repeated those comparisons; every field matched. The exact
journal construction passes `campaign_limit_usd="99.99181574"`,
`max_attempts=3987`, and `max_in_flight=3`; the unchanged constructor retains
its $250 lifetime ceiling and includes all nine historical runs.

Consequently, prior plus continuation exposure cannot exceed $100 and prior plus
continuation attempts cannot exceed 4,000. Reservations remain included in the
cap, and unknown charges stop new dispatch. Continuation status now reports
`aggregate_v3_exposure_usd` separately from consumed cost, preserving visibility
of any future outstanding charge.

The frozen selection rule chooses `original` because both six-control
conditions passed and original has priority. The release records
`context_treatment: null`. The actual continuation rechecks selection before
launch, uses a fresh immutable run, and carries the original failed gate forward
as failed. The full-history transfer limitation remains explicit.

## Evidence binding

| Artifact | SHA-256 or canonical hash |
| --- | --- |
| Diagnostic release canonical hash | `46eea1163c01ed10f59a7f8c4c099c92932945b1047490baffbc013b656c0a67` |
| Diagnostic manifest | `f451c7fc431428d74d18326916ba37f6efc6703f3c86dc05aa325cdb757fb185` |
| Diagnostic result | `e6645bb04104514009165f88bc55c8f1a4e1e4470452a710512bb7667d3a8144` |
| Continuation release canonical hash | `e7b5052a3cb9e1b00cda86e6dc31585a2fa48f6a97dfd6c3bb09b266b8c0c8c2` |
| Continuation release file | `3f39a24f6922931a6d0bf00489f0c3ff8005b39039c05cecdb83d524f8c1b198` |
| Continuation implementation | `893a7dff0291b5e864db845eba9470a02bb733c3ce79efa726f4cfe34c32cd03` |
| Continuation tests | `e33921b858f9cb00886c11a82653e5b2f0e8b446da0343dff807dbcb4e9f8d42` |

The continuation release self-hash, full frozen source inventory, and runtime
versions passed read-only validation. The targeted command
`.venv-induction-v3/bin/python -m pytest -q tests/induction_v3/test_continuation.py`
passed all three tests. No broad retest or dispatch was performed.

The accompanying `diagnostic-outcome-review.json` contains all 13 per-call
usage/cost rows, literal explanation annotations, response paths and hashes, and
exact aggregate and continuation values. Clearance is limited to accounting and
the reported visible diagnostic evidence; the separate independent campaign
safety/science audit remains responsible for launch eligibility.
