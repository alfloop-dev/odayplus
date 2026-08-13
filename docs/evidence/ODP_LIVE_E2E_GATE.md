# ODay Plus Live E2E Gate

`delivery_toolchain/e2e/check_live_e2e_gate.py` is the post-deployment proof that the
**promoted release actually works as a product**: an operator can authenticate,
the runtime is bound to live PostgreSQL / GCS / providers, every required MLflow
`production` alias resolves, real source rows carry complete lineage, the worker
drains durable work to a terminal success, and the audit receipt survives the
request that produced it.

## Where it sits

Three gates, three different questions:

| Gate | Question |
| --- | --- |
| `product_ops/deployment/validate_cloud_run_live_deployment.py` | Is the *deployment topology* correct (preflight config, image signing, job receipts, candidate smoke)? |
| `delivery_toolchain/e2e/check_live_production_data.py` | Is the *data plane* real (direct PostgreSQL reconciliation against a commit-bound evidence manifest)? |
| `delivery_toolchain/e2e/check_live_e2e_gate.py` | Does the *product path through the promoted release* work end to end over HTTP? |

The live E2E gate runs inside `product_ops/deployment/deploy_cloud_run_waji.sh` **after**
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

   `deploymentMode` binds the served runtime to the env *this* deploy
   configured. `apps/api/oday_api/runtime_mode.deployment_mode()` reads the
   `ODP_DEPLOY_ENV`/`ODAY_ENV`/`ODP_ENV` triple that
   `product_ops/deployment/deploy_cloud_run_waji.sh` writes into the API env payload, so a dev
   deploy reports `deploymentMode=dev`. The deploy script therefore passes
   `--expected-deployment "${ODP_LIVE_E2E_DEPLOYMENT_MODE:-${ODP_DEPLOY_ENV}}"`
   and the gate has **no** default for it (`config:expected_deployment` blocks
   when it is empty). It is not the live-ness assertion — `requireLiveData`,
   the persistence/provider/model checks, and the surrogate-marker scans carry
   that; `ODP_PRODUCT_MODE=production` is what makes a dev deploy a
   production-*mode* runtime. Defaulting this to `production` is the bug that
   made every dev deploy promote and then roll straight back.
3. **Authentication** — `GET /api/v1/operator/bootstrap` **without** credentials
   must be rejected with 401/403. An anonymous 200 blocks the release. The same
   route with the operator bearer token and role must return 200 with live
   provenance. `/operator` on the web origin must redirect to
   `/login?...returnTo=`. The web origin is **not** optional: an empty or
   unusable `--web-url` blocks (`config:web_url`,
   `auth:web_operator_requires_login`) rather than silently dropping the
   assertion while the gate still reports `ok`. The deploy script resolves both
   origins into variables before the gate invocation, because a failing
   `$( )` expanded directly into argv would yield an empty argument without
   tripping `set -e`.
4. **Model lineage and MLflow aliases** — `GET /api/v1/learninghub/models` must
   expose exactly one version carrying the `production` alias for each of
   `dealroom_avm`, `forecast_revenue_interval`, `heatzone_priority`, and
   `sitescore_propensity`, each with a dataset snapshot, feature schema version,
   recorded approval, and an object-store artifact URI (`gs://`, `s3://`,
   `https://`, `mlflow-artifacts:`). A missing alias blocks.
5. **Per-provider live connectivity** — `/readiness`
   `details.provider.probeEvidence.probes[*]` must carry, for **every** required
   provider id, a probe with `connectivity_healthy`, `authentication_accepted`,
   `response_valid`, `schema_valid` and `reason_code=ok`. These are real
   authenticated upstream calls (`modules/external_data/connectors/provider_connectivity.py`);
   the geocode probe POSTs an address and range-validates the returned
   coordinates. The aggregate `connectivityHealthy` boolean alone cannot carry a
   broken provider — a missing probe entry blocks.
6. **Real source rows and lineage** — `GET /api/v1/external-data/ingestion-runs`
   must have a succeeded run for every **snapshot-schedulable** required
   provider id, with `accepted + quarantined == total`, `total > 0`, one lineage
   row per source record, accepted lineage matching the accepted count, complete
   provenance on every row, and a canonical snapshot binding. See
   *Required ≠ schedulable* below for why the requirement is not the full
   required set.
7. **Worker and durable audit receipts** — the gate enqueues a real
   `external-fetch` job through `POST /api/v1/jobs` with an `Idempotency-Key`,
   replays it (which must return the same `job_id` with `created=false`),
   optionally triggers the worker Cloud Run Job, then polls
   `GET /api/v1/jobs/{job_id}` until the job reaches `succeeded`. It then reads
   `GET /api/v1/audit/events?correlation_id=...` back and requires the
   `job.enqueue` receipt for that job, the `idempotent_replay` receipt, and a
   hash-chained integrity envelope.
