# Runtime and dependencies

- Python: requires 3.11 or later; locally verified on 3.13.7. `.python-version` records that tested interpreter version.
- Third-party runtime dependency: `openai==3.37.0`, used only by explicitly invoked empirical controllers: the completed historical Stage 2, selected-version Gate 1, Gate 2 smoke, the now-paused Gate 3 Phase 1 path, and the future unified v1.2 path after its exact package is finalized and authorized. Importing the offline formation-peer substudy neither constructs a client nor loads credentials.
- Test framework: standard-library `unittest`; the full suite runs inside the locked environment because it also exercises the SDK-facing Stage 2 boundary with injected fake clients.
- Config format: TOML, parsed with the standard-library `tomllib`.
- Network/package downloads: no provider/network access is required for validation, tests, the original offline runs, fake-live staged runs, the isolated formation-peer mock substudy, analysis, annotation, or release-proposal inspection. Initial locked-environment setup may require package-index access; only explicitly authorized empirical execution uses the OpenAI API.
- Repository execution: set `PYTHONPATH=src`; the Makefile invokes `uv run --frozen python` by default. This is currently a source-checkout workflow, not an installed wheel.

`pyproject.toml` pins the direct dependency exactly, and the resolver-generated `uv.lock` records its transitive environment. Install without re-resolving it:

```bash
uv sync --frozen --python 3.13.7
```

New staged-run manifests record the exact Python version and implementation, the installed OpenAI SDK version for empirical transport, the configuration and aggregate implementation hashes, and the individual source-file hashes. The original offline runner records its runtime and source hashes under its earlier manifest schema. Do not hand-edit `uv.lock`; regenerate and review it if the dependency set changes.

The standard library's seeded pseudorandom implementation and serialization are part of the runtime assumption. Use the recorded interpreter when reproducing exact fixture bytes; fixture hashes detect drift. Provider randomness is a separate empirical concern and is not controlled by these seeds.

The legacy `gate2-readiness` command is retired and always fails closed. The completed Gate 1/Gate 2 verifiers and historical `collusion_recovery.gate3_phase1 inspect` path use the same frozen environment and validate release bindings without SDK-client construction, credential loading, network access, or dispatch. Before execution, Gate 3 credential-free inspection passed against proposal `1b85e13a8b10c98ad4314611de0da56624328225b54a0aa7764ac72ecabd3b5e` as `ready_awaiting_external_human_authorization`. That label is no longer current: the one-use authorization was consumed by partial run `3e0d7744-45d7-4db5-a36a-a46b634c0b98`, which is paused fail-closed and cannot be replayed. Every execution path is a separate provider-capable boundary requiring its own exact proposal and unique external authorization.

## October 23 WP1/WP2 and Stage 2 dependency boundary

The fake-live provider, canonical prompt renderer, conservative budget ledger, staged orchestration, durable resume, and protocol-freeze writer remain standard-library-only. `configs/fake_live.toml` names only the deterministic local fake provider, contains no credential field, and cannot select empirical transport.

The OpenAI provider module remains an inert translation boundary: it can build stateless Responses payloads and parse captured responses without network access, and its transport wrapper accepts only an already constructed client. The historical `stage2`, selected-version Gate 1, completed Gate 2, and paused Gate 3 Phase 1 controllers are the empirical boundaries that import the locked SDK, securely load a credential, construct the official client, and inject it into that wrapper. Earlier completed contracts do not authorize later releases. Consequently:

- Offline tests, mock runs, fake-live runs, prompt inspection, and analysis require no API key and make no provider request.
- Stage 2 requires exactly OpenAI SDK `3.37.0` from `uv.lock`; the controller refuses a different installed SDK version.
- Stage 2 reads only `OPENAI_KEY` from the repository-root `.env.local`. The file must be a regular file owned by the current user, mode `0600` or otherwise have no group/world permission bits, ignored and untracked by Git, and not a symlink. The secret is not placed in configuration, manifests, journals, payloads, or reports.
- Every ambient `OPENAI_*` variable is rejected so SDK credentials, custom headers, organization, project, routing, or logging settings cannot silently change the authorized client.
- The injected wrapper records the exact official base URL, 60-second timeout, OpenAI SDK version, disabled SDK retry setting, disabled environment trust, and disabled redirects; that metadata is part of the authorization binding. Environment-derived HTTP proxy and TLS settings therefore cannot alter this release.
- `configs/empirical_stage2.toml` fixes model/settings, the verified pricing source and timestamp, the **$1.78** cost ceiling, 67-attempt ceiling, 211,932-input-token ceiling, 94,208-output-token ceiling, 9,353-input-token per-request ceiling, 4,096-output-token per-request ceiling, and maximum in-flight value of one.
- The controller is bound to `runs/empirical/stage2-2026-10-23/`, rejects alternate live run directories, and immutably persists and journal/manifest-binds the complete authorization package before the first reservation. It runs the exact two-logical-call Gate 0 first and runs exactly 20 calibration reviews only if Gate 0 passes. It contains no smoke or Gate 2 execution path.
- The Gate 2 successor was distinct from Stage 2 and executed only the one-use 152-request v1.1 block-0 smoke. Run `6abffb64-f71b-4868-89cb-28b0aff0c7e3` completed in 153 attempts, used 2,954,997 input tokens and 10,867 output/reasoning tokens for `$7.4959330`, passed mechanical/external integrity review, and hard-stopped with zero later-stage dispatches. Its authorization is consumed. The negative feasibility observation—C marker 0/3 and no A/B difference in one descriptive block-0 smoke—does not authorize the next stage and is not a scientific result.
- The Gate 3 Phase 1 controller is scoped only to blocks 1–4 U/C formation. Its one-use authorization was consumed by run `3e0d7744-45d7-4db5-a36a-a46b634c0b98`, which stopped at `paused_ambiguous_timeout` after 228 planned attempts: 227 `ok`, one ambiguous HTTP 500, 92 undispatched, and 5/8 checkpoints. Conservative consumed accounting was 2,624,832 input tokens, 20,412 output/reasoning tokens, and `$6.7658595`, including the ambiguous request's full reservation. No out-of-scope request ran. The controller cannot be replayed, and no current authority permits retry, continuation, or Phase 2.
- Protocol v1.1 has a dedicated Gate 1 controller/config/proposal for 20 new `calibration/v1.1` reviews, which stops before smoke and persists the decision that the later Gate 2 proposal must bind. After the October 24 documentation reconciliation, the proposal/schema were regenerated against the amended protocol and current official pricing and passed credential-free inspection. The exact authorization was then consumed by the successful capability release; 20/20 responses were valid and audited, and the release hard-stopped before smoke with `experiment_started: false`. No Gate 2 authority followed from that pass.
- The calibration-only protocol v1.2 Gate 1 controller and static proposal/schema package are retained as superseded preparation artifacts. They must not be used to dispatch or bypass the unified master contract. The current design instead binds all four conditional slices to one final static proposal, one run/namespace, and one exact external single-use authorization.
- Model availability, hosted behavior, SDK behavior, prices, and provider-side token accounting remain time-sensitive. Every future empirical package, including the final unified v1.2 master package and anything beyond it, must bind a fresh verification; no completed budget can be treated as authorization for further calls.

