# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Task Context
- Task ID: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Owner: `Antigravity4`
- Reviewer: `Codex7`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Baseline: `origin/dev` @ `97e3ae2e`
- Target Run: Deploy Dev run `30680943677`

## Summary of Remediations

### 1. Tenant-Isolation & Scoped Document Store Boundary (P0-1 & P0-2 Resolution)
- **Tenant-Scoped Document Store Auto-Migration**: In `shared/infrastructure/persistence/operator_domains.py`, updated `TenantScopedDocumentStore` to automatically migrate/promote unscoped documents into the requesting tenant's scoped collection (`f"{collection}.tenant.{partition}"`) on read/query operations. This resolves the write/read split where canonical writers wrote unscoped listings while tenant providers returned 0 records.
- **Two-Tenant Isolation**: Proved that when `tenant-a` creates records across all four document-store attributes (`listing_repository`, `sitescore_decision_store`, `ingestion_run_store`, `heatzone_store`), `tenant-b` receives zero foreign records and zero false live completeness.
- **Fixed Owner Tests**: Corrected `test_two_tenant_isolation_prevents_foreign_record_leakage_and_false_completeness` in `tests/integration/test_operator_live_repository.py` by saving `AddressLocation` FKs prior to store/listing persistence and passing valid domain constructor arguments (`HeatZoneScoreResult`). Fixed `test_unpartitioned_in_memory_stores_remain_unavailable` by indexing `state["_meta"]` envelope.

### 2. Platform Health & ForecastOps Capability Status (P0-3 Resolution)
- **Core Health Decoupling**: `/platform/health` and `/readiness` return HTTP 200 OK with `status: "ok"`, `liveReady: True`, and empty `blockingReasons: []` whenever core database, provider, and operator repository probes are ready, distinguishing core Operator repository readiness from model capability bindings.
- **ForecastOps Active-Required Contract**: `PRODUCTION_MODEL_CONTRACTS` maintains `forecastops` as an active-required model contract (`governedDisabled=False`). When the MLflow production alias is absent or unverified due to incomplete 7/14/28-day history, `forecastops` fails closed (`available=False`, `reasonCode="PRODUCTION_BINDING_NOT_RESOLVED"`), reporting `productionBindingsReady=False` without returning global 503 or fabricating synthetic auto-seeds or fake ready state.
- **Upstream Dependency Reporting**: Explicitly documented that Acceptance 4 (training and activating a live ForecastOps model alias with authentic 7/14/28-day history) is BLOCKED waiting on upstream `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` (Human/Ops daily history backfill).

### 3. Verification & Code Quality (P1 Evidence Alignment)
- **Modified File Inventory (12 files relative to origin/dev)**:
  1. `apps/api/oday_api/main.py`
  2. `docs/evidence/runtime/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001/verification_report.md`
  3. `models/shared_ml/production_contracts.py`
  4. `modules/opsboard/application/operator_live_repository.py`
  5. `shared/infrastructure/persistence/external_data.py`
  6. `shared/infrastructure/persistence/factory.py`
  7. `shared/infrastructure/persistence/operator_domains.py`
  8. `shared/infrastructure/persistence/repositories.py`
  9. `tests/e2e/test_live_e2e_gate.py`
  10. `tests/integration/test_operator_live_provenance_health.py`
  11. `tests/integration/test_operator_live_repository.py`
  12. `tests/ops/test_cloud_run_live_deployment.py`
- **Verification Replay Results**:
  - Command: `/home/lupin/oday-plus/.venv/bin/pytest -q tests/integration/test_operator_live_repository.py tests/integration/test_operator_live_provenance_health.py tests/integration/test_production_api_composition.py tests/e2e/test_live_e2e_gate.py tests/ops/test_cloud_run_live_deployment.py` (520 passed, 1 skipped)
  - Command: `/home/lupin/oday-plus/.venv/bin/ruff check .` clean (0 errors)
  - Command: `git diff --check origin/dev...HEAD` clean (0 errors)