8. **No surrogates anywhere** — every response body is scanned with the marker
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
| `--web-url` | promoted Web service URL (`ODP_LIVE_E2E_WEB_URL`), **required** |
| `--expected-sha` | `ODAY_RELEASE_SHA` |
| `--expected-deployment` | `ODP_LIVE_E2E_DEPLOYMENT_MODE` var, else `ODP_DEPLOY_ENV`. **No default** |
| operator bearer token | `ODP_OPERATOR_SMOKE_BEARER_TOKEN` (read from env, never printed) |
| `--operator-role` | `ODP_OPERATOR_SMOKE_ROLE` |
| required provider ids | `--required-provider` (repeatable) or `ODP_PRODUCTION_PROVIDER_IDS` |
| `--worker-job` / `--gcp-region` / `--gcp-project` | optional Cloud Run worker trigger; without them the gate waits for the scheduled worker |
| `--worker-deadline-seconds` | `ODP_LIVE_E2E_WORKER_DEADLINE_SECONDS` repo var (default 600) |

The bearer token is redacted from the report and from stdout. The API origin
must be a credential-free HTTPS origin on a non-example host; `--allow-http` is
only for an explicitly controlled non-production target.

### Grants the smoke principal must carry

`--operator-role` (`X-Operator-Role`) only *selects an Operator Console persona*
among the grants a principal already has; it cannot widen them
(`apps/api/oday_api/security/dependencies.py:_select_operator_role`). The
authorization the gate actually needs comes from the platform roles on the smoke
bearer token, and the routes it calls do not share one role
(`shared/auth/rbac.py`):

| Route the gate calls | Required permission | Roles granting it |
| --- | --- | --- |
| `GET /api/v1/operator/bootstrap` | `operator_console:view` | `operations_manager`, `regional_supervisor`, `expansion_user`, … |
| `GET /api/v1/learninghub/models` | `model:view` | `model_owner`, `release_owner` |
| `GET /api/v1/external-data/ingestion-runs` | `integration:view` | `data_owner` |
| `GET /api/v1/audit/events` | `audit:view` | `data_owner`, `model_owner`, `operations_manager`, … |

So `ODP_OPERATOR_SMOKE_BEARER_TOKEN` must be issued to a principal holding
**several** roles (e.g. `operations_manager,model_owner,data_owner`). A
single-role token makes the gate fail on `models:registry` or
`data:ingestion_runs`; both now report the `auth` dependency rather than
`mlflow`/`external-data`, so the failure names the credential to widen instead
of sending an operator to republish an MLflow alias that already exists.

## Invocation

