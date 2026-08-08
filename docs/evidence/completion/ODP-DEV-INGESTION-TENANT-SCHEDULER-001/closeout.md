# ODP-DEV-INGESTION-TENANT-SCHEDULER-001 — Closeout

**Title:** Propagate tenant identity through scheduled ingestion
**Owner:** Claude3 · **Reviewer:** Claude2 · **Phase:** P0 Runtime
**Branch:** `task/ODP-DEV-INGESTION-TENANT-SCHEDULER-001`

## Acceptance status

| Acceptance criterion | Status | Where |
| --- | --- | --- |
| scheduled ingestion never defaults silently to an empty tenant | met | scheduler `_require_tenant_id()`, handler tenant gate, `run_scheduled` tenant requirement |
| configured tenant reaches every scheduled ingest call | met | `ODP_SCHEDULED_INGESTION_TENANT_ID` → payload → handler → `run_scheduled` → run record → watermark |
| cross-tenant run visibility is rejected | met | `_assert_same_tenant` on both replay lookups, tenant-filtered scheduler re-seed, `TenantScopedExternalFetchStateStore`, durable per-tenant run store |
| missing tenant fails with an actionable error | met | four named errors, each naming the env var / provider / schedule; handler dead-letters instead of retrying |
| focused tests and completion evidence are delivered | met | 15 new tests + 3 updated; this evidence folder |

## Changed files

Runtime:
- `apps/scheduler/oday_scheduler/main.py`
- `apps/worker/oday_worker/handlers.py`
- `modules/external_data/application/ingestion_service.py`
- `modules/external_data/workers/scheduled_fetch.py`

Tests:
- `tests/integration/test_scheduled_ingestion_tenant_propagation.py` (new, 15 tests)
- `tests/integration/test_worker_scheduler_runtime.py` (watermark assertions are now
  tenant-scoped; the scheduler is constructed with a tenant)
- `tests/integration/test_external_ingestion_persistence.py` (`run_scheduled` passes a tenant)

Evidence:
- `docs/evidence/completion/ODP-DEV-INGESTION-TENANT-SCHEDULER-001/{implementation,verification,closeout}.md`

## Verification summary

```
python3 -m pytest tests/integration/test_scheduled_ingestion_tenant_propagation.py -q
# 15 passed

python3 -m pytest tests/integration/test_worker_scheduler_runtime.py \
  tests/integration/test_external_ingestion_persistence.py \
  tests/integration/test_external_ingestion_multisource.py -q
# 24 passed

python3 -m pytest tests/integration modules/external_data tests/contract -q \
  -k "(external or scheduler or worker or ingest or tenant) and not adlift"
# 263 passed, 6 skipped, 0 failed (exit 0)

python3 -m ruff check apps/scheduler apps/worker modules/external_data <changed tests>
# All checks passed!
```

Full mapping of each acceptance criterion to its test is in `verification.md`;
the design and the layer boundaries are in `implementation.md`.

## Behaviour changes a reviewer should weigh

1. **The scheduler now refuses to tick without a tenant.** Deployments must set
   `ODP_SCHEDULED_INGESTION_TENANT_ID` (or `ODP_TENANT_ID`). Until then the
   scheduler enqueues nothing and logs
   `error_code=scheduled_ingestion_tenant_missing` each tick. This is the
   intended fail-closed posture; the alternative was continuing to persist
   canonical data under the empty tenant.
2. **The fetch watermark is now per tenant.** An unscoped
   `external_fetch_state_store.last_success_watermark(provider_id)` returns
   `None` for schedule-written state; read it through
   `TenantScopedExternalFetchStateStore(store, tenant_id)`. The three updated
   assertions in `test_worker_scheduler_runtime.py` are exactly this change.
3. **Multi-tenant scheduling requires the durable bundle** — see "Known
   constraint" in `implementation.md`. On a shared in-memory store a second
   tenant's identical window now raises `CrossTenantIngestionRunError` rather
   than silently replaying the first tenant's run.

## Not in scope

No change to the manual/API ingest route, provider connectors, DQ/quarantine,
lineage, freshness classification, `PersistenceBundle` scoping helpers, or the
durable store schemas. No provider credentials or training data were required.
