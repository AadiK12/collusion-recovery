# Recovery runner status

The additive runner is now implemented at `experiments/recovery_v3/runner.py`
and passes 129 focused recovery tests (including administrative handoff and
analysis tests at this snapshot). It includes the six-source fixed schedule,
matched five arms, independent immediate branches, serial four-step carryover,
fresh six-call capability gate, exact initial-request hashes, locked historical
allocation, one shared paced transport, and a single-use independently audited
release. The minimal first wave is explicitly reference-1 only: 86 total calls.
All six endpoints remain prospectively assigned; no source is selected on outcome.

Endpoint and decision helpers are implemented and tested. Independent science
and security reviews are underway. Remaining before first paid call: independent
exact release review; the parent's reviewed administrative handoff utility quiesces and
finalizes induction safely; then freeze the terminal inventory and launch.
No recovery provider call has occurred. Parent owns administrative handoff and
fixture modules. Execution owns runner, endpoints, decisions and analysis modules.

The analysis helper is already assigned to execution's accounting subagent; no
parallel parent edit is needed. It is not on the capability dispatch path beyond
an import and final reporting.

Parent launch-review fixes applied: explicit reference-1-only wave; identical
forced `submit_verdict` in precomputed and executed request bodies; and
`repair_started` now requires an HTTP-produced response artifact, not merely a
spend reservation. The first such request is durably recorded separately.

Administrative handoff completed successfully: original induction is terminal
`partial`, 429 local calls settled, zero local in-flight/reserved balance,
original journal prefix preserved. Proof and semantic verification are in
`administrative-handoff-proof.json` and `administrative-handoff-final.json`.

First-wave release now exists: `release/recovery-v3/wave-1.json`, release hash
`85e504d8d55d98d2b1eeced85f990d1c4df4a7bfd59f104d90a849ebef3933ac`.
All three independent execution agents passed their final exact-release audits.
All 144 recovery tests passed. Combined audit `release/recovery-v3/wave-1.audit.json`
binds the three reviews, tests and handoff evidence.

The sole live runner has launched in exec session 30045 for
`induction-v3-recovery-3f57db64-9595-43fb-9462-8df2816e54e9`.
Read its `STATUS.json`, `capability_result.json`, and eventual
`REPAIR_STARTED.json` for actual progress. Six capability calls precede 80
research calls. Invocation is not itself proof that a research call occurred.
No further induction or human approval is pending. All bound files are frozen.

LIVE RESEARCH CONFIRMED: the fresh capability result is 6/6 passed and the first
three hypothesis-bearing repair decisions have returned. The live run's
`REPAIR_STARTED.json` binds the actual HTTP-produced response artifact, request
reservation and confirmation time. Recovery has started; it is no longer only
prepared or waiting on capability. Root continues monitoring the finite80-call
first wave and independent outcome/accounting agents are checking its artifacts.

## Final first-wave outcome

Wave 1 is completed: 86 settled calls, six correct capability responses and all
80 assigned first-wave research decisions valid. The sole runner exited zero;
no calls remain in flight and no new liability remains. Independent accounting
and all86-decision protocol verification passed with zero discrepancies. New
estimated cost is $0.69186867; the prior $0.6402 liability remains held once.

Immediate and four-step sequential results are identical: full evidence with
retained history, with correction, or with reset alone each accepted every
correct claim and rejected every faulty claim. Reset plus correction rejected
everything, including all eight correct claims across the two phases. The
constrained retained-neutral baseline also rejected everything as required, so
the earlier unsupported-acceptance behavior did not carry into this diagnostic
context even without correction/reset. Do not claim a uniquely demonstrated
repair mechanism or deliberate collusion.

This is one completed source pair. All six sources remain assigned and 400/480
research cells remain unknown; the full study is incomplete. Final independent
reports are `wave-1-scientific-readout.md`, `wave-1-independent-accounting.md`,
and `wave-1-protocol-verification.json`. No further paid dispatch is running.
