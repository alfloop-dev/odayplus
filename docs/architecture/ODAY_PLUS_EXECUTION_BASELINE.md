---
doc_id: ODP-R0-EXECUTION-BASELINE
title: "ODay Plus Execution Baseline"
version: 0.1.0
status: approved
document_class: execution-governance
project: ODay Plus
language: zh-TW
updated_at: 2026-06-26
owner: "Architecture Owner / Program Manager"
approvers: "Technology Lead / Product Lead / QA Lead"
content_format: markdown
source_documents:
  - ODP-00-04_DOCUMENT_VERSION_AND_ADR_GOVERNANCE.md
  - ODP-00-05_REQUIREMENTS_TRACEABILITY_MATRIX.md
  - ODP-SD-01_SYSTEM_OVERALL_ARCHITECTURE.md
  - ODP-SD-12_CICD_IAC_AND_ENVIRONMENT_DESIGN.md
  - ODP-OPS-01_DEVELOPMENT_WBS_AND_RELEASE_PLAN.md
---

# ODay Plus Execution Baseline

## 1. Purpose

本文件把 ODay Plus 正式交付文件轉成工程執行基線。正式產品、資料、測試與維運內容仍以 72 份 ODP 文件為 source of truth；本文件只定義後續 implementation tasks 必須遵守的可執行治理邊界。

## 2. Source Authority

| Layer | Source of truth | Execution use |
|---|---|---|
| Scope and document governance | `ODP-00-01` to `ODP-00-05` | Scope, document status, ADR, RTM, ownership, change class |
| Business and requirements | `ODP-SA-01` to `ODP-SA-10` | Use cases, FR/NFR, business rules, integration requirements |
| Data and integration | `ODP-DATA-01` to `ODP-DATA-07` | Source contracts, canonical model, model-ready views, data quality |
| Platform design | `ODP-SD-01` to `ODP-SD-12` | Architecture, service boundaries, API, event, workflow, security, CI/CD |
| Module design | `ODP-MOD-00` to `ODP-MOD-11` | Module behavior, data inputs/outputs, workflows, acceptance |
| AI, causal, optimization | `ODP-ML-01` to `ODP-ML-05`, `ODP-OR-01` | Model lifecycle, validation, registry, causal and solver constraints |
| UX | `ODP-UX-01` to `ODP-UX-05` | IA, design system, screens, maps, frontend implementation |
| QA and acceptance | `ODP-QA-01` to `ODP-QA-07` | Test gates, UAT, evidence matrix, formal acceptance |
| Operations | `ODP-OPS-01` to `ODP-OPS-08` | WBS, release plan, deployment, runbook, DR, admin/user manuals |

Conflict handling follows `ODP-00-04`: contractual and approved governance sources override draft implementation notes; runtime deviations must be recorded as defects or deviation records, not silently accepted as new truth.

## 3. Platform Execution Principles

1. ODay Plus targets the full module set: Integration Layer, External Data Platform, HeatZone Radar, Listing Pipeline, SiteScore, ForecastOps, InterventionOps, PriceOps, AdLift, DealRoomAVM, NetPlan, Learning Hub, OpsBoard, Governance, and Audit.
2. Stage, phase, and release planning control dependency and activation order only; they must not shrink the final approved scope.
3. IoT and internal data platforms are upstream data providers. ODay Plus consumes them through Integration Layer contracts and must not depend on their internal implementation.
4. Prediction, recommendation, approval or decision, execution, and outcome must be stored as separate records with versions, actor, time, policy, and source metadata.
5. First implementation favors a modular monolith plus a small set of deployable workers and services. Service extraction requires an ADR when it changes ownership, release cadence, runtime isolation, data contracts, or reliability boundaries.
6. Batch, synchronous API, asynchronous event, scheduled job, and long-running worker paths all exist in the baseline. Long geospatial, training, scoring, reporting, and solver work must be asynchronous.
7. GCP is the formal cloud baseline and OSS components are allowed when they are versioned, observable, supportable, and covered by release gates.

## 4. Foundation Architecture Baseline

