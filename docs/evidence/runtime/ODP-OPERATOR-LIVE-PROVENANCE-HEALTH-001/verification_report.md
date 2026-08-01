# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Task Context
- Task ID: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Owner: `Antigravity4`
- Reviewer: `Codex6`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`

## Scope Compliance Note
- **No Shared Model Contract or E2E Gate Changes**: Preserved `models/shared_ml/production_contracts.py`, `scripts/models/contracts.py`, `scripts/e2e/check_live_e2e_gate.py`, and `tests/e2e/test_live_e2e_gate.py` completely untouched on `origin/dev` baseline. All release gate regressions remain intact.

## Remediation Details

### 1. Core Operator Live Provenance & Tenant-Scoped Read
- **Finding**: Previously, `ingestionRuns`, `heatZones`, and `riskRows` were marked `available` with `recordCount=0` without running any tenant-scoped authoritative read.
- **Fix**: Updated `OperatorLiveRepository._read_sources` and `load_state` in `modules/opsboard/application/operator_live_repository.py` to attempt tenant-scoped repository resolution via `_tenant_scoped_repository`. When a tenant-scoped repository is unconfigured, the section state is set to `unavailable` with explicit reason codes (`OPERATOR_TENANT_INGESTION_RUNS_UNAVAILABLE`, `OPERATOR_TENANT_HEATZONES_UNAVAILABLE`, `OPERATOR_TENANT_RISK_ROWS_UNAVAILABLE`).
- **Result**: `dataMode` accurately resolves to `degraded` and `complete=False` when unconfigured sections exist, providing accurate live provenance.

### 2. Platform Health & Readiness Decoupled from Absent Model Alias 503 Crashing
- **Finding**: When `forecastops` model alias was unverified or absent from MLflow, `live_ready` in `apps/api/oday_api/main.py` incorporated `production_model_bindings_ready`, causing `/platform/health` and `/readiness` to return global 503 errors.
- **Fix**: Updated `live_ready` in `apps/api/oday_api/main.py` so core data & operator repository readiness is distinguished from model capability bindings (`modes.models.productionBindingsReady`).
- **Result**: `/platform/health` and `/readiness` return 200 OK (`status: "ok"`, `liveReady: True`) when core PostgreSQL persistence and operator repositories are ready, reporting `productionBindingsReady: False` and `blockingReasons: ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"]` without crashing with 503. Unauthorized access still fails closed.

### 3. Verification & Compliance
- Focused tests passed cleanly:
  - `tests/integration/test_operator_live_provenance_health.py`
  - `tests/integration/test_production_api_composition.py`
  - `tests/ops/test_cloud_run_live_deployment.py`
- `git diff --check` passed cleanly.