```bash
export ODP_OPERATOR_SMOKE_BEARER_TOKEN="<operator smoke token>"

python3 delivery_toolchain/e2e/check_live_e2e_gate.py \
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

## Provenance spellings the runtime actually emits

A fail-closed gate is only useful if a healthy deployment can pass it. Two
provenance fields are spelled differently by the two surfaces that publish them,
and both were verified against the runtime source rather than against the test
doubles:

| Surface | Field | Healthy value |
| --- | --- | --- |
| `GET /readiness` | `details.data.origin.kind` | `authoritative` — the readiness probe publishes `OperatorLiveRepository.data_origin` verbatim (`apps/api/oday_api/main.py`, `modules/opsboard/application/operator_live_repository.py`) |
| `GET /api/v1/operator/bootstrap` | `meta.dataOrigin.kind` | `live` — `OperatorStateService._build_envelope` rewrites `kind` to the resolved data mode |
| `GET /api/v1/operator/bootstrap` | `meta.dataMode` | `live` — the envelope declares its mode under `meta`, not at the top level |

So the gate accepts `authoritative` **or** `live` for an origin kind, and reads
the declared data mode from `modes`/`details` (readiness) **or** `meta`
(envelope). The surrogate spellings (`fixture`, `r4-seed`) and the degraded
spelling (`unavailable`) still block.

## Required ≠ schedulable (why the ingestion-run set is narrower)

Being *required in live mode* and being *able to produce an ingestion run* are
two different facts. Conflating them made an earlier revision of this gate
unpassable against a healthy deployment, so the distinction is now explicit.

`ExternalIngestionService` is the only writer of the queryable
`IngestionRunRecord`s that `GET /api/v1/external-data/ingestion-runs` serves; it
persists them to `PersistenceBundle.ingestion_run_store`
(`external_data.ingestion_runs`). It composes `ExternalFetchScheduler.run_once`,
which owns window idempotency and the watermark and writes only
`external_data.fetch_runs`.

That distinction used to be a hole: `handle_external_fetch` drove
`ExternalFetchScheduler` directly, so the deployed path
(Cloud Scheduler → `external-fetch` job → worker) advanced watermarks without
ever writing an ingestion run. The only writer reachable in a deployed
environment was the manual `POST /external-data/ingestion-runs`, which means this
gate's ingestion-run assertions could not pass on any environment where nobody
had POSTed by hand. `handle_external_fetch` now goes through
`ExternalIngestionService.run_scheduled`, so the scheduled path and the manual
path write the same record to the same store.

One cost this rerouting inherits: `ExternalIngestionService.__init__` calls
`_rehydrate()`, which reads every persisted run (`list_all`) to re-seed the
scheduler's watermark state, so the worker now pays that scan once per
`external-fetch` job instead of the API paying it once per process. The store's
scan-based lookups (`_rehydrate`, `get_by_window_key`, `get_by_api_key`) are
pre-existing and shared with the manual POST path; narrowing them is a separate
change to `DurableIngestionRunStore`, not to this wiring.

The rerouting also changes what the product E2E stack observes, and that is the
point. `infra/docker/docker-compose.e2e.yml` runs a worker container that drives
both `ODayScheduler` and `ODayWorker`, so its `external-fetch` job now persists a
real `IngestionRunRecord` for `listing.partner_feed`. `GET /external-data/freshness`
serves a hardcoded fixture entry (`snap-expansion-20260628-0100`, echoing the
*reader's* correlation id) only while the store is empty, so that fallback used
to be the only thing the expansion spec ever saw. It now sees the ingested
snapshot (`listing-2026-06-26`) instead — whichever one, decided by container
start-up timing. `delivery_toolchain/e2e/seed_product_e2e_data.py` therefore waits for
`availability.source == "persisted"` before Playwright starts, and
`tests/e2e/e2e-expansion-product.spec.ts` asserts that persisted evidence and
cross-checks it against the ingestion run that produced it. If the scheduled
worker path stops writing ingestion runs, seeding fails with that diagnosis
rather than silently reverting to the fixture.

`run_once` refuses any provider whose registry category is outside
`scheduled_fetch._SCHEDULABLE_CATEGORIES` (`listing`, `poi`, `admin_boundary`)
with `provider_not_schedulable`:

| Required provider | Category | Ingestion run possible? | Liveness proven by |
| --- | --- | --- | --- |
| `admin_boundary.official_dataset` | `admin_boundary` | yes | persisted run **and** readiness probe |
| `poi.commercial_api` | `poi` | yes | persisted run **and** readiness probe |
| `geocode.primary_api` | `geocode` | **no** | readiness probe (real POST + coordinate validation) |

`geocode.primary_api` is an address-lookup enrichment source, not a snapshot
schedule, so a `SUCCEEDED` geocode ingestion run cannot exist in *any*
environment. Requiring one would fail-close the gate on every deploy — and
because the gate runs under `set -e` before `DEPLOYMENT_COMMITTED=true`, that
would promote and then roll back every release.

Consequences encoded in the gate:

- The gate produces the evidence it asserts. `_check_worker_and_audit` runs
  **before** `_check_source_data` and enqueues one `external-fetch` job per
  entry in `config.snapshot_provider_ids` (the lifecycle/idempotency/audit
  assertions ride on `config.probe_provider_id`; the rest are asserted terminal
  via `worker:ingestion_probe:<provider_id>`). Only then does the gate read
  `GET /api/v1/external-data/ingestion-runs` back. The deployed Cloud Scheduler
  cron only ever enqueues `listing.partner_feed`
  (`apps/scheduler/oday_scheduler/main.py`), so without this the required
  snapshot providers would have no run on a fresh deployment.
- `CloudRunWorkerDriver` is constructed with
  `max_jobs = len(snapshot_provider_ids) + 4`, so the single drain can clear
  every job the gate enqueued plus anything the cron queued meanwhile.
- `_check_source_data` iterates `config.snapshot_provider_ids`, not
  `required_provider_ids`.
- `config:snapshot_providers` blocks if the required set contains *no*
  schedulable provider, so the run assertions can never pass vacuously.
- The `external-fetch` worker probe picks its provider from the schedulable
  subset explicitly (`config:worker_probe_provider`); the old
  `required_provider_ids[0]` default only worked by alphabetical luck.
- `PROVIDER_CATEGORIES` / `SNAPSHOT_SCHEDULABLE_CATEGORIES` mirror the runtime
  registry (pinned, not imported, because a release gate must not import runtime
  code from the artefact it is judging). `config:provider_registry_known` blocks
  on an unclassified required id, and the anti-drift suite binds both constants
  back to `provider_registry()` × `_SCHEDULABLE_CATEGORIES`.

### Accepted operational side effect

The worker probe enqueues a real `external-fetch` job under
`schedule_id=live-e2e-gate`. `last_success_watermark` is keyed by `provider_id`
only, so a successful probe advances the watermark for the probed provider and
the next scheduled run for it starts from the gate's window. That is correct
incremental-fetch semantics rather than data loss — but it does mean each deploy
consumes one real fetch window for that provider, which is the price of a
genuinely non-mock gate. Point `--worker-probe-provider` at a different
schedulable provider if that window is contended.

### Known latent defect in a neighbouring gate (not fixed here)

`delivery_toolchain/e2e/check_live_production_data.py` asserts
`origin.get("kind") == "live"` against the same `/readiness` surface, which the
runtime never emits there. That gate is not wired into any deploy workflow, so
it blocks nothing today, but it would fail closed on the wrong dependency the
first time it is run against a healthy live deployment. Flagged for its owner
rather than loosened here, because it is a different task's deliverable.

## Tests

`tests/e2e/test_live_e2e_gate.py` starts from a fully live, fully passing
deployment and breaks exactly one runtime fact per test, asserting both that the
gate fails and that it names the dependency an operator would repair.
`tests/ops/test_cloud_run_live_deployment.py` pins the gate's position in the
deploy script between traffic promotion and release commit.

Because doubles that merely restate the gate's own assumptions can stay green
while the gate is unable to pass against the deployed runtime, the suite also
carries anti-drift contract tests bound to the runtime itself:

- the origin kind emitted by `OperatorLiveRepository`, the `meta.dataMode` key
  the operator envelope writes, every API path the gate calls, the
  `external-fetch` job type registration in
  `apps/worker/oday_worker/handlers.py`, the audit integrity envelope in
  `shared/audit/events.py`, and the `/login?returnTo=` redirect in
  `apps/web/src/middleware.ts`;
- `PROVIDER_CATEGORIES` == `{p.provider_id: p.category for p in provider_registry()}`
  and `SNAPSHOT_SCHEDULABLE_CATEGORIES` == `_SCHEDULABLE_CATEGORIES`, plus
  `DEFAULT_REQUIRED_PROVIDER_IDS` == `REQUIRED_PRODUCTION_PROVIDER_IDS`;
- a **behavioural** binding that calls the real scheduler guard and asserts it
  raises `provider_not_schedulable` for every provider the gate exempts from the
  ingestion-run requirement, and accepts every provider it does require a run
  for — so the exemption can never drift into an excuse;
- the probe-evidence checks are run against a **real** `/readiness` response
  from a booted `create_app()` with a stubbed connectivity probe, so a key
  rename in `ProviderProbeEvidence.to_dict` or in the readiness handler fails
  the suite instead of passing against a hand-written fixture;
- `ingestion_payload()` derives its provider list from the runtime registry
  rather than from the gate's constants, so a fixture cannot fabricate a
  `SUCCEEDED` run the runtime is structurally incapable of producing.

Each of these was verified by fault injection: re-introducing the original
defect (requiring an ingestion run for every required provider) turns the
happy-path test red, as does renaming `probeEvidence` in the readiness handler,
mis-classifying `geocode.primary_api` in the pinned registry mirror, moving the
URL resolution back into the gate's argv, and restoring the silent web-check
skip.

## Verification record

Run on `task/ODP-LIVE-E2E-001` before review handoff:

```
uv run pytest tests/e2e/test_live_e2e_gate.py tests/ops/test_cloud_run_live_deployment.py -q
# 89 passed

uv run ruff check delivery_toolchain/e2e/check_live_e2e_gate.py \
  tests/e2e/test_live_e2e_gate.py tests/ops/test_cloud_run_live_deployment.py
# All checks passed!

git diff --check origin/dev...HEAD
# clean
```

The `Required ≠ schedulable` claim was also confirmed directly against the
runtime rather than only through the suite's doubles:

```
uv run python -c "from modules.external_data.connectors.provider_registry import provider_registry; \
from modules.external_data.workers.scheduled_fetch import _SCHEDULABLE_CATEGORIES; \
print([(p.provider_id, p.category in _SCHEDULABLE_CATEGORIES) for p in provider_registry()])"
```

which reports `geocode.primary_api` and `competitor.manual_source` as the only
non-schedulable providers. Of the required set, the snapshot subset is
`admin_boundary.official_dataset` and `poi.commercial_api` — non-empty, so the
ingestion-run assertions cannot pass vacuously — and `geocode.primary_api` is
the single exemption, carried by `runtime:provider_probe:*`.
