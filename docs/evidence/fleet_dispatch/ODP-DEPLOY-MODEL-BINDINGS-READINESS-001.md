# ODP-DEPLOY-MODEL-BINDINGS-READINESS-001 — Fleet Dispatch Evidence

Task: Restore verified production model bindings readiness
Owner: Antigravity2 · Reviewer: Claude · Date: 2026-08-19

## 1. Summary of Investigation & Analysis

Deploy Dev run [30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086) stopped after worker smoke passed due to candidate API smoke returning 8 non-passing checks.

### Analysis of Candidate Smoke Receipt (`candidate-cloud-run-smoke-run-30436771086.json`)

The 8 failing candidate smoke checks and their root cause classification / task ownership:

1. **`smoke:/platform/health:http` (status=503)**:
   - **Root cause**: `PRODUCTION_MODEL_BINDINGS_UNVERIFIED`. `avm`, `heatzone`, and `sitescore` reported `governedDisabled: true` with `reasonCode: "DATA_CONTRACT_NOT_MATURE"` (PG16 model-ready inventory has zero eligible labeled rows). `forecastops` required-active capability returned `available: false`, `reasonCode: "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"` (`error: "forecast_revenue_interval: configured MLflow registry has no production alias"`).
   - **Ownership / Delegation**: Fail-closed gate working as intended. Owned by `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` (backfilling 28-day history) and `ODP-PRODUCTION-MODEL-REGISTRY-001` (training & releasing production MLflow alias).

2. **`smoke:/readiness:http` (status=503)**:
   - **Root cause**: Identical to `smoke:/platform/health:http`. `productionBindingsReady` was `false`.
   - **Ownership / Delegation**: Owned by `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` and `ODP-PRODUCTION-MODEL-REGISTRY-001`.

3. **`smoke:/platform/health:live_data_mode` (status=unhealthy data_mode=<missing>)**:
   - **Root cause**: Consequential failure resulting directly from the 503 HTTP status return on `/platform/health`.
   - **Ownership / Delegation**: Owned by `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` and `ODP-PRODUCTION-MODEL-REGISTRY-001`.

4. **`smoke:/readiness:live_data_mode` (status=unhealthy data_mode=<missing>)**:
   - **Root cause**: Consequential failure resulting directly from the 503 HTTP status return on `/readiness`.
   - **Ownership / Delegation**: Owned by `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` and `ODP-PRODUCTION-MODEL-REGISTRY-001`.

5. **`smoke:/platform/health:job_queue` (missing or non-worker/in-memory job queue)**:
   - **Root cause**: `apps/api/oday_api/main.py` previously returned `queue_details = "healthy"`. `validate_cloud_run_live_deployment.py:1548` required the job queue status string to explicitly contain `"worker"`, `"cloud"`, or `"durable"`, and not contain forbidden markers.
   - **Ownership & Fix**: Owned and fixed by `ODP-DEPLOY-MODEL-BINDINGS-READINESS-001`. `apps/api/oday_api/main.py` updated to emit `queue_details = "healthy (durable postgresql job queue)"` when `bundle.is_durable` is True. Added regression test `test_real_app_platform_health_job_queue_contract` in `tests/ops/test_cloud_run_live_deployment.py`.

6. **`smoke:/api/v1/operator/bootstrap:provenance` (data_mode=degraded data_source=operator-shell-production)**:
   - **Root cause**: Bootstrap returned HTTP 200, but reported `data_mode=degraded`. This originates from `_meta.dataMode` in `modules/opsboard/application/operator_state.py` because Operator read-model sections (forecastops / model capability status) are unavailable/degraded when model bindings are unverified. Validator (`validate_cloud_run_live_deployment.py:1588`) requires `bootstrap_mode == "live"`, causing `degraded` to fail.
   - **Ownership / Delegation**: Consequential to model readiness / model registry completion. Owned by `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` / `ODP-OPERATOR-LIVE-PREFLIGHT-001`. Once model bindings are published to MLflow, operator state `dataMode` elevates to `live`.

7. **`smoke:/api/v1/operator/bootstrap:read_provenance` (origin_kind=degraded persistence_mode=postgresql live_ready=True)**:
   - **Root cause**: Identical to `smoke:/api/v1/operator/bootstrap:provenance`. `origin_kind=degraded` is produced by operator read-model section unavailability when model bindings are unverified.
   - **Ownership / Delegation**: Owned by `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` / `ODP-OPERATOR-LIVE-PREFLIGHT-001`.

8. **`smoke:web:/operator` (status=307 protected_redirect=false)**:
   - **Root cause**: Candidate Web `/operator` returned 307 redirect, but protected redirect check failed contract evaluation.
   - **Ownership / Delegation**: Delegated to task `ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001`.

### Candidate Traffic Rollback Evidence (Run 30436771086)

- **Rollback Execution**: When candidate smoke validation failed with 8 non-passing checks, `scripts/deploy_cloud_run_waji.sh` executed the traffic rollback step:
  ```bash
  python3 scripts/deployment/cloud_run_traffic.py rollback --service oday-api ...
  ```
- **Traffic Shift Receipt**: 100% of live traffic was safely preserved on the prior stable revision (`oday-api-00042-xyz`). Candidate revision (`oday-api-93ae1b2e75e1056c`) was allocated 0% traffic.

## 2. Minimum Fail-Closed Remediation

- **`apps/api/oday_api/main.py`**:
  Updated `queue_details` to return `"healthy (durable postgresql job queue)"` when `bundle.is_durable` is True, satisfying validator marker rules without altering model readiness or safety gates.
- **`tests/ops/test_cloud_run_live_deployment.py`**:
  - Updated handler mocks to use real app string format `"healthy (durable postgresql job queue)"`.
  - Added regression test `test_real_app_platform_health_job_queue_contract` verifying durable, non-durable, and bare regressed payloads against the validator check.
- No model readiness gates, provider probes, secret bindings, migration checks, or rollback triggers were bypassed or weakened.

## 3. Verification & Receipts

- **Receipt captured**: `docs/evidence/runtime/ODP-DEPLOY-MODEL-BINDINGS-READINESS-001/candidate-cloud-run-smoke-run-30436771086.json`
- **Focused tests**:
  - `pytest tests/integration/test_production_api_composition.py` & `tests/integration/test_operator_canonical_wiring.py`: 23 passed.
  - `pytest tests/ops/test_cloud_run_live_deployment.py`: 357 passed (including `test_real_app_platform_health_job_queue_contract`).
- **Ruff check**: `ruff check` passed with zero errors on task diff.
