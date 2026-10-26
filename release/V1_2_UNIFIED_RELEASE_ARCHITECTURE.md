# Protocol v1.2 unified-release architecture

Status: **offline design and contract primitives only; not authorizable and not
dispatchable**

The unified release replaces four spending approvals with one source-bound,
single-use authorization for the complete 1,580-request pilot. It does not
remove the scientific stop rules. Later request slices may proceed under that
same authorization only when the earlier frozen evidence conditions pass.

## Authority and budget

- One master run and one namespace cover Gate 1, Gate 2, Gate 3 Phase 1, and
  Gate 3 Phase 2.
- The authorization binds the exact 1,580 logical identities, the maximum
  5,517 physical dispatch attempts, their stage partition, their request order,
  the provider and transport, pricing, retry policy, source inventory, and all
  rendered token/cost ceilings.
- The cumulative operational hard cap is **$250.000000**. It is reserved before
  every physical dispatch, survives resumes, and never resets at a stage
  boundary. It is a source constant, not a CLI option or environment setting.
- A separate **$29,190.226320** absolute theoretical envelope assumes every one
  of 5,517 attempts consumes the model's full available context and the most
  expensive applicable token categories. It is deliberately extreme,
  non-predictive, and grants no spending authority.
- A final proposal cannot issue authorization unless the sum of the exact
  rendered stage reservation ceilings fits within the $250 operational cap.

## Conditional release sequence

1. The 20-request calibration slice runs first. A failed capability assessment
   stops every hypothesis-bearing request.
2. The 192-request block-0 smoke runs only after calibration passes. An
   exhaustive, outcome-blind mechanical audit of every scheduled identity,
   attempt, payload, visibility boundary, checkpoint, provider record, and
   budget record must pass before automatic continuation. Human transcript and
   claim review is deferred until after the run but remains mandatory before
   publication or any confirmation decision.
3. The 480-request blocks 1--4 formation slice runs only after smoke integrity
   passes.
4. The controller computes the frozen five-block C-history decision, including
   block 0. Fewer than three qualifying histories stops the pilot.
5. The remaining 888 evaluation/probe requests run only after the formation
   decision passes. Completion hard-stops before confirmation or any secondary
   study.

Every transition and failure is durable. A failed post-run human transcript or
claim audit invalidates publication claims and hard-stops confirmation. No caller may inject a provider,
transport, executor, gate result, cost limit, or later-stage override.

## Superseded stage-scoped release

`configs/gate1_v1_2_calibration_proposal.json` and
`release/GATE1_V1_2_HUMAN_AUTHORIZATION_SCHEMA.json` are not valid authority for
the unified run. Adding the unified implementation changes the complete package
implementation hash, and the prior Gate 1 artifacts bind a different run ID,
namespace, plan, scope, and hard stop. The master proposal marks them
superseded and non-dispatchable; they remain in the repository only as an audit
record.

## Exact work remaining before any live execution

1. Render the canonical unified plan into four exact stage inventories,
   including request/wire hashes and conservative per-request token bounds.
2. Compute each stage's aggregate token and strict reservation ceilings from
   those rendered shapes; verify their cumulative projected cost is at most
   $250.
3. Implement the dedicated live master controller and durable one-run state
   machine. The generic executor must remain unable to start this run.
4. Bind automatic calibration assessment, the exhaustive outcome-blind smoke
   audit, and the five-block formation decision to exact immutable evidence
   hashes. Bind the deferred human transcript/claim audit before publication or
   any confirmation release.
5. Enforce one cumulative attempt/token/cost ledger across every slice and
   resume, including the frozen ambiguous-5xx policy and per-slice affected-
   identity caps.
6. Implement source-identity checks so the live path alone constructs the exact
   official SDK transport; reject caller-supplied providers, clients,
   transports, or executors.
7. Complete offline adversarial tests for authorization replay, source drift,
   out-of-order evidence, gate bypass, budget reset, forged provider identity,
   ambiguous completion, crash/resume, and receipt races.
8. Reverify current official pricing, render the final proposal and byte-exact
   authorization schema, and complete an independent artifact audit.
9. Only after that audit may a fresh owner-only authorization be packaged and
   atomically consumed outside the repository. Receipt creation must precede
   credential access, client construction, networking, or spend.

The current chat approval expresses the owner's desired approval model, but it
cannot bind artifacts that do not yet exist. No credential access, provider
client, network call, paid call, run directory, or v1.2 model observation is
created by this architecture work.
