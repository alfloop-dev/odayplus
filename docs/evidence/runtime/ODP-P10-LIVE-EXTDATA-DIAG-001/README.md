# ODP-P10-LIVE-EXTDATA-DIAG-001 — successful worker probes, zero persisted ingestion runs

Owner: Claude · Reviewer: Claude2 · Phase: Package10LiveClosure · 2026-08-09

Diagnose the contradiction in the Package 10 live-closure gate authority: the
live E2E gate's worker probe reaches `succeeded` on the first attempt, and the
authenticated readback of `GET /api/v1/external-data/ingestion-runs` reports
`runs=0` in the same gate execution.

**Verdict: a write/read tenant-partition split.** The gate enqueues its
`external-fetch` probes under the *deployment* tenant `tenant-dev`, the worker
persists the resulting `IngestionRunRecord`s into that tenant's document
partition, and the gate then reads back under the *smoke principal's* tenant,
which is a different partition. Both sides are healthy and both are doing
exactly what they were configured to do. No code patch is made in this task;
§7 is the exact T11 handoff.

This supersedes the mechanism recorded by the 2026-08-03 prior art
(`ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001`), which found the worker writing
under `tenant_id=''`. That path no longer exists at the deployed SHA — it is
now fail-closed — and the same symptom is produced by a *different*, provable
mechanism (§3).

---

## 1. The contradiction, from one primary artifact

