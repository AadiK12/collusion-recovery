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
- The preferred experiment-specific envelope is an exact rendered shape sum.
  It includes all 1,580 initial attempts, all 760 possible message-overlength
  retry shapes, and the 17 most expensive distinct logical identities allowed
  by the per-stage ambiguous-5xx policy: **2,357 potentially billable shapes**.
  The other 3,160 transport-retry slots remain in the physical-dispatch ceiling
  but contribute no tokens or cost because they represent known-not-completed
  attempts whose reservations must be released.
- The current internal renderer produces a strict shape-sum reservation ceiling
  of **$5,032.461128**: $1.344343 calibration, $614.038465 smoke,
  $1,003.969050 remaining formation, and $3,413.109270 completion. Independent
  reconciliation confirmed this value and traced the earlier **$5,032.537796**
  surrogate to obsolete namespace/fixture inputs. The canonical aggregate is
  1,022,983,098 input tokens and 9,654,272 output tokens, with a maximum
  per-request input bound of 776,741 tokens. This ceiling does not authorize
  spend and is not an actual-spend forecast.
- A separate **$29,190.226320** coarse context-window stress bound assumes every
  one of 5,517 attempts consumes the model's full available context and the most
  expensive applicable token categories. It is deliberately extreme,
  non-predictive, and is not the budget decision envelope.
- The $250 cap intentionally need not cover the complete worst-case shape sum.
  Issuance instead requires independently verified ledger semantics: before
  every dispatch, cumulative reconciled actual spend plus that request's strict
  reservation must fit under $250. If not, the controller durably terminates as
  `budget_limited` before dispatch. That is a safe, non-complete pilot outcome;
  neither authorization nor the cap guarantees all 1,580 requests will run.

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

1. Freeze and independently audit the reconciled 1,580-request inventory,
   exact shape selection, and projection hashes against the canonical
   $5,032.461128 aggregate.
2. Prove in the live controller that one cumulative ledger survives every stage
   and resume, reconciles actual spend, reserves the next request before
   dispatch, and records a durable non-complete `budget_limited` terminal when
   the $250 cap cannot cover the next request. Any cap change must occur in
   reviewed source and force fresh proposal and authorization hashes.
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
cannot bind the unresolved final proposal and therefore cannot yet be consumed
as live authority. No credential access, provider client, network call, paid
call, run directory, or v1.2 model observation is created by this architecture
work.
