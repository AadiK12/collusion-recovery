# Exact offline recovery proof

The three interrupted episodes are recoverable without resampling a completed provider call. Actual replay through the pinned episode runner passed **43 historical request-digest checks**, consumed **42 original successful responses**, and preserved both late responses that the stopped transport had not ingested. No provider calls or participant-code execution occurred. Fifteen tests passed, including corruption rejection, stable checkpoint reconstruction, the exact first-live-call handoff, and concurrent thread isolation.

This proves the replay boundary. It does not clear an unreviewed resumption dispatcher or erase the original failed run. Any resumed dispatch requires the new prospective release/accounting review. The old HTTP429 attempt remains a physical attempt with its original unresolved liability; requesting its exact body again is one explicitly new technical retry.

## Bound source

Source run: `runs/empirical/induction-v3-continuation-97f9c0b4-0bcb-4125-97f9-431caee260e4`.

- Frozen release hash: `e7b5052a3cb9e1b00cda86e6dc31585a2fa48f6a97dfd6c3bb09b266b8c0c8c2`.
- Terminal journal file SHA256: `8f5e4b84fda9702edf77e1b8ee47039da030225a725ec2859ead4e1aceb1e474`.
- Manifest SHA256: `a869d51aecb9be1d1bdaffdaf1914bb848569bd14737a2cc33389701824f1483`.
- The journal and frozen source inventory are verified read-only before replay. Tests hash every source-run file before and after replay and require equality.

All 455 physical attempts have archived request and response records. There are 454 completed response bodies and one HTTP429 error. All 455 request inputs exactly match a prefix of one actor's retained history. All 452 already-ingested responses preserve their original ordered output objects; all 420 executed function calls have saved tool outputs. All 420 reasoning items have their opaque encrypted replay state; no hidden reasoning was interpreted.

There are 453 distinct request files for 455 attempts. Identical request bodies occurred in different dyads. Accordingly, the replay tape uses dyad, episode, physical call index, and reservation identity, never request hash alone, to choose a response.

## Proven boundaries

| Dyad | Interrupted episode | Matched requests | Consumed original responses | Next boundary |
|---|---:|---:|---:|---|
| development-warmup_raw-1 | 5 / main 2 | 14 | 13 | Bob communication round 1, exact failed request index 13 |
| development-reference-1 | 7 / main 7 | 22 | 22 | Alice reflection, never-dispatched index 22 |
| development-warmup_summary-1 | 7 / main 4 | 7 | 7 | Bob task phase after answer save, never-dispatched index 7 |

The late reference response `6cb0fb9fa75641b1bd2d49a3450bc80d` is an original `submit_verdict` call. Replay executes that deterministic source transition once, evaluates the episode, adds normal feedback, and stops before the new reflection. It does not invent a verdict or reflection.

The late warmup-summary response `c7b15049ba0a4ad2aa935e78a02546fe` is an original `save_final_answer` call. Replay executes it once and stops at the next task request. Its subsequent `get_log` has not yet been sampled or executed.

The HTTP429 reservation is `ebb929c67f6b4506ba3f5f091572c78e`. Its exact canonical request hash is `eef5c49475cd58bf4239179b64a237c8a88ddec56d2b0a33af93922589313f16`. The replay does not manufacture a response to that failed attempt.

## Mechanism and limits

Replay begins with the last completed `agents.messages` snapshots, then runs the original `run_one_episode` with the same pair, episode index, recipe policy, and logical dyad ID. Every cached provider call must reproduce the complete archived request body and digest. Provider item ordering, IDs, phases, content, and encrypted strings are returned unchanged. Both actors' reconstructed histories and protocol state are checked against the offline proof before a live fallback is allowed.

Only historical pytest and SQL payloads are substituted from the exact saved tool-output strings. Original protocol handlers still perform file validation, answer saving, raw-log construction, get-log completion, messaging/inbox delivery, rejected-message attempts, verdict recording, feedback, and phase transitions. Historical pytest output includes elapsed-time text; rerunning those tests would change raw evidence and is prohibited. Test files are recreated as data in a fresh temporary scratch directory, never executed. Scratch paths are normalized to their original names in checkpoint data; stable checkpoint digests are tested across distinct temporary directories. Original run files remain unchanged.

The source has no serialized Python program counter. Checkpoints are inspectable data, not resumable stacks. The live adapter must replay deterministic control flow again and switch to the separately accounted transport **inside that same invocation** at the verified boundary. Starting `run_one_episode` with incomplete histories would repeat the episode opening and is not a valid recovery method.

The remaining five planned dyads had no dispatched calls. They may start only as their original scheduled logical dyads, with the same frozen recipes and context treatment. Completed episodes are copied with origin provenance, not counted as fresh observations. No dyad replacement, task reshuffle, extra capability gate, changed prompt, or result-dependent branch follows from this recovery proof.

## API and evidence

`ReplayTransport(delegate, resume_run_id=...)` wraps the new accounted/paced transport. Call `engine.install_adapters(sandbox, wrapper, scratch_root)`, then `install_replay_hooks(wrapper)`. For an interrupted episode, set `.context(...)` and enter `.episode_replay(source_run, source_dyad, proof, histories=last_completed_histories)`. Fresh episodes outside this scope delegate normally. The replay state is thread-local.

Before fallback the adapter checks exact response-consumption rows, the boundary body, both histories, and normalized protocol state. It adds a distinct `resume_run_id`, source boundary hash, source logical index, and failed-reservation linkage to new transport metadata. Cached calls never reserve or settle again. Future tool work uses the already installed Docker helpers.

Authoritative final proof files are `replay-final-reference-1.json`, `replay-final-warmup_raw-1.json`, and `replay-final-warmup_summary-1.json`. Earlier `replay-*.json` / `replay-proof-*.json` files are intermediate feasibility evidence and do not carry the corrected stable path-normalized checkpoint. `replay-adapter-tests.txt` records **15 passed**. Exact file hashes appear in `rate-limit-recovery-science.json`.

Source semantics inspected: `vendor/salt_v3/experiments/episode_runner.py` (task/communication/verdict order and feedback/reflections), `agents.py` (ordered tool execution and history insertion), `protocol/dispatch.py` (raw-log inclusion), `protocol/messaging.py` (inbox and get-log), `protocol/code_tests.py` and `files.py` (tool side effects), `output.py` (visible history snapshots); `experiments/induction_v3/transport.py` (archive-before-stop behavior), `engine.py` (sandbox adapters and incomplete histories).
