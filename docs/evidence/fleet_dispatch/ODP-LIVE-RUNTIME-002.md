---
doc_id: ODP-LIVE-RUNTIME-002
title: ODay Plus Real Runtime and Model Fleet Execution Ledger
status: in-progress
owner: Product Platform Engineering
updated_at: 2026-07-26
---

# ODay Plus Real Runtime and Model Fleet Execution Ledger

## Objective

Replace fixture- or mock-only product paths with governed production runtime
bindings, real source data, versioned OSS model execution, durable model release,
and live end-to-end evidence before publishing the system entry URL.

No lane may claim completion from unit tests or generated evidence alone. A lane
is complete only after independent review, exact-head CI, deployed runtime
verification, and the applicable live-data assertions pass.

## Active Execution Lanes

| Task | Fleet owner | Branch / PR | Scope | Current state |
|---|---|---|---|---|
| `ODP-LIVE-AUTH-001` | Main integration | `task/ODP-LIVE-RUNTIME-002` / PR `#384` | Verified web OIDC token forwarding, authoritative principal mapping, short-lived deploy smoke identity | Implementation committed; exact-head CI green |
| `ODP-FORECAST-PROD-001` | Forecast fleet + independent reviewer | shared integration worktree / PR `#384` | Horizon-correct OSS forecasts, point-in-time labels, tenant-scoped durable jobs, one MLflow production runtime for API and worker | Prior review returned `CHANGES_REQUESTED`; correction fleet is active |
| `ODP-LEARNINGHUB-RELEASE-001` | LearningHub fleet | shared integration worktree / PR `#384` | Durable release saga, MLflow aliases/tags, approval separation, idempotent replay, rollback and compensation | Second review returned `CHANGES_REQUESTED`: complete metadata compensation, saga lease/fencing, principal-bound second actor, formal validation/model-card tags, HTTP compatibility, and full PostgreSQL/MLflow recovery test |
| `ODP-GEO-MODEL-001` | Geo model fleet | `task/ODP-MODEL-READY-GEO-001` / PR `#381` | Point-in-time SiteScore and HeatZone labels, zero-label coverage, output contracts, immutable lineage, atomic view installation | P0 review corrections in progress |
| `ODP-OFFICIAL-OUTCOMES-001` | Official outcome fleet | `task/ODP-AVM-OFFICIAL-OUTCOMES` / PR `#383` | MOI/NTPC official transaction ingestion, stable source identities, tenant-safe persistence, migration and release gates | P1 review corrections in progress |
| `ODP-GCP-RUNTIME-001` | Main integration | GCP project `alfaloop-data-project` | GitHub WIF, Cloud Run/SQL/GCS/MLflow/provider bindings, secrets, real backfill and governed aliases | Waiting for interactive Google OAuth verification code |
| `ODP-LIVE-E2E-001` | Main integration | exact deployed release SHA | Authenticated operator flow, real provider/database assertions, model inference, audit receipt, worker/job completion | Pending integration and deployment |

## Binding Acceptance Evidence

### Forecast

- Training features and labels are point-in-time safe for `4/8/12/24` horizons.
- API and worker resolve the same tenant-bound MLflow production model.
- Forecast jobs, acknowledgements, retries, and handoffs survive process restart.
- StatsForecast or another approved OSS engine is locally validated and governed
  as a challenger; no unreviewed fallback is presented as production.

### LearningHub

- Release intent is durable before any remote MLflow mutation.
- Lost responses replay by idempotency key without creating a second release.
- Promotion, rollback, compensation, and reconciliation are serialized and
  preserve immutable audit evidence.
- Production aliases carry the approved model card, validation, actor, and
  approval tags.

### SiteScore and HeatZone

- Label maturity, trainer inputs, serialized artifact schema, and runtime output
  schema agree.
- SiteScore and HeatZone units match their public application contracts.
- Complete authoritative store/order partitions include eligible zero outcomes.
- H3 and all other features are computed point-in-time.
- View installation and lineage publication are atomic and replayable.

### Official Real-Estate Outcomes

- MOI and NTPC rows have stable natural identities, including rows without a
  transfer number.
- Snapshot progression is monotonic and concurrent ingestion is idempotent.
- Tenant-qualified foreign keys, advisory locks, and Alembic ordering are
  enforced in PostgreSQL.
- A dataset without a compatible production inference runtime cannot receive a
  production alias.

### Deployment and Live E2E

- GitHub deploy authentication uses WIF rather than a long-lived service-account
  key.
- Cloud Run health reports PostgreSQL, GCS, providers, jobs, and required MLflow
  aliases healthy.
- Live E2E rejects fixtures/mocks and verifies source row counts, model/version
  lineage, durable decisions, and background job completion.
- The entry URL is published only after the deployed release SHA and live E2E
  evidence agree.

## Merge Order

1. Finish and independently review each active model/data lane.
2. Rebase model/data branches onto the current `dev` baseline and resolve shared
   model-contract files as one reviewed integration.
3. Run focused PostgreSQL, model-contract, lifecycle, reliability, and product
   E2E suites.
4. Push exact integration heads and require all CI checks to pass.
5. Configure GCP and GitHub deployment identities and live runtime bindings.
6. Backfill eligible real data, train/release governed models, deploy, and run
   `ODP-LIVE-E2E-001`.
