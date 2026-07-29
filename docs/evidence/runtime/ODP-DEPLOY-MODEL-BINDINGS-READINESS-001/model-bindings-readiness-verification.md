# Model Bindings Readiness Verification Report

Task ID: ODP-DEPLOY-MODEL-BINDINGS-READINESS-001
Owner: Antigravity7
Reviewer: Claude
Date: 2026-07-29

## Verification & Analysis Summary

### 1. Complete Breakdown of Candidate Smoke Receipt (Deploy Dev run 30436771086)

The candidate smoke receipt `candidate-cloud-run-smoke-run-30436771086.json` recorded 8 non-passing checks:

| Check Name | Status | Detail / Cause | Ownership / Delegation |
|---|---|---|---|
| `smoke:/platform/health:http` | `503` | `PRODUCTION_MODEL_BINDINGS_UNVERIFIED` | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` |
| `smoke:/readiness:http` | `503` | `PRODUCTION_MODEL_BINDINGS_UNVERIFIED` | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` |
| `smoke:/platform/health:live_data_mode` | `unhealthy` | Consequential to 503 response envelope | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` |
| `smoke:/readiness:live_data_mode` | `unhealthy` | Consequential to 503 response envelope | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` |
| `smoke:/platform/health:job_queue` | `failed` | Emitted bare "healthy"; required "durable" marker | Owned & Fixed by `ODP-DEPLOY-MODEL-BINDINGS-READINESS-001` |
| `smoke:/api/v1/operator/bootstrap:provenance` | `degraded` | `_meta.dataMode` degraded when model bindings unverified | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` / `ODP-OPERATOR-LIVE-PREFLIGHT-001` |
| `smoke:/api/v1/operator/bootstrap:read_provenance` | `degraded` | `origin_kind=degraded` when model bindings unverified | `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` & `ODP-PRODUCTION-MODEL-REGISTRY-001` / `ODP-OPERATOR-LIVE-PREFLIGHT-001` |
| `smoke:web:/operator` | `307` | Protected redirect validation contract | Delegated to `ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001` |

### 2. Traffic Rollback Evidence (Run 30436771086)

- **Rollback Execution**: Deploy Dev run 30436771086 candidate smoke failed, triggering automatic rollback in `scripts/deploy_cloud_run_waji.sh`.
- **Outcome**: 100% of live production traffic was retained on the previous stable revision. Candidate revision received 0% traffic.

### 3. Root Cause & Fail-Closed Behavior

- `PRODUCTION_MODEL_BINDINGS_UNVERIFIED` is caused by `forecastops` lacking a production alias in the configured MLflow registry (`MLFLOW_TRACKING_URI`).
- `ODP-PRODUCTION-MODEL-REGISTRY-001` failed closed during training because PG16 dataset had only 4 days of history (below the 28-day window needed for 7/14/28-day canonical forecast horizons).
- Fail-closed behavior is functioning as designed: an unverified model binding must prevent live traffic routing.
- Job queue smoke contract ambiguity resolved: `main.py` updated to return `"healthy (durable postgresql job queue)"` when `bundle.is_durable` is True.
- Regression test `test_real_app_platform_health_job_queue_contract` added to `tests/ops/test_cloud_run_live_deployment.py`.

### 4. Verification Suites Passed

- `pytest tests/ops/test_cloud_run_live_deployment.py` (including `test_real_app_platform_health_job_queue_contract`)
- `pytest tests/integration/test_production_api_composition.py tests/integration/test_operator_canonical_wiring.py` (23 passed)
- `ruff check` on modified files (passed with 0 errors)
