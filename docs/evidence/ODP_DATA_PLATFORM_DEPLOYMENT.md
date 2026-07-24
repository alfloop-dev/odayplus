# ODP Data Platform Deployment Contract Evidence

Scope: immutable GKE runtime for the production `fongniao_prod` MongoDB to
PostgreSQL canonical data plane.

## Enforced controls

- App and Cloud SQL Proxy images are accepted only as `@sha256` references.
- Every workload carries the full Git release SHA and identical app image digest.
- The migration Job runs independently before any data read.
- Backfill runtime verifies a durable migration receipt for the same release SHA
  and image digest before opening MongoDB.
- Migration covers Alembic `0001/0002`, Assisted Intake `001-004`, PostgreSQL
  runtime `000008`, `data_plane` control DDL, and structural schema validation.
- Mongo and PostgreSQL credentials are only Secret references.
- Daily data is bounded; trade and device logs are suspended, manual-only,
  one-day, maximum-100,000 workloads.
- Reconciliation evidence is available in PostgreSQL, workload logs, and the
  Kubernetes termination message.
- All Pods use `oday-data-platform`, resource requests/limits, deadlines,
  restricted security contexts, and a native Cloud SQL Auth Proxy sidecar.

## Explicitly not claimed

This artifact does not claim that the image has been pushed, Secrets have been
created, migrations have run, or live rows have been reconciled. Those are
runtime release gates and require exact-head deployment evidence.