| Area | Baseline decision | Primary source |
|---|---|---|
| Backend | Python service modules exposed through FastAPI where API surface is needed | `ODP-SD-01`, `ODP-SD-04`, `ODP-SD-06` |
| Frontend | React / Next.js OpsBoard shell with module workspaces | `ODP-SD-01`, `ODP-UX-05` |
| Relational storage | Cloud SQL for transactional domain records; PostGIS where geospatial transactional queries are required | `ODP-SD-01`, `ODP-SD-05` |
| Analytical storage | BigQuery for raw, canonical, mart, and model-ready analytical datasets | `ODP-SD-01`, `ODP-DATA-04`, `ODP-DATA-06` |
| Object storage | Cloud Storage for snapshots, model artifacts, generated reports, evidence packages, and release artifacts | `ODP-SD-01`, `ODP-QA-07` |
| Eventing | Pub/Sub-style asynchronous event bus with versioned event contracts | `ODP-SD-01`, `ODP-SD-07` |
| Workflow and jobs | Durable job table, scheduler, worker execution, status API, retry, quarantine, and backfill support | `ODP-SD-08`, `ODP-OPS-02` |
| Data transformation | dbt Core or equivalent governed transformation pipeline with tests and lineage | `ODP-SD-12`, `ODP-DATA-07` |
| Model lifecycle | MLflow-style registry with dataset snapshots, model cards, aliases, shadow, canary, and rollback | `ODP-ML-01`, `ODP-ML-04`, `ODP-SD-12` |
| Optimization | OR-Tools-compatible solver path with hard constraint tests and infeasibility diagnostics | `ODP-OR-01`, `ODP-MOD-09` |
| Observability | OpenTelemetry-style logs, metrics, traces, audit trails, and release watch windows | `ODP-SD-11`, `ODP-SD-12` |

## 5. Mandatory Engineering Gates

Every implementation task that changes production behavior must identify the affected requirement IDs, downstream design documents, tests, and evidence rows in the RTM.

| Gate | Required evidence |
|---|---|
| Code gate | lint, format, type or static checks where applicable, unit/component tests |
| Contract gate | OpenAPI, AsyncAPI, data contract, event schema, or model input/output compatibility checks where applicable |
| Data gate | schema, freshness, lineage, point-in-time correctness, backfill, and quarantine checks where applicable |
| Model gate | dataset snapshot, baseline comparison, segment metrics, calibration, drift review, model card, approval, rollback path |
| Security gate | secret scan, dependency/SAST scan, RBAC/ABAC and export/privacy tests for affected surfaces |
| E2E gate | business-critical flow evidence for affected module chains |
| UAT gate | role-based sign-off for user-visible release candidates |
| Ops gate | runbook, monitoring, rollback, on-call, and release note for production release |
| Audit gate | decision log and evidence export for high-risk decisions and subsidy commitments |

## 6. Change Control

| Change class | Execution rule |
|---|---|
| C0 Editorial | May be handled by document owner with narrow review |
| C1 Clarification | Requires owner and reviewer; update RTM only if trace semantics change |
| C2 Non-breaking design | Requires Product, Architecture, and QA review; update tests and RTM |
| C3 Breaking | Requires business owner, architecture owner, and data or service owner; requires migration and rollback plan |
| C4 High-risk decision | Requires decision owner, risk or validation owner, and business owner; requires feature flag or equivalent control |
| C5 Contractual | Requires executive, program, and legal path; cannot be implemented as a normal engineering-only change |

## 7. Task Definition of Done

An implementation task is not done until:

1. Changed files are committed through the task branch workflow.
2. The task identifies affected RTM rows or adds new rows.
3. Focused verification has been run and recorded, or the reason for not running it is explicit.
4. Release gate impact is recorded in `docs/release/RELEASE_GATE_CHECKLIST.md` or a task-specific evidence packet.
5. ADR impact is checked. A new ADR is required for material architecture, data semantics, service boundary, deployment, model release, solver, or high-risk governance decisions.
6. Reviewer approval is recorded before owner finalization.

## 8. Initial Foundation Scope

R0 Foundation covers governance artifacts, ADR baseline, release gate checklist, RTM working structure, repository/package skeleton, CI/document checks, platform service skeletons, and evidence conventions. R0 does not implement full business modules, production cloud resources, production model releases, or formal UAT sign-off.

## Change Log

| Version | Date | Change Class | Summary | Author | Approver |
|---|---|---|---|---|---|
| 0.1.0 | 2026-06-26 | C1 | Initial engineering execution baseline derived from ODP governance, architecture, QA, and operations documents | Codex2 | Claude |
