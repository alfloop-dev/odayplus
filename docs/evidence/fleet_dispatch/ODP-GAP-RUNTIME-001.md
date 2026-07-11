# ODP-GAP-RUNTIME-001: Runtime wiring closeout evidence

## Scopes Delivered

1. **Product Job Execution**:
   - Replaced heartbeat-only placeholder worker logic (`apps/worker`) with `ODayWorker` engine.
   - Replaced scheduler placeholder logic (`apps/scheduler`) with `ODayScheduler`.
   - The worker periodically claims `queued` jobs, transitions their status to `running`, executes the domain service (e.g. `ForecastOpsService.forecast`), and handles success/failure states.
   - The scheduler enqueues external-fetch jobs periodically with idempotency keys.

2. **Orchestrator Scheduled Fetch**:
   - Integrated `ExternalFetchScheduler` into the scheduler loop to orchestrate fetch runs.
   - Added `DurableExternalFetchStateStore` using `SqliteDocumentStore` to maintain persistent watermarks and circuit breaker states across process restarts.

3. **Retry & Dead-Letter Behavior**:
   - Implemented a 3-strike retry policy inside the worker. Failed jobs are put back in the queue with an updated `_retry_count` attribute inside `payload_json`.
   - Once retries are exhausted, the job status is marked as `failed` (dead-letter).

4. **Persistence Integration**:
   - Implemented `claim_next` and `update_status` methods in both `InMemoryJobQueue` and `DurableJobQueue` (SQLite-backed) for status updates and transaction-safe worker claiming.
   - Wired `DurableExternalFetchStateStore` into the `PersistenceBundle`.

## Verification Evidence

All 935 unit, integration, and E2E checks passed successfully:
```bash
$ make test
935 passed, 10 deselected, 6 warnings in 55.88s
```

The E2E worker script (`scripts/e2e/worker_heartbeat.py`) now runs threads for both `ODayWorker` and `ODayScheduler` simultaneously while maintaining the E2E lifecycle heartbeats.
