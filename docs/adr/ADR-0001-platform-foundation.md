---
adr_id: ADR-0001
title: "Platform Foundation Baseline"
version: 0.1.1
status: accepted
document_class: architecture-decision-record
project: ODay Plus
language: zh-TW
decision_date: 2026-06-26
updated_at: 2026-06-26
owner: "Architecture Owner"
approvers: "Technology Lead / Product Lead / QA Lead"
owners:
  - Architecture Owner
  - Technology Lead
content_format: markdown
source_documents:
  - ODP-00-04_DOCUMENT_VERSION_AND_ADR_GOVERNANCE.md
  - ODP-00-05_REQUIREMENTS_TRACEABILITY_MATRIX.md
  - ODP-SD-01_SYSTEM_OVERALL_ARCHITECTURE.md
  - ODP-SD-04_REPOSITORY_AND_CODE_MODULE_DESIGN.md
  - ODP-SD-12_CICD_IAC_AND_ENVIRONMENT_DESIGN.md
  - ODP-QA-01_TEST_MASTER_PLAN.md
  - ODP-OPS-01_DEVELOPMENT_WBS_AND_RELEASE_PLAN.md
related_requirements:
  - ODP-HLR-GOV-001
  - ODP-HLR-GOV-002
  - ODP-HLR-GOV-005
  - ODP-HLR-GOV-006
  - ODP-HLR-GOV-009
  - ODP-HLR-GOV-010
  - ODP-HLR-INT-001
  - ODP-HLR-INT-004
  - ODP-HLR-INT-007
review_trigger: "Review when service ownership, deployment topology, data platform, event bus, model registry, solver runtime, or production SLO assumptions materially change."
---

# ADR-0001: Platform Foundation Baseline

## Context

ODay Plus is a full decision-loop platform for expansion, operations, intervention, pricing, advertising, asset valuation, network planning, model governance, and audit. The first engineering foundation must let teams build all modules without forcing every function into a separate service or allowing model output to bypass human and policy controls.

The source documents establish these constraints:

- The final platform scope includes all modules; releases sequence delivery but do not reduce final scope.
- IoT and internal data platforms are upstream providers and must be consumed through governed integration contracts.
- Prediction, recommendation, approval or decision, execution, and outcome must be separated and auditable.
- High-risk functions require feature flags, manual approval, canary, rollback, or equivalent controls.
- Release artifacts must be traceable to requirements, tests, evidence, and commit SHA.
- `ODP-00-04` requires ADR coverage for Monorepo/Multirepo boundaries, and `ODP-SD-04` recommends Monorepo for the first phase while preserving future service or repository extraction paths.

## Decision Drivers

1. Support full module coverage while keeping R0 implementation manageable.
2. Preserve traceability across requirements, data, API, jobs, model versions, decision policies, tests, releases, and audit evidence.
3. Avoid premature microservice fragmentation before ownership, cadence, and runtime boundaries are proven.
4. Avoid premature repository fragmentation while most modules share domain, auth, audit, job, workflow, OpenAPI, and data-contract surfaces.
5. Keep GCP as the production deployment baseline while allowing OSS components where they are supportable and governed.
6. Make model, solver, and high-risk decision releases reversible.

## Considered Alternatives

| Alternative | Summary | Reason not selected as baseline |
|---|---|---|
| Microservice-first platform | Split each module into a separately deployed service from the beginning | Adds coordination, deployment, data ownership, and observability cost before module boundaries are validated |
| Monolith-only platform | Put web, API, jobs, data, model serving, and solver execution in one runtime | Makes long-running work, model release, job reliability, and scaling boundaries too rigid |
| Multirepo-first platform | Split frontend, backend, data, models, solver, and modules into separate repositories from the beginning | Adds cross-repository versioning, contract synchronization, review, CI, and release overhead before independent ownership or cadence exists |
| SaaS-heavy managed decision platform | Use managed low-code or packaged decisioning for core workflows | Risks lock-in and weak traceability for custom geospatial, causal, solver, and subsidy evidence requirements |
| Data warehouse only implementation | Build mostly in BigQuery/dbt and reports | Cannot cover transactional approvals, workflows, job control, rollback, role-based operations, and audit logs |

## Decision

Adopt a monorepo, modular-monolith-first platform foundation with explicit deployable boundaries for web/BFF, core API, asynchronous workers, scheduled jobs, data transformation, model training/serving, and solver execution.

The repository decision is Monorepo for R0 and the first implementation phase. Frontend, API, workers, modules, shared packages, data pipelines, model code, solver code, infra, tests, and governance docs should start in one repository with package and module boundaries enforced by CI. A move to Multirepo is not part of this baseline; it requires a follow-up ADR when independent team ownership, security domains, deployment cadence, external delivery, regulatory isolation, or materially different lifecycle needs make repository extraction justified.

