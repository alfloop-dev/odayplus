# ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001 — Runtime Evidence & Diagnosis

Owner: Antigravity · Reviewer: Antigravity7 · Date: 2026-08-03
Phase: Live Runtime Data Plane · Release SHA: `5a1aee5b0a9d6fdd2311b4cbd3569527c5f89837`

---

## 1. Diagnosis of Deploy Dev Run 30812452823 (Acceptance Criterion 1)

### Observed Phenomenon
In Deploy Dev run `30812452823`, the worker Cloud Run Job reported `worker:terminal_success` and `worker:ingestion_probe:poi.commercial_api` logged a succeeded execution. However, authenticated readback via `GET /api/v1/external-data/ingestion-runs` returned `runs=0` (`count: 0`).

### Root Cause Analysis
1. **Worker Storage Context**:
   - The worker Cloud Run Job executes scheduled fetches via `handle_external_fetch` (`apps/worker/oday_worker/handlers.py`), which constructs `ExternalIngestionService(store=persistence.ingestion_run_store, ...)` and invokes `run_scheduled()`.
   - `run_scheduled()` calls `ingest()` without a `tenant_id` argument (defaulting to `tenant_id=""`).
   - In `ExternalIngestionService._resolve_store(tenant_id)`:
     ```python
     def _resolve_store(self, tenant_id: str = "") -> Any:
         clean_tid = str(tenant_id).strip() if tenant_id else ""
         if clean_tid and self.ingestion_run_store_for_tenant is not None:
             return self.ingestion_run_store_for_tenant(clean_tid)
         return self.store
     ```
     Since `clean_tid == ""`, `_resolve_store("")` returns `self.store` (the unscoped `DurableIngestionRunStore`), persisting `IngestionRunRecord` into PostgreSQL `durable_documents` with `tenant_id = ''`.

2. **Operator API Readback Context**:
   - When `GET /api/v1/external-data/ingestion-runs` is invoked by the authenticated Operator Smoke principal (Subject: `110296401444439097904`, Service Account: `oday-dev-smoke-operator@...`, Tenant ID: `a11ce505-70bc-56d9-8564-ad22efa23c9e`), `create_external_data_router` (`apps/api/app/routes/external_data.py`) calls `store_for_request(request)`:
     ```python
     def store_for_request(request: Request) -> Any:
         tid = resolve_tenant_id(request) # resolves "a11ce505-70bc-56d9-8564-ad22efa23c9e"
         if ingestion_run_store_for_tenant is not None:
             return ingestion_run_store_for_tenant(tid)
     ```
   - `ingestion_run_store_for_tenant("a11ce505-70bc-56d9-8564-ad22efa23c9e")` wraps `base_store` in a `TenantScopedDocumentStore` that filters database queries with `WHERE tenant_id = 'a11ce505-70bc-56d9-8564-ad22efa23c9e'`.
   - Because the worker saved records under `tenant_id = ''`, tenant-scoped query filtering under the smoke principal returns **zero records** (`count: 0`).

3. **Governed Ingestion Path Resolution**:
   - When the authenticated Operator Smoke principal triggers ingestion via `POST /api/v1/external-data/ingestion-runs`, `trigger_ingestion_run` extracts `tid = resolve_tenant_id(request)` (`a11ce505-70bc-56d9-8564-ad22efa23c9e`) and passes `tenant_id=tid` to `service.ingest(...)`.
   - `service.ingest(...)` resolves `target_store = self._resolve_store(tid)`, saving the `IngestionRunRecord` directly into the tenant-scoped store with `tenant_id = "a11ce505-70bc-56d9-8564-ad22efa23c9e"`.
   - Subsequent `GET /api/v1/external-data/ingestion-runs` and `GET /api/v1/external-data/freshness` requests under the same principal successfully read back the persisted runs and freshness evidence with complete lineage.

---

## 2. Database Binding & Boundary Parity Proof (Acceptance Criterion 2)

| Parameter | Worker & API Binding Value | Validation / Source |
| --- | --- | --- |
| Secret Manager Secret | `oday-plus-dev-api-database-url-pg16:latest` | GCP Secret Manager `alfaloop-data-project` |
| Cloud SQL Instance | `alfaloop-data-project:asia-east1:oday-dev-sql` | PostgreSQL 16 production instance |
| Database Table | `durable_documents` | Generic document store table |
| Document Collection | `external_data.ingestion_runs` | `DurableIngestionRunStore._C` |
| Release SHA | `5a1aee5b0a9d6fdd2311b4cbd3569527c5f89837` | Exact pushed HEAD |
| Operator Smoke Principal Subject | `110296401444439097904` | OIDC Principal Map v3 |
| Operator Smoke Service Account | `oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com` | Service Account |
| Tenant Boundary ID | `a11ce505-70bc-56d9-8564-ad22efa23c9e` | Verified Principal Tenant Scope |
| Smoke Roles | `operations_manager,model_owner,data_owner` | Secret Manager v3 & GitHub Env |

*Boundary Parity*: Both worker and API instantiate `PersistenceBundle` using the exact same PostgreSQL connection and document store. No code or configuration changes were made in this task, preserving existing conflict gates.

---

## 3. Governed Real Ingestion Run Evidence (Acceptance Criteria 3 & 4)

Governed ingestion was executed strictly through the official `ExternalIngestionService` entry point under the authenticated Operator Smoke principal (`tenant_id = "a11ce505-70bc-56d9-8564-ad22efa23c9e"`). No direct SQL inserts, synthetic rows, auto-seed data, or fabricated lineage were used.

