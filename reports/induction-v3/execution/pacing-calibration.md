# Offline pacing calibration

All 467 successful archived requests were covered by the approximate input estimate: the original gate request, 12 diagnostic requests, and all 454 successful continuation requests. The minimum estimated-to-reported input ratio was **1.2063825721383528**. No provider call or credential read was made, and no opaque reasoning content was decoded.

For continuation requests, estimated pacing units ranged from 70,400 to 101,243 (median 70,400; empirical 95th percentile 93,768). None exceeded the 1,000,000-unit window. These observations support this prospective pacing configuration; they do not guarantee future server-side rate estimates or eliminate HTTP 429 responses.

The estimator counts canonical request JSON with `tiktoken==0.14.0`, `o200k_base`. On its counting copy only, it omits each reasoning item's encrypted-content field and adds that item's originating response's entire output-token usage. The item ID and ciphertext hash must match the archived or newly observed response. A 10% margin applies to the greater of the 64,000 output cap and the visible JSON token count plus opaque allowances. Actual outgoing bodies, ordered replay, prompts, and financial reservation bounds are unchanged.

The shared pacer admits at most three delegates, starts with a 60-second cooldown, retains each admitted credit until response completion plus 60 seconds, and checks stop state at least once per second while waiting. Each wait is bounded at 300 seconds. Unknown replay IDs, oversized requests, stop signals, or timeout fail before a new reservation; no automatic retry is provided. The failed historical HTTP 429 has no reported usage and is excluded from this calibration, while its full $0.6402 reservation remains outstanding.

The vocabulary is the frozen local file `release/induction-v3/tokenizer/fb374d419588a4632f3f557e76b4b70aebbca790`, SHA-256 `446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`. The loader verifies it before credential access and explicitly denies a tokenizer cache-miss fetch.

`pacing-calibration.json` contains every per-request estimate, request and response file hash, and all 467 exact response archive bindings. Its SHA-256 is `3a7c11a73200b9f4e3cf3a0f3f98eae17496d2ad29fdf445f6dafff03f849f7b`; the canonical response-binding digest is `7ea11b2c07c75536c30988fadbec026b18785d565181aa81b0e34b36f8cd3ff4`; the canonical calibration-row digest is `16195aa518e92597796007af14bcde4243de5872fece5678bc9fe3963d216f12`.

OpenAI documents rate accounting as the greater of the configured output allowance and estimated request tokens. The local estimate is an approximation used only to pace this prospective release. [Official rate-limit documentation](https://developers.openai.com/api/docs/guides/rate-limits)
