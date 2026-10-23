# Runtime and dependencies

- Python: requires 3.11 or later; locally verified on 3.13.7. `.python-version` records that tested interpreter version.
- Third-party runtime dependencies: none.
- Third-party test dependencies: none. Tests use `unittest` and the standard library.
- Config format: TOML, parsed with the standard-library `tomllib`.
- Network/package downloads: not required for validation, tests, runs, or analysis.
- Repository execution: set `PYTHONPATH=src` or use the Makefile. This is currently a source-checkout workflow, not an installed wheel.

There is no third-party dependency lockfile because no third-party packages are used. Each run records its exact Python version, empty dependency list, configuration hash, and source-file hashes. If external packages are added later, introduce an actual resolver-generated lockfile and record it in manifests before live experiments.

The standard library's seeded pseudorandom implementation and serialization are part of the runtime assumption. Use the recorded interpreter when reproducing exact fixture bytes; fixture hashes detect drift. Provider randomness is a separate future concern and is not controlled by these seeds.
