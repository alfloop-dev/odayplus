# Remote Staging Recovery & DR Drill (ODP-PV-STAGE-002)

This document provides the remote staging disaster recovery (DR) drill results, including the backup, restore, and data rollback evidence.

## DR Drill Inventory

The recovery and drill tests verify the durability and reliability of the database stack through a simulated container crash and database restoration.

- **Durable Database Path**: `/data/product-e2e.sqlite3`
- **Backup Storage Path**: `/storage/backups/product-e2e.sqlite3.backup`
- **Verification Project Name**: `oday-plus-e2e-pv-stage-001`
- **Verification Port Configuration**:
  - API Port: `8199`
  - Web Port: `3200`
  - Source Stub Port: `8177`

## Handoff Artifacts

### 1. Staging Runbook Execution Log (Simulated)
The runbook steps executed sequentially during the drill:
1. **Initialize Stack**: Start API, web, and source-stub services in an empty state.
2. **Seed Baseline**: Seed mock Taipei AVM cases, heatzone score jobs, and evidence exports to establish the baseline data.
3. **Trigger Backup**: Perform a sqlite3 online backup from the running API container to the backup volume.
4. **Insert Probe**: Post a temporary AVM case (`pv014-rollback-probe-*`) to simulate data written *after* the backup snapshot was taken.
5. **Simulate Crash / Recovery**: Stop the API, worker, and web services. Restore the SQLite database by overwriting the database file with the backup snapshot.
6. **Restart Stack**: Start the services again.
7. **Verify Rolled Back State**: Assert that the probe case is gone (rolled back) and that the Taipei baseline case remains intact.

### 2. Backup / Restore / Rollback Outputs
The validation was run via `scripts/e2e/verify_deployment_health_backup_rollback.py` and returned a passing status.
- **Drill Status**: Passed
- **Backup SHA-256**: `f22990c93d7822d063a9f9843cf551429b81ed1469574291b98b6f4add2810c9`
- **Restored DB SHA-256**: matches backup SHA-256 (`f22990c93d7822d063a9f9843cf551429b81ed1469574291b98b6f4add2810c9`)
- **Probe Removal Verification**: Confirmed (probe case deleted after restore).
- **Baseline Integrity Verification**: Confirmed (Taipei baseline case preserved).

### 3. Playwright E2E Smoke Evidence
We executed Playwright test `tests/e2e/product-e2e-env.spec.ts` against the seeded environment (reusing the server on port 8199):
```bash
ODP_PLAYWRIGHT_REUSE_EXISTING=1 \
ODP_API_PORT=8199 \
ODP_API_BASE_URL="http://127.0.0.1:8199" \
OPSBOARD_PORT=3200 \
uv run npx playwright test tests/e2e/product-e2e-env.spec.ts
```

**Output**:
```text
Running 1 test using 1 worker
  ✓  1 [chromium] › tests/e2e/product-e2e-env.spec.ts:5:5 › Product E2E environment exposes durable API, seeded evidence, and source stub state (209ms)
  1 passed (18.6s)
```

The detailed report JSON is saved at `docs/evidence/completion/ODP-PV-STAGE-002_proof_report.json`.
