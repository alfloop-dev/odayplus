# ODay Plus Live E2E Gate

`scripts/e2e/check_live_e2e_gate.py` is the post-deployment proof that the
**promoted release actually works as a product**: an operator can authenticate,
the runtime is bound to live PostgreSQL / GCS / providers, every required MLflow
`production` alias resolves, real source rows carry complete lineage, the worker
drains durable work to a terminal success, and the audit receipt survives the
request that produced it.

## Where it sits

Three gates, three different questions:

| Gate | Question |
| --- | --- |
| `scripts/deployment/validate_cloud_run_live_deployment.py` | Is the *deployment topology* correct (preflight config, image signing, job receipts, candidate smoke)? |
| `scripts/e2e/check_live_production_data.py` | Is the *data plane* real (direct PostgreSQL reconciliation against a commit-bound evidence manifest)? |
| `scripts/e2e/check_live_e2e_gate.py` | Does the *product path through the promoted release* work end to end over HTTP? |

The live E2E gate runs inside `scripts/deploy_cloud_run_waji.sh` **after**
`promote_service_traffic` and **before** `DEPLOYMENT_COMMITTED=true`. That
placement is the fail-closed contract: the gate exercises exactly the revision
users will get, and a non-zero exit falls through the script's `EXIT` trap so
`rollback_release_traffic` and `restore_scheduler_trigger` put the previous
release back.

## What it asserts

Every assertion is made against the deployed HTTP surface, bound to one exact
release SHA.

1. **Release binding** — `/platform/version` reports the expected 40-hex
   `release_sha`. Drift blocks.
2. **Runtime dependencies** — `/readiness` must report `requireLiveData`,
   `deploymentMode`, PostgreSQL persistence (durable + reachable +
   production-supported), live and healthy providers, `mlflow-production` model
   bindings with `autoSeeded=false`, a live operator data origin, and an empty
   `blockingReasons` list.
3. **Authentication** — `GET /api/v1/operator/bootstrap` **without** credentials
   must be rejected with 401/403. An anonymous 200 blocks the release. The same
   route with the operator bearer token and role must return 200 with live
   provenance. When a web origin is configured, `/operator` must redirect to
   `/login?...returnTo=`.
4. **Model lineage and MLflow aliases** — `GET /api/v1/learninghub/models` must
   expose exactly one version carrying the `production` alias for each of
   `dealroom_avm`, `forecast_revenue_interval`, `heatzone_priority`, and
   `sitescore_propensity`, each with a dataset snapshot, feature schema version,
   recorded approval, and an object-store artifact URI (`gs://`, `s3://`,
   `https://`, `mlflow-artifacts:`). A missing alias blocks.
5. **Real source rows and lineage** — `GET /api/v1/external-data/ingestion-runs`
   must have a succeeded run for every required provider id, with
   `accepted + quarantined == total`, `total > 0`, one lineage row per source
   record, accepted lineage matching the accepted count, complete provenance on
   every row, and a canonical snapshot binding.
6. **Worker and durable audit receipts** — the gate enqueues a real
   `external-fetch` job through `POST /api/v1/jobs` with an `Idempotency-Key`,
   replays it (which must return the same `job_id` with `created=false`),
   optionally triggers the worker Cloud Run Job, then polls
   `GET /api/v1/jobs/{job_id}` until the job reaches `succeeded`. It then reads
   `GET /api/v1/audit/events?correlation_id=...` back and requires the
   `job.enqueue` receipt for that job, the `idempotent_replay` receipt, and a
   hash-chained integrity envelope.
7. **No surrogates anywhere** — every response body is scanned with the marker
   vocabulary owned by `check_live_production_data.py` (imported, not copied, so
   the two gates cannot drift). A `fixture`, `mock`, `synthetic`, `seed`,
   `demo`, `sample`, `fake`, `memory`, or `sqlite` marker in any live response
   blocks.

## Failure output

Failures are grouped by the runtime dependency that owns them, and each carries
the next action:

```
Live E2E gate failed. Blocking runtime dependencies:
* mlflow: Publish/approve the MLflow model versions and point the 'production' alias at them (MLFLOW_TRACKING_URI registry).
  - models:heatzone:production_alias: model=heatzone_priority versionsWithProductionAlias=0 (exactly one required)
* worker: Restore the worker runtime: the Cloud Run worker job and its Cloud Scheduler trigger must drain the durable queue to a terminal state.
  - worker:terminal_success: status=queued attempts=0 error=none deadline=600.0s
```

The dependency vocabulary is `config`, `release`, `api-runtime`, `auth`,
`postgresql`, `object-store`, `provider`, `mlflow`, `external-data`, `worker`,
`audit`, `data-binding`.

## Inputs

| Input | Source |
| --- | --- |
| `--api-url` | promoted API service URL (`ODP_LIVE_E2E_API_URL`) |
| `--web-url` | promoted Web service URL (`ODP_LIVE_E2E_WEB_URL`), optional |
| `--expected-sha` | `ODAY_RELEASE_SHA` |
| operator bearer token | `ODP_OPERATOR_SMOKE_BEARER_TOKEN` (read from env, never printed) |
| `--operator-role` | `ODP_OPERATOR_SMOKE_ROLE` |
| required provider ids | `--required-provider` (repeatable) or `ODP_PRODUCTION_PROVIDER_IDS` |
| `--worker-job` / `--gcp-region` / `--gcp-project` | optional Cloud Run worker trigger; without them the gate waits for the scheduled worker |
| `--worker-deadline-seconds` | `ODP_LIVE_E2E_WORKER_DEADLINE_SECONDS` repo var (default 600) |

The bearer token is redacted from the report and from stdout. The API origin
must be a credential-free HTTPS origin on a non-example host; `--allow-http` is
only for an explicitly controlled non-production target.

## Invocation

```bash
export ODP_OPERATOR_SMOKE_BEARER_TOKEN="<operator smoke token>"

python3 scripts/e2e/check_live_e2e_gate.py \
  --api-url "https://<promoted-api-origin>" \
  --web-url "https://<promoted-web-origin>" \
  --expected-sha "<deployed commit SHA>" \
  --operator-role "<operator role>" \
  --worker-job "<worker cloud run job>" \
  --gcp-region "<region>" \
  --gcp-project "<project>" \
  --output .odp_data/deployment/live-e2e-gate.json
```

Missing inputs return exit code `1` before any request is issued, so an
unconfigured gate can never be mistaken for a passing one.

## Tests

`tests/e2e/test_live_e2e_gate.py` starts from a fully live, fully passing
deployment and breaks exactly one runtime fact per test, asserting both that the
gate fails and that it names the dependency an operator would repair.
`tests/ops/test_cloud_run_live_deployment.py` pins the gate's position in the
deploy script between traffic promotion and release commit.
