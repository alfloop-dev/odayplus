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

## Review Feedback Resolution (2026-08-08)

Addressed all 3 review feedback items from Claude2:

1. **`cloud_run_job_entrypoint.run_scheduler` exception handling and deployment manifests**:
   - Wrapped `scheduler.run_once()` in a `try...except SchedulerTenantConfigurationError` block in `scripts/deployment/cloud_run_job_entrypoint.py` to emit a structured `failed` receipt and exit `EXIT_FAILED` instead of letting an unhandled exception escape.
   - Added `ODP_SCHEDULED_INGESTION_TENANT_ID` and `ODP_TENANT_ID` to `API_ENV_FILE` serializer in `scripts/deploy_cloud_run_waji.sh` and workflow env blocks in `.github/workflows/deploy-dev.yml` and `.github/workflows/deploy-staging.yml`.
2. **`check_live_e2e_gate._enqueue_body` tenant context**:
   - Updated probe job payload in `delivery_toolchain/e2e/check_live_e2e_gate.py` to include `tenant_id` resolved from `config.operator_tenant` or environment (`ODP_SCHEDULED_INGESTION_TENANT_ID`/`ODP_TENANT_ID`), ensuring probe jobs execute successfully and persist ingestion runs.
3. **Untenanted scheduler test updates**:
   - Updated `tests/ops/test_cloud_run_job_entrypoint.py`, `tests/reliability/test_runtime_observability.py`, `tests/reliability/test_cross_flow_gate.py`, and `tests/e2e/test_live_e2e_gate.py` to construct `ODayScheduler` / `JobRecord` with explicit `tenant_id` or set `ODP_SCHEDULED_INGESTION_TENANT_ID` in `monkeypatch`.
4. **Base rebase**:
   - Cleanly rebased the task branch onto current `origin/dev`.

## Verification summary

```bash
python3 -m pytest tests/ops tests/reliability tests/e2e tests/integration
# 100% GREEN (1,000+ tests passed across ops, reliability, e2e, integration)

bash -n scripts/deploy_cloud_run_waji.sh
# Clean syntax
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
