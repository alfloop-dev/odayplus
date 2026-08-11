# ODP-DEV-INGESTION-TENANT-SCHEDULER-001 — Implementation

**Title:** Propagate tenant identity through scheduled ingestion
**Owner:** Claude3 · **Reviewer:** Claude2 · **Phase:** P0 Runtime

## Problem

The manual/API ingestion route already resolves a tenant from a verified
principal and refuses the request without one
(`apps/api/app/routes/external_data.py::resolve_tenant_id` →
`TENANT_SCOPE_DENIED`). The **scheduled** route had no equivalent, and it fell
through to the unscoped default at every hop:

| Hop | Before |
| --- | --- |
| `ODayScheduler.run_once` | enqueued an `external-fetch` payload with no `tenant_id` |
| `handle_external_fetch` | never read `tenant_id` (unlike `handle_forecast`, which fails closed) |
| `ExternalIngestionService.run_scheduled` | no `tenant_id` parameter → `ingest(tenant_id="")` |
| `IngestionRunRecord` | persisted with `tenant_id=""` into the shared store |
| fetch state store | one watermark / circuit per `provider_id`, shared by all tenants |

So every scheduled run persisted canonical output, lineage, and a watermark
under the empty tenant, and a second tenant on the same store would have
replayed the first tenant's run (window and API idempotency keys are not
tenant-qualified).

## Change by layer

### 1. `apps/scheduler/oday_scheduler/main.py` — configured tenant, fail closed

- `SCHEDULED_TENANT_ENV_VAR = "ODP_SCHEDULED_INGESTION_TENANT_ID"`, falling back
  to the process-wide `ODP_TENANT_ID` already used by
  `scripts/external_data_backfill.py`; `resolve_scheduled_tenant_id(env)` reads
  them in that order.
- `ODayScheduler(..., tenant_id=None, env=None)` resolves once at construction;
  an explicit `tenant_id=` wins (this is how tests and single-tenant embedders
  pin it).
- `run_once()` calls `_require_tenant_id()` **before** enqueueing and raises
  `SchedulerTenantConfigurationError` (`code =
  scheduled_ingestion_tenant_missing`) when unset. Nothing is enqueued, so the
  worker is never handed a job it would have to guess at.
- The payload now carries `tenant_id`, and the queue idempotency key is
  `scheduled-fetch:<tenant>:<YYYYMMDDHHMM>` — two tenants sharing a window must
  not collapse into one job.
- `loop()` catches only that error, logs it at ERROR with the reason code, and
  continues. Fail-closed but not a crash loop: the misconfiguration is
  re-reported every tick until an operator fixes it.

### 2. `apps/worker/oday_worker/handlers.py` — tenant gate on the job

- `handle_external_fetch` now reads `tenant_id` from the payload and raises
  `NonRetryableJobError` when it is missing, naming the env var to set. This
  mirrors `handle_forecast`; the job dead-letters on attempt 1 instead of
  retrying a deployment misconfiguration three times.
- The service is constructed with `ingestion_run_store_for_tenant` via the new
  `_tenant_ingestion_store_resolver`, which hands over
  `PersistenceBundle.ingestion_run_store_for_tenant` **only for a durable
  bundle** — the same rule `apps/api/oday_api/main.py` already applies. On the
  in-memory bundle the shared store is used and isolation is carried by the
  record's `tenant_id` plus the cross-tenant guard below.
- `run_scheduled(..., tenant_id=tenant_id)`.

### 3. `modules/external_data/application/ingestion_service.py`

- `run_scheduled` takes `tenant_id` and raises `ScheduledIngestionTenantError`
  on an empty/whitespace value, with a message naming the provider, the
  schedule, and the env var. `ingest()` still tolerates `tenant_id=""` — that is
  the legacy unscoped in-process store, and the API path supplies a real tenant.
- `_assert_same_tenant()` guards **both** replay lookups (`get_by_api_key`,
  `get_by_window_key`). A record whose `tenant_id` differs from the caller's
  raises `CrossTenantIngestionRunError` instead of being returned. An unscoped
  caller is treated as a distinct scope, not a wildcard.
- `_seed_scheduler_state()` replaces three copies of the re-seed loop and skips
  records belonging to another tenant. Without it, tenant B's scheduler would
  inherit tenant A's watermark from a shared store and skip a window it never
  ingested — cross-tenant visibility through the back door.
- Per-tenant schedulers now get a namespaced view of the deployment's fetch
  state store (below) instead of a throwaway in-memory one, so a tenant's
  watermark stays durable across restarts.
- The audit event metadata carries `tenant_id`.

### 4. `modules/external_data/workers/scheduled_fetch.py`

New `TenantScopedExternalFetchStateStore(inner, tenant_id)`: a wrapper that
namespaces `provider_id` and `idempotency_key` with `tenant:<id>:` on the way
in and strips them on the way out. It works over *any* state store
(`InMemory…`, `Durable…`), so one provisioned backend serves every tenant while
no tenant can read another's watermark, circuit state, or run.

## Deployment note

`ODP_SCHEDULED_INGESTION_TENANT_ID` (or `ODP_TENANT_ID`) must be set on the
`oday-scheduler` deployment. Until it is, the scheduler enqueues nothing and
logs `error_code=scheduled_ingestion_tenant_missing` every tick — the intended
fail-closed posture, not a silent unscoped ingest.

## Known constraint: multi-tenant schedules need the durable bundle

`IngestionRunRecord.idempotency_key` is the scheduler's window key
(`provider:schedule:window_start:window_end`) and is **not** tenant-qualified,
because it round-trips through `to_external_fetch_run()` into scheduler state.
On a physically scoped store that is fine — each tenant has its own index —
and `test_durable_scheduled_run_lands_only_in_the_tenant_scoped_store` shows a
durable scheduled run landing only in its own tenant's store, with the unscoped
store and the other tenant both empty.

If two tenants are scheduled against **one shared** store (the in-memory bundle,
i.e. dev/test), the second tenant's window lookup resolves to the first
tenant's record and now raises `CrossTenantIngestionRunError`; the job retries
and dead-letters. That is deliberate: a loud, named failure is the correct
outcome for a configuration that cannot keep tenants apart, and it replaces the
previous behaviour of silently handing tenant B tenant A's run. Multi-tenant
scheduling is supported on the durable bundle.

## Deliberately not changed

- The manual/API ingest route and its `TENANT_SCOPE_DENIED` behaviour.
- Provider connectors, DQ/quarantine, lineage, freshness classification.
- `PersistenceBundle` scoping helpers and durable store schemas (reused as-is).