Gate authority: Deploy Dev run
[31316767710](https://github.com/alfloop-dev/odayplus/actions/runs/31316767710),
job `deploy` (`93253511518`), head SHA
`9c95ecc3e1f2d0885bb4078070a116e852487f69`, gate generated `2026-08-09T14:07:16Z`,
correlation `corr-live-e2e-9c95ecc3e1f2-1786284436`, `ok=false`, 43/50 checks,
`blocking_dependencies=[external-data, mlflow]`.

Report artifact `cloud-run-dev-validation` → `live-e2e-gate.json`, preserved
here verbatim as `live-e2e-gate-run-31316767710.json`:

| Check | Result | Detail |
| --- | --- | --- |
| `worker:enqueue` | PASS | `status=202 jobStatus=queued auditEventId=present` |
| `worker:idempotent_replay` | PASS | `sameJob=True created=False` |
| `worker:drain_trigger` | PASS | `worker job oday-worker-r-9c95ecc3e1f2 execution completed` |
| `worker:terminal_success` | PASS | `status=succeeded attempts=1 error=none` |
| `worker:ingestion_probe:poi.commercial_api` | PASS | `status=succeeded attempts=1 error=none` |
| `data:ingestion_runs` | **FAIL** | `runs=0` |
| `data:admin_boundary.official_dataset:run_exists` | **FAIL** | no persisted ingestion run |
| `data:poi.commercial_api:run_exists` | **FAIL** | no persisted ingestion run |
| `runtime:provider_probe:*` (all three) | PASS | `reasonCode=ok` for each required provider |
| `data:no_surrogate_markers` | PASS | `none` |

Gate `worker` block: `job_id=06cb31de-f047-4a16-bc74-d97deb709a0e`,
`job_type=external-fetch`, `terminal_status=succeeded`,
`ingestion_probe_provider_ids=[admin_boundary.official_dataset, poi.commercial_api]`,
`worker_probe_provider_id=admin_boundary.official_dataset`.

`_check_worker_and_audit` runs *before* `_check_source_data`
(`scripts/e2e/check_live_e2e_gate.py`), so the two rows above describe the same
deploy, seconds apart: the probes that succeeded at 14:07 are precisely the runs
the readback could not find at 14:08.

The remaining four blockers are `mlflow` (`models:registry versions=0`,
`forecastops` production alias) and are owned by the model-readiness lane, not
by this task.

---

## 2. What the phenomenon is *not*

Each of these was checked and ruled out against the exact-SHA artifacts, so the
root cause in §3 is the only surviving explanation.

- **Not a failed or drained fetch.** `worker:terminal_success` reports
  `attempts=1`. `handle_external_fetch`
  (`apps/worker/oday_worker/handlers.py:92`) returns without raising in exactly
  two cases: the run is not `FAILED`, or the run is `FAILED` with
  `reason_code=provider_not_selected`. The second is impossible here —
  `worker-validation-run-31316767710.json` records
  `jobs-smoke:worker:provider_selection selected=admin_boundary.official_dataset,geocode.primary_api,poi.commercial_api`
  and `jobs-smoke:worker:selected_provider_release_match ok=true`, so both
  probed providers are on this release's allowlist. Any other `FAILED` reason
  code raises (`NonRetryableJobError` or `RuntimeError`) and the job could not
  have reached `succeeded` with one attempt. **A non-`FAILED`
  `IngestionRunRecord` was therefore persisted for both snapshot providers.**
- **Not a provider, credential, or connectivity fault.** All three
  `runtime:provider_probe:*` checks report `connectivityHealthy=True
  authenticated=True schemaValid=True reasonCode=ok`, and
  `jobs-smoke:worker:secret_bindings` confirms `ODAY_DATABASE_URL`,
  `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN`, `ODP_GEOCODE_PROVIDER_API_KEY`,
  `ODP_POI_PROVIDER_API_KEY` are all bound to Secret Manager references.
- **Not an authentication or RBAC gap on the readback.** A missing
  `integration:view` grant would have failed the request itself; the gate
  received a 200 with an empty `items` list (`_check_source_data` reports
  `runs=0` only on `status == 200`), and `auth:operator_bootstrap` +
  `auth:anonymous_denied` both pass.
- **Not a query filter.** The gate calls
  `GET /api/v1/external-data/ingestion-runs?limit=100` with no provider filter;
  the route (`apps/api/app/routes/external_data.py:172`) applies only that limit
  to whatever the resolved store returns.
- **Not the 2026-08-03 unscoped-write path.** `run_scheduled` now refuses an
  empty tenant with `ScheduledIngestionTenantError`
  (`modules/external_data/application/ingestion_service.py:337`), and
  `handle_external_fetch` dead-letters an untenanted payload before touching
  the service. Neither fired: the job succeeded.
- **Not a stale release.** Every commit in the tenant chain is an ancestor of
  the deployed SHA (§4).

---

## 3. Root cause: the probe writes and the readback reads two different tenant partitions

### 3.1 Write side — `tenant-dev`

1. `.github/workflows/deploy-dev.yml:101-102` sets, for the whole `deploy` job:
   `ODP_SCHEDULED_INGESTION_TENANT_ID: ${{ vars.ODP_SCHEDULED_INGESTION_TENANT_ID || 'tenant-dev' }}`
   and the same for `ODP_TENANT_ID`. The run log for job `93253511518` prints
   the resolved value 15 times across the job's steps —
   `ODP_SCHEDULED_INGESTION_TENANT_ID: tenant-dev` — so the repository variable
   is unset and the literal placeholder default is what the deployment actually
   ran with.
2. `scripts/deploy_cloud_run_waji.sh:665-674` invokes the gate with no
   `--operator-tenant`, so `GateConfig.operator_tenant` is `""`.
3. `check_live_e2e_gate._enqueue_body` (line 1315) therefore falls through to
   the environment: `config.operator_tenant or ODP_SCHEDULED_INGESTION_TENANT_ID
   or ODP_TENANT_ID or "tenant-e2e"` → **`tenant-dev`**, and puts it in the job
   payload.
4. `POST /api/v1/jobs` (`apps/api/oday_api/main.py:764`) rebinds
   `payload.tenant_id` to the authenticated tenant **only for
   `job_type == "forecast"`**. For `external-fetch` the caller's value is
   enqueued verbatim.
5. The worker reads `job.payload["tenant_id"]` (`handlers.py:114`), passes it to
   `ExternalIngestionService.run_scheduled(..., tenant_id="tenant-dev")`, which
   resolves `_resolve_store("tenant-dev")` → `ingestion_run_store_for_tenant`
   → a `TenantScopedDocumentStore`.

### 3.2 Read side — the smoke principal's tenant

`GET /api/v1/external-data/ingestion-runs` → `store_for_request(request)` →
`resolve_tenant_id(request)` (`apps/api/app/routes/external_data.py:75-111`),
which takes the tenant **only** from the verified principal's
`scope.tenant_id`. That value is a literal `tenant_id` claim carried by the
principal-map entry for the smoke service account
(`modules/opsboard/auth/claims.py:82`, map bound via
`ODP_AUTH_PRINCIPAL_MAP_SECRET`). An `x-tenant-id` header cannot widen or
redirect it: a header that differs from the principal scope is rejected 403
(line 106).

### 3.3 Why that yields exactly `runs=0`

`TenantScopedDocumentStore` does not filter a shared collection — it *renames*
it: `_collection()` returns `f"{collection}.tenant.{sha256(tenant_id)}"`
(`shared/infrastructure/persistence/operator_domains.py:18-42`). The base
collection is `external_data.ingestion_runs` in the `durable_documents` table.
So the two sides address disjoint partitions:

| Side | Tenant | Collection actually read/written |
| --- | --- | --- |
| gate probe → worker (write) | `tenant-dev` | `external_data.ingestion_runs.tenant.7c51172bedb79ef6b6d0d0eb675210470d2cc2e0a4947ab7221616199a9c01f6` |
| gate readback (read) | smoke principal's tenant, documented as `a11ce505-70bc-56d9-8564-ad22efa23c9e` | `external_data.ingestion_runs.tenant.da57d47ac40b5f8fa57ac349b3b1a154b3b64d4e807c142a1e9ba1bdef834b5b` |

(Partition names computed locally from the runtime's own
`sha256(tenant_id).hexdigest()`; they are opaque digests of non-secret tenant
ids.)

The proof does **not** depend on the exact readback tenant value. §2 establishes
that a non-`FAILED` run was persisted for both snapshot providers at 14:07, and
the artifact shows the readback returned zero items at 14:08. Two records
written and zero read back is only possible if the write partition and the read
partition differ — i.e. if the smoke principal's tenant is not `tenant-dev`.
The specific value `a11ce505-70bc-56d9-8564-ad22efa23c9e` is carried from the
2026-08-03 prior art and `docs/evidence/runtime/ODP-OPERATOR-SMOKE-RBAC-LIVE-002/`;
it was **not** re-verified live in this task (§6), so T11 must read the current
value from the principal-map secret rather than trust this document.

### 3.4 Where the misconfiguration actually is

`scripts/deploy_cloud_run_waji.sh:45-46` deliberately fails closed when neither
`ODP_SCHEDULED_INGESTION_TENANT_ID` nor `ODP_TENANT_ID` is set. The workflow's
`|| 'tenant-dev'` supplies a placeholder before the script can ever see the
unset state, so **that fail-closed guard cannot fire in CI** and a placeholder
became the dev deployment's canonical ingestion tenant on 2026-08-08. Every
other live surface in this deployment is scoped to the smoke principal's tenant
(`auth:operator_bootstrap:provenance data_mode=live
data_source=operator-shell-production` passes in the same report; the 2026-08-03
governed ingestion wrote there too). The ingestion tenant is the outlier.

---

## 4. Release / tenant / schema / store / provider correlation

| Parameter | Value | Source |
| --- | --- | --- |
| Release SHA | `9c95ecc3e1f2d0885bb4078070a116e852487f69` | gate `expected_release_sha`; `jobs-smoke:worker:release_sha` ok |
| Deploy Dev run / job | `31316767710` / `93253511518` | GitHub Actions API |
| Gate correlation id | `corr-live-e2e-9c95ecc3e1f2-1786284436` | `live-e2e-gate.json` |
| Probe job id | `06cb31de-f047-4a16-bc74-d97deb709a0e` | `live-e2e-gate.json` |
| Worker execution | `oday-worker-r-9c95ecc3e1f2-hm5jl` (receipt), drain `oday-worker-r-9c95ecc3e1f2` | worker validation receipt; `worker:drain_trigger` |
| Write tenant | `tenant-dev` | deploy job env, printed in run log |
| Read tenant | smoke principal `scope.tenant_id` from `ODP_AUTH_PRINCIPAL_MAP_SECRET` | `claims.py:82`; value per prior art, not re-verified here |
| Cloud SQL instance | `alfaloop-data-project:asia-east1:oday-dev-sql` | deploy job env `GCP_CLOUD_SQL_INSTANCE` |
| Table / collection | `durable_documents` / `external_data.ingestion_runs[.tenant.<sha256>]` | `DurableIngestionRunStore`, `operator_domains.py:41` |
| Required providers | `admin_boundary.official_dataset`, `geocode.primary_api`, `poi.commercial_api` | gate `inputs.required_provider_ids` |
| Snapshot-schedulable subset | `admin_boundary.official_dataset`, `poi.commercial_api` | gate `inputs.snapshot_provider_ids` |
| Secret env bindings | names only: `ODAY_DATABASE_URL`, `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN`, `ODP_GEOCODE_PROVIDER_API_KEY`, `ODP_POI_PROVIDER_API_KEY` | worker validation receipt |

Commit chain that introduced the current tenant semantics, all confirmed
ancestors of the deployed SHA via `git merge-base --is-ancestor`:

- `fcc9d4a0` (2026-08-08) `ODP-DEV-INGESTION-TENANT-SCHEDULER-001: anchor scheduled tenant path` — `run_scheduled` fail-closed on empty tenant; worker dead-letters untenanted payloads.
- `17f35834` (2026-08-08) same task, review round — adds `tenant_id` to the gate's probe payload **and** the `|| 'tenant-dev'` workflow defaults.
- `f7bd3d9b` (2026-08-08) `ODP-RUNTIME-CONFIG-CODE-CLOSEOUT-001`.

`9c95ecc3` is itself an ancestor of current `origin/dev` (`8eabc973`), so the
diagnosis applies to dev tip unchanged.

No secret value appears in this note or in either preserved artifact; both carry
`secret_values_redacted: true` and were scanned for token/bearer/password/URL
material before being committed.

---

## 5. Disposition of both required snapshot providers

| Provider | Probe | Persisted run | API-readable | Disposition |
| --- | --- | --- | --- | --- |
| `admin_boundary.official_dataset` | `worker:terminal_success` succeeded, attempts=1 (this is the `worker_probe_provider_id`) | yes — under `tenant-dev` | **no** | Blocked by the tenant split alone. No provider, credential, schema, or lineage defect is implicated. Cleared by the T11 alignment; no per-provider work needed. |
| `poi.commercial_api` | `worker:ingestion_probe:poi.commercial_api` succeeded, attempts=1 | yes — under `tenant-dev` | **no** | Identical. |

`geocode.primary_api` is required but not snapshot-schedulable and is correctly
exempt from the ingestion-run assertions (`docs/evidence/ODP_LIVE_E2E_GATE.md`,
*Required ≠ schedulable*); its liveness is carried by
`runtime:provider_probe:geocode.primary_api`, which passes.

---

## 6. Why there is no runtime-only closure in T10

Acceptance criterion 4 (runtime-only closure producing non-empty, succeeded,
lineage-complete, API-readable runs) is **not attainable within this task**, and
criterion 5 applies instead:

1. The task's own stop condition *"code or configuration edit required"* is hit.
   Every path to a green `data:*` needs either a deployment variable change, a
   principal-map secret change, or a code change — all T11 surfaces. T10's
   `writable_paths` is `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/**`
   only, with `apps/**`, `scripts/**`, `.github/**` forbidden.
2. Manually POSTing governed ingestion runs under the smoke principal (what the
   2026-08-03 prior art did) would not close the gate. The gate runs *inside*
   `deploy_cloud_run_waji.sh` before `DEPLOYMENT_COMMITTED=true` and creates its
   own probe runs each time; while the split stands, the next deploy writes to
   `tenant-dev` and reads the operator tenant again. Pre-seeded rows would mask
   the defect for one run, not close it.
3. This worker has no operator bearer token, and `gcloud` on this host has no
   usable credentials (`Reauthentication failed` on the active account; the
   compute service account lacks the required scopes), so no authenticated live
   call was possible even had it been in scope.

Everything done in this task was read-only: GitHub Actions API reads
(`gh run view`, `gh api .../jobs/93253511518/logs`,
`gh api .../artifacts/9039216001/zip`), repository reads, and local
`sha256`/`git merge-base` computation. **No database write, no live API
mutation, no fabricated row, and no synthetic or fixture data was produced.**

### Open sub-question (does not affect the root cause)

The 2026-08-03 governed runs recorded under the operator tenant by
`ODP-LIVE-REQUIRED-PROVIDER-INGESTION-001` did not appear in this readback
either — `runs=0` counts *everything* in that partition, not just the fresh
probes. Candidate explanations (a dev database reset or lifecycle purge between
2026-08-03 and 2026-08-09, a principal-map rotation changing the tenant claim,
or those rows having been written against a different environment) cannot be
separated without a direct read of `durable_documents`, which was out of scope
here. It changes nothing above: the gate creates its own runs before reading, so
the alignment in §7 makes `data:*` green regardless of what happened to the
older rows. T11 should confirm it while it has database access.

---

## 7. T11 handoff — exact scope

Hand to a task with write access to `.github/workflows/**`,
`apps/api/oday_api/main.py`, `scripts/e2e/check_live_e2e_gate.py`, deployment
variables, and the principal-map secret. Items 1–2 alone turn `data:*` green;
3–4 stop the class of defect from recurring silently.

1. **Align the ingestion tenant with the deployment's operator tenant
   (config, unblocks the gate).** Read the current `tenant_id` for the smoke
   service-account entry in `ODP_AUTH_PRINCIPAL_MAP_SECRET` (documented as
   `a11ce505-70bc-56d9-8564-ad22efa23c9e`; verify, do not assume), then set the
   dev repository/environment variables `ODP_SCHEDULED_INGESTION_TENANT_ID` and
   `ODP_TENANT_ID` to that value. Re-run Deploy Dev and require
   `data:ingestion_runs`, `data:admin_boundary.official_dataset:run_exists`,
   `data:poi.commercial_api:run_exists` green in `live-e2e-gate.json`.
   *Direction matters:* align the ingestion variable to the existing operator
   tenant, not the reverse. Repointing the principal map at `tenant-dev` would
   move every already-live operator surface in dev (bootstrap provenance,
   operator-shell data) into an empty partition. Moving ingestion the other way
   strands only the gate probe runs written on 2026-08-08/09, which are
   disposable. `admin_boundary.official_dataset` is the probe provider, so
   expect one real fetch window to be consumed per deploy (documented accepted
   side effect).
2. **Remove the placeholder default.** `.github/workflows/deploy-dev.yml:101-102`
   and `deploy-staging.yml:84-85` supply `|| 'tenant-dev'` / `'tenant-staging'`,
   which defeats the fail-closed guard at
   `scripts/deploy_cloud_run_waji.sh:45-46`. Pass the variables through unset so
   an unconfigured deployment fails closed instead of inventing a tenant.
3. **Bind the `external-fetch` enqueue tenant to the authenticated principal.**
   In `POST /api/v1/jobs` (`apps/api/oday_api/main.py:764`), apply to
   `external-fetch` the rule `forecast` already has: reject a supplied
   `payload.tenant_id` that differs from the authenticated tenant with
   `TENANT_SCOPE_MISMATCH`, and rewrite it from the principal otherwise. Today
   any `job:execute` caller can direct canonical ingestion into an arbitrary
   tenant partition and get a 202 — the gate is simply the first caller to
   notice, via a failure it could not name. The deployed Cloud Scheduler path
   enqueues straight into the durable queue, not through this route, so it is
   unaffected.
4. **Stop the gate from guessing.** With item 3 in place, drop the
   `ODP_SCHEDULED_INGESTION_TENANT_ID` / `ODP_TENANT_ID` / `"tenant-e2e"`
   fallback chain in `check_live_e2e_gate._enqueue_body` (line 1315) so the
   probe cannot write anywhere the same credential cannot read. If the payload
   must keep a tenant field, source it from the authenticated identity the gate
   already holds.

Suggested regression coverage: a test asserting `POST /api/v1/jobs` refuses an
`external-fetch` payload whose `tenant_id` differs from the authenticated
principal; and a gate test asserting the enqueue tenant equals the readback
tenant, so this split cannot be reintroduced by an environment variable.

---

## 8. Artifacts

- `live-e2e-gate-run-31316767710.json` — full gate report, 50 checks, verbatim
  from artifact `cloud-run-dev-validation` of run 31316767710.
- `worker-validation-run-31316767710.json` — worker Cloud Run Job smoke receipt
  for the same run (`ok=true`, release SHA bound, provider selection, secret
  bindings by name).
