# ODP-DEV-INGESTION-TENANT-SCHEDULER-001 — Verification

**Owner:** Claude3 · **Reviewer:** Claude2

## Acceptance → test mapping

All new tests live in
`tests/integration/test_scheduled_ingestion_tenant_propagation.py` (15 tests)
unless noted.

### 1. Scheduled ingestion never defaults silently to an empty tenant

| Test | Asserts |
| --- | --- |
| `test_scheduler_tick_without_configured_tenant_enqueues_nothing` | `run_once()` raises `SchedulerTenantConfigurationError` and the queue holds **zero** `external-fetch` jobs |
| `test_scheduler_loop_keeps_reporting_missing_tenant_without_enqueueing` | `loop()` survives the misconfiguration (no crash loop) and still enqueues nothing |
| `test_run_scheduled_rejects_an_empty_tenant` | `""` and `"   "` both raise `ScheduledIngestionTenantError`; the store stays empty |
| `test_external_fetch_job_without_tenant_is_dead_lettered_not_retried` | an untenanted payload goes straight to `JobStatus.FAILED` with no `_retry_count`, and no run is persisted |

### 2. Configured tenant reaches every scheduled ingest call

| Test | Asserts |
| --- | --- |
| `test_scheduled_tenant_is_read_from_environment_with_fallback` | `ODP_SCHEDULED_INGESTION_TENANT_ID` wins over `ODP_TENANT_ID`; whitespace is not a tenant |
| `test_configured_tenant_reaches_payload_run_record_and_watermark` | end-to-end scheduler → queue payload → worker → persisted `IngestionRunRecord.tenant_id` → tenant-scoped watermark |
| `test_two_tenants_in_the_same_window_are_scheduled_separately` | the tenant is in the queue idempotency key, so neither tenant's job is deduped away |
| `test_scheduled_audit_event_carries_the_tenant` | `external_data.ingested.v1` metadata carries `tenant_id` |
| `test_scheduled_ingestion_persists_with_scheduled_trigger` (existing, updated) | `run_scheduled` persists `trigger="scheduled"` **and** the tenant |

### 3. Cross-tenant run visibility is rejected

| Test | Asserts |
| --- | --- |
| `test_cross_tenant_window_replay_is_refused_on_a_shared_store` | tenant B hitting tenant A's window key raises `CrossTenantIngestionRunError`; only tenant A's record exists |
| `test_cross_tenant_api_key_replay_is_refused` | same guard on the `Idempotency-Key` lookup |
| `test_unscoped_reader_cannot_replay_a_tenant_run` | `tenant_id=""` is a distinct scope, not a wildcard |
| `test_tenant_fetch_state_is_isolated_within_one_shared_backend` | watermark + run readable through the owning scope only; invisible to the other tenant *and* to an unscoped read of the same backend |
| `test_tenant_scheduler_state_is_not_seeded_from_another_tenants_runs` | re-seeding tenant B's scheduler from a shared store skips tenant A's runs |
| `test_durable_scheduled_run_lands_only_in_the_tenant_scoped_store` | on the durable bundle the scheduled run is physically scoped: unscoped store and tenant B both see `[]` |
| `test_scheduler_enqueue_then_worker_claim_execute_success` (existing, updated) | the watermark advances for the run's tenant and stays `None` for another tenant and for the unscoped key |
| `test_durable_watermark_persists_across_restart` (existing, updated) | the tenant watermark survives a process restart and is still not inherited by another tenant |

### 4. Missing tenant fails with an actionable error

Each error names what to set and where:

- `SchedulerTenantConfigurationError` → both env vars + `ODayScheduler(tenant_id=...)`,
  `code=scheduled_ingestion_tenant_missing`. Asserted in
  `test_scheduler_tick_without_configured_tenant_enqueues_nothing`.
- `ScheduledIngestionTenantError` → provider id, schedule id, env var.
- `NonRetryableJobError` from the handler → provider id, schedule id, env var.
- `CrossTenantIngestionRunError` → `run_id`, record tenant, requested tenant,
  `code=cross_tenant_ingestion_run`.

## Commands run

### Focused new suite
```
python3 -m pytest tests/integration/test_scheduled_ingestion_tenant_propagation.py -q
# 15 passed
```

### Directly touched existing suites
```
python3 -m pytest \
  tests/integration/test_worker_scheduler_runtime.py \
  tests/integration/test_external_ingestion_persistence.py \
  tests/integration/test_external_ingestion_multisource.py -q
# 24 passed
```

### Related surface (regression sweep)
```
python3 -m pytest tests/integration modules/external_data tests/contract -q \
  -k "(external or scheduler or worker or ingest or tenant) and not adlift"
# 263 passed, 6 skipped, 0 failed (exit 0)
```

### Lint
```
python3 -m ruff check apps/scheduler apps/worker modules/external_data \
  tests/integration/test_scheduled_ingestion_tenant_propagation.py \
  tests/integration/test_worker_scheduler_runtime.py \
  tests/integration/test_external_ingestion_persistence.py
# All checks passed!
```

## Known environment-only failure (not caused by this task)

`tests/integration/test_adlift_incrementality.py::test_batch_worker_succeeds_and_serialises`
fails in this worktree with `RuntimeError: statsmodels is required for
matched-control DiD estimation`; `python3 -c "import statsmodels"` →
`ModuleNotFoundError`. It is an optional dependency missing from this
environment, in a module this task does not touch, so it is excluded from the
sweep with `-k "... and not adlift"`.
