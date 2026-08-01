# Verification Report: ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001

## Task Context
- Task ID: `ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Owner: `Codex9`
- Reviewer: `Codex8`
- Branch: `task/ODP-OPERATOR-LIVE-PROVENANCE-HEALTH-001`
- Baseline: `origin/dev` @ `eed83c0937f491211247ee3fdb0bdf8d932564fb` (exact `eed83c09`)
- Target Run: Deploy Dev run `30680943677`
- Replacement implementation anchor: `0480302923b2e44344f936e5533636301bf4fba2`

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

### B5. Memory Compatibility And Durable Fail-Closed Resolution
- **Memory/POC Compatibility**: `create_app` now keeps the process-local ingestion store for a non-durable persistence bundle. This restores fixture ingestion, persisted freshness, idempotency, quarantine, and cold live-provider availability behavior in memory/POC modes.
- **Durable Tenant Boundary Preserved**: SQLite and PostgreSQL bundles still inject `ingestion_run_store_for_tenant`. Every durable request resolves its store before API-key replay, window replay, or writes, and resolver `None`/exceptions propagate without falling back to the global store.
- **Rejected Regression Set**: The six memory/POC ingestion regressions from rejected head `2c02f8cb` pass at replacement implementation anchor `cdf58993`.

### B6. Dependency-Accurate Authenticated Tenant Mismatch
- **Verified Principal Path**: The listing mismatch regression now stubs the authentication dependency that writes `request.state.operator_principal`, instead of injecting middleware state that the real dependency overwrites.
- **Zero Victim Writes**: A verified `tenant-attacker` principal paired with untrusted `x-tenant-id: tenant-victim` receives HTTP 403 `TENANT_SCOPE_DENIED`; the listing repository is asserted exactly unchanged after the request.

### B7. Diff Hygiene And Final Replay Scope
- Removed the three terminal blank lines reported by `git diff --check` in `test_external_ingestion_persistence.py`, `test_listing_pipeline.py`, and `test_operator_live_domain_modules.py`.
- Expanded the replay from seven integration files to the required eight-file suite by including `tests/ops/test_cloud_run_live_deployment.py`.

### B8. HeatZone And SiteScore Memory/POC Writer Routing
- **Non-Durable Compatibility**: `create_app` now injects the HeatZone and SiteScore tenant-store factories only for durable persistence bundles, matching the existing ingestion composition rule. Memory/POC bundles retain their process-local `HeatZoneResultStore` and `SiteScoreDecisionWorkflow` instead of invoking a factory that cannot scope those in-memory stores.
- **Durable Boundary Preserved**: SQLite and PostgreSQL bundles still inject `heatzone_store_for_tenant` and `sitescore_decision_store_for_tenant`; unresolved tenant stores continue to fail closed with no global fallback.
- **Rejected Route Regressions Covered**: The full `test_domain_api_rbac.py`, `test_heatzone_flow.py`, and `test_sitescore_decision.py` suites pass. This includes the four cases rejected at `2d3d1097`: authorized HeatZone listing, HeatZone batch scoring, absent-feature validation, and the SiteScore decision loop.
- **Restart Composition Covered**: `test_canonical_writer_restart_provenance` and the production composition suite pass with durable tenant factories still active.

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
21. `tests/integration/test_operator_live_domain_modules.py`
22. `tests/integration/test_operator_live_provenance_health.py`
23. `tests/integration/test_operator_live_repository.py`
24. `tests/integration/test_production_api_composition.py`
25. `tests/ops/test_cloud_run_live_deployment.py`

## Verification Replay
- Command: `pytest -q tests/integration/test_external_ingestion_persistence.py tests/integration/test_external_ingestion_multisource.py tests/integration/test_operator_live_provenance_health.py tests/integration/test_operator_live_repository.py tests/integration/test_production_api_composition.py tests/integration/test_operator_live_domain_modules.py tests/integration/test_listing_pipeline.py tests/ops/test_cloud_run_live_deployment.py` (exit 0; full eight-file replay passed, with the existing environment-conditional skip)
- Command: `pytest -q tests/integration/test_domain_api_rbac.py tests/integration/test_heatzone_flow.py tests/integration/test_sitescore_decision.py` (exit 0; full rejected-route regression replay passed)
- Command: `ruff check <all Python files changed from origin/dev>` (0 errors)
- Command: `git diff --check origin/dev...HEAD` (0 errors)
- Command: `git diff --name-only origin/dev...HEAD -- <task forbidden paths>` (empty; Package 10 visuals/routes/design archive, deploy workflows, model/release contracts, retired-path inventory, orchestrator, and GitHub workflows unchanged)
- Warning: pre-existing Starlette `TestClient` / `httpx` and HTTP 422 constant deprecation warnings remain; they do not fail the replay.
