# Runtime and dependencies

- Python: requires 3.11 or later; locally verified on 3.13.7. `.python-version` records that tested interpreter version.
- Third-party runtime dependency: `openai==3.37.0`, currently used only by explicitly invoked empirical controllers: the completed Stage 2 path and the authorization-gated Gate 2 successor `execute` path. The newly required fresh selected-version Gate 1 path is not yet implemented; it must use the same locked, explicit-controller boundary rather than an ad hoc client.
- Test framework: standard-library `unittest`; the full suite runs inside the locked environment because it also exercises the SDK-facing Stage 2 boundary with injected fake clients.
- Config format: TOML, parsed with the standard-library `tomllib`.
- Network/package downloads: no provider/network access is required for validation, tests, the original offline runs, fake-live staged runs, analysis, annotation, or successor-proposal inspection. Initial locked-environment setup may require package-index access; only explicitly authorized empirical execution uses the OpenAI API.
- Repository execution: set `PYTHONPATH=src`; the Makefile invokes `uv run --frozen python` by default. This is currently a source-checkout workflow, not an installed wheel.

`pyproject.toml` pins the direct dependency exactly, and the resolver-generated `uv.lock` records its transitive environment. Install without re-resolving it:

```bash
uv sync --frozen --python 3.13.7
```

New staged-run manifests record the exact Python version and implementation, the installed OpenAI SDK version for empirical transport, the configuration and aggregate implementation hashes, and the individual source-file hashes. The original offline runner records its runtime and source hashes under its earlier manifest schema. Do not hand-edit `uv.lock`; regenerate and review it if the dependency set changes.

The standard library's seeded pseudorandom implementation and serialization are part of the runtime assumption. Use the recorded interpreter when reproducing exact fixture bytes; fixture hashes detect drift. Provider randomness is a separate empirical concern and is not controlled by these seeds.

The legacy `gate2-readiness` command is retired and always fails closed. The `collusion_recovery.gate2_successor inspect` path uses the same frozen environment and validates a checked proposal without SDK-client construction, credential loading, network access, or dispatch; its `execute` path is a separate provider-capable boundary that requires an exact proposal and unique external authorization.

## October 23 WP1/WP2 and Stage 2 dependency boundary

The fake-live provider, canonical prompt renderer, conservative budget ledger, staged orchestration, durable resume, and protocol-freeze writer remain standard-library-only. `configs/fake_live.toml` names only the deterministic local fake provider, contains no credential field, and cannot select empirical transport.

The OpenAI provider module remains an inert translation boundary: it can build stateless Responses payloads and parse captured responses without network access, and its transport wrapper accepts only an already constructed client. The separate historical `stage2` controller and the authorization-gated Gate 2 successor `execute` path are the empirical code paths that import the locked SDK, securely load a credential, construct the official client, and inject it into that wrapper. Consequently:

- Offline tests, mock runs, fake-live runs, prompt inspection, and analysis require no API key and make no provider request.
- Stage 2 requires exactly OpenAI SDK `3.37.0` from `uv.lock`; the controller refuses a different installed SDK version.
- Stage 2 reads only `OPENAI_KEY` from the repository-root `.env.local`. The file must be a regular file owned by the current user, mode `0600` or otherwise have no group/world permission bits, ignored and untracked by Git, and not a symlink. The secret is not placed in configuration, manifests, journals, payloads, or reports.
- Every ambient `OPENAI_*` variable is rejected so SDK credentials, custom headers, organization, project, routing, or logging settings cannot silently change the authorized client.
- The injected wrapper records the exact official base URL, 60-second timeout, OpenAI SDK version, disabled SDK retry setting, disabled environment trust, and disabled redirects; that metadata is part of the authorization binding. Environment-derived HTTP proxy and TLS settings therefore cannot alter this release.
- `configs/empirical_stage2.toml` fixes model/settings, the verified pricing source and timestamp, the **$1.78** cost ceiling, 67-attempt ceiling, 211,932-input-token ceiling, 94,208-output-token ceiling, 9,353-input-token per-request ceiling, 4,096-output-token per-request ceiling, and maximum in-flight value of one.
- The controller is bound to `runs/empirical/stage2-2026-10-23/`, rejects alternate live run directories, and immutably persists and journal/manifest-binds the complete authorization package before the first reservation. It runs the exact two-logical-call Gate 0 first and runs exactly 20 calibration reviews only if Gate 0 passes. It contains no smoke or Gate 2 execution path.
- The Gate 2 successor controller is distinct from Stage 2 and is scoped to a one-use block-0 smoke. It has no current checked default proposal or authorization, and its exact-source tests are not yet green; its mere presence does not authorize provider access or dispatch.
- Protocol v1.1 now requires a separate Gate 1 controller/config/proposal for 20 new `calibration/v1.1` reviews before that smoke proposal can be prepared. No such selected-version Gate 1 execution path exists yet. It must receive its own review, exact caps, and unique non-reusable authorization, stop before smoke, and persist the decision that the later Gate 2 proposal binds.
- Model availability, hosted behavior, SDK behavior, prices, and provider-side token accounting remain time-sensitive. A later stage must reverify them and obtain a new release; the Stage 2 budget cannot be treated as authorization for further calls.

The dependency lock, configuration, and entry point do not themselves prove that a live model or paid API call completed. Completion must be established from the integrity-checked durable run and immutable `stage2_report.json`. Exact provider list-price cost is reported only when cached-input, cache-write-input, and reasoning-token details are complete; otherwise it is null while conservative local accounting remains explicit. The frozen Stage 2 price table is $2.00/M ordinary input, $0.10/M cached input, $2.50/M cache writes, and $10.00/M output including reasoning. No commit or push is performed by the Stage 2 command.
