# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Task Context
- Task ID: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Owner: `Antigravity2`
- Reviewer: `Codex8`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Baseline: `origin/dev` @ `a0333308`
- Target Run: Deploy Dev run `30680943677`

## Summary of Remediations

### 1. Single Tenant Store Resolution & Ingestion Isolation (Codex8 Rejection Remediation)
- **Exactly-Once Tenant Store Resolution**: Updated `ExternalIngestionService` in `modules/external_data/application/ingestion_service.py` to resolve and cache tenant-scoped stores in `self._tenant_stores` so that tenant store resolution happens exactly once per tenant on the service instance. Passed `target_store` directly into `_get_scheduler_and_captures(tenant_id, target_store=target_store)` so scheduler state rehydration and ingest writes execute against the identical store instance.
- **Call-Count & Non-Stable Factory Regression**: Added `test_resolver_call_count_and_non_stable_factory_isolation` in `tests/integration/test_external_ingestion_persistence.py` verifying that tenant store resolver is called exactly once per tenant and that non-stable factories (returning a new store instance on every call) preserve idempotency and window state.
- **Same-Key & Same-Window Tenant A/B Isolation**: Added `test_same_api_key_and_same_window_tenant_ab_isolation` in `tests/integration/test_external_ingestion_persistence.py` verifying that tenant A and tenant B submitting identical API idempotency keys and identical time windows execute as independent runs in their respective tenant stores without cross-tenant replay leaks.

### 2. Canonical Operator Endpoint Verification & Restart Provenance
- **Canonical Route Test Wiring**: Updated `test_canonical_writer_restart_provenance` in `tests/integration/test_operator_live_repository.py` to replace the nonexistent `/api/v1/operator` endpoint with the canonical `/api/v1/operator/bootstrap` endpoint.
- **App Wiring & Live Repository Double**: Updated `create_app` in `apps/api/oday_api/main.py` to accept `operator_live_repository: Any = None` and updated `create_operator_router` in `apps/api/app/routes/operator.py` to evaluate `effective_require_live_data = require_live_data or (live_repository is not None)`.
- **Restart Isolation Proof**: Passed `operator_live_repository=OperatorLiveRepository(bundle2)` to the second `create_app` instance and verified that post-restart `tenant-canonical` retrieves its authoritative records (`recordCount=1`, `listings` available, `dataMode="live"`, `complete=True`), while `tenant-b` receives `recordCount=0` and ownership-less records remain excluded.

### 3. ForecastOps Scope Boundaries & Platform Health Readiness
- **Scope Restoration**: Preserved `models/shared_ml/production_contracts.py`, `scripts/e2e/check_live_e2e_gate.py`, and `tests/e2e/test_live_e2e_gate.py` at `origin/dev` tip to preserve canonical model contracts and release gate integrity without out-of-scope modifications.
- **Platform Health & Readiness Decoupling**: Preserved `/platform/health` and `/readiness` returning HTTP 200 OK when core Operator database, provider, and repository probes are ready. Unverified model capability aliases set `modes.models.capabilities.forecastops.available=False` without triggering global HTTP 503.

### 4. Replay Test Suite & Code Quality
- **Ruff Clean**: Resolved all import ordering and lint errors (`ruff check modules/external_data/ apps/api/ tests/integration/` clean, 0 errors).
- **Git Diff Clean**: Confirmed `git diff --check` passes cleanly (0 errors).
- **5-Suite Replay**: All 40 tests across the 5 focused integration and repository test suites (`test_external_ingestion_persistence.py`, `test_external_ingestion_multisource.py`, `test_operator_live_provenance_health.py`, `test_operator_live_repository.py`, `test_production_api_composition.py`) pass cleanly.

## Modified File Inventory (relative to origin/dev)
1. `apps/api/app/routes/external_data.py`
2. `apps/api/app/routes/listings.py`
3. `apps/api/app/routes/operator.py`
4. `apps/api/app/routes/sitescore.py`
5. `apps/api/oday_api/main.py`
6. `apps/api/oday_api/routes/heatzone.py`
7. `docs/evidence/runtime/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001/verification_report.md`
8. `modules/external_data/application/ingestion_service.py`
9. `modules/external_data/application/ingestion_store.py`
10. `modules/heatzone/workers/scoring_worker.py`
11. `modules/opsboard/application/operator_live_repository.py`
12. `shared/domain/models.py`
13. `shared/infrastructure/persistence/external_data.py`
14. `shared/infrastructure/persistence/factory.py`
15. `shared/infrastructure/persistence/operator_domains.py`
16. `shared/infrastructure/persistence/repositories.py`
17. `shared/workflow/sitescore.py`
18. `tests/integration/test_external_ingestion_persistence.py`
19. `tests/integration/test_operator_live_provenance_health.py`
20. `tests/integration/test_operator_live_repository.py`
21. `tests/integration/test_production_api_composition.py`
22. `tests/ops/test_cloud_run_live_deployment.py`

## Verification Replay
- Command: `/home/lupin/oday-plus/.venv/bin/pytest -q tests/integration/test_external_ingestion_persistence.py tests/integration/test_external_ingestion_multisource.py tests/integration/test_operator_live_provenance_health.py tests/integration/test_operator_live_repository.py tests/integration/test_production_api_composition.py` (40 passed, 1 skipped)
- Command: `/home/lupin/oday-plus/.venv/bin/ruff check modules/external_data/ apps/api/ tests/integration/` (0 errors)
- Command: `git diff --check` (0 errors)