## October 25 protocol v1.2 unified dependency boundary

The unified v1.2 design does not add a provider SDK or agent-framework
dependency. It retains the direct official Responses API boundary, SDK
`max_retries=0`, stateless payloads, maximum in-flight concurrency of one, and
the existing secure `OPENAI_KEY` loading rules. It changes the v1.2 release
topology from four human-authorized processes to one conditionally released
run. The final static package must bind the exact installed dependency versions,
source and protocol hashes, provider settings, one namespace, and the full
ordered `20 + 192 + 480 + 888 = 1,580` logical-request plan.

Within that run, calibration, smoke, Phase 1, and Phase 2 keep non-fungible
maximum dispatch-attempt ceilings of `61 / 694 / 1,765 / 2,997` (`5,517`
total). Automatic continuation is capability pass -> exhaustive outcome-blind
smoke-integrity pass -> 3/5 C-history marker pass -> Phase 2. Human transcript
and claim audit occurs after the run and before publication rather than between
smoke and Phase 1.

The `$5,032.461128` complete-plan shape-sum reservation envelope is a
theoretical projection and is not the live budget. The executor must reject the
next dispatch if cumulative actual settled spend plus that request's full
reservation would exceed `$250.000000`, persisting `budget_limited` if this
prevents completion. Gate 4, confirmation, publication, formation-peer, and all
secondary studies are outside the unified authority. At this checkpoint there
has been no exact v1.2 master authorization, credential use for v1.2, or v1.2
provider call. No v1.2 calls have run.

The dependency lock, configuration, and entry point do not themselves prove that a live model or paid API call completed. Completion must be established from the integrity-checked durable run and immutable `stage2_report.json`. Exact provider list-price cost is reported only when cached-input, cache-write-input, and reasoning-token details are complete; otherwise it is null while conservative local accounting remains explicit. The frozen Stage 2 price table is $2.00/M ordinary input, $0.10/M cached input, $2.50/M cache writes, and $10.00/M output including reasoning. No commit or push is performed by the Stage 2 command.

## October 24 formation-peer dependency and execution boundary

The [formation-peer specification](docs/FORMATION_PEER_CONTROLS.md), `experiments/formation_peer_controls.py`, and `experiments/formation_peer_analysis.py` add no third-party dependency. They reuse deterministic fixtures, immutable visible histories, prompt rendering, strict scoring, and paired uncertainty helpers from the main package. The command runs from the source checkout as `PYTHONPATH=src uv run --frozen python -m experiments.formation_peer_controls`; output must be a new exclusive directory.

This isolated runner offers only the `evidence_policy`, `always_accept`, and `always_reject` focal controls. Its artifacts carry `source: mock`, protocol `formation-peer-v1`, separate source/plan hashes, zero paid calls, and zero cost. Main-experiment analysis rejects these artifacts. There is no live-provider option, credential path, network dispatch, or resume path. The nominal inventory is 56 scripted calls per block; the fixed single overlength-message retry bounds attempts separately, preserves raw failures, and stops on the retained-history byte ceiling.

Any future live secondary controller needs a separately reviewed request inventory, exact token/context/cost limits, current source/prompt bindings, and unique authorization; none of the main Stage 2/Gate 1/Gate 2/Gate 3 contracts or the unified v1.2 master contract covers it. The formation-peer workflow remains offline-only and one-round under the explicit v1.2 compatibility decision. Gate 3's consumed authorization grants no secondary or further main dispatch authority. Protocol v1.2 is selected for offline preparation as an early-futility methodological deviation; its calibration, smoke, Phase 1, and Phase 2 are conditionally sequenced within one prospective master authorization, and no dependency lock or v1.1 artifact grants that authority.
