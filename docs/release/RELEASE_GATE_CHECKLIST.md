---
doc_id: ODP-R0-RELEASE-GATE-CHECKLIST
title: "ODay Plus Release Gate Checklist"
version: 0.1.0
status: approved
document_class: release-governance
project: ODay Plus
language: zh-TW
updated_at: 2026-06-26
owner: "QA Lead / Release Owner"
approvers: "Product Lead / Architecture Owner / SRE Owner / Security Owner"
content_format: markdown
source_documents:
  - ODP-QA-01_TEST_MASTER_PLAN.md
  - ODP-QA-06_UAT_AND_FORMAL_ACCEPTANCE_CHECKLIST.md
  - ODP-QA-07_SUBSIDY_AUDIT_EVIDENCE_MATRIX.md
  - ODP-SD-12_CICD_IAC_AND_ENVIRONMENT_DESIGN.md
  - ODP-OPS-01_DEVELOPMENT_WBS_AND_RELEASE_PLAN.md
---

# ODay Plus Release Gate Checklist

## 1. Purpose

本文件提供每次 ODay Plus release candidate 的工作檢核表。正式測試策略、UAT、查核證據與 release planning 仍以 ODP QA/OPS 文件為 source of truth；本檢核表作為工程任務與 release review 的統一入口。

## 2. Release Metadata

| Field | Value |
|---|---|
| Release ID | `[ASSIGNMENT_REQUIRED]` |
| Release type | Foundation / Domain / Integration / Governance / UAT / Production / Hotfix / Model / Data Contract |
| Environment | local / dev / integration / staging / production / sandbox |
| Build version | `[ASSIGNMENT_REQUIRED]` |
| Git commit | `[ASSIGNMENT_REQUIRED]` |
| Data snapshot | `[ASSIGNMENT_REQUIRED]` |
| Model versions | `[ASSIGNMENT_REQUIRED_OR_NA]` |
| Feature flags | `[ASSIGNMENT_REQUIRED_OR_NA]` |
| Release owner | `[ASSIGNMENT_REQUIRED]` |
| Approval owner | `[ASSIGNMENT_REQUIRED]` |
| RTM rows | `[ASSIGNMENT_REQUIRED]` |

## 3. Gate Summary

| Gate | Required for | Status | Evidence |
|---|---|---|---|
| Code Gate | All code releases | not-started | lint, type/static checks, unit/component report |
| Contract Gate | API, event, data, model interface changes | not-started | OpenAPI/AsyncAPI/schema/model IO compatibility report |
| Data Gate | Data ingestion, canonical, dbt, feature/view changes | not-started | dbt/quality/freshness/lineage/PIT/backfill report |
| Model Gate | Model or decision model promotion | not-started | dataset snapshot, backtest, calibration, segment metrics, model card |
| Solver Gate | Optimization release | not-started | hard constraints, feasibility, infeasibility diagnostics, runtime report |
| Security Gate | All production or sensitive releases | not-started | secret/dependency/SAST/RBAC/ABAC/privacy/export report |
| Performance Gate | User-facing API, batch, map, solver, report generation | not-started | API P95, batch window, render/runtime report |
| E2E Gate | Domain and integration releases | not-started | business flow evidence |
| UAT Gate | UAT or production candidate | not-started | role-based sign-off |
| Ops Gate | Production release | not-started | runbook, monitoring, rollback, on-call, release note |
| Audit Gate | High-risk decisions and subsidy commitments | not-started | decision log export, evidence manifest, audit package |

Allowed statuses: `not-started`, `passed`, `passed-with-deviation`, `failed`, `not-applicable`.

## 4. Code Gate

| Check | Required evidence | Status |
|---|---|---|
| Formatting and lint checks completed | CI job link or local command output | not-started |
| Unit tests completed for changed backend/domain logic | test report | not-started |
| Component tests completed for changed frontend surfaces | test report | not-started |
| Static/type checks completed where configured | CI job link or local command output | not-started |
| Build artifacts are immutable and traceable to commit SHA | image/artifact metadata | not-started |

