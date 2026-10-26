# Independent execution security review

Status: the reviewed CLI/capability follow-up findings are resolved and independently
checked. No open blocking security finding remains in the reviewed scope. Exact
reviewed source hashes are recorded in `security-audit.json`; later source changes
require checking against that binding before relying on this review.

Release history: the first `release/induction-v3/frozen.json` was correctly
rejected for source drift because seeded READY received an interpretation-only
revision after freezing. That release is superseded. The current exact-release
binding and any launch clearance are recorded in `security-audit.json`.

Final binding passes for `release/induction-v3/campaign.json`, release hash
`c394415eb66b51134a3f82d47a37ddee1884e3fcb6099251d3b4da58d486ea39`, run
`induction-v3-b2522673-f58a-4d9e-8be0-ae08b511db53`. All 292 frozen files matched,
both final handoffs matched their complete bindings (26 warm-up files and 11
seeded files), runtime versions matched, and all four recipes were present.
No reviewed implementation file changed after the final behavioral checks. The
new run directory did not yet exist. This exact release is cleared by the scoped
security review; the execution owner combines it with the separate scientific
and accounting release reviews.

Scope: `cli.py`, `capability.py`, `engine.py`, `transport.py`, `upstream.py`, `common.py`, the pinned tool
dispatch and filesystem paths, and their interaction with the Docker task runner.
This review does not independently approve the scientific release or substitute
for the accounting audit and full release tests.

One blocking concurrency finding was identified and fixed by the execution owner:
content-addressed request paths can collide across matched parallel dyads, while
the original atomic writer reused a fixed `.tmp` path. The writer now uses a
unique same-directory temporary file. A targeted stress check completed 60 writes
to the same request path across three threads, with a valid final artifact and
no temporary residue.

Resolved follow-up release findings:

- SEC-002 (P1): the initial `collect_recipes` searched the serialized READY text
  for a path and hash independently and did not validate all listed evidence.
  An offline malformed fixture with zero recipe hashes, unrelated real hashes,
  and a missing evidence file was initially accepted. The revised collector
  verifies every `files` and `source_files_verified` path/hash pair and freezes
  them, plus READY itself, in the source inventory. Independent checks now reject
  mismatched paired hashes and missing nonrecipe evidence, and accept correctly
  paired recipe hashes.
- SEC-003 (P2): a capability failure returned before publishing comparison and
  `verify_run` artifacts. The early return is removed. An independent CLI test
  used a real zero-call accounting journal in a temporary root with mocked provider
  and sandbox: the blocked result published comparison/verification artifacts and
  retained both scheduled dyads as `not_started`.
- SEC-004 (P2): the capability pass check accepted a matching verdict with no
  required `reasoning` field. The source schema requires string reasoning and a
  valid verdict. The revised gate checks both. Independent mocked-response checks
  rejected missing/wrong-type reasoning after one response, and accepted all six
  correctly formed responses.

The parent's subsequent replay/concurrency review prompted additional changes,
which were independently checked here:

- Original Responses output items now retain their exact order, IDs, phases,
  content blocks, and opaque encrypted state. The adapter propagates these items
  through normal turns and private reflections. An actual pinned-engine canned
  episode verified preservation across 22 model-turn substitutions, including both
  persisted private reflections, without provider calls.
- A reflection response containing a tool call now stops after accounting for
  that response. No unexecuted tool call is silently omitted or replayed with an
  invented result. The focused transport suite passed 10 tests.
- Validation future failures stop the journal inside the executor context before
  waiting for the other worker. An independent CLI integration test with a real
  zero-call journal verified that the sibling saw the stop before finishing,
  its next dispatchability check failed, and a partial verified report was saved.
- Development submission order is now schedule first, recipe second, rotating
  recipes across the first matched dyad before submitting their second dyads.
  Concurrency limits remain unchanged.

An independent offline build of all six capability cases used the integrated
adapters and verified actual pytest output: five tests passed for every case,
and each raw log contained four task entries. Missing-raw provider messages
omitted that log. The builder made zero provider calls.

No generated-code host execution bypass was found in the installed adapter path.
The original `_run_tests` retains its path and vacuity checks, then the patched
`_run_python_tests` sends explicit code/test text to the verified Docker runner.
Agent SQL dispatch aliases call the isolated query/inspection methods. The
task paths resolve within the frozen vendor task directory. Host-side reference
SQL evaluation is pinned trusted source, not agent-supplied SQL. Scratch names
include task, dyad/run, episode, and actor identity; the root adapter must include
only that actor's generated helper files.

The paid transport uses an explicit official endpoint, a clean non-proxy HTTP
client with redirects disabled, one semaphore bounded to three calls, and
thread-local call identities. Credential loading returns only `OPENAI_KEY` in
memory without populating environment variables. Credentials are not part of
request artifacts. Success bodies are checked for an exact credential echo;
provider error fields are redacted. A catch-all post-dispatch handler preserves
unsettled reservations and stops dispatch on processing failures.

Upstream imports were tested with socket connection functions blocked and
instrumented: zero connection attempts occurred. Calls from agent turns and
reflection resolve the patched `_complete_with_retries`; the upstream CLI and
route-check entrypoint are not invoked by this engine. Hidden reasoning extraction
is disabled; provider-issued encrypted replay state remains available.

The helper-enabled sandbox passed 22 real-Docker/unit tests. The integrated engine
now transports only explicit prior same-actor helper tests after checking the
resolved parent against that actor's scratch directory and rejecting symlinks.
The CLI installs adapters before capability or induction, revalidates the release
and audit under the held program lock, verifies the sandbox, then creates the
single reservation journal before loading the authorized credential. No upstream
CLI or separate unaccounted route probe is invoked. Each paid capability call uses
the same accounted transport as induction, and is durably reported as it completes.

Final release binding remains the execution owner's responsibility: freeze the exact engine/image/source hashes,
call `verify()` before task execution, install adapters once before concurrent
dyads, and run the complete offline release suite before paid launch. No provider
calls or credential reads were performed during this review.