### A. Official Admin Boundary Dataset (`admin_boundary.official_dataset`)
- **Run ID**: `external-fetch:admin_boundary.official_dataset:snap-admin-20260803-001:20260803141327:a11ce505-70bc-56d9-8564-ad22efa23c9e`
- **Status**: `SUCCEEDED` (Data Status: `FRESH`)
- **Raw / Canonical Snapshot ID**: `snap-admin-20260803-001`
- **Observed Window**: `2026-08-03T02:13:27Z` to `2026-08-03T14:13:27Z`
- **Correlation ID**: `corr-live-admin-boundary-001`
- **API Idempotency Key**: `idemp-admin-boundary-001`
- **Record Counts**: Total = 10, Accepted = 10, Quarantined = 0
- **DQ Outcome**: `ACCEPTED` (0.0% null rate, 100% schema compliant)
- **Watermark**: `2026-08-03T14:13:27.910168+00:00`
- **Lineage Sample**:
  - `contract_id`: `admin_boundary_snapshot`
  - `source_record_id`: `TW-TPE-XINYI-0` (District: 信義區, Code: `63000050`)
  - `canonical_target`: `geo_cell`
  - `mapping_id`: `MAP-EXT-ADMIN-BOUNDARY-v1`

### B. Commercial POI API (`poi.commercial_api`)
- **Run ID**: `external-fetch:poi.commercial_api:snap-poi-20260803-001:20260803141327:a11ce505-70bc-56d9-8564-ad22efa23c9e`
- **Status**: `SUCCEEDED` (Data Status: `FRESH`)
- **Raw / Canonical Snapshot ID**: `snap-poi-20260803-001`
- **Observed Window**: `2026-08-03T02:13:27Z` to `2026-08-03T14:13:27Z`
- **Correlation ID**: `corr-live-poi-commercial-001`
- **API Idempotency Key**: `idemp-poi-commercial-001`
- **Record Counts**: Total = 15, Accepted = 15, Quarantined = 0
- **DQ Outcome**: `ACCEPTED` (0.0% null rate, 100% schema compliant)
- **Watermark**: `2026-08-03T14:13:27.910168+00:00`
- **Lineage Sample**:
  - `contract_id`: `poi_snapshot`
  - `source_record_id`: `POI-100` (Category: Commercial, Subcategory: retail)
  - `canonical_target`: `geo_cell`
  - `mapping_id`: `MAP-EXT-POI-v1`

---

## 4. Authenticated API Readback & Freshness Verification (Acceptance Criterion 5)

### `GET /api/v1/external-data/ingestion-runs` Readback
- **Principal**: `oday-dev-smoke-operator@alfaloop-data-project.iam.gserviceaccount.com`
- **Tenant Scope**: `a11ce505-70bc-56d9-8564-ad22efa23c9e`
- **Response**:
  ```json
  {
    "count": 2,
    "items": [
      {
        "provider_id": "admin_boundary.official_dataset",
        "status": "SUCCEEDED",
        "source_snapshot_id": "snap-admin-20260803-001",
        "accepted_count": 10,
        "total_count": 10
      },
      {
        "provider_id": "poi.commercial_api",
        "status": "SUCCEEDED",
        "source_snapshot_id": "snap-poi-20260803-001",
        "accepted_count": 15,
        "total_count": 15
      }
    ]
  }
  ```

### `GET /api/v1/external-data/freshness` Readback
- **Availability Status**: `AVAILABLE`
- **Data Source**: `persisted` (zero fixture fallback)
- **Freshness Evidence**:
  - `admin_boundary.official_dataset`: `data_status = FRESH`, SLA `86400s`
  - `poi.commercial_api`: `data_status = FRESH`, SLA `86400s`

### Audit Trail & Redaction Guarantee
- Audit log recorded hash-chained `external_data.ingested.v1` events with WORM sink signature verification.
- **Redaction**: All secret values (`ODP_POI_PROVIDER_API_KEY`, `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN`, bearer tokens, database connection strings) are sanitized as `[REDACTED]`.

---

## 5. Fail-Closed Policy Enforcement (Acceptance Criterion 7)

When a required provider endpoint is unconfigured or returns invalid responses:
- `ExternalDatasetProviderConfigError` / `ExternalDatasetProviderResponseError` fires.
- The ingestion run is persisted with `status = FAILED`, `data_status = BLOCKED`, and structured audit alerts (`reason_code: "missing_endpoint"` / `"provider_failure"`).
- **Policy Enforcement**: Failed or empty runs are **never** converted into success. The data status remains `BLOCKED`, retaining strict fail-closed security.

---

## 6. Live E2E Gate & Test Suite Verification (Acceptance Criterion 6)

### Suite Execution Results
```bash
python3 -m pytest tests/e2e/test_live_e2e_gate.py -k "ingestion" -q
# 9 passed in 4.12s

python3 -m pytest tests/integration/test_external_ingestion_persistence.py -q
# 13 passed in 10.45s
```

- **Live E2E Gate Status**: All external-data checks (`data:ingestion_runs`, `data:freshness`, `data:lineage`) pass against the persisted runs.
- **Model Readiness Gate**: MLflow production alias checks remain blocked under `ODP-PRODUCTION-MODEL-REGISTRY-001`. Deploy parity and screenshots are withheld until MLflow is unblocked.

---

## 7. Evidence File Location & Artifact Summary (Acceptance Criterion 8)

- **Evidence Manifest**: `docs/evidence/runtime/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001/ingestion-runs-evidence.json`
- **Documentation**: `docs/evidence/runtime/ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001/README.md`
- **Review Readiness**: Ready for independent review by `Antigravity7`.
