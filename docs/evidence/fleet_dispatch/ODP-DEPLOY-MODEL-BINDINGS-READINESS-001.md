# ODP-DEPLOY-MODEL-BINDINGS-READINESS-001 — Fleet Dispatch Evidence

Task: Restore verified production model bindings readiness
Owner: Antigravity7 · Reviewer: Claude · Date: 2026-07-29

## 1. Summary of Investigation & Analysis

Deploy Dev run [30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086) stopped after worker smoke passed due to candidate API smoke returning `503 Service Unavailable` with blocking reason `PRODUCTION_MODEL_BINDINGS_UNVERIFIED`.

Analysis of candidate smoke receipt `candidate-cloud-run-smoke-run-30436771086.json`:

1. **`PRODUCTION_MODEL_BINDINGS_UNVERIFIED` (Fail-Closed Gate)**:
   - `avm`, `heatzone`, `sitescore`: Report `governedDisabled: true` with `reasonCode: "DATA_CONTRACT_NOT_MATURE"` because PG16 model-ready inventory has zero eligible labeled rows.
   - `forecastops`: Required-active capability. Returned `available: false`, `reasonCode: "PRODUCTION_MODEL_REGISTRY_UNAVAILABLE"` (`error: "forecast_revenue_interval: configured MLflow registry has no production alias"`).
   - `ODP-PRODUCTION-MODEL-REGISTRY-001` failed closed on training `forecastops` because PG16 history spanned only 4 calendar days (below 28-day window needed for 7/14/28-day canonical horizons); no DEV candidate or production alias was released to MLflow.
   - Therefore `productionBindingsReady` was `false`, `/platform/health` and `/readiness` returned 503 with `PRODUCTION_MODEL_BINDINGS_UNVERIFIED`, and traffic rollback occurred. This is the **intended fail-closed behavior** for unverified model bindings.

2. **Job Queue Smoke Disambiguation (`smoke:/platform/health:job_queue`)**:
   - The candidate smoke receipt showed `smoke:/platform/health:job_queue` failing with `detail: "missing or non-worker/in-memory job queue"`.
   - Inspection revealed `apps/api/oday_api/main.py` returned `queue_details = "healthy"` for durable PostgreSQL queue. `validate_cloud_run_live_deployment.py` expected the string to contain `worker`, `cloud`, or `durable`.
   - Updated `apps/api/oday_api/main.py` to return `queue_details = "healthy (durable postgresql job queue)"` when `bundle.is_durable` is True (and `"healthy (in-memory job queue)"` otherwise), cleanly disambiguating durable PostgreSQL job queue from in-memory queue.

## 2. Minimum Fail-Closed Remediation

- **`apps/api/oday_api/main.py`**:
  Updated `queue_details` to return `"healthy (durable postgresql job queue)"` when `bundle.is_durable` is True, allowing smoke validators to confirm durable PostgreSQL persistence without altering model readiness or safety gates.
- No model readiness gates, provider probes, secret bindings, migration checks, or rollback triggers were bypassed or weakened.

## 3. Verification & Receipts

- **Receipt captured**: `docs/evidence/runtime/ODP-DEPLOY-MODEL-BINDINGS-READINESS-001/candidate-cloud-run-smoke-run-30436771086.json`
- **Focused tests**:
  - `tests/integration/test_production_api_composition.py` & `tests/integration/test_operator_canonical_wiring.py`: 23 passed.
  - `tests/ops/test_cloud_run_live_deployment.py`: 363 passed (1 environment-only gap: `uv` executable absent in worker sandbox).
- **Ruff check**: `ruff check` passed with zero errors on task diff.
