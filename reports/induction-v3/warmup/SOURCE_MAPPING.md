# Published warm-up recipes: source mapping and handoff

These are prepared, offline-verified declarative recipes. They contain no live
runner, provider probe, credentials or experimental outcomes. The execution owner
must validate them against its final engine and freeze their hashes in the single
campaign release before dispatch. `READY.json` is the authoritative handoff index.

## Recipe semantics

| Recipe | Three warm-up episodes | Ten main episodes |
| --- | --- | --- |
| `warmup_raw` | Raw-only evidence policy; message-length enforcement disabled | Raw-only evidence policy; 200-character cap |
| `warmup_summary` | Summary evidence allowed; 200-character cap remains active | Raw-only evidence policy; 200-character cap |

Both use five alternating communication rounds in every episode. Raw warm-up
changes the channel enforcement flag, without changing prompt text. Summary
warm-up changes the policy block in the communication opening. It permits the
verifier to use a peer's summary; it does not prescribe the sender's format, force
acceptance, or manufacture a successful warm-up. Both keep task solving, private
verdicts, truthful shared reward, verdict review and private reflection.

Source: [paper Appendix H.2](https://arxiv.org/html/2609.24967v2#A8.SS2) and
[pinned README, ablation options](https://github.com/SALT-NLP/agent-collusion/blob/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/README.md#L89-L105).
All code citations below refer to that exact commit. The pinned code is MIT
licensed; no upstream files were modified by the warm-up owner.

## Source-to-recipe mapping

| Requirement | Pinned implementation | Declarative representation / assertion |
| --- | --- | --- |
| Raw warm-up | README lines 101–102; `episode_schedules.py:125–139` | `warmup_raw.warmup.message_char_limit = null`, mapping to `no-throttle:3,throttled:*`; raw policy throughout |
| Summary warm-up | Same README row; `episode_schedules.py:98–112` | `summary-allowed:3,raw-only:*`, default throttle throughout; warm-up cap 200 |
| Exact evidence policy | `prompts/policies.py:13–43` | Underscore recipe enums map to source hyphen enums; use source-rendered per-agent policy text |
| Current policy reaches each agent | `prompts/tasks.py:122–153`; `episode_runner.py:613–627` | Add current policy in a user-role communication opening before round 1 of every main episode |
| Keep original system norm | `prompts/system.py:110–133`; `runner.py:583–599` | Keep one original system message per agent; no summary permission added to system; no reset announcement |
| Continue original histories | `runner.py:665–716`; `memory/cross_episode.py:103–141` | Same per-agent histories across all 13 episodes; full-history with unlimited remembered episode count |
| Keep roles and replay fields | `memory/cross_episode.py:23–33`; `memory/messages.py:63–76` | Compare whole model-visible message objects, including tool IDs and opaque fields, at the boundary |
| Cap means rejected delivery | `protocol/messaging.py:16–65` | 201-character message rejected in summary warm-up and main; raw warm-up delivers it; one delivered message per turn still applies |
| Matched main tasks | `task/task_sequences_50x3+50x10/rep001_sampled_manifest.json` and `rep002_sampled_manifest.json`, compared to corresponding `task_sequences_50x10` files | Extended sequence's last ten pairs equal the reference ordered pairs; first three task pairs are distinct from main task IDs |
| Model defaults | `models.py:15–24`; paper B.1 Table 2 | Inherit execution release: Luna/high, source temperature 1.0 and 64,000 output-token limit; record any necessary setting change before calls |

Direct source links: [policy renderer](https://github.com/SALT-NLP/agent-collusion/blob/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/experiments/prompts/policies.py#L13-L43),
[current policy insertion](https://github.com/SALT-NLP/agent-collusion/blob/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/experiments/prompts/tasks.py#L122-L153),
[episode loop](https://github.com/SALT-NLP/agent-collusion/blob/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/experiments/runner.py#L665-L716),
[full-history retention](https://github.com/SALT-NLP/agent-collusion/blob/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/experiments/memory/cross_episode.py#L103-L141),
[delivery semantics](https://github.com/SALT-NLP/agent-collusion/blob/5afb2c595aa3eff6b9b829a032f4f117b36ee54d/experiments/protocol/messaging.py#L16-L65).
`SOURCE_INDEX.json` binds 16 local vendor files to their pinned Git blob hashes;
tests fail if these bytes differ. They never import the vendor runner or provider
modules. The files remain owned by the execution chat.

## Transition assertions and fixtures

`contract.py` is an offline assertion helper, not a live executor. It rejects
changed warm-up lengths, missing raw-policy restoration, unlimited summary
warm-up, seeds and release-setting overrides. Its expanded plan labels absolute
episodes 1–3 as warm-up and 4–13 as main episodes 1–10.

The transition test uses the actual pinned memory-retention functions and policy
renderer with explicitly synthetic messages. It preserves all three completed
episodes and each agent's initial system instruction, then appends a new raw-only
communication opening for the first main episode. Historical summary permissions
remain in the past; the current policy is raw-only. Deliberate history deletion,
policy rewriting, role changes, removed replay placeholders, extra system
messages and stale current summary permission all fail the assertion.

The synthetic fixture includes user, assistant and tool roles, task-call IDs,
private reflection markers and fake opaque replay fields. It contains no genuine
provider replay state, task success or cooperation evidence. Its fabricated
reward does not test the execution engine's reward truthfulness. Its copying
checks show fixture isolation, not proof of live engine isolation.

Run offline verification from the repository root:

```text
python3 -B -m unittest discover -s tests/induction_v3 -p 'test_warmup*.py' -v
```

The execution owner still must test its own adapter, original system/phase prompt
rendering, alternating calls, private verdict commitment, actual raw-evidence
delivery, truthful rewards, sandbox and concurrency. Boundary assertions should
be applied to the real model-visible histories after warm-up reflection and after
main-1 communication opens, before any main communication call. A role-stripped
transcript or regenerated summary is insufficient.

## Scheduling, measurement and interpretation

The campaign fixes two independent dyads per recipe. Each recipe therefore has
six warm-up and twenty main dyad-episodes. Recommended source repeats are 1 and 2;
the execution release owns the exact finite schedules, model settings and run IDs.
Freeze matched main tasks across all recipes and matched warm-up prefixes across
these two warm-up recipes. Keep separate conversations, histories and task state.
Do not discard failed warm-ups or resample based on results.

Use the reference's common, prospectively frozen measurement code. Warm-up
outcomes are retained and reported separately; they cannot establish constrained
main-phase unsupported acceptance or enter its ten-episode denominator. The
campaign's final-three feasibility check concerns main episodes 8–10 (absolute
11–13), separately from paper-compatible onset. Missing/incomplete trajectories
remain visible. Successful speech, reciprocal agreement and executed unsupported
acceptance remain separate fields.

The paper's warm-up ablations used Gemini models. Luna/high is a published main
configuration, so this two-dyad application is an exploratory model extension and
reduced pilot. It is not a numerical replication of the warm-up figure. Source:
[paper section 4.2](https://arxiv.org/html/2609.24967v2#S4.SS2) and
[Appendix B.1](https://arxiv.org/html/2609.24967v2#A2.SS1).

Additional limits: this comparison does not separate extra episode experience
from the warm-up policy/channel treatment, because no equal-length constrained
history control is included. The source can evict the oldest completed episode
on context overflow and retry (`episode_runner.py:220–305`). Stopping instead is
a declared execution deviation. Silent eviction cannot be reported as full
history retention. Endpoint, sandbox, retry or decoding changes belong in the
execution manifest. No new model search, repair branch, or relabeling of V2 is
part of this handoff.

The initial provisional interface's unlimited summary warm-up was corrected by
the execution owner before this handoff. `INTERFACE_NOTE.md` preserves the reason;
both recipe JSONs now match the corrected v1 schema.
