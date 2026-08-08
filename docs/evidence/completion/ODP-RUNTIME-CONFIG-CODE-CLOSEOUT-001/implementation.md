# ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001 — Implementation Details

**Title:** Close repository-owned runtime configuration gaps  
**Owner:** Antigravity3 · **Reviewer:** Claude2 · **Phase:** P0 Runtime  
**Branch:** `task/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001`  

---

## 1. Overview & Architecture

Task `ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001` closes repository-owned runtime configuration gaps across Cloud Run API, Web, Worker, Scheduler, and Migration roles. It ensures fail-closed runtime operation, unified release identity propagation, explicit tenant wiring, and audited rollback targets while strictly excluding secrets and live data.

---

## 2. Key Technical Changes

### A. Unified Release Identity Propagation (`ODAY_RELEASE_SHA`)
- Created `shared/runtime_config.py` containing `get_release_identity()`.
- Centralized release identity resolution hierarchy across all runtime roles:
  1. `ODAY_RELEASE_SHA` (authoritative deployment contract)
  2. `ODP_RELEASE_COMMIT_SHA` (compatibility fallback)
  3. `RELEASE_SHA` (generic release identity)
  4. `GITHUB_SHA` (CI/CD build context)
  5. `COMMIT_SHA` (VCS context)
- Updated API (`apps/api/oday_api/main.py`), Scheduler (`apps/scheduler/oday_scheduler/main.py`), Worker (`apps/worker/oday_worker/main.py`), Notifications (`modules/notifications/infrastructure/adapters.py`), and Cloud Run Job Entrypoint (`scripts/deployment/cloud_run_job_entrypoint.py`) to consume `get_release_identity()`.

### B. Fail-Closed Required Environment Values
- Enforced top-level required environment assertions in `scripts/deploy_cloud_run_waji.sh`.
- Added fail-closed checks for `ODP_SCHEDULED_INGESTION_TENANT_ID` / `ODP_TENANT_ID` at both bash script initialization and Python env file serialization boundaries.
- Replaced implicit fallback to `"tenant-dev"` with explicit `ValueError` when required tenant variables are unwired in deployment environments.

### C. Wired Scheduler Tenant Configuration
- Ensured `ODP_SCHEDULED_INGESTION_TENANT_ID` and `ODP_TENANT_ID` are fully serialized into `API_ENV_FILE` and propagated to Scheduler, Worker, and API Cloud Run job/service manifests.
- Added explicit exception handling in `cloud_run_job_entrypoint.py` for `SchedulerTenantConfigurationError`, emitting a structured `failed` receipt with `reason="missing_tenant_configuration"` and returning `EXIT_FAILED`.

### D. Explicit Rollback Targets
- Verified `scripts/deploy_cloud_run_waji.sh` and `scripts/deployment/cloud_run_release_traffic.sh` arm explicit rollback traps (`ROLLBACK_ARMED=true` and `SCHEDULER_ROLLBACK_ARMED=true`).
- Confirmed pre-deployment traffic snapshots (`API_TRAFFIC_SNAPSHOT`, `WEB_TRAFFIC_SNAPSHOT`) and trigger snapshots (`SCHEDULER_TRIGGER_SNAPSHOT`, `WORKER_TRIGGER_SNAPSHOT`) capture explicit target revisions and trigger definitions prior to any runtime mutation.
- Enforced automatic restoration of recorded revision splits and trigger targets upon deployment failure or live E2E gate rejection before committing the deployment (`DEPLOYMENT_COMMITTED=true`).

---

## 3. Changed Files

### Runtime & Configuration:
- `shared/runtime_config.py` (new)
- `apps/api/oday_api/main.py`
- `apps/scheduler/oday_scheduler/main.py`
- `apps/worker/oday_worker/main.py`
- `modules/notifications/infrastructure/adapters.py`
- `scripts/deploy_cloud_run_waji.sh`
- `scripts/deployment/cloud_run_job_entrypoint.py`

### Tests & Verification:
- `tests/ops/test_runtime_config_code_closeout.py` (new, 5 contract tests)
- `tests/ops/test_cloud_run_live_deployment.py`

### Evidence:
- `docs/evidence/completion/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001/implementation.md`
- `docs/evidence/completion/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001/verification.md`
- `docs/evidence/completion/ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001/closeout.md`
