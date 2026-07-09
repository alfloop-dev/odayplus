# Migration and Backfill Runbook

Source baseline: `ODP-SD-05_DATABASE_AND_STORAGE_DESIGN`,
`ODP-SD-08_WORKFLOW_AND_JOB_DESIGN`, `ODP-DATA-06_MODEL_READY_DATASET_DESIGN`,
`ODP-OPS-04_RUNBOOK`.

## Migration Flow

1. Build an immutable image and record the git SHA.
2. Take a database backup or verify point-in-time recovery for the target
   environment.
3. Render a migration plan:

   ```bash
   python -m apps.cli.oday_cli migration-plan --environment staging \
     --output artifacts/migration-plan.json
   ```

4. Review migration file hashes, companion SQL hashes, the manifest checksum,
   target revision, rollback command, and owner.
5. Validate the same checksum manifest through the migration runner:

   ```bash
   python -m apps.cli.oday_cli migration-runner --environment staging \
     --expected-manifest-sha256 "$(jq -r .manifest_sha256 artifacts/migration-plan.json)" \
     --output artifacts/migration-runner.json
   ```

6. Run Alembic from a credentialed release runner:

   ```bash
   ODAY_DATABASE_URL="$STAGING_DATABASE_URL" \
     python -m apps.cli.oday_cli migration-runner --environment staging \
     --expected-manifest-sha256 "$(jq -r .manifest_sha256 artifacts/migration-plan.json)" \
     --execute \
     --output artifacts/migration-apply.json
   ```

7. Confirm `migration-apply.json` has `checksum_status=verified`,
   `returncode=0`, and the expected target revision.
8. Run API health, contract, data, and smoke checks.

Rollback requires an approved window, a fresh backup/PITR checkpoint, and the
migration owner. The first rollback command is `alembic downgrade -1`; destructive
rollbacks must use restore/PITR instead of ad hoc SQL.

## Backfill Flow

Backfills are idempotent jobs tied to a source snapshot, model-ready target view,
and closed time window.

```bash
python -m apps.cli.oday_cli backfill-plan \
  --environment staging \
  --job-type model-ready-backfill \
  --source-snapshot-id txn-20260627 \
  --target-view forecast_training_view \
  --window-start 2026-06-01T00:00:00Z \
  --window-end 2026-06-28T00:00:00Z \
  --output artifacts/backfill-plan.json
```

Before rerun, verify:

| Check | Evidence |
|---|---|
| Source snapshot exists | Snapshot id and hash. |
| Point-in-time boundaries hold | PIT validation report. |
| Data quality threshold passes | Quality score and failed rows. |
| Target view count matches expectation | Before/after row counts. |
| Quarantine is empty or explained | Quarantine table export. |

The CLI-generated idempotency key is the job identity. A rerun with the same
inputs must reuse that key and must not overwrite decided results.
