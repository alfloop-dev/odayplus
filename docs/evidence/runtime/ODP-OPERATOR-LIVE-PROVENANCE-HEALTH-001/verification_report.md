# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Task Context
- Task ID: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Owner: `Antigravity4`
- Reviewer: `Codex7`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`

## Scope Compliance Note
- **No Shared Model Contract or E2E Gate Changes**: Preserved `models/shared_ml/production_contracts.py`, `scripts/models/contracts.py`, `scripts/e2e/check_live_e2e_gate.py`, and `tests/e2e/test_live_e2e_gate.py` completely untouched on `origin/dev` baseline. All release gate regressions remain intact.

## Remediation Details

### 1. Core Operator Live Provenance & Tenant-Scoped Risk Projection (P0-1)
- **Finding**: Previously `OperatorLiveRepository` looked for a non-existent `risk_repository` on `PersistenceBundle`, marking `riskRows` unavailable and computing `dataMode=degraded`, `origin.kind=degraded`, and `complete=False`.
- **Fix**: Replaced the non-existent `risk_repository` lookup with `_project_risk_rows` in `OperatorLiveRepository`, deriving tenant-scoped authoritative risk rows and section availability from `stores`, `interventions`, and `forecastAlerts`. Updated `dataOrigin.kind` to `"authoritative"` when `dataMode == "live"`.
- **Result**: `dataMode` accurately resolves to `"live"`, `origin.kind` to `"authoritative"`, and `complete=True` when backed by PostgreSQL persistence, satisfying the Cloud Run deployment validator preflight contract.

### 2. Platform Health & Readiness Decoupled from Model Bindings (P0-2)
- **Finding**: When `forecastops` model alias was unverified or absent from MLflow, `live_ready` in `apps/api/oday_api/main.py` incorporated `production_model_bindings_ready`, causing `/platform/health` and `/readiness` to return global 503 errors.
- **Fix**: Updated `live_ready` in `apps/api/oday_api/main.py` so core data & operator repository readiness is distinguished from model capability bindings (`modes.models.productionBindingsReady`).
- **Result**: `/platform/health` and `/readiness` return 200 OK (`status: "ok"`, `liveReady: True`) when core PostgreSQL persistence and operator repositories are ready, reporting `productionBindingsReady: False` without returning 503. ForecastOps remains governed-disabled with explicit evidence until authentic history backfill completes. Unauthorized access still fails closed.

### 3. Portable Test Environment & Reviewer Alignment (P1)
- **Fix**: Replaced hardcoded `/home/lupin/.local/bin` in `tests/ops/test_cloud_run_live_deployment.py` with portable `Path.home() / '.local' / 'bin'`. Aligned verification report and commit trailers with assigned reviewer `Codex7`.

## Verification & Compliance
- Focused tests passed cleanly:
  - `tests/integration/test_operator_live_provenance_health.py`
  - `tests/integration/test_production_api_composition.py`
  - `tests/reliability/test_live_data_fail_closed.py`
  - `tests/ops/test_cloud_run_live_deployment.py`
- `git diff --check` passed cleanly.
