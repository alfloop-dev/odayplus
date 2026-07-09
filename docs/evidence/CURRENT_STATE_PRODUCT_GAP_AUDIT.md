# Current-State Product Gap Audit

Task: ODP-PV-000
Generated: 2026-06-28
Owner: Codex
Reviewer: Claude

## Executive Finding

The repository contains a meaningful product-grade skeleton: FastAPI routes,
domain modules, a Next.js OpsBoard shell, module workspaces, source-data
contracts, dbt model-ready views, in-process model / solver code, Playwright
E2E specs, and release-gate documentation. It is not production-ready.

The main gap is not absence of code. The gap is that most product surfaces still
run on in-memory repositories, bundled demo data, static fixtures, and
deterministic tests. There is no durable production data path from external
sources through canonical storage into the UI/API, no live map provider, no
production model registry or evidence store, no deployed environment proof, and
no completed release metadata or UAT signoff.

## Evidence Inventory

| Area | Current evidence | Current state | Product gap |
|---|---|---|---|
| Branch truth | `docs/evidence/BRANCH_TRUTH_TABLE.md` | `task/ODP-PV-000`, `origin/main`, and `origin/dev` have identical product trees | task closeout still needs PR into `dev` because branch topology differs |
| API | `apps/api/oday_api/main.py`, `apps/api/app/routes/*.py` | FastAPI app exposes health, jobs, audit, HeatZone, listings, AVM, ForecastOps, SiteScore, AdLift, and interventions routes | default stores are in-memory; persistence, auth, multi-tenant scoping, and production service dependencies are not wired |
| Frontend | `apps/web/src/app/*/page.tsx`, `apps/web/features/*/*Workspace.tsx`, `packages/ui/src/nav/routes.ts` | OpsBoard has 14 top-level areas and rich module workspaces | most screens render from bundled `data.ts` fixtures instead of API-backed state |
| Map / geospatial | `apps/web/features/expansion/ExpansionWorkspace.tsx`, `modules/external_data/geo/pipeline.py` | HeatZone has a grid-like map preview and deterministic H3-compatible cell keys | no real map provider, tile layer, spatial database query path, or live geocoder integration is present |
| External data | `packages/schemas/source_contracts/index.json`, `modules/external_data/application/external_contracts.py` | contracts exist for POI, competitor store, and listing snapshots | external connectors are contract definitions only; acquisition, licensing, scheduling, quarantine storage, and provider credentials are not productionized |
| Data / model-ready views | `pipelines/dbt/models/model_ready/*.sql`, `docs/data/MODEL_READY_VIEWS_BASELINE.md` | dbt-facing baseline views and PIT rules are documented and tested | no demonstrated BigQuery/dbt execution against production snapshots; several broader catalog views remain follow-on |
| Domain modules | `modules/*/domain`, `modules/*/application`, `modules/*/infrastructure/repositories.py` | domain/application boundaries exist across core modules | infrastructure layer is intentionally in-memory in most modules |
| Model lifecycle | `models/shared_ml/*`, `modules/learninghub/*` | model cards, registry concepts, validation runs, aliases, and release decisions exist in code | no MLflow or artifact registry integration; no approved production model versions or rollback run evidence |
| Optimization | `solver/netplan`, `solver/pricing`, module tests | pricing and NetPlan solver paths exist | no production solver runtime, queue, SLA evidence, or persisted alternatives store |
| E2E / acceptance | `tests/e2e/*.spec.ts`, `tests/e2e/test_acceptance_coverage.py` | formal QA-03 registry maps scenarios, deterministic datasets, roles, routes, and evidence refs | registry includes manual UAT items; passing local tests do not equal production evidence package |
| Readiness package | `docs/evidence/PRODUCTION_READINESS_PACKAGE.md` | release gates and blockers are documented | release metadata fields are unassigned; dependency audit was recorded as failed with high findings; production approval is explicitly blocked |

## Product-Code Reality

### What Is Real Enough To Build On

- The monorepo has stable landing zones for API, web, worker, scheduler, CLI,
  domain modules, shared primitives, dbt, models, solvers, infrastructure, and
  tests.
- `apps/api/oday_api/main.py` composes module routers into a FastAPI app and
  stores audit/job queues on `api.state`, which gives follow-on tasks concrete
  integration points.
- `packages/ui/src/nav/routes.ts` declares the full OpsBoard information
  architecture, including role-aware visibility and the required work areas:
  home, tasks, search, expansion, operations, interventions, pricing, adlift,
  avm, netplan, learning, audit, admin, and franchisee.
- `tests/e2e/test_acceptance_coverage.py` is a useful acceptance registry. It
  is explicit about P0/P1 scenarios, datasets, role ownership, routes, and audit
  evidence identifiers.
- `docs/evidence/SUBSIDY_EVIDENCE_MATRIX.md` and the audit export code define
  the shape of subsidy evidence without claiming subsidy payment approval.

### What Must Not Be Claimed Yet

- Do not claim production readiness. `docs/evidence/PRODUCTION_READINESS_PACKAGE.md`
  still has assignment placeholders for release ID, environment, build version,
  git commit, data snapshot, owners, model versions, and feature flags.
- Do not claim live external source integration. The repo has contracts and
  deterministic validation, not provider-backed ingestion.
- Do not claim production geospatial map readiness. Current UI map evidence is
  a workspace preview; current geospatial code is deterministic and dependency
  light.
- Do not claim durable decision/audit retention. The default API composition
  uses `InMemoryAuditLog`, `InMemoryJobQueue`, and module in-memory repositories.
- Do not claim formal UAT closure. Manual UAT references remain in the
  acceptance registry and `docs/uat/*` is a plan/template surface.

## Priority Gap Backlog

| Priority | Gap | Evidence | Suggested follow-on |
|---|---|---|---|
| P0 | Durable API persistence and repository wiring | `InMemory*Repository` classes under `modules/*/infrastructure` | replace in-memory defaults with database-backed repositories behind the same interfaces |
| P0 | Web data binding | `apps/web/features/*/data.ts` | introduce API client contracts and replace bundled fixture reads screen by screen |
| P0 | Release blockers | `PRODUCTION_READINESS_PACKAGE.md` dependency audit failure and unassigned metadata | create security/dependency remediation and release metadata tasks before any product-ready claim |
| P0 | Evidence storage and audit export durability | `shared.audit.InMemoryAuditLog`; audit evidence docs | persist audit events and evidence bundles with hash, retention, privacy, and export-scope controls |
| P1 | External source ingestion | source contract registry and external data facade | implement scheduled/API/file ingestion, quarantine, licensing flags, and provider-specific freshness checks |
| P1 | Production map/geocoder path | HeatZone UI preview and `GeoPipeline` static provider protocol | wire real map rendering, PostGIS/H3 lookup, and approved geocoder provider adapters |
| P1 | Model registry integration | `models/shared_ml/registry.py`, Learning Hub repository | integrate artifact storage/MLflow-style registry and generate signed model cards |
| P1 | Deployment evidence | `infra/terraform`, `docker-compose.yml`, `infra/docker/*` | run environment provisioning and capture deployment, health, backup, and rollback evidence |

## Baseline Decision

Fleet tasks can proceed from this baseline if they treat current code as a
demo-capable modular product skeleton, not as a production-ready platform. The
next wave should prioritize durable persistence, fixture-to-API data binding,
external ingestion, audit evidence retention, and release blocker remediation.