The foundation baseline is:

1. Backend modules use Python service modules, with FastAPI for API surfaces.
2. OpsBoard uses React / Next.js.
3. Transactional records use Cloud SQL, with PostGIS where geospatial transactional querying is required.
4. Analytical and model-ready data uses BigQuery.
5. Snapshots, reports, evidence packages, model artifacts, and release artifacts use Cloud Storage.
6. Events use a Pub/Sub-style bus with versioned event contracts.
7. Workflow and long-running work use a durable job framework with status API, retry, quarantine, backfill, and audit.
8. Data transformations use dbt Core or equivalent governed transformations with tests and lineage.
9. Model lifecycle uses an MLflow-style registry pattern with model cards, dataset snapshots, aliases, shadow, canary, and rollback.
10. Solver execution uses an OR-Tools-compatible path with hard constraint tests, solver status, alternative plans, and infeasibility diagnostics.
11. CI/CD must enforce code, contract, data, model, security, E2E, ops, and audit gates appropriate to each release.

## Positive Consequences

- Teams can implement module behavior without committing to premature service splits.
- Shared contracts, generated clients, schema checks, tests, and governance artifacts can evolve atomically in one review and release flow.
- The architecture supports synchronous APIs, asynchronous jobs, events, data pipelines, model releases, and solver workflows from the start.
- High-risk decisions remain auditable and controlled by policy, feature flags, approvals, and rollback.
- RTM, release gate, and evidence artifacts can point to a stable foundation.

## Negative Consequences / Risks

- A modular monolith needs strong package and ownership discipline to avoid hidden coupling.
- A monorepo needs dependency, ownership, and CI discipline to prevent unrelated modules from coupling through shared internals.
- Large changes may require stricter review scopes, generated artifact controls, and selective CI to keep feedback fast.
- Some future module boundaries may require extraction and migration.
- Cloud SQL, BigQuery, Cloud Storage, Pub/Sub, and model registry patterns must be wired carefully to avoid duplicate truth.
- The foundation creates governance overhead that must be automated through CI where possible.

## Migration and Rollback

The R0 foundation should start with repository structure, package boundaries, document checks, and local/dev execution. Production resources should be introduced through IaC after service boundaries and data contracts are stable enough for review.

Repository migration from Monorepo to Multirepo is a future extraction path, not the default rollback path. Extraction requires a dedicated ADR, ownership assignment, compatibility window, release choreography, CI split plan, and contract publishing plan.

Rollback for foundation changes uses:

- git revert for code and document changes;
- feature flags for user-visible and high-risk surfaces;
- previous Cloud Run revisions for deployed API/web/worker artifacts;
- expand/contract database migration rules for schema changes;
- previous dbt manifest or view definition for transformation changes;
- model alias rollback for model releases.

## Security / Cost / Operations Impact

- IAM, RBAC/ABAC, audit events, export controls, and secret handling must be part of foundation work, not post-release cleanup.
- Cost monitoring is required for BigQuery, Cloud Run jobs, external data connectors, solver runs, and model training.
- Observability must include logs, metrics, traces, audit logs, job status, model release status, and release watch windows.

## Related Documents

- `docs/architecture/ODAY_PLUS_EXECUTION_BASELINE.md`
- `docs/release/RELEASE_GATE_CHECKLIST.md`
- `docs/rtm/ODAY_PLUS_EXECUTION_RTM.md`
- `ODP-00-04_DOCUMENT_VERSION_AND_ADR_GOVERNANCE.md`
- `ODP-00-05_REQUIREMENTS_TRACEABILITY_MATRIX.md`
- `ODP-SD-01_SYSTEM_OVERALL_ARCHITECTURE.md`
- `ODP-SD-04_REPOSITORY_AND_CODE_MODULE_DESIGN.md`
- `ODP-SD-12_CICD_IAC_AND_ENVIRONMENT_DESIGN.md`
- `ODP-QA-01_TEST_MASTER_PLAN.md`
- `ODP-OPS-01_DEVELOPMENT_WBS_AND_RELEASE_PLAN.md`

## Change Log

| Version | Date | Change Class | Summary | Author | Approver |
|---|---|---|---|---|---|
| 0.1.1 | 2026-06-26 | C1 | Added formal ADR metadata and explicit Monorepo/Multirepo decision required by ODP-00-04 and ODP-SD-04 | Codex2 | Claude |
| 0.1.0 | 2026-06-26 | C2 | Proposed initial platform foundation decision for R0 execution | Codex2 | Claude |
