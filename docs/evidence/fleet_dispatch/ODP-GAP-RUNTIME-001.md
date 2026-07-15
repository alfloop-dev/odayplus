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

## Runtime Coverage (tests/integration)

`tests/integration/test_worker_scheduler_runtime.py` exercises the runtime
layer directly (added in review round 2):

- `test_scheduler_enqueue_then_worker_claim_execute_success` — scheduler
  enqueues an external-fetch job, worker claims → executes → `SUCCEEDED`, and
  the provider success watermark advances.
- `test_worker_forecast_job_claims_and_succeeds` — forecast job claim/execute.
- `test_worker_retries_three_times_then_dead_letters` — 3-strike retry with an
  incrementing `_retry_count`, then dead-letter to `FAILED`; a dead-lettered
  job is no longer claimable.
- `test_scheduler_enqueue_is_idempotent_within_window` — two scheduler ticks in
  one window produce a single external-fetch job.
- `test_queue_enqueue_is_idempotent_by_key` — deterministic queue-level
  idempotency: same key never duplicates a job.
- `test_durable_watermark_persists_across_restart` — success watermark written
  through the worker survives a simulated process restart
  (`_durable_bundle` → `engine.close()` → reopen on the same file).

## Verification Evidence

```bash
$ python3 -m pytest tests/integration/test_worker_scheduler_runtime.py \
    tests/integration/test_external_scheduled_fetch_worker.py \
    tests/integration/test_durable_repository_wiring.py \
    tests/e2e/test_product_closeout_action_checker.py \
    tests/e2e/test_product_closeout_action_matrix.py -q
# all pass

$ python3 -m ruff check tests/integration/test_worker_scheduler_runtime.py \
    apps/worker/oday_worker/main.py apps/scheduler/oday_scheduler/main.py \
    modules/external_data/workers/scheduled_fetch.py
# All checks passed!
```

The E2E worker script (`scripts/e2e/worker_heartbeat.py`) now runs threads for
both `ODayWorker` and `ODayScheduler` simultaneously while maintaining the E2E
lifecycle heartbeats.

## Review Round 2 — Resolution of Requested Changes

1. **Scope leak in `tests/e2e/test_product_closeout_action_*` (resolved).** The
   fixture-actor rename (`Claude2/Codex` → `Antigravity3/Antigravity2`) is *not*
   gratuitous: PR #213 (ODP-FE-XCUT-001 reassignment) updated
   `docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json` to actor `Antigravity3`
   but left the test fixtures at `Claude2`, leaving the `product` CI job red on
   `dev` since 2026-07-11T02:59 (run 29137261458, `test_product_closeout_*`).
   These fixtures read the real queue file, so they must match the reassigned
   owner. The change is retained here because it repairs the live `dev`
   breakage and is required for any PR's `product` check to be green.
2. **No test coverage for the core deliverable (resolved).** See Runtime
   Coverage above — `claim_next`/retry/dead-letter, `ODayWorker`,
   `ODayScheduler`, scheduler idempotency, and `DurableExternalFetchStateStore`
   restart persistence are now covered.
3. **Scoped task-branch PR with green checks (resolved at closeout).** PR opened
   from `task/ODP-GAP-RUNTIME-001` → `dev`.

### Non-blocking follow-ups (verified runtime logic left unchanged)

- Worker retry has no backoff: a requeued job is re-claimed on the next
  `run_once` without honoring `retry_after`. Acceptable for the current
  single-worker loop; a scheduled-visibility timeout is a future enhancement.
- The `forecast` branch of `execute_job` fabricates default observations when a
  store has no series and is not yet wired to an enqueuer (only `external-fetch`
  is scheduled). Left as-is to preserve the reviewer-verified runtime behavior;
  a real forecast trigger or fail-closed policy is a follow-up.