## 5. Contract Gate

| Check | Required evidence | Status |
|---|---|---|
| OpenAPI diff reviewed for breaking changes | OpenAPI diff | not-started |
| Event schema compatibility checked | AsyncAPI/schema report | not-started |
| Data contract compatibility checked | schema/dbt contract report | not-started |
| Model input/output compatibility checked | model contract report | not-started |
| Breaking changes have migration, compatibility window, and rollback plan | approved change record | not-started |

## 6. Data Gate

| Check | Required evidence | Status |
|---|---|---|
| Source contract and sample data reviewed | source contract report | not-started |
| Canonical mapping tests passed | mapping test report | not-started |
| Data quality tests passed | dbt/Great Expectations or equivalent report | not-started |
| Freshness and lineage evidence captured | lineage report | not-started |
| Point-in-time correctness checked for model-ready data | PIT report | not-started |
| Backfill, retry, and quarantine path tested where applicable | recovery test report | not-started |

## 7. Model and Decision Gate

| Check | Required evidence | Status |
|---|---|---|
| Dataset snapshot is reproducible | snapshot ID and hash | not-started |
| Baseline comparison completed | validation report | not-started |
| Segment metrics reviewed | segment report | not-started |
| Calibration or coverage checked where applicable | calibration report | not-started |
| Model card or decision card completed | card path | not-started |
| Human approval and rollback path recorded | approval record | not-started |
| Feature flag, shadow, canary, or equivalent control configured for high-risk release | flag/canary evidence | not-started |

## 8. Security and Privacy Gate

| Check | Required evidence | Status |
|---|---|---|
| Secrets scan passed | CI report | not-started |
| Dependency and SAST scan passed with no unresolved critical/high findings | security report | not-started |
| RBAC/ABAC tests passed for affected roles | permission test report | not-started |
| Sensitive export and audit controls checked | export/audit test report | not-started |
| IAM and infrastructure changes reviewed | security review record | not-started |

## 9. UAT and Formal Acceptance Gate

| Check | Required evidence | Status |
|---|---|---|
| UAT scope maps to affected roles | UAT plan | not-started |
| UAT data and accounts are ready | UAT readiness note | not-started |
| P0/P1 UAT defects are zero | defect summary | not-started |
| High-risk workflows have sign-off | sign-off record | not-started |
| Conditions for accepted-with-actions are documented with owner and due date | action list | not-started |

## 10. Operations and Rollback Gate

| Check | Required evidence | Status |
|---|---|---|
| Release note completed | release note path | not-started |
| Deployment plan completed | deployment record | not-started |
| Rollback plan completed and reviewed | rollback plan | not-started |
| Monitoring watch window assigned | watch plan | not-started |
| Runbook updated for changed operational behavior | runbook diff | not-started |
| Backup/DR impact reviewed where applicable | DR review | not-started |

## 11. Audit and Subsidy Evidence Gate

| Check | Required evidence | Status |
|---|---|---|
| Evidence IDs mapped to RTM rows | RTM links | not-started |
| Evidence manifest generated | manifest path and hash | not-started |
| Screenshots/videos/API responses/job logs show environment and build version | evidence package | not-started |
| Decision logs include actor, role, policy version, reason, override, and timestamp | audit export | not-started |
| Evidence package can be reproduced from manifest | reproduction note | not-started |

## 12. Release Decision

| Decision field | Value |
|---|---|
| Release decision | pending / approved / approved-with-actions / rejected |
| Decision owner | `[ASSIGNMENT_REQUIRED]` |
| Decision date | `[ASSIGNMENT_REQUIRED]` |
| Conditions | `[ASSIGNMENT_REQUIRED_OR_NA]` |
| Follow-up task IDs | `[ASSIGNMENT_REQUIRED_OR_NA]` |

## Change Log

| Version | Date | Change Class | Summary | Author | Approver |
|---|---|---|---|---|---|
| 0.1.0 | 2026-06-26 | C1 | Initial release gate checklist derived from ODP QA, SD, and OPS documents | Codex2 | Claude |
