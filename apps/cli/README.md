# CLI App

Admin, migration, and one-off operational utilities.

The first production-readiness baseline intentionally renders auditable plans
instead of mutating live infrastructure directly. Operators can attach these
JSON plans to release evidence before running Alembic, dbt, or job workers in a
credentialed environment.

## Commands

```bash
python -m apps.cli.oday_cli migration-plan --environment dev
python -m apps.cli.oday_cli backfill-plan \
  --environment dev \
  --job-type model-ready-backfill \
  --source-snapshot-id txn-20260627 \
  --target-view forecast_training_view \
  --window-start 2026-06-01T00:00:00Z \
  --window-end 2026-06-28T00:00:00Z
```

`migration-plan` records migration file hashes, target revision, database URL
environment variable, and rollback preconditions.

`backfill-plan` records an idempotency key, the source snapshot, model-ready
target view, time window, point-in-time/data-quality checks, and quarantine
table.
