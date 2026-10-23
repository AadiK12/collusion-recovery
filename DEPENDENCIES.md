# Runtime and dependencies

- Python: requires 3.11 or later; locally verified on 3.13.7. `.python-version` records that tested interpreter version.
- Third-party runtime dependencies: none.
- Third-party test dependencies: none. Tests use `unittest` and the standard library.
- Config format: TOML, parsed with the standard-library `tomllib`.
- Network/package downloads: not required for validation, tests, the original offline runs, fake-live staged runs, or analysis.
- Repository execution: set `PYTHONPATH=src` or use the Makefile. This is currently a source-checkout workflow, not an installed wheel.

There is no third-party dependency lockfile because no third-party packages are used. New staged-run manifests record the exact Python version and implementation, an empty third-party dependency list, the configuration and aggregate implementation hashes, and the individual source-file hashes. The original offline runner records its runtime and source hashes under its earlier manifest schema. If external packages are added later, introduce an actual resolver-generated lockfile and record it in manifests before live experiments.

The standard library's seeded pseudorandom implementation and serialization are part of the runtime assumption. Use the recorded interpreter when reproducing exact fixture bytes; fixture hashes detect drift. Provider randomness is a separate empirical concern and is not controlled by these seeds.

## October 23 WP1/WP2 dependency boundary

The fake-live provider, canonical prompt renderer, conservative budget ledger, staged orchestration, durable resume, and protocol-freeze writer remain standard-library-only. `configs/fake_live.toml` names only the deterministic local fake provider, contains no credential field, and cannot select empirical transport.

The OpenAI module is an inert translation boundary, not an installed or configured API client. It can build a stateless Responses payload and parse captured responses without network access. Its SDK transport wrapper accepts only an already constructed client supplied by a future authorized caller; it does not import the OpenAI package, read an API key, construct a client, or fall back to HTTP. Consequently:

- `openai` is not a current repository dependency and no package was installed for WP1/WP2.
- No API key or other credential is required for the checked-in test suite or fake-live workflow.
- Adding empirical execution later requires an explicit dependency/version decision, a resolver-generated lockfile, secure credential provisioning outside durable artifacts, and a separately reviewed transport entry point.
- The injected wrapper requires an explicit SDK version string and records it with the exact official base URL, timeout, and disabled SDK retry setting; that metadata is part of the separately authorized transport hash.
- Model availability, hosted behavior, SDK behavior, prices, and provider-side token accounting are time-sensitive and must be reverified before any paid preflight.

No live model or paid API call was made while establishing this dependency boundary.
