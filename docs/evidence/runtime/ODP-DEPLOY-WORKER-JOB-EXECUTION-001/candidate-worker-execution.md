# Candidate worker Cloud Run Job execution at the fix head

Task: ODP-DEPLOY-WORKER-JOB-EXECUTION-001 · Owner: Claude2 · Reviewer: Antigravity5

Deploy Dev run
[30436771086](https://github.com/alfloop-dev/odayplus/actions/runs/30436771086)
(`workflow_dispatch`, 2026-07-29 08:45:10Z → 09:04:00Z) at exact head
`93ae1b2e75e1056c2bfeccd1d59e25e354f4f21f` — this branch's HEAD, the fix
(`14b9d282`) plus the evidence commit.

Receipts in this directory:

- `candidate-deploy-dev-run-30436771086-worker-pass.log` — the deploy job's own
  log for the job gates and the API/Web smoke + rollback.
- `candidate-cloud-run-smoke-run-30436771086.json` — the run's uploaded
  `cloud-run-dev-validation` artifact (`.odp_data/deployment/cloud-run-smoke.json`),
  `secret_values_redacted: true`.

## 1. Worker gate: passed, first attempt

```
09:01:00 Executing worker Cloud Run Job...
09:01:22 Execution [oday-worker-r-93ae1b2e75e1-c9gms] has successfully completed.
09:01:27 Cloud Run worker Job smoke passed.
         report=.odp_data/deployment/cloud-run-jobs/worker-validation.json
```

| | pre-fix run 30412416116 | candidate run 30436771086 |
| --- | --- | --- |
| release SHA | `79cf9b67e62c…` | `93ae1b2e75e1…` |
| job / execution | `oday-worker-r-79cf9b67e62c-6fhw5` | `oday-worker-r-93ae1b2e75e1-c9gms` |
| wall clock | 01:07:16 → 01:08:12 (56 s, 4 attempts) | 09:01:00 → 09:01:22 (22 s, 1 attempt) |
| terminal state | `NonZeroExitCode`, `failedCount=1`, `retriedCount=1` | success, `succeededCount>=1`, `failedCount=0` |
| deploy gate | `Error: worker Cloud Run Job failed; deployment stopped.` | `Cloud Run worker Job smoke passed.` |

The `oday-worker-r-93ae1b2e75e1` job name is derived from the release SHA by
`scripts/deploy_cloud_run_waji.sh`, and the same script labels the job
`oday-release-sha=93ae1b2e…,oday-runtime=worker,oday-data-binding=live`. So the
execution above is bound to this head and to no other.

### What "worker Job smoke passed" asserts

`capture_job_proof worker` runs
`validate_cloud_run_live_deployment.py jobs-smoke --job-kind=worker
--expected-sha="${ODAY_RELEASE_SHA}"` over a live `gcloud run jobs describe` and
the resolved latest `gcloud run jobs executions describe`. It is fail-closed on
all of:

| Check | Assertion |
| --- | --- |
| `jobs-smoke:worker:release_sha` | exact release SHA present in image/env/labels |
| `jobs-smoke:worker:entrypoint` | bounded `worker` entrypoint is configured |
| `jobs-smoke:worker:provider_selection` | the Job declares exactly one readable `ODP_PRODUCTION_PROVIDER_IDS`, matching the release value |
| `jobs-smoke:worker:secret_bindings` | required secrets are bound, none in plaintext |
| `jobs-smoke:worker:execution` | latest execution completed with `succeededCount>=1` **and `failedCount=0`** |
| `jobs-smoke:worker:execution_receipt` | execution carries a queryable Cloud Run status receipt |

`failedCount=0` is the one that carries this task's claim: the pre-fix execution
retried three times on a deterministic configuration rejection and then failed;
the fixed release drains that same enqueued `listing.partner_feed` fetch on
attempt 1 and exits `0`. That is the deploy-gate-level counterpart of
`tests/ops/test_cloud_run_job_entrypoint.py::test_worker_drains_deselected_provider_fetch_without_retrying`.

### Evidence gap (bounded, disclosed)

`worker-validation.json` itself is not retrievable. `deploy-dev.yml` uploads
`.odp_data/deployment/*.json`, which is not recursive, so nothing under
`.odp_data/deployment/cloud-run-jobs/` survives the runner. `.github/**` is a
forbidden path for this task, so the glob is not widened here — it is left as a
follow-up (see §4). What is retrievable and is recorded above: the gcloud
execution-success line, the `Cloud Run worker Job smoke passed.` gate line (the
script is `set -e`, so the gate line cannot print unless every check above
passed), the release-derived job name, and the run-wide release SHA receipt
`expected_sha` / `version.release_sha = 93ae1b2e…` in the uploaded smoke report.

## 2. Why the run is still red: API/Web readiness, not the worker

The deploy stopped *after* the worker gate, at the release-aware API/Web smoke:

```
09:03:48 Running release-aware smoke checks against tagged candidate revisions...
09:03:52 Cloud Run live deployment smoke failed (fail-closed):
         - smoke:/platform/health:http: status=503
         - smoke:/readiness:http: status=503
         - smoke:/platform/health:live_data_mode: status=unhealthy data_mode=<missing>
         - smoke:/readiness:live_data_mode: status=unhealthy data_mode=<missing>
         - smoke:/platform/health:job_queue: missing or non-worker/in-memory job queue
         - smoke:/api/v1/operator/bootstrap:provenance: data_mode=degraded ...
         - smoke:/api/v1/operator/bootstrap:read_provenance: origin_kind=degraded ...
         - smoke:web:/operator: status=307 protected_redirect=false
```

The candidate API revision reports exactly **one** blocking reason
(`candidate-cloud-run-smoke-run-30436771086.json`, `readiness.details.data`):

```json
{"blockingReasons": ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"],
 "liveReady": false, "mode": "unavailable",
 "operatorRepositoryReady": true,
 "origin": {"kind": "authoritative", "persistenceMode": "postgresql"}}
```

`modes.models.mode = mlflow-production-unverified`,
`productionBindingsReady = false`, over the four required services:

| Service | `available` | `reasonCode` |
| --- | --- | --- |
| `avm` | false | `DATA_CONTRACT_NOT_MATURE` (governed-disabled, 0/120 eligible rows) |
| `forecastops` | false | `PRODUCTION_MODEL_REGISTRY_UNAVAILABLE` — `forecast_revenue_interval: configured MLflow registry has no production alias` |
| `heatzone` | false | `DATA_CONTRACT_NOT_MATURE` (governed-disabled, 0/200 eligible rows) |
| `sitescore` | false | `DATA_CONTRACT_NOT_MATURE` (governed-disabled) |

That reason chain is a platform model-readiness state owned outside this task
(`docs/evidence/PRODUCTION_MODEL_RISK_ACCEPTANCE_2026-07-25.md`,
`docs/evidence/PLATFORM_COMPLETENESS_INVENTORY_2026-07-25.md`), and it is
reached only after the worker gate is already green. Everything this task's
neighbourhood owns is healthy in the same payload: `database: healthy`,
`dependencies.job_queue: healthy`, all three required external providers probing
`http_status=200 / reason_code=ok`, `operatorRepositoryProbe.ready: true` on
`postgresql`, and `smoke:/platform/version:release_sha` matching the exact head.

Two of the eight failures are payload-shape gaps rather than unhealthy
dependencies, and are also not in this task's writable paths:

- `smoke:/platform/health:job_queue` — the smoke requires the dependency string
  to contain one of `worker` / `cloud` / `durable`; the API emits the bare word
  `healthy`, so a healthy queue reads as "missing or non-worker/in-memory".
- `smoke:web:/operator` — a `307` that the smoke does not classify as a
  protected redirect.

## 3. Fail-closed rollback preserved (acceptance criterion 6)

The failure path behaved exactly as the criterion requires — no candidate
traffic, recorded split restored:

```
09:03:52 Deployment failed; restoring the recorded API/Web traffic split.
09:03:52 Restoring oday-api traffic to oday-api-00005-gin=100...
09:03:54 Restoring oday-web traffic to oday-web-00008-ws4=100...
```

Post-restore traffic tables in the log show `100% oday-api-00005-gin` and
`100% oday-web-00008-ws4`, with `oday-api-release-93ae1b2e75e1` and
`oday-web-release-93ae1b2e75e1` both at `0%`. The candidate was only ever
reachable through its `candidate-93ae1b2e75e1056c` tag. Migration job, migration
compatibility smoke, and scheduler job all passed earlier in the same run
(`08:58:31`, `08:58:42`, `09:01:00`), so no preserved gate regressed.

## 4. Follow-ups (not done here — out of this task's writable paths)

1. `deploy-dev.yml` should upload `.odp_data/deployment/**/*.json` so the
   per-job `*-validation.json` receipts survive the runner. `.github/**` is
   forbidden for this task.
2. `PRODUCTION_MODEL_BINDINGS_UNVERIFIED` blocks every Deploy Dev run
   independently of this fix; it needs its own task on the model-readiness lane.
3. The API health payload should name the job-queue backend so
   `smoke:/platform/health:job_queue` stops failing on a healthy queue.
