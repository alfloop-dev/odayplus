# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Task Context
- Task ID: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Owner: `Antigravity2`
- Reviewer: `Codex8`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Baseline: `origin/dev` @ `eed83c0937f491211247ee3fdb0bdf8d932564fb` (exact `eed83c09`)
- Target Run: Deploy Dev run `30680943677`

## Summary of Remediations (Codex8 Rejection Batch)

### B1. Tenant Authorization Scope Verification & Global Fallback Removal
- **Scope Verification**: Updated `_repository` in `apps/api/app/routes/listings.py`, `resolve_tenant_id` and `store_for_request` in `apps/api/app/routes/external_data.py`, `apps/api/oday_api/routes/heatzone.py`, and `apps/api/app/routes/sitescore.py` to derive tenant ID strictly from verified `request.state.operator_principal` scope.
- **Strict Scope Rejection**: Rejects missing principal or tenant scope with HTTP 403 `TENANT_SCOPE_DENIED`. If `x-tenant-id` header is present and does not match the verified principal tenant scope, rejects with HTTP 403 `TENANT_SCOPE_DENIED`.
- **Global Fallback Removal**: Removed fallback to unpartitioned/global stores across Listing, ExternalData, HeatZone, and SiteScore routes when operating under tenant scope.
- **Negative API Coverage**: Added `test_listing_import_rejects_missing_or_mismatched_tenant_scope` in `tests/integration/test_listing_pipeline.py` verifying that bypass attempts are denied with 403 and write 0 records.

### B2. Unsafe Authoritative Reads (TenantScopedDocumentStore Partition-Only Reads)
- **Partition-Only Reads**: Updated `TenantScopedDocumentStore` (`get`, `list_all`, `list_by_group`, `latest_in_group`) in `shared/infrastructure/persistence/operator_domains.py` to query only tenant-partitioned collections (`<collection>.tenant.<partition>`). Removed all fallback calls to unpartitioned base collections.
- **No-Unscoped-Call Regression Test**: Added `test_tenant_scoped_document_store_never_queries_unpartitioned_collections` in `tests/integration/test_operator_live_domain_modules.py` proving zero calls reach unpartitioned collections.

### B3. Per-Request Resolver Contract (ExternalIngestionService)
- **Per-Request Resolution**: Removed lifetime store caching (`self._tenant_stores`) in `ExternalIngestionService` (`modules/external_data/application/ingestion_service.py`). The tenant store factory `ingestion_run_store_for_tenant` is invoked on every ingest request.
- **Failure & Rotation Propagation**: Passed resolved `target_store` through replay, scheduler, and write operations.
- **Per-Request Tests**: Updated `test_resolver_call_count_and_non_stable_factory_isolation` and added `test_per_request_resolver_failure_and_rotation` in `tests/integration/test_external_ingestion_persistence.py` proving per-request resolver call counts and verifying that second-request resolver failures propagate without suppression.

### B4. Verification Precision & Exact Decoupling Assertions
- **Exact Baseline SHA Labeling**: Corrected `verification_report.md` baseline labeling to exact `origin/dev` SHA `eed83c0937f491211247ee3fdb0bdf8d932564fb`.
- **Restored Exact Assertion**: Restored exact assertion `assert operator.json()["meta"]["dataMode"] == "live"` in `test_production_routes_gate_only_the_dependency_they_use` in `tests/integration/test_production_api_composition.py`.

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
18. `tests/integration/_authz.py`
19. `tests/integration/test_external_ingestion_persistence.py`
20. `tests/integration/test_listing_pipeline.py`
21. `tests/integration/test_operator_canonical_wiring.py`
22. `tests/integration/test_operator_live_domain_modules.py`
23. `tests/integration/test_operator_live_provenance_health.py`
24. `tests/integration/test_operator_live_repository.py`
25. `tests/integration/test_production_api_composition.py`
26. `tests/ops/test_cloud_run_live_deployment.py`

## Verification Replay
- Command: `/home/lupin/oday-plus/.venv/bin/pytest -q tests/integration/test_external_ingestion_persistence.py tests/integration/test_external_ingestion_multisource.py tests/integration/test_operator_live_provenance_health.py tests/integration/test_operator_live_repository.py tests/integration/test_production_api_composition.py tests/integration/test_operator_live_domain_modules.py tests/integration/test_listing_pipeline.py` (ALL PASSED)
- Command: `/home/lupin/oday-plus/.venv/bin/ruff check modules/external_data/ apps/api/ tests/integration/` (0 errors)
- Command: `git diff --check` (0 errors)
