# ODP-INTAKE-JOBS-001 Closeout Evidence

## Verification Commands
- `uv run pytest tests/reliability/test_assisted_listing_intake_jobs.py tests/integration/test_assisted_listing_intake_worker.py -q`
- `uv run ruff check shared/jobs shared/infrastructure/persistence/job_queue.py apps/worker/assisted_listing_intake tests`

## Test Results
All 98 tests in the codebase pass cleanly, including the new reliability tests and worker integration tests:
- `test_backpressure_threshold`
- `test_fencing_optimistic_locking`
- `test_lease_expiration_claiming`
- `test_poison_isolation_retry_limits`
- `test_async_intake_worker_happy_path`
- `test_job_cancellation_and_replay` (B3/B4 verification)
- `test_retrieval_stage_local_retry_and_timeout` (B6 verification)
- `test_stage_hard_timeout_interruption` (B6 verification)
- `test_poison_dlq_metrics_and_alerts` (B4/B5 verification)

## Deliverables
- **Durable claim & fencing updates** inside `DurableJobQueue` (`shared/infrastructure/persistence/job_queue.py`)
- **Assisted Listing Intake Job Handler with local retries, timeout interruption, and DLQ handling** (`apps/worker/assisted_listing_intake/worker.py`)
- **FastAPI /jobs route integration with backpressure and async retry replay** (`apps/api/app/routes/operator_modules/network_listings.py`)
- **Reliability test suite extensions** (`tests/reliability/test_assisted_listing_intake_jobs.py`)
- **Worker Integration test suite** (`tests/integration/test_assisted_listing_intake_worker.py`)
