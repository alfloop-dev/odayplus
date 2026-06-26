---
doc_id: ODP-R0-EXECUTION-RTM
title: "ODay Plus Execution RTM"
version: 0.1.0
status: draft-for-review
document_class: requirements-traceability
project: ODay Plus
language: zh-TW
updated_at: 2026-06-26
owner: "Product Lead / QA Lead"
approvers: "Architecture Owner / Program Manager"
content_format: markdown
source_documents:
  - ODP-00-05_REQUIREMENTS_TRACEABILITY_MATRIX.md
  - ODP-QA-01_TEST_MASTER_PLAN.md
  - ODP-QA-07_SUBSIDY_AUDIT_EVIDENCE_MATRIX.md
---

# ODay Plus Execution RTM

## 1. Purpose

本文件是工程執行用 RTM 工作面，用來把 ODP high-level requirements、design artifacts、implementation tasks、tests、release gates 與 evidence 連在一起。正式需求基線仍以 `ODP-00-05_REQUIREMENTS_TRACEABILITY_MATRIX.md` 為 source of truth。

## 2. Traceability Chain

```text
Source / Commitment
→ HLR / FR / NFR / Business Rule
→ Design document / ADR
→ Implementation task / PR / commit
→ Test case / release gate
→ Evidence artifact
→ Acceptance / audit status
```

## 3. RTM Row Schema

| Field | Description |
|---|---|
| RTM ID | Stable execution trace row ID, format `ODP-RTM-<DOMAIN>-NNN` |
| Requirement IDs | HLR/FR/NFR/BR IDs from source documents |
| Requirement summary | Short requirement statement |
| Source | Source document or commitment ID |
| Design references | Architecture, SD, MOD, ADR, QA, OPS documents |
| Implementation references | Task ID, PR, commit, package/module |
| Test references | Test case, suite, report, or gate |
| Evidence references | Evidence IDs or manifest paths |
| Owner | Accountable role |
| Priority | MUST / SHOULD / CONDITIONAL |
| Status | baselined / implementing / verified / accepted / deferred / retired |
| Notes | Gaps, decisions, deviations, or follow-up IDs |

## 4. Foundation RTM Rows

