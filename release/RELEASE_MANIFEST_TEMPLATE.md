# Release manifest template

This file describes the evidence package without containing credentials or granting permission to publish it. Copy it to a versioned release record only after exactly one report path is locked and that path's data are locked.

## 1. Release identity

| Field | Value |
| --- | --- |
| Release version | [REQUIRED] |
| Created at / timezone | [REQUIRED] |
| Created by | [REQUIRED] |
| Exact paper title | [REQUIRED] |
| Locked report path | [CONFIRMATION / EXPLORATORY PILOT-ONLY; ONE REQUIRED] |
| Release purpose | [analysis review / sprint submission / public reproducibility] |
| Authorization record | [REQUIRED FOR EXTERNAL RELEASE] |
| Source repository | [REQUIRED] |
| Full Git commit | [REQUIRED] |
| Git tree clean | [true required] |
| Release tag | [OPTIONAL / REQUIRED IF PUBLIC] |

## 2. Scientific lineage

| Artifact | Version / namespace | Path or immutable locator | SHA-256 | Reviewer |
| --- | --- | --- | --- | --- |
| Scientific protocol | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Protocol revisions | [NONE / LIST] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Historical Stage 2 provenance / consumed-release receipt | `calibration/v1` historical only | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Selected-version Gate 1 controller/config/proposal | [`calibration/v1.1` OR `fallback-two-round/v1.2/calibration`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Calibration request plan | [`calibration/v1.1` OR `fallback-two-round/v1.2/calibration`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 1 authorization / consumption receipt / decision | [REQUIRED; UNIQUE RELEASE] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 2 smoke controller/config/proposal | [`pilot/v1.1/block-0` OR `fallback-two-round/v1.2/pilot/block-0`] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Gate 2 authorization / consumption receipt / smoke-integrity decision | [REQUIRED; SEPARATE UNIQUE RELEASE] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
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

### Pilot run IDs

- [REQUIRED]

### Confirmation run IDs

- [REQUIRED FOR CONFIRMATION / NOT RUN FOR PILOT-ONLY]

### Excluded or invalidated runs

| Run ID | Split | Reason | Decision artifact | Retained for audit? |
| --- | --- | --- | --- | --- |
| [REQUIRED OR NONE] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [YES REQUIRED] |

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

## 4. Analysis and paper artifacts

| Artifact | Source inputs | Path | SHA-256 | Reproduction command | Verified by |
| --- | --- | --- | --- | --- | --- |
| Empirical flow table | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Primary contrasts | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Secondary contrasts | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Missingness bounds | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Pilot joint-violation feasibility report (gate/exploratory only) | [PILOT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Confirmation joint-violation-marker prevalence report (required for C02/C10) | [CONFIRM IDS / NOT RUN FOR PILOT-ONLY] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] | [CONDITIONAL] |
| Aggregate/immediate/early/late B−A analysis | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Countermeasure FAR/CPA/balanced-accuracy analysis | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
| Six-cell peer diagnostic and three frozen contrasts | [LOCKED REPORT-SPLIT IDS] | [REQUIRED] | [REQUIRED] | [REQUIRED] | [REQUIRED] |
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
- [ ] The release implements and identifies the single frozen protocol version (v1.1 or, if the sole fallback was activated, v1.2); no historical v1.0 or superseded v1.1 readiness artifact is presented as current authorization or current protocol evidence.
- [ ] All scheduled blocks and outcomes in the locked report split are accounted for; on the confirmation path this includes every scheduled confirmation block.
- [ ] Challenge-pair balance, six peer-policy cells, timing windows, and—on the confirmation path—the confirmation-size decision reproduce from the frozen selected-version artifacts.
- [ ] The compliant/approval-only message hashes match exactly, approval-only/reciprocal messages pass the common-prefix audit, and no superseded peer-prompt hash is presented as belonging to the selected protocol.
- [ ] Claim intervals, all-scheduled gates, and configuration-level C10 thresholds reproduce exactly; C10 is not described as same-block or individual-agent co-occurrence.
- [ ] Every headline claim is `VERIFIED` in the claim-evidence ledger.
- [ ] The release contains no unresolved `RESULT REQUIRED`, `VERIFY`, `DECISION REQUIRED`, `TODO`, or `[REQUIRED]` marker in user-facing artifacts.
- [ ] Offline reproduction was tested from a clean checkout.
- [ ] Analysis reproduction matched the listed table and figure hashes.
- [ ] Secret, privacy, licensing, and provider-term reviews passed.
- [ ] External publication/submission was explicitly authorized.

**Manifest reviewer:** [NAME / DATE]
**Scientific sign-off:** [NAME / DATE]
**Release authorization:** [NAME / SCOPE / DATE / TIME]
**Manifest SHA-256:** [COMPUTE AFTER COMPLETION]
