# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Task Context
- Task ID: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Owner: `Antigravity4`
- Reviewer: `Codex7`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Baseline: `origin/dev` @ `97e3ae2e`
- Target Run: Deploy Dev run `30680943677`

## Summary of Remediations

### 1. Tenant Scoping & Canonical Writer Restart Provenance (`shared/infrastructure/persistence/operator_domains.py`)
- **Canonical Production Writer Compatibility**: In `shared/infrastructure/persistence/operator_domains.py`, updated `TenantScopedDocumentStore` to query both tenant-partitioned collections (`f"{collection}.tenant.{partition}"`) and unscoped base collections (`collection`), applying tenant matching (`_item_matches_tenant`) and entity key deduplication (`_object_key`).
- **Proven Restart Recovery**: Proven that records written by canonical production writers (`bundle.listing_repository`, `bundle.sitescore_decision_store`, `bundle.ingestion_run_store`, `bundle.heatzone_store`) are correctly retrieved by tenant-partitioned Operator queries after process restarts.
- **Regression Test**: Added `test_canonical_writer_restart_provenance` in `tests/integration/test_operator_live_repository.py` which writes via `bundle.listing_repository`, closes and reopens the durable bundle, and asserts `OperatorLiveRepository(reopened_bundle).load_state(tenant_id="tenant-canonical")` returns `recordCount=1`, `listings` available, `dataMode="live"`, `complete=True`.

### 2. Platform Health & ForecastOps Active-Required Contract (P0 ForecastOps Resolution)
- **Active-Required ForecastOps Contract**: `PRODUCTION_MODEL_CONTRACTS` maintains `forecastops` as an active-required model contract without synthetic auto-seed, fabricated alias, or fake ready state. When the MLflow production alias is absent/unverified due to incomplete 7/14/28-day history, `forecastops` fails closed (`available=False`, `reasonCode="PRODUCTION_BINDING_NOT_RESOLVED"`), reporting `productionBindingsReady=False`.
- **Platform Health Readiness Parity**: `/platform/health` and `/readiness` return HTTP 200 OK with `status: "ok"`, `liveReady: True`, and empty `blockingReasons: []` when core Operator repository probes are ready, distinguishing core Operator repository readiness from model capability bindings without returning a global 503 solely because an evidence-insufficient model alias is absent.

### 3. Replay Test Suite & Ruff Cleanup (P1 Resolution)
- **Fixed Ruff Lint Errors**: Fixed I001 and F811 duplicate import issues in `tests/integration/test_operator_live_repository.py`.
- **Fixed ListingDedupKey Constructor**: Pass valid dataclass parameters (`source_id`, `source_listing_id`, `normalized_address`, `rent_amount`, `area_ping`), resolving `TypeError` during five-suite replay.
- **Enforced Tenant Isolation Assertions**: Verified multi-tenant isolation assertions in `test_operator_live_repository.py` confirming `tenant-a` data is preserved intact after `tenant-b` reads.

## Modified File Inventory ( relative to origin/dev )
1. `apps/api/oday_api/main.py`
2. `docs/evidence/runtime/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001/verification_report.md`
3. `modules/opsboard/application/operator_live_repository.py`
4. `shared/infrastructure/persistence/external_data.py`
5. `shared/infrastructure/persistence/factory.py`
6. `shared/infrastructure/persistence/operator_domains.py`
7. `shared/infrastructure/persistence/repositories.py`
8. `tests/e2e/test_live_e2e_gate.py`
9. `tests/integration/test_operator_live_provenance_health.py`
10. `tests/integration/test_operator_live_repository.py`
11. `tests/integration/test_production_api_composition.py`
12. `tests/ops/test_cloud_run_live_deployment.py`

## Verification Replay
- Command: `/home/lupin/oday-plus/.venv/bin/pytest -q tests/integration/test_operator_live_repository.py tests/integration/test_operator_live_provenance_health.py tests/integration/test_production_api_composition.py tests/e2e/test_live_e2e_gate.py tests/ops/test_cloud_run_live_deployment.py` (524 passed, 1 skipped, 525 tests total)
- Command: `/home/lupin/oday-plus/.venv/bin/ruff check .` (0 errors)
- Command: `git diff --check origin/dev...HEAD` (0 errors)
