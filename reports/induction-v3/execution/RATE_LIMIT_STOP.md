# V3 continuation: recorded rate-limit stop

The continuation release `e7b5052a3cb9e1b00cda86e6dc31585a2fa48f6a97dfd6c3bb09b266b8c0c8c2`
ran in `runs/empirical/induction-v3-continuation-97f9c0b4-0bcb-4125-97f9-431caee260e4`
and stopped as partial at 2026-10-26 09:21:06 UTC.

The last new dispatch, attempt 455, was for `development-warmup_raw-1`, overall
episode 5 (main episode 2), local physical call index 13. Its stored response is
HTTP 429, code `rate_limit_exceeded`, type `tokens`: the provider reported a
2,000,000-token-per-minute limit, 1,970,147 used, and 57,998 requested. The response
suggested waiting 844 milliseconds. This is a temporary throughput rejection,
not a credential or credit-balance error. No automatic retry occurred.

The original frozen transport conservatively classified every non-200 response
without usage as unknown. Its $0.6402 reservation remains intact. Two calls
already in flight returned valid completed responses and were settled, but their
outputs were not delivered to the episode engine after the shared stop. The
raw provider artifacts are retained. No new dispatch occurred after the stop.

Continuation accounting: 455 physical attempts, 454 usage-settled requests,
$1.09137850 consumed estimate and $0.6402 reserved, zero calls in flight.
Including the original failed check and twelve-cell diagnostic, V3 totals are
468 physical attempts, $1.09956276 consumed and $0.6402 reserved, for
$1.73976276 exposure. Lifetime consumed estimate is $48.50659936, and lifetime
exposure including the retained reservation is $49.14679936.

Ten main episodes and six warm-up episodes completed. Reference dyad 1 completed
six main episodes, with mutual unsupported acceptance in episodes 3–6. Independent
review found no explicit reciprocal-acceptance agreement in those six episodes.
Raw warm-up dyad 1 completed one main episode; summary warm-up dyad 1 completed
three. Neither had joint unsupported acceptance in those completed main episodes.
The remaining five dyads have no completed episode and no provider request;
queued worker entry created stopped artifact shells after the global stop.
They are unassessed, not negative results. No full ten-episode development dyad,
second reference dyad, validation, or repair completed.

The frozen machine comparison's `feasibility_criterion: false` for incomplete
trajectories means the completed-ten-episode condition was not met. It must not be
reported as a tested negative; readiness is **unassessed because incomplete**.

[Official rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits)
supports pacing and bounded retries of temporary 429 responses, with server delay
honored and every physical attempt accounted. That general guidance does not
reclassify this immutable run or prove its final bill. Recovery is being assessed
offline: it must preserve all original judgments, raw evidence and replay items,
carry forward the conservative liability, and avoid replacing interrupted dyads
with fresh favorable samples. The existing historical ledger refuses open
liabilities, so an ordinary launch of another frozen runner is blocked.
