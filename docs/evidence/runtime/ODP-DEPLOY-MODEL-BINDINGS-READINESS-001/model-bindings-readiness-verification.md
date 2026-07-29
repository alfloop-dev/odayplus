# Model Bindings Readiness Verification Report

Task ID: ODP-DEPLOY-MODEL-BINDINGS-READINESS-001
Owner: Antigravity7
Reviewer: Claude
Date: 2026-07-29

## Verification Summary

1. **Receipt Analysis (Deploy Dev run 30436771086)**:
   - `smoke:/platform/health:http`: `status=503` (`ok=false`)
   - `smoke:/readiness:http`: `status=503` (`ok=false`)
   - `blockingReasons`: `["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]`
   - `capabilities`:
     - `avm`: `governedDisabled: true`, `reasonCode: "DATA_CONTRACT_NOT_MATURE"`
     - `heatzone`: `governedDisabled: true`, `reasonCode: "DATA_CONTRACT_NOT_MATURE"`
     - `sitescore`: `governedDisabled: true`, `reasonCode: "DATA_CONTRACT_NOT_MATURE"`
     - `forecastops`: `available: false`, `reasonCode: "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"`, `error: "forecast_revenue_interval: configured MLflow registry has no production alias"`

2. **Root Cause Analysis**:
   - `PRODUCTION_MODEL_BINDINGS_UNVERIFIED` is caused by `forecastops` lacking a production alias in the configured MLflow registry (`MLFLOW_TRACKING_URI`).
   - `ODP-PRODUCTION-MODEL-REGISTRY-001` failed closed during training because PG16 dataset had only 4 days of history (below the 28-day window needed for 7/14/28-day canonical forecast horizons).
   - Fails-closed behavior is functioning as designed: an unverified model binding must prevent live traffic routing.
   - Job queue smoke contract ambiguity resolved: `main.py` updated to return `"healthy (durable postgresql job queue)"` when `bundle.is_durable` is True.

3. **Verification Suites Passed**:
   - `pytest tests/integration/test_production_api_composition.py tests/integration/test_operator_canonical_wiring.py` (23 passed)
   - `pytest tests/ops/test_cloud_run_live_deployment.py` (363 passed)
   - `ruff check` on modified files (passed with 0 errors)
