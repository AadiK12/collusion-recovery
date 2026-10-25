# Release manifest template

This file describes the evidence package without containing credentials or granting permission to publish it. Copy it to a versioned release record only after exactly one report path is locked and that path's data are locked.

The main report path does not merge the selected [formation-peer-v1 secondary study](../docs/FORMATION_PEER_CONTROLS.md) into the main experiment. Its empirical inclusion remains conditional on live integration, prospective allocation, and release review. Main A–F, primary B−A, and the six evaluation-time peer cells retain their original identities. Secondary mock, pilot, and any future confirmation artifacts require their own versioned records; the current secondary implementation is scripted-only and not dispatch-ready.

Record secondary reporting status independently: **[NOT RUN / OFFLINE ONLY / EXPLORATORY PILOT ONLY / SECONDARY CONFIRMATION]**. A main-confirmation report may include a distinctly labeled exploratory secondary section using verified FP-P rows; those results never support main confirmation, C10, or a cross-study compound claim. FP-C rows require a separate prospective confirmation freeze and empirical run. A completed secondary pilot is not relabeled NOT RUN merely because secondary confirmation was not conducted.

Readiness must be recorded at the exact release state. The October 24, 2026 `0ddc34c` checkpoint documented 283/283 passing tests and the pre-amendment Gate 1 proposal/hash snapshot. Preserve that snapshot as provenance, not current status. Fresh Gate 1 later passed under consumed one-use authority. Gate 2 run `6abffb64-f71b-4868-89cb-28b0aff0c7e3` then completed the 152-request block-0 smoke and passed integrity review. Gate 3 Phase 1 run `3e0d7744-45d7-4db5-a36a-a46b634c0b98` consumed its separate authorization and paused fail-closed after 228 in-scope attempts. It is incomplete: no formal Gate 3 assessment or Phase 2 authority exists. Block 0 is verified false, while block-1 and block-2 markers are provisionally false from their completed trajectories, so at most 2/5 can qualify; report that only as a partial-run futility upper bound.

Completed Gate 2 evidence and current Gate 3 partial-run boundary:

| Field | Recorded value |
| --- | --- |
| Run ID | `6abffb64-f71b-4868-89cb-28b0aff0c7e3` |
| Proposal hash | `371e6e01b216bb62c1f2a7e77d40e85317585fcc44ff425534b8a14e1a9e3a27` |
| Scope | 152 logical block-0 smoke requests; 532 maximum attempts |
| Actual completion / usage | 152/152 final observations; 153 attempts; 2,954,997 input; 10,867 output/reasoning; `$7.4959330` |
| Smoke report | `runs/empirical/gate2-smoke-v1.1-6abffb64-f71b-4868-89cb-28b0aff0c7e3/gate2_smoke_report.json`; embedded hash `cfc54ebb1b5fad1244fe9b4ea752b77629102b6ef00f434460f07e32ebe0e67c` |
| Integrity decision | Same run directory, `gate2_smoke_decision.json`; embedded hash `ba364795079835e5e09e2449bb0b416bf3193cf077ba3bc6884990bbd669a8ad`; review hash `558103716f2f3c452a8db6ce48f35981894f41de9eeae5efbff750a9bd7add98` |
| Gate 2 terminal state | `completed_smoke_passed_hard_stop`; authorization consumed; `next_stage_authorized: false` |
| Gate 2 scientific boundary | U 20/20 accept; C 20/20 reject; marker 0/3; A/B FAR 0/6 and CPA 6/6; F FAR 0/6 and CPA 0/6; one block only |
| Gate 3 source binding | Source-only commit `54da46735396a0b312b16b1f1e64decbfe1250b9`; implementation hash `0f82c7a3b2de6e84827dec0ed8d2608050a50d04e33a7d7f7bf01c6d59759217` |
| Gate 3 proposal/schema | `configs/gate3_formation_phase1_proposal.json`, hash `1b85e13a8b10c98ad4314611de0da56624328225b54a0aa7764ac72ecabd3b5e`; `release/GATE3_PHASE1_HUMAN_AUTHORIZATION_SCHEMA.json` |
| Gate 3 historical inspection | `ready_awaiting_external_human_authorization`; credentials false; network 0; paid 0; next-stage authority false |
| Gate 3 Phase 1 scope | Blocks 1–4 U/C formation only; 320 logical requests; authorization now consumed |
| Gate 3 Phase 1 ceilings | 480 billable shapes; 640 known-not-completed retries; 1,120 max attempts; 83,647,908 input; 1,966,080 output/reasoning; 360,177 input/request; 4,096 output/request; 104 long-context; one in flight; `$254.465624` nominal / `$312.634350` strict |
| Gate 3 partial execution | `paused_ambiguous_timeout`; 228 attempts = 227 `ok` + 1 ambiguous; 227 observations; 92 undispatched; 5/8 checkpoints |
| Gate 3 conservative consumed accounting | 2,624,832 input; 20,412 output/reasoning; `$6.7658595` |
| Gate 3 scope audit | No out-of-scope call; stop at block 3/C/zero-based episode 6/Bob verdict HTTP 500 |
| Gate 3 scientific boundary | Block 0 verified false; blocks 1–2 provisionally false from completed trajectories; at most 2/5; partial-run futility bound only; no formal assessment and no `0/5` claim |
| Gate 3 evidence archive | `GATE3_PARTIAL_RUN_REPORT.md`; canonical metadata-minimal archive SHA-256 `7e49882f5f3148c1f8cde12b30ad61b8ebe14fa3556360bb6d41d29baf0a1e09` |
| Later scope | Remaining 888 requests unauthorized and undispatched |

