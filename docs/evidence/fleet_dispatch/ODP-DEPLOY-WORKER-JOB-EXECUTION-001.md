# ODP-DEPLOY-WORKER-JOB-EXECUTION-001: worker Cloud Run Job execution closeout evidence

Owner: Claude2 · Reviewer: Antigravity5 · Phase: Live Runtime Deployment ·
2026-07-29

Diagnose and remediate the worker Cloud Run Job execution failure that stopped
Deploy Dev after the migration compatibility and scheduler gates passed.

Full runtime detail:
`docs/evidence/runtime/ODP-DEPLOY-WORKER-JOB-EXECUTION-001/` (README §1–§8 plus
`candidate-worker-execution.md`).

## 1. Diagnosis before any behaviour change

Deploy Dev run
[30412416116](https://github.com/alfloop-dev/odayplus/actions/runs/30412416116)
at exact SHA `79cf9b67e62ce9fbd762b6695a214965ea9fe258` stopped at
`Executing worker Cloud Run Job...` with execution
`oday-worker-r-79cf9b67e62c-6fhw5` reporting `NonZeroExitCode`,
`failedCount=1`, `retriedCount=1`.

Cloud Logging for that execution (25 entries, no secrets) resolves the whole
attempt chain, correlation id `b0d45705-10c5-4fab-bcf8-53222af23d26` stable
across all four attempts:

| Attempt | Cause | Outcome |
| --- | --- | --- |
| 1–2 | `RuntimeError` — `ExternalFetchProviderConfigurationError: Provider is not selected by the production provider allowlist` | `retry_queued`, exit 75 |
| 3–4 | `RuntimeError` — `provider circuit open until 2026-07-29T01:22:40Z` | retry, then `failed`, exit 1 |

First failing job `633856a9-8860-4a2f-869f-b264680df6b6`, type `external-fetch`,
`provider_id=listing.partner_feed`, `schedule_id=hourly-listing`,
`reason_code=provider_not_selected`.

**Verdict: runtime configuration + failure classification, not a poisoned
queue record and not a release/entrypoint/schema defect.** The runtime
bootstrapped cleanly (`mode=postgresql durable=True`) on all four attempts and
the job was claimed and executed normally; the failure is regenerated from a
*freshly enqueued* job on every deploy, independently confirmed by a later dev
run at a different SHA (`427e3290`, run
[30434707018](https://github.com/alfloop-dev/odayplus/actions/runs/30434707018),
job `oday-worker-r-427e32909c38-p8t9l`) failing the same gate. No live queue
record was deleted or mutated.

Two defects are visible in that chain: a deterministic deployment-configuration
rejection flattened into a retryable `RuntimeError`, and the same rejection fed
to the provider circuit breaker so that attempts 3–4 reported the circuit
instead of the real reason code.

## 2. What shipped (commit `14b9d282`)

- `modules/external_data/workers/scheduled_fetch.py` — `CONFIGURATION_REASON_CODES`
  names the deterministic configuration rejections and keeps them out of the
  resilience circuit. The provider is refused before it is ever contacted, so
  the rejection carries no signal about provider health and must not mask its
  own reason code. Real provider faults still open the circuit.
- `apps/worker/oday_worker/handlers.py` — classify by reason code:
  `provider_not_selected` drains the queue job (the operator deliberately
  excluded that provider from this deployment); other configuration codes raise
  `NonRetryableJobError` and dead-letter on attempt 1 while the message still
  carries the real cause; everything else keeps the existing retryable path.
- `modules/external_data/workers/__init__.py` — re-export the two new names.

A drained job is not a silent success: the blocked ingestion run and its alert
are still persisted, no snapshot is written, and no watermark advances.

Unchanged: the worker claim/retry/dead-letter state machine, the Cloud Run job
entrypoint receipt shape, the scheduler enqueue contract, and the provider
allowlist/registry rules. `scripts/deploy_cloud_run_waji.sh` is untouched, so
the migration job, migration compatibility, scheduler, fail-closed rollback,
provider readiness, model readiness, and secret binding gates are preserved.
No Package 10 visual, Operator UI, or design-archive surface is touched.

## 3. Deterministic regression

`tests/ops/test_cloud_run_job_entrypoint.py` drives
`run_worker(max_jobs=1, require_job=True)` — the exact call the deploy gate
makes — against the live dev `ODP_PRODUCTION_PROVIDER_IDS` value:

- `test_worker_drains_deselected_provider_fetch_without_retrying` — exit `0`,
  `attempts == 1` (pre-fix: three retries), `queue_active == 0`, structured
  worker receipt carrying `release_sha`
- `test_deselected_provider_fetch_stays_auditable_as_a_blocked_run`
- `test_worker_dead_letters_unregistered_provider_on_the_first_attempt`

`tests/integration/test_external_scheduled_fetch_worker.py`:

- `test_configuration_rejection_does_not_poison_the_provider_circuit` —
  reproduces the attempt-3/attempt-4 masking exactly
- `test_provider_failure_still_opens_the_circuit` — the anti-overcorrection guard

## 4. Candidate execution at the exact fix head

Deploy Dev run
[30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086)
at head `93ae1b2e75e1056c2bfeccd1d59e25e354f4f21f`:

```
09:01:00 Executing worker Cloud Run Job...
09:01:22 Execution [oday-worker-r-93ae1b2e75e1-c9gms] has successfully completed.
09:01:27 Cloud Run worker Job smoke passed.
```

Passed on the **first attempt** (`jobs-smoke:worker:execution` requires
`succeededCount>=1` and `failedCount=0`), 22 s, against the same dev
configuration that made `…-6fhw5` burn three retries and exit 1.
`jobs-smoke:worker:release_sha` binds the execution to this exact head.

The run as a whole is still red **after** that gate: the release-aware API/Web
smoke fails fail-closed on the single blocking reason
`PRODUCTION_MODEL_BINDINGS_UNVERIFIED` (avm / heatzone / sitescore
`DATA_CONTRACT_NOT_MATURE`, forecastops `PRODUCTION_MODEL_REGISTRY_UNAVAILABLE`)
— a platform model-readiness state owned outside this task and unchanged by this
branch. The recorded traffic split was then restored to
`oday-api-00005-gin=100` / `oday-web-00008-ws4=100`, i.e. the fail-closed
rollback of acceptance criterion 6 demonstrated live. Migration, migration
compatibility, and scheduler gates all passed in the same run.

Receipts: `candidate-worker-execution.md`,
`candidate-deploy-dev-run-30436771086-worker-pass.log`,
`candidate-cloud-run-smoke-run-30436771086.json`.

The PR head then advanced past `93ae1b2e` to clear a `BEHIND` base. Everything
that differs between `93ae1b2e` and the reviewed head is `.orchestrator/**`
(supervisor tooling, excluded by `.dockerignore:11` from
`infra/docker/worker.Dockerfile`) or `docs/evidence/**`, so the worker image and
the deploy gate are byte-identical to the ones that produced
`oday-worker-r-93ae1b2e75e1-c9gms`. File-level proof in
`candidate-worker-execution.md` §4.

## 5. Verification

`docs/evidence/runtime/ODP-DEPLOY-WORKER-JOB-EXECUTION-001/` §7:

- focused worker tests (`tests-focused-worker.txt`): 38 passed
- full ops suite (`tests-ops-full.txt`): 434 tests, 20 skipped, 1 pre-existing
  environmental failure —
  `test_deploy_preflight_imports_runtime_dependencies_via_locked_python` fails
  with `required command 'uv' is not installed`, a worker-sandbox gap; `uv` is
  installed by the Deploy Dev workflow itself
- `ruff check` on the task diff (`ruff-diff-check.txt`): all checks passed
- exact-head CI on PR #501

`ruff format --check` reports 4 task files as unformatted. This is pre-existing
repository state: dev's own `apps/worker/oday_worker/handlers.py` fails the same
check, and no workflow under `.github/workflows/` runs `ruff format`.

## 6. Follow-ups (not in this task's scope)

- `deploy-dev.yml` uploads the non-recursive glob `.odp_data/deployment/*.json`,
  so the per-job `*-validation.json` receipts do not survive the runner.
  `.github/**` is a forbidden path for this task.
- `PRODUCTION_MODEL_BINDINGS_UNVERIFIED` blocks every Deploy Dev run
  independently of this fix; it needs its own task on the model-readiness lane.
- The API health payload should name the job-queue backend, so
  `smoke:/platform/health:job_queue` stops failing on a queue it reports as
  healthy.
- After this merges, `ODP-P10-DEV-REDEPLOY-VERIFY-001` is re-dispatched to
  Antigravity3 on the exact merged `dev` SHA.
