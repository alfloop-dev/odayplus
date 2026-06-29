# Deployment, Health, Backup, Restore, and Rollback Evidence

Task: ODP-PV-014  
Status: release-gate candidate  
Generated: 2026-06-29  
Environment: deterministic E2E stack (`infra/docker/docker-compose.e2e.yml`)

## Evidence Command

```bash
python3 scripts/e2e/verify_deployment_health_backup_rollback.py
```

The command starts the E2E API, web, worker, and source-stub services; seeds deterministic data; checks health; creates a SQLite backup; writes a rollback probe record; restores the backup; restarts the stack; and verifies the probe was removed while seeded data survived.

## Verified Controls

| Control | Evidence |
|---|---|
| API health | `GET /platform/health` returns `status=ok` in the E2E stack |
| Web health | root web route returns HTTP 200 through the composed web container |
| Source-stub health | external fixture endpoint `/external/listing_raw_snapshot.valid.json` returns deterministic JSON |
| Worker health | worker writes `/storage/worker-heartbeat.jsonl` with `worker=product-e2e-scheduler` |
| Backup | `/data/product-e2e.sqlite3` is copied to `/storage/backups/product-e2e.sqlite3.backup` and SHA-256 hashed |
| Restore | backup is copied back to `/data/product-e2e.sqlite3` with API/worker stopped |
| Data rollback | post-backup AVM rollback probe disappears after restore |
| Seed preservation | deterministic seeded AVM case for `e2e-store-taipei-001` remains after restore |

## Diagnostics

Default diagnostics directory:

```text
.odp_data/deployment-health-backup-rollback/
```

Expected files:

- `deployment-health-backup-rollback-report.json`
- `seed-summary.json`
- `compose-ps.txt`
- `compose-tail.log`

The report contains the backup SHA-256, restore SHA-256, health payloads, worker heartbeat, case counts before/after restore, and a `report_sha256` for the evidence payload.

## Rollback Coverage

| Area | Status | Notes |
|---|---|---|
| Durable DB/data | exercised | SQLite backup/restore is executed against deterministic E2E data |
| API/web/worker runtime | exercised | stack is stopped/restarted around restore and health checked afterward |
| Model alias rollback | covered elsewhere | PV-007 product E2E exercises Learning Hub canary/full/rollback and registry evidence |
| Policy/image rollback | documented | this E2E stack uses immutable checked-in policy/code inside container images; image rollback is redeploying the previous image tag |
| Remote staging rollout | blocked | `.github/workflows/deploy-staging.yml` still documents a placeholder because staging host/url secrets are not configured |

## Release Decision Impact

This closes the deterministic E2E deployment/backup/restore proof required by PV-014. It does not claim a live remote staging deployment; if production promotion requires remote infrastructure, `ODP_STAGING_DEPLOY_URL` and host/secrets must be configured and this command must be rerun against that target or an equivalent staging drill.
