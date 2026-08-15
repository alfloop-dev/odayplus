# ODP-DEPLOY-WORKER-JOB-EXECUTION-001 — runtime evidence

Task: Diagnose and remediate worker Cloud Run Job execution failure.

Owner: Claude2 · Reviewer: Antigravity5 · Collected: 2026-07-29

Scope guard: this task changes worker/scheduler failure *classification* only.
No Package 10 visual, Operator UI, or design-archive surface is touched, and no
live queue record is deleted or mutated.

## 1. What failed

Deploy Dev run
[30412416116](https://github.com/alfloop-dev/odayplus/actions/runs/30412416116)
at exact SHA `79cf9b67e62ce9fbd762b6695a214965ea9fe258`. Migration job,
migration compatibility smoke, and the scheduler job all passed; the pipeline
stopped at

```
Executing worker Cloud Run Job...
ERROR: (gcloud.run.jobs.execute) The execution failed.
  oday-worker-r-79cf9b67e62c-6fhw5
Error: worker Cloud Run Job failed; deployment stopped.
```

`execution-describe-oday-worker-r-79cf9b67e62c-6fhw5.json`:

| Field | Value |
| --- | --- |
| `metadata.labels.oday-release-sha` | `79cf9b67e62ce9fbd762b6695a214965ea9fe258` |
| `metadata.labels.oday-runtime` | `worker` |
| `status.startTime` → `status.completionTime` | `01:07:16.985Z` → `01:08:12.867Z` |
| `status.failedCount` / `status.retriedCount` | `1` / `1` |
| `status.conditions[Completed]` | `NonZeroExitCode` — "Task ...-task0 failed with exit code: 1" |

## 2. First failing job, exception class, retry state, correlation id

`cloud-logging-oday-worker-r-79cf9b67e62c-6fhw5.json` (25 entries, no secrets —
all payloads are the runtime's own structured audit records).

| Time (UTC) | Attempt | Logged cause | Outcome |
| --- | --- | --- | --- |
| 01:07:26.558 | 1 | `RuntimeError` — `External fetch failed for listing.partner_feed: ExternalFetchProviderConfigurationError: Provider is not selected by the production provider allowlist.` | `retry_queued`, `exit(75)` |
| 01:07:40.325 | 2 | same | `retry_queued`, `exit(75)` |
| 01:07:53.772 | 3 | `RuntimeError` — `provider circuit open until 2026-07-29T01:22:40.257696+00:00` | `retry_queued`, `exit(75)` |
| 01:08:09.068 | 4 | `provider circuit open until ...` | `failed` (max retries), `exit(1)` |

- First failing job: `633856a9-8860-4a2f-869f-b264680df6b6`, job type
  **`external-fetch`**, payload `provider_id=listing.partner_feed`,
  `schedule_id=hourly-listing`.
- Exception class as raised to the queue: **`RuntimeError`** (retryable);
  underlying cause **`ExternalFetchProviderConfigurationError`**,
  `reason_code=provider_not_selected`.
- Retry state: `retry_count` 1 → 2 → 3, then `job_status=failed`,
  `queue_active=0`.
- Correlation id: **`b0d45705-10c5-4fab-bcf8-53222af23d26`**, stable across all
  four attempts.

Two independent defects are visible in that table:

1. **Misclassification.** A deterministic deployment-configuration rejection was
   flattened into a retryable `RuntimeError`, so the worker burned three retries
   on an outcome that cannot change within a release.
2. **Diagnosis loss.** Each rejection was also fed to the provider circuit
   breaker. After two ticks the circuit opened, and attempts 3 and 4 reported
   `provider circuit open until ...` instead of the real reason code — the
   original cause is only recoverable from attempts 1 and 2.

## 3. Not a poisoned queue record

The three candidate hypotheses separate cleanly:

- **Poisoned persisted queue record** — ruled out. The job carries no anomalous
  payload; `provider_id`/`schedule_id` are exactly what the scheduler enqueues
  each tick, and the scheduler job in the same run passed. The failure is
  regenerated on every deploy from a *fresh* job, so deleting the record would
  fix nothing (and criterion 4 forbids mutating live queue state anyway).
  Confirmed by re-observation: see §6.
- **Release code entrypoint / schema dependency** — ruled out. The runtime
  bootstrapped cleanly (`Runtime bootstrapped: mode=postgresql durable=True`) on
  all four attempts, the migration job and migration compatibility smoke passed
  earlier in the same run, and the job was claimed and executed normally.
- **Runtime configuration + failure classification** — confirmed. Dev deploys
  the worker with `ODP_PRODUCTION_PROVIDER_IDS` covering
  `poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset`,
  while the scheduler enqueues an hourly `listing.partner_feed` fetch. The
  allowlist is the operator's deliberate statement of which providers this
  deployment runs live; the *decision* is correct, the *handling* of it is not.

## 4. Fix (smallest scope)

Commit `14b9d282` — `ODP-DEPLOY-WORKER-JOB-EXECUTION-001: anchor fetch retry
classification`.

- `modules/external_data/workers/scheduled_fetch.py` — name the deterministic
  configuration rejections (`CONFIGURATION_REASON_CODES`) and keep them out of
  the resilience circuit. The provider is refused before it is ever contacted,
  so the rejection carries no signal about provider health and must never mask
  its own reason code. Real provider faults still open the circuit.
- `apps/worker/oday_worker/handlers.py` — classify by reason code:
  `provider_not_selected` drains the queue job (the operator excluded this
  provider from this deployment); the other configuration codes raise
  `NonRetryableJobError` so they dead-letter on the first attempt while the
  message still carries the real cause; everything else keeps the existing
  retryable `RuntimeError` path.
- `modules/external_data/workers/__init__.py` — re-export the two new names.

Unchanged: the worker claim/retry/dead-letter state machine, the Cloud Run job
entrypoint receipt shape, the scheduler enqueue contract, and the provider
allowlist/registry rules themselves. The migration job, migration compatibility,
scheduler, fail-closed rollback, provider readiness, model readiness, and secret
binding gates in `product_ops/deployment/deploy_cloud_run_waji.sh` are untouched.

A drained job is **not** a silent success: the blocked ingestion run and its
alert are still persisted, no snapshot is written, and no watermark advances —
asserted by
`tests/ops/test_cloud_run_job_entrypoint.py::test_deselected_provider_fetch_stays_auditable_as_a_blocked_run`.

## 5. Deterministic regression

`tests/ops/test_cloud_run_job_entrypoint.py` reproduces the deploy gate at the
entrypoint layer, driving `run_worker(max_jobs=1, require_job=True)` — the exact
call `execute_job "worker" ... --max-jobs 1` makes — against the live dev
`ODP_PRODUCTION_PROVIDER_IDS` value:

- `test_worker_drains_deselected_provider_fetch_without_retrying` — exit `0`,
  `attempts == 1` (the pre-fix path burned three retries), `queue_active == 0`,
  and a structured worker receipt carrying `release_sha`.
- `test_deselected_provider_fetch_stays_auditable_as_a_blocked_run` — the audit
  trail and the no-fabrication invariants above.
- `test_worker_dead_letters_unregistered_provider_on_the_first_attempt` — the
  other configuration codes terminate on attempt 1 instead of retrying.

`tests/integration/test_external_scheduled_fetch_worker.py`:

- `test_configuration_rejection_does_not_poison_the_provider_circuit` — four
  consecutive rejections all still report `provider_not_selected`, the circuit
  stays closed, and the provider factory is never called. This is the exact
  attempt-3/attempt-4 masking seen in the execution above.
- `test_provider_failure_still_opens_the_circuit` — the guard against
  over-correcting: a real provider fault still opens the circuit.

## 6. Re-observation at current dev head

`deploy-dev-rerun-427e3290-worker-still-fails.log` — Deploy Dev run
[30434707018](https://github.com/alfloop-dev/odayplus/actions/runs/30434707018)
at dev SHA `427e32909c38339b127753c0bba0e9beaf7670be` (2026-07-29 08:31Z), i.e.
after the migration-compatibility remediation merged and with this fix *not*
yet in dev:

```
08:30:44 Cloud Run scheduler Job smoke passed.
08:30:44 Executing worker Cloud Run Job...
08:31:56 Executing job failed
         oday-worker-r-427e32909c38-p8t9l
08:31:59 Error: worker Cloud Run Job failed; deployment stopped.
```

A different release SHA, a different job name, a different execution id — the
same gate, from a freshly enqueued job. That is the direct proof that the
failure is regenerated by configuration + classification, not carried by a
poisoned record.

## 7. Checks at the exact fix head

Head `84babe894ccd5e5379606203fb81519a13f30fcf` (this branch merged with dev
`427e3290`).

| Receipt | Result |
| --- | --- |
| `tests-focused-worker.txt` | 38 passed, 0 failed — scheduled-fetch, worker/scheduler runtime, intake, and Cloud Run job entrypoint suites |
| `tests-ops-full.txt` | 434 tests, 1 failure, 20 skipped |
| `ruff-diff-check.txt` | `ruff check` on the task diff: **All checks passed** |

The single `tests/ops` failure is
`test_deploy_preflight_imports_runtime_dependencies_via_locked_python`, which
asserts the deploy preflight's exit code and fails here with
`required command 'uv' is not installed` — a worker-sandbox environment gap, not
a code regression. `uv` is installed by the Deploy Dev workflow itself.

`ruff format --check` reports 4 task files as unformatted. This is pre-existing
repository state, not a regression introduced here: dev's own
`apps/worker/oday_worker/handlers.py` fails the same check, and no workflow in
`.github/workflows/` runs `ruff format`.

## 8. Candidate worker execution at the fix head

Full write-up: **`candidate-worker-execution.md`**, with receipts
`candidate-deploy-dev-run-30436771086-worker-pass.log` and
`candidate-cloud-run-smoke-run-30436771086.json`.

Local `gcloud` in the worker sandbox cannot be used for this — its credentials
require an interactive `gcloud auth login` reauthentication — so the candidate
execution was driven through the Deploy Dev workflow, which authenticates by
Workload Identity Federation.

Deploy Dev run
[30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086)
at exact head `93ae1b2e75e1056c2bfeccd1d59e25e354f4f21f`:

```
09:01:00 Executing worker Cloud Run Job...
09:01:22 Execution [oday-worker-r-93ae1b2e75e1-c9gms] has successfully completed.
09:01:27 Cloud Run worker Job smoke passed.
```

The worker gate passed on the **first attempt** (`failedCount=0`, one execution,
22 s) against the same dev configuration that made `…-6fhw5` burn three retries
and exit `1` in §1–§2. `jobs-smoke:worker:release_sha` binds that execution to
this head; the run-wide smoke report carries
`expected_sha = version.release_sha = 93ae1b2e75e1056c2bfeccd1d59e25e354f4f21f`.

The run as a whole is still red, **after** the worker gate: the release-aware
API/Web smoke fails fail-closed with the single blocking reason
`PRODUCTION_MODEL_BINDINGS_UNVERIFIED` (avm / heatzone / sitescore
`DATA_CONTRACT_NOT_MATURE`, forecastops `PRODUCTION_MODEL_REGISTRY_UNAVAILABLE`)
— a platform model-readiness state owned outside this task, unchanged by this
branch. The recorded traffic split was then restored to
`oday-api-00005-gin=100` / `oday-web-00008-ws4=100`, which is the fail-closed
rollback of acceptance criterion 6 demonstrated live. Migration, migration
compatibility, and scheduler gates all passed in the same run.

`worker-validation.json` itself could not be retrieved: `deploy-dev.yml` uploads
the non-recursive glob `.odp_data/deployment/*.json`, and `.github/**` is a
forbidden path for this task. That gap and its fix are recorded in
`candidate-worker-execution.md` §1 and §5.

The PR head later advanced past `93ae1b2e` to clear a `BEHIND` base. The
candidate run is not repeated for that: every file that differs between
`93ae1b2e` and the reviewed head is under `.orchestrator/**` (supervisor
tooling, excluded by `.dockerignore:11` from `worker.Dockerfile`'s `COPY . .`)
or `docs/evidence/**`. The worker image and deploy gate are byte-identical.
`candidate-worker-execution.md` §4 carries the file-level proof.
