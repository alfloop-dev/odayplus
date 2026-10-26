# ODP-MIGRATION-VERSION-TABLE-ISOLATION-001

The dev migration execution in [Runtime Release run 36278150009](https://github.com/alfloop-dev/odayplus/actions/runs/36278150009) failed while resolving Alembic revision `29b539ebc72a`. This change isolates new ODay app migration history in `public.oday_plus_alembic_version` and preserves recognized legacy app history.

## Verified origin and remaining live diagnosis

`29b539ebc72a` belongs to [Dagster's official migration tree](https://github.com/dagster-io/dagster/blob/a148082de80eba8dfa343020284316e8dd2327eb/python_modules/dagster/dagster/_core/storage/alembic/versions/29b539ebc72a_use_longtext_on_bulk_actions_body_in_.py). Its parent is `f495c27d5019`; its PostgreSQL upgrade does no DDL. The official file was retrieved through the GitHub contents API; SHA-256 is `0e735ea3043fa50f7641af7d46d12d6e638a17d41bbfc6c931f668fa6b669f78`.

At preparation time, `oday-data-platform` dev manifests configure Dagster run/event/schedule storage through `ODAY_POSTGRES_DSN`, and the webserver points at database `oday_plus`. ODay's original `infra/db/migrations/env.py` uses the default `alembic_version` table. Together these support a shared version-table collision, but do not prove which live database the API secret currently selects.

GCP user credentials require reauthentication. The existing runtime service account was denied target-project SQL/secret inspection. No target secret payload, database rows, schema changes or live migration were obtained or performed for this evidence. The older diagnosis task was archived as superseded without database readback; that disposition is not evidence of a completed diagnosis.

## Behavior and compatibility

- Fresh databases, or databases containing only foreign legacy history, use `public.oday_plus_alembic_version`. Foreign `public.alembic_version` and application data remain untouched by version bookkeeping.
- Recognized legacy ODay history continues in `public.alembic_version`, so existing deployments do not replay baseline migrations.
- Mixed app/foreign legacy revisions, two app history authorities, or existing `core.tenants` with no app history table fail before upgrade. Unknown revisions in the dedicated table still fail through Alembic.
- PostgreSQL version tables are explicitly in `public`, independent of `search_path`. Other dialects retain their default schema.
- The online connection owns its inspection transaction so migrations do not silently roll back when SQLAlchemy 2 inspection starts a transaction. Tests prove rollback for the synthetic failing chain; they do not claim every product SQL file is transaction-free.
- Offline SQL generation uses the dedicated table. Existing legacy installations must use online migration selection, because offline generation cannot inspect their history. Do not apply fresh-install offline output to a legacy installation.

No revision is stamped, reset, deleted or relabeled as an app revision. No workflow, secret, IAM policy or release lease is modified by the code patch.

## Verification

The local reproduction with unmodified `env.py` failed with the same missing Dagster revision. With this patch, 9 isolated PostgreSQL tests and 41 existing migration/job-entrypoint tests passed; Ruff passed. Exact commands, output identifiers and tested file hashes are in `local-verification.json`.

The new tests use the production Alembic environment and a tiny synthetic revision chain to isolate history selection, compatibility, rollback and preservation. They do not exercise the entire PostGIS schema or a live service. The existing CI PostgreSQL job selects `requires_live_env and not requires_postgis` across `tests/ops`, so these tests are included there.

Original code preparation: interactive Codex. Assigned owner must independently inspect/adopt the patch and retain this provenance before normal reviewer handoff.

## Deployment continuation

1. Reauthenticate an authorized GCP user for project `odayplus-runtime-20260825`. Read the API database secret in memory only; never log its DSN/password or write it into receipts.
2. Connect with read-only transactions and bounded timeouts. Record only connection target identifiers, schemas, version-table locations/revisions and whether app schema exists. Confirm the actual target matches the intended dev database. Preserve Dagster's version table.
3. If the app schema exists without trustworthy app migration history, stop and compare schema/provenance before choosing any repair. This patch deliberately does not authorize stamping/resetting that state. If the secret targets the wrong database, correct that root cause instead of assuming version isolation is sufficient.
4. Merge the independently reviewed fix only through the normal task PR process. Product migration code changed, so the old candidate `419e6bf4958269c5b9e94efcb80770e28cd54dda` and manifest cannot deliver this repair: build a new candidate and manifest.
5. Obtain a fresh release request and signed supervisor lease for that exact new candidate. The September 26 request/lease was consumed/expired and must not be reused. Resume the existing Runtime Release path and retain migration, service revision, smoke, sources-off and egress receipts.
6. Keep the parent rollout blocked until real deployment checks pass. Passing local tests or a merged code fix is not dev rollout completion. A failed migration/deploy must use the existing deployment rollback procedure; do not use a baseline downgrade against shared data as an automatic workaround.
