# ODP-DEV-MIGRATION-LIVE-PREFLIGHT-001

On 2026-09-27, interactive Codex used the reauthenticated `deborah.lu@dev.cctech-support.com` session to inspect the actual dev API database target. The readback used read-only PostgreSQL transactions and queried only identity, version and schema metadata. No live data or schema was changed.

## Confirmed diagnosis

Secret `oday-plus-dev-api-database-url-pg16`, version 1, selects Cloud SQL instance `odayplus-runtime-20260825:asia-east1:oday-dev-sql`, database `oday_plus`. The secret was read only in memory. The host's Cloud SQL Auth Proxy v2.25.4 listened only on loopback and used the logged-in GCP user. The diagnosis script validates the original socket target before translating transport to that proxy.

`public.alembic_version` contains `29b539ebc72a`, matching Dagster's official migration tree. Dagster `runs` and `event_logs` tables are present. Only the public application-visible schema exists; `core.tenants` and `public.oday_plus_alembic_version` do not exist. PostGIS 3.6.0 is installed, and the configured DB user has database CREATE and public-schema CREATE privileges. The connection reported `transaction_read_only=on`.

These live observations confirm the version-table collision diagnosed in PR #1372 and satisfy the fresh-app-schema condition of its isolation fix. They do not prove that every subsequent application migration or deployment will pass. No reset, stamp, foreign-history deletion, secret replacement or ad-hoc live migration is necessary to address the observed revision lookup error.

## Candidate and outstanding admission

- Migration fix: PR #1372, merged as candidate `355a94b52b14badc236be4b3e52eb936a7075549`.
- Successful candidate build: Runtime Release run `36313147910`; manifest `sha256:a1e3fcf6b765861dc269e3fd395b7bec6ac4b2b6f3b3207a3ffb623c6ca6dfaa`.
- Build/gate reconciliation: merged PR #1373; dev gates 0/1 passed, gate 4 blocked and release NO-GO.
- Fresh complete dependency audit on this exact candidate: 1 high (`js-yaml`) and 2 moderate (`vitest`, `@vitest/mocker`), 0 critical. `npm audit --json --package-lock-only` exited 1 because findings remain. This is not recorded as a pass.
- `package-lock.json`, root `package.json` and `apps/web/package.json` are unchanged since prior risk-acceptance baseline `dc0eb370b29e50f2fc916e008bdab3d08e0a3ddc`. Findings match the previously named advisories, but the prior decision expressly excludes other baselines; no new approval is inferred.
- The successful build's production-only audit has zero findings. Four LGPL cases retain their existing conditional operator decision; external authoritative receipt H01 is still outstanding.

Before deployment, a candidate-applicable Human/Ops decision must resolve the dev admission blockers, and the current candidate must receive a fresh signed supervisor release lease through the existing Runtime Release flow. Existing consumed/expired leases cannot be reused. Stage/prod and unrelated legal authority blockers remain unchanged.

Evidence in this directory is a real local live readback plus local dependency audit, prepared by interactive Codex. The assigned owner must independently inspect/adopt it and retain the provenance. GCP authentication recovery, a code merge, or a successful build alone is not evidence that dev deployment completed.

## Full migration replay on an isolated database

The exact candidate's `product_ops.deployment.cloud_run_job_entrypoint.run_migration()` completed twice on a disposable local PostgreSQL 16/PostGIS 3.5 database seeded with Dagster revision `29b539ebc72a` and a synthetic `public.runs` sentinel. Both runs exited 0 and reported `runtime_schema_verified=true`, including assisted-intake migrations and schema validation. App history reached `0020`; the Dagster revision and sentinel survived unchanged. The container was stopped and removed after the run.

`full-migration-replay.json` and the two command logs are local replay evidence, not Cloud Run execution receipts: their environment label comes from the candidate entrypoint configured with `ODP_DEPLOY_ENV=dev`. Live Cloud SQL uses PostGIS 3.6.0, whereas this local replay uses 3.5. No image/runtime equivalence or live rollout completion is claimed.