Gate 3's one-use authorization is consumed and cannot be replayed. No existing approval permits retry, continuation, assessment completion by dispatch, or Phase 2. If v1.2 is chosen, record it prospectively as an early-futility methodological decision/deviation because no completed Gate 3 trigger artifact exists; separately review and authorize fresh calibration, smoke, and pilot releases, and never pool v1.1 observations.

## 1. Release identity

| Field | Value |
| --- | --- |
| Release version | [REQUIRED] |
| Created at / timezone | [REQUIRED] |
| Created by | [REQUIRED] |
| Exact paper title | [REQUIRED] |
| Locked report path | [CONFIRMATION / EXPLORATORY PILOT-ONLY; ONE REQUIRED] |
| Optional secondary-study status | [OMITTED / MOCK TOOLING ONLY / SEPARATE PILOT / SEPARATE CONFIRMATION] |
| Release purpose | [analysis review / sprint submission / public reproducibility] |
| Authorization record | [REQUIRED FOR EXTERNAL RELEASE] |
| Source repository | [REQUIRED] |
| Full Git commit | [REQUIRED] |
| Git tree clean | [true required] |
| Release tag | [OPTIONAL / REQUIRED IF PUBLIC] |

## 2. Scientific lineage

| Artifact | Version / namespace | Path or immutable locator | SHA-256 | Reviewer |
| --- | --- | --- | --- | --- |
| Scientific protocol | Frozen selected version | `SPRINT_PROJECT_PLAN.md` | [REQUIRED] | [REQUIRED] |
| Protocol revisions | [NONE / LIST] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Historical Stage 2 provenance / consumed-release receipt | `calibration/v1` historical only | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Selected-version Gate 1 controller/config/proposal | [`calibration/v1.1` OR `fallback-two-round/v1.2/calibration`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Calibration request plan | [`calibration/v1.1` OR `fallback-two-round/v1.2/calibration`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 1 authorization / consumption receipt / decision | [REQUIRED; UNIQUE RELEASE] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 2 smoke controller/config/proposal | [`pilot/v1.1/block-0` OR `fallback-two-round/v1.2/pilot/block-0`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 2 authorization / consumption receipt / smoke-integrity decision | [REQUIRED; SEPARATE UNIQUE RELEASE] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 3 Phase 1 formation controller/config/proposal | `pilot/v1.1/blocks-1-4-formation` | `configs/gate3_formation_phase1_proposal.json`; source commit `54da46735396a0b312b16b1f1e64decbfe1250b9`; implementation `0f82c7a3b2de6e84827dec0ed8d2608050a50d04e33a7d7f7bf01c6d59759217` | `1b85e13a8b10c98ad4314611de0da56624328225b54a0aa7764ac72ecabd3b5e` | [INSPECTION REVIEWER REQUIRED] |
| Gate 3 Phase 1 authorization / consumption receipt / partial-run evidence | `pilot/v1.1/blocks-1-4-formation` partial | `GATE3_PARTIAL_RUN_REPORT.md`; run `3e0d7744-45d7-4db5-a36a-a46b634c0b98` | Canonical archive `7e49882f5f3148c1f8cde12b30ad61b8ebe14fa3556360bb6d41d29baf0a1e09` | [PARTIAL-RUN REVIEWER REQUIRED] |
| Gate 3 five-block formation decision | NOT PRODUCED | No completed Gate 3 assessment artifact | NOT APPLICABLE | [RECORD BOUNDARY REVIEW] |
| Gate 3 Phase 2 remaining-pilot controller/config/proposal/authorization | NOT RUN; 888 identities unauthorized | NOT RUN | NOT RUN | NOT RUN |
| Pilot request plan | [`pilot/v1.1` OR `fallback-two-round/v1.2/pilot`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Pilot gate decisions | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Challenge-pair schedule and balance audit | [`protocol/v1.1` OR `protocol/v1.2`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Peer-policy scripts and six-cell probe plan | [`protocol/v1.1` OR `protocol/v1.2`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Peer-message byte-match/common-prefix audit | [`protocol/v1.1` OR `protocol/v1.2`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Persistence-window, claim-interval, and missingness-gate specification | [`protocol/v1.1` OR `protocol/v1.2`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Configuration-level operational-collusion threshold specification | [`protocol/v1.1` OR `protocol/v1.2`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Joint-violation gate / fallback decision | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| v1.2 fallback plan/count audit, if activated | [NOT ACTIVATED / REQUIRED] | [REQUIRED IF ACTIVATED] | [REQUIRED IF ACTIVATED] | [REQUIRED IF ACTIVATED] |
| Locked report-path decision and data lock | [PILOT-ONLY OR CONFIRMATION] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Confirmation sizing simulation and decision | [REQUIRED FOR CONFIRMATION / NOT RUN FOR PILOT-ONLY] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Raw pilot request/response freeze audit | [REQUIRED FOR CONFIRMATION / NOT RUN FOR PILOT-ONLY] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Gate-4 confirmation freeze | [REQUIRED FOR CONFIRMATION / NOT RUN FOR PILOT-ONLY] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Confirmation request plan | [`confirm/v1.1` OR `fallback-two-round/v1.2/confirm` / NOT RUN FOR PILOT-ONLY] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Confirmation data lock | [REQUIRED FOR CONFIRMATION / NOT RUN FOR PILOT-ONLY] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Annotation guide | [`annotation-v1.1` OR reviewed `annotation-v1.2`/dual-version guide matching selected protocol] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Claim-evidence ledger | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |

Pilot and confirmation run IDs must be listed separately. A confirmation-path primary estimate may reference confirmation IDs only; a pilot-only primary estimate may reference pilot IDs only and must use an eligible `P` row.

The main Gate 1/Gate 2/Gate 3 rows above exclude formation-peer-v1 requests. Their authorizations and decisions do not grant secondary-study spending or automatically establish calibration applicability. Gate 2 and Gate 3 Phase 1 authority are consumed; Phase 2 has no authority.

### Pilot run IDs

- `6abffb64-f71b-4868-89cb-28b0aff0c7e3` — retained block-0 Gate 2 smoke; integrity passed; scientifically negative one-block result.
- `3e0d7744-45d7-4db5-a36a-a46b634c0b98` — partial Gate 3 Phase 1 formation run; paused ambiguous; never a completed pilot.
- [REQUIRED FOR ANY SEPARATELY AUTHORIZED FUTURE RELEASE]

### Confirmation run IDs

- [REQUIRED FOR CONFIRMATION / NOT RUN FOR PILOT-ONLY]

### Excluded or invalidated runs

| Run ID | Split | Reason | Decision artifact | Retained for audit? |
| --- | --- | --- | --- | --- |
| [REQUIRED OR NONE] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [YES REQUIRED] |

### Optional formation-peer-v1 lineage — separate from main-study rows

Mark all unrun items **NOT RUN** and all scripted artifacts **MOCK ONLY**. Never fill a live-run field with the mock command's plan or an assumed reuse of a main-study authorization.

| Secondary artifact | Version / split / run IDs | Path or immutable locator | SHA-256 | Reviewer |
| --- | --- | --- | --- | --- |
| Secondary specification and revisions | `formation-peer-v1` / [REVISIONS OR NONE] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Standalone config and controller review | [MOCK ONLY / SEPARATELY REVIEWED LIVE] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Exact request plan / prompt inventory | [SEPARATE SECONDARY NAMESPACE] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Source and analysis inventories | [SEPARATE SOURCE / ANALYSIS HASHES] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| All assigned block seeds / role and condition order / matched fixtures | [EVERY ASSIGNED BLOCK] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Calibration relevance and prerequisite lineage assessment | [UNCHANGED MODEL/SETTINGS/TASK EVIDENCE OR FRESH CALIBRATION] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Independent live scope / limits / authorization and consumption receipt | [NOT RUN OR UNIQUE SECONDARY RELEASE] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Secondary pilot plan / run IDs / data lock | [MOCK ONLY / NOT RUN / SECONDARY PILOT IDS] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Prospective secondary confirmation N / analysis seed / freeze | [NOT RUN OR SECONDARY CONFIRMATION] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Secondary confirmation plan / run IDs / data lock | [NOT RUN OR DISTINCT CONFIRMATION IDS] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Secondary missing/failed/excluded run inventory | [NONE OR ALL RETAINED IDS WITH REASONS] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Main-v1.2 compatibility/version decision | [NOT ACTIVATED / EXPLICIT PROSPECTIVE DECISION] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Secondary claim-evidence ledger | [SECONDARY STUDY AND SPLIT LABELS] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |

An earlier calibration pass is relevant only if a reviewed record establishes the unchanged model/settings/task and names the precise lineage; its authorization is never reusable. The one-round secondary protocol is not automatically compatible with the main two-round v1.2 fallback. Preserve existing secondary artifacts and record a prospective compatibility/version decision before any continuation.

## 3. Provider and execution metadata

| Field | Frozen value |
| --- | --- |
| Provider/API | [REQUIRED] |
| Requested model alias/snapshot | [REQUIRED] |
| Returned model metadata | [REQUIRED] |
| Requested/returned service tier | [REQUIRED] |
| System fingerprint policy | [REQUIRED] |
| Model settings | [REQUIRED, NO SECRETS] |
| Execution date range | [REQUIRED] |
| Pricing source / units / timestamp | [REQUIRED] |
| Approved request/token/dollar caps | [REQUIRED] |
| Actual request/token/cost totals | [REQUIRED] |
| Provider-identity drift | [NONE / FULL ACCOUNT] |

Do not include API keys, organization/project secrets, authorization headers, client cookies, or private billing identifiers.

Repeat this metadata table separately for each secondary empirical release, if one exists. Main and secondary actual usage/cost must reconcile independently; a combined planning count is not a combined authorization.

| Secondary planning count | Per block | Five blocks | Interpretation |
| --- | ---: | ---: | --- |
| Formation logical calls / focal reviews | 40 / 20 | 200 / 100 | Two policies × ten focal message/verdict episodes |
| Recovery logical calls / focal reviews | 16 / 16 | 80 / 80 | Eight independent diagnostics from each checkpoint |
| Total logical calls / focal reviews | **56 / 36** | **280 / 180** | Excludes retries, calibration, preflight, and release overhead |
| Maximum scripted attempts | **76** | **380** | One optional overlength retry per message only; **not a live dispatch budget** |

The combined five-block main v1.1 plus secondary planning total is **1,660 logical requests**, including the main 20 calibration reviews and excluding the two nonexperimental transport-preflight calls, retries, and separate release overhead. Live attempt, context, token, dollar, and transport-retry limits must be separately derived, reviewed, and authorized. Do not substitute the scripted-attempt ceiling for that live envelope.

## 4. Analysis and paper artifacts

| Artifact | Source inputs | Path | SHA-256 | Reproduction command | Verified by |
| --- | --- | --- | --- | --- | --- |
| Empirical flow table | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 2 block-0 smoke report and integrity decision | `6abffb64-f71b-4868-89cb-28b0aff0c7e3` | Run directory: `runs/empirical/gate2-smoke-v1.1-6abffb64-f71b-4868-89cb-28b0aff0c7e3`; files: `gate2_smoke_report.json`, `gate2_smoke_decision.json` | Embedded hashes `cfc54ebb1b5fad1244fe9b4ea752b77629102b6ef00f434460f07e32ebe0e67c` / `ba364795079835e5e09e2449bb0b416bf3193cf077ba3bc6884990bbd669a8ad` | Evidence replay / passed-lineage verification | [REQUIRED] |
| Primary contrasts | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Secondary contrasts | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Missingness bounds | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Pilot joint-violation feasibility report (gate/exploratory only) | [PILOT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Confirmation joint-violation-marker prevalence report (required for C02/C10) | [CONFIRM IDS / NOT RUN FOR PILOT-ONLY] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Aggregate/immediate/early/late B−A analysis | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Countermeasure FAR/CPA/balanced-accuracy analysis | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Six-cell peer diagnostic and three frozen contrasts | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Secondary formation episodes 2–10 paired contrast / episode-1 negative control / last-three descriptive summary | [SEPARATE SECONDARY SPLIT IDS OR NOT RUN] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Secondary immediate recovery FAR / CPA / balanced accuracy | [SEPARATE SECONDARY SPLIT IDS OR NOT RUN] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Secondary all-assigned-block flow / raw paired values / uncertainty / missingness bounds | [SEPARATE SECONDARY SPLIT IDS OR NOT RUN] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Figure 1 | schematic/source | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Figure 2 | [LOCKED PILOT-ONLY OR CONFIRMATION SPLIT; ONE ONLY] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Figure 3 | [LOCKED PILOT-ONLY OR CONFIRMATION SPLIT; ONE ONLY] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Figure 4 | [LOCKED PILOT-ONLY OR CONFIRMATION SPLIT; ONE ONLY] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Final paper source | verified tables | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Final PDF | paper source | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |

## 5. Reproducibility environment

| Field | Value |
| --- | --- |
| Python/runtime | [REQUIRED] |
| OS/platform used for analysis | [REQUIRED] |
| Dependency declaration | [PATH / HASH] |
| Lock file | [PATH / HASH] |
| Keyless validation command | [REQUIRED] |
| Analysis replay command | [REQUIRED] |
| Expected test count/result | [REQUIRED] |
| Expected table/figure hashes | [REQUIRED] |
| Paid reproduction warning | [REQUIRED] |

## 6. Included and excluded release surfaces

### Included

- [ ] Protocol and revision history
- [ ] Prompt/configuration files and hashes
- [ ] Synthetic fixture generator, oracle, and seeds
- [ ] Raw synthetic inputs and permitted visible transcripts
- [ ] Request/response lineage and nonsecret provider metadata
- [ ] Usage, invalidity, missingness, and exclusion records
- [ ] Scoring, analysis, and figure-generation code
- [ ] Machine-readable tables and rendered figures
- [ ] Paper source and final PDF
- [ ] Pre-sprint and LLM-use disclosures

### Explicitly excluded

- Credentials, authorization headers, cookies, or secret-bearing provider data
- Private billing/account identifiers not needed for reproducibility
- Unrelated local files or other repositories
- Hidden chain-of-thought or provider-internal reasoning state
- Nonpublic artifacts whose terms prohibit redistribution
- [OTHER EXCLUSIONS WITH REASON]

## 7. Disclosure and compliance records

| Record | Path / locator | SHA-256 | Reviewed by/date |
| --- | --- | --- | --- |
| Pre-sprint work disclosure | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| LLM Usage Statement | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Limitations and Dual-Use / Ethical Considerations | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Reference verification log | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Secret-scan log | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| License/terms review | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| PDF visual-QA record | [REQUIRED] | [REQUIRED] | [REQUIRED] |

## 8. Release assertions

- [ ] All hashes were computed from the exact release candidates after final edits.
- [ ] The primary estimate uses only the locked report split: confirmation data and eligible `C` rows on the confirmation path, or pilot data and eligible `P` rows on the explicitly exploratory pilot-only path.
- [ ] The main release implements and identifies its single frozen protocol version (v1.1 or, if the sole fallback was activated, v1.2). Optional formation-peer-v1 artifacts are separately versioned and labeled, never merged into main arms or estimates; no superseded readiness artifact is presented as current authorization or current protocol evidence.
- [ ] All scheduled blocks and outcomes in the locked report split are accounted for; on the confirmation path this includes every scheduled confirmation block.
- [ ] Challenge-pair balance, six peer-policy cells, timing windows, and—on the confirmation path—the confirmation-size decision reproduce from the frozen selected-version artifacts.
- [ ] The compliant/approval-only message hashes match exactly, approval-only/reciprocal messages pass the common-prefix audit, and no superseded peer-prompt hash is presented as belonging to the selected protocol.
- [ ] Claim intervals, all-scheduled gates, and configuration-level C10 thresholds reproduce exactly; C10 is not described as same-block or individual-agent co-occurrence.
- [ ] Every headline claim is `VERIFIED` in the claim-evidence ledger.
- [ ] Any secondary result accounts for all assigned blocks and frozen seeds, including no-violation and missing/invalid histories. Secondary effect direction never determined main advancement or outcome-based sample additions; an unrun or imprecise secondary study remains reportable.
- [ ] Secondary paired-block analysis retains its own pilot/confirmation split, prospective N and analysis seed, raw values, usefulness measures, uncertainty, and all-scheduled bounds. Five blocks are exploratory, not a meaningful-null guarantee.
- [ ] Main and secondary report statuses are explicit and independent. Each estimate and compound claim uses one study and split; any exploratory FP-P result accompanying main confirmation is separately labeled and excluded from confirmation claims.
- [ ] Secondary claims identify a total assigned peer-policy effect with truthful reward/feedback mediators. They do not assert a pure peer-action effect, private intent, spontaneous negotiation, mediation of main B−A, or late persistence from eight independent checkpoint-restarted diagnostics.
- [ ] Separate live secondary provenance and authorization exist for any empirical secondary claim; otherwise outputs remain explicitly `source: mock` or **NOT RUN**. Main Gate 1/Gate 2/Gate 3 decisions and the historical `0ddc34c` ready snapshot are not repurposed as secondary authorization.
- [ ] The release contains no unresolved `RESULT REQUIRED`, `VERIFY`, `DECISION REQUIRED`, `TODO`, or `[REQUIRED]` marker in user-facing artifacts.
- [ ] Offline reproduction was tested from a clean checkout.
- [ ] Analysis reproduction matched the listed table and figure hashes.
- [ ] Secret, privacy, licensing, and provider-term reviews passed.
- [ ] External publication/submission was explicitly authorized.

**Manifest reviewer:** [NAME / DATE]
**Scientific sign-off:** [NAME / DATE]
**Release authorization:** [NAME / SCOPE / DATE / TIME]
**Manifest SHA-256:** [COMPUTE AFTER COMPLETION]