| RTM ID | Requirement IDs | Requirement summary | Source | Design references | Implementation references | Test references | Evidence references | Owner | Priority | Status | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `ODP-RTM-GOV-001` | `ODP-HLR-GOV-001` | Full module scope must be preserved; phases only sequence delivery and activation. | `SRC-WORKING-DECISIONS` | `ODAY_PLUS_EXECUTION_BASELINE.md`, `ODP-00-01`, `ODP-SA-03` | `ODP-R0-002` | Scope review | Release scope note | Executive Sponsor | MUST | baselined | Initial governance artifact row |
| `ODP-RTM-GOV-002` | `ODP-HLR-GOV-002` | Prediction, recommendation, approval/decision, execution, and outcome must be stored separately. | `SRC-ODP-ARCH` | `ODAY_PLUS_EXECUTION_BASELINE.md`, `ADR-0001-platform-foundation.md`, `ODP-SD-08` | `ODP-R0-002` | Schema review, E2E gate | Audit export | Product Lead | MUST | baselined | Drives data model and workflow acceptance |
| `ODP-RTM-GOV-003` | `ODP-HLR-GOV-005` | Requirements must have stable IDs and trace to design, tests, and acceptance evidence. | `SRC-WORKING-DECISIONS` | `ODAY_PLUS_EXECUTION_RTM.md`, `ODP-00-05`, `ODP-QA-07` | `ODP-R0-002` | RTM integrity check | Evidence manifest | Product Lead / QA Lead | MUST | baselined | This document is the working execution RTM |
| `ODP-RTM-GOV-004` | `ODP-HLR-GOV-006` | Major architecture, data semantics, service boundary, and high-risk technical decisions require ADRs. | `SRC-ODP-ARCH` | `ADR-0001-platform-foundation.md`, `ODP-00-04`, `ODP-SD-01` | `ODP-R0-002` | ADR audit | ADR index / review notes | Architecture Owner | MUST | baselined | ADR-0001 starts the foundation decision log |
| `ODP-RTM-GOV-005` | `ODP-HLR-GOV-007` | Formal documents must use Markdown, front matter, stable IDs, versions, and status management. | `SRC-WORKING-DECISIONS` | `ODAY_PLUS_EXECUTION_BASELINE.md`, `ODP-00-04` | `ODP-R0-002` | Document lint | commit SHA | Program Manager | MUST | baselined | Future CI should automate document linting |
| `ODP-RTM-GOV-006` | `ODP-HLR-GOV-009` | High-risk functions require feature flags, manual approval, canary, or equivalent controls. | `SRC-ODP-ARCH` | `RELEASE_GATE_CHECKLIST.md`, `ADR-0001-platform-foundation.md`, `ODP-SD-12` | `ODP-R0-002` | Release gate, feature flag test | release evidence package | Risk / Validation Owner | MUST | baselined | Applies to PriceOps, AdLift, NetPlan, model releases, and decision policy changes |
| `ODP-RTM-GOV-007` | `ODP-HLR-GOV-010` | IoT and internal data platforms are upstream providers; ODay Plus must use integration contracts. | `SRC-WORKING-DECISIONS` | `ODAY_PLUS_EXECUTION_BASELINE.md`, `ODP-DATA-02`, `ODP-MOD-00` | `ODP-R0-002` | Boundary review, contract test | data contract evidence | Architecture Owner | MUST | baselined | Prevents direct coupling to upstream internals |
| `ODP-RTM-INT-001` | `ODP-HLR-INT-001` | Integration Layer must support batch, CDC, API, file, and event source ingestion modes. | `SRC-ODP-ARCH` | `ADR-0001-platform-foundation.md`, `ODP-DATA-02`, `ODP-DATA-03`, `ODP-MOD-00` | `ODP-R0-002` | Connector integration tests | source ingestion evidence | Data Platform Owner | MUST | baselined | Foundation architecture reserves all ingestion modes |
| `ODP-RTM-INT-002` | `ODP-HLR-INT-004` | Source data must support replay, backfill, quarantine, retry, and reproducible snapshots. | `SRC-ODP-ARCH` | `ODAY_PLUS_EXECUTION_BASELINE.md`, `RELEASE_GATE_CHECKLIST.md`, `ODP-SD-10`, `ODP-OPS-02` | `ODP-R0-002` | Recovery test, data gate | recovery report | Data Platform Owner | MUST | baselined | Required for data and model reproducibility |
| `ODP-RTM-INT-003` | `ODP-HLR-INT-007` | Models must use canonical layers and model-ready views, not uncontrolled upstream raw tables. | `SRC-ODP-ARCH` | `ODAY_PLUS_EXECUTION_BASELINE.md`, `ODP-DATA-04`, `ODP-DATA-06`, `ODP-SD-03` | `ODP-R0-002` | Access policy, architecture test | lineage report | Architecture Owner | MUST | baselined | Required before model work starts |

## 5. Evidence Mapping Seeds

| Evidence ID | RTM IDs | Evidence type | Expected artifact |
|---|---|---|---|
| `AUD-CLOUD-001` | `ODP-RTM-GOV-006` | `DEPLOYMENT_RECORD` | GCP deployment record and architecture evidence |
| `AUD-CLOUD-004` | `ODP-RTM-GOV-006` | `TEST_REPORT` | OpenAPI/API test report |
| `AUD-OPSBD-002` | `ODP-RTM-GOV-002`, `ODP-RTM-GOV-006` | `AUDIT_EXPORT` | Decision log export |
| `AUD-ML-004` | `ODP-RTM-GOV-006` | `MODEL_REPORT` | Model card |
| `AUD-ML-006` | `ODP-RTM-GOV-006` | `DR_REPORT` | Model release rollback report |
| `AUD-ML-007` | `ODP-RTM-INT-003` | `DATA_REPORT` | Point-in-time correctness report |
| `AUD-MOD-006` | `ODP-RTM-GOV-007`, `ODP-RTM-INT-001`, `ODP-RTM-INT-002` | `DATA_REPORT` | Integration and data lineage report |

## 6. Maintenance Rules

1. Every implementation task that changes behavior must update existing RTM rows or add new rows.
2. A row may remain `baselined` during design-only work, but it cannot become `verified` without test or evidence references.
3. A row cannot become `accepted` without acceptance owner sign-off and evidence references.
4. Any approved deviation must be linked in the Notes field with owner and due date.
5. Retired requirements keep their IDs and must point to replacement rows when applicable.

## Change Log

| Version | Date | Change Class | Summary | Author | Approver |
|---|---|---|---|---|---|
| 0.1.0 | 2026-06-26 | C1 | Initial execution RTM working artifact with foundation governance rows | Codex2 | Pending |
