# ODP-P10-LIVE-EXTDATA-REMEDIATE-001 — bind the external-fetch ingestion tenant to the authenticated principal

Owner: Claude · Reviewer: Antigravity6 · Phase: Package10LiveClosure · 2026-08-11

Corrects the external-data persistence/readback defect proven by
`ODP-P10-LIVE-EXTDATA-DIAG-001` (T10): the live E2E gate's worker probe reached
`succeeded` on the first attempt while the authenticated readback of
`GET /api/v1/external-data/ingestion-runs` reported `runs=0` in the same gate
execution, because the probe *wrote* one tenant partition and the gate *read*
another.

**Fix: `POST /api/v1/jobs` no longer takes the ingestion tenant from the
caller.** For `external-fetch` it binds the tenant from the authenticated
principal — the rule `forecast` already followed — so the worker's write and the
API's readback resolve to one durable run by construction, not by deployment
configuration agreeing with a secret.

This implements items **3 and 4** of the T10 handoff
(`docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/README.md` §7). Items 1
and 2 are deliberately **not** shipped here; §5 explains why, and why the gate
goes green without them.

---

## 1. What changed

| # | Surface | Change |
| --- | --- | --- |
| 3 | `apps/api/oday_api/main.py` | `external_fetch_job_tenant()` resolves the enqueue tenant from the verified principal. `enqueue_job` applies it to `job_type == "external-fetch"`: a payload `tenant_id` that differs from the authenticated tenant is refused `403 TENANT_SCOPE_MISMATCH`; otherwise the payload tenant is rewritten from the principal. Anonymous → `401 AUTHENTICATION_REQUIRED`; missing `integration:create` → `403 EXTERNAL_FETCH_CREATE_FORBIDDEN`; principal with no tenant claim → `403 TENANT_SCOPE_REQUIRED`. |
| 3 | `apps/api/oday_api/main.py` | The queue idempotency key is tenant-qualified for `external-fetch` (`external-fetch:v1:<tenant>:<key>`), so two tenants sharing one key are not collapsed into one job. The `forecast:v1:` key format is unchanged byte-for-byte. |
| 4 | `delivery_toolchain/e2e/check_live_e2e_gate.py` | `_enqueue_body` drops the `ODP_SCHEDULED_INGESTION_TENANT_ID` → `ODP_TENANT_ID` → `"tenant-e2e"` fallback chain. It omits `tenant_id` entirely and lets the API bind it. An explicit `--operator-tenant` is still sent, so a stale override fails loudly instead of writing somewhere unreadable. |
| — | `tests/integration/test_external_fetch_enqueue_tenant_binding.py` | New, 11 tests (§2). |

`.github/workflows/**`, `deploy_cloud_run_waji.sh`, provider code, the ingestion
service, and the worker handler are **unchanged**.

### Why this is the smallest fix

The split had three candidate repair points: the deployment variable, the
principal-map secret, and the enqueue route. Only the route removes the failure
*mode* rather than one instance of it. Before this change, any `job:execute`
caller could direct canonical ingestion into an arbitrary tenant partition and
receive a `202`; the gate was simply the first caller to notice, via a failure
it could not name. Aligning the variable would have made the two tenants match
today and left the next mismatch just as silent.

## 2. Deterministic regression

`tests/integration/test_external_fetch_enqueue_tenant_binding.py`, on the
**durable** bundle (`_durable_bundle`) — the in-memory bundle only *tags* runs
with a tenant, so the partition rename that causes the defect exists only there.

| Test | Pins |
| --- | --- |
| `test_foreign_enqueue_tenant_reproduces_worker_success_with_no_api_readable_run` | **The defect.** Queue carries the pre-fix payload (caller's tenant, verbatim) → worker `run_once()` is `True`, job `SUCCEEDED`, run persisted under `tenant-dev` → operator credential's readback returns `[]`. Worker success with a missing API run, reproduced. |
| `test_enqueue_without_a_tenant_writes_where_the_same_credential_reads` | **The fix.** Enqueue with no tenant → route supplies the principal's → worker succeeds → readback returns the run; exactly one run exists, in the caller's partition, none in `tenant-dev`. |
| `test_matching_tenant_on_the_payload_is_accepted` | An explicit correct tenant stays a `202` (redundant, not wrong). |
| `test_foreign_tenant_on_the_payload_is_refused_and_enqueues_nothing` | The exact request the gate used to send → `403 TENANT_SCOPE_MISMATCH`, nothing queued, both partitions still empty. |
| `test_anonymous_enqueue_is_unauthenticated` | `401 AUTHENTICATION_REQUIRED`. |
| `test_principal_without_the_integration_create_grant_is_forbidden` | `integration:view` alone reads runs, cannot create them. |
| `test_principal_without_a_tenant_scope_is_forbidden` | No tenant claim → refuse, never guess. |
| `test_idempotent_replay_returns_the_same_job_for_one_tenant` | The gate's `worker:idempotent_replay` probe still sees `sameJob=True created=False`. |
| `test_two_tenants_sharing_one_idempotency_key_are_not_collapsed` | Tenant B's probe is never answered with tenant A's job. |
| `test_gate_enqueue_body_omits_the_tenant_instead_of_guessing` | With both deployment variables set to an unreadable tenant, the probe body carries no tenant at all. |
| `test_gate_still_sends_an_explicit_operator_tenant` | `--operator-tenant` is asserted, not silently dropped. |

**Pre-fix behaviour verified, not assumed.** The two code edits were reverted
(`git checkout --`), the suite re-run, and the fix re-applied:

```
7 failed, 4 passed   # fix reverted
11 passed            # fix applied
```

The 7 failures include `test_enqueue_without_a_tenant_writes_where_the_same_credential_reads`.
`test_foreign_enqueue_tenant_reproduces_worker_success_with_no_api_readable_run`
passes in **both** states by design: it pins the partition mechanism, which this
task does not change and which must stay covered.

## 3. Verification

Run at `755a72aa` (code) on this branch:

```
tests/integration/test_external_fetch_enqueue_tenant_binding.py     11 passed
tests/integration/test_external_ingestion_persistence.py    ┐
tests/integration/test_scheduled_ingestion_tenant_propagation.py │
tests/integration/test_forecastops_tenant_runtime_contract.py    ├ 64 passed
tests/reliability/test_cross_flow_gate.py                        │
tests/integration/test_worker_scheduler_runtime.py          ┘
tests/e2e/test_live_e2e_gate.py                                   132 passed
ruff check apps/api/oday_api/main.py delivery_toolchain/e2e/check_live_e2e_gate.py \
           tests/integration/test_external_fetch_enqueue_tenant_binding.py
                                                                  All checks passed
```

`ruff format` is not enforced by CI (`.github/workflows/ci.yml` runs `ruff
check` only). The new test file is `ruff format`-clean; the two edited files
carry exactly the same pre-existing format drift as `origin/dev` (33 and 67
lines respectively, measured against the base blobs), so this change adds none.

Acceptance criterion 4 ("retry idempotency, DQ, tenant audit and failure
classification remain fail closed") is carried by the suites above:
`test_scheduled_ingestion_tenant_propagation.py` alone re-asserts the
scheduler's fail-closed tenant handling, cross-tenant replay refusal, the
audit-event tenant, and dead-lettering of untenanted payloads — all unchanged.

## 4. Rollback

Single revert of the task commit(s); no migration, no data change, no
configuration change to undo.

- **State touched:** none. The change is request-path only. No row was written,
  altered, or moved by this task, in dev or anywhere else. The runs stranded in
  the `tenant-dev` partition on 2026-08-08/09 stay exactly where they are and
  are not read by anything.
- **On revert:** `POST /api/v1/jobs` returns to accepting a caller-supplied
  `external-fetch` tenant, the gate returns to the environment fallback chain,
  and `data:*` returns to failing as it does today. No worse than the current
  state, and no cleanup step.
- **Forward-compat:** the tenant-qualified idempotency key changes the *queue*
  key for `external-fetch` only. Keys are per-request dedupe markers, not
  durable identity; a revert mid-flight can at worst let one already-enqueued
  probe be enqueued a second time, which the ingestion service's window
  idempotency then absorbs.
- **Blast radius on the deployed scheduler:** none. Cloud Scheduler enqueues
  straight into the durable queue, not through `POST /api/v1/jobs`, so it never
  reaches the new binding.

## 5. Not shipped here, and why — handoff items 1 and 2

Both are **operator actions on the deployment**, not code, and both are
sequencing-coupled. `gh variable list` on `alfloop-dev/odayplus` returns empty,
confirming T10 §3.1: `vars.ODP_SCHEDULED_INGESTION_TENANT_ID` is unset and the
workflow's `|| 'tenant-dev'` literal is what dev actually deploys with.

**Item 1 — align the deployment variables to the operator tenant.** Requires
reading the current `tenant_id` for the smoke service-account entry in
`ODP_AUTH_PRINCIPAL_MAP_SECRET` and setting repository variables. This worker
has no Secret Manager access and no authorization to mutate shared deployment
configuration.

**Item 2 — remove the `|| 'tenant-dev'` / `'tenant-staging'` placeholder
defaults.** Shipping this *without* item 1 would make `deploy_cloud_run_waji.sh:45`
fail closed on every Deploy Dev run — a hard stop on the very pipeline this task
exists to turn green, with no way for this worker to unblock it. It is correct
and should land, but it must land **together with** item 1.

**The gate does not need either of them.** With item 3 in place the enqueue no
longer consults the environment for a tenant, so `data:ingestion_runs`,
`data:admin_boundary.official_dataset:run_exists`, and
`data:poi.commercial_api:run_exists` go green on the next Deploy Dev with the
variables left exactly as they are. Items 1–2 remain necessary for the deployed
**scheduler**, which has no principal to bind to and still reads
`ODP_SCHEDULED_INGESTION_TENANT_ID` directly — so scheduled ingestion keeps
landing in `tenant-dev` until an operator sets the variable.

The smoke principal already holds `data_owner`
(`docs/evidence/runtime/ODP-OPERATOR-SMOKE-RBAC-LIVE-002/`, principal-map
version 3), which grants `integration:create`, so the new enqueue guard does not
require a credential change.

## 6. Writable-path ceiling: two files are outside it — reviewer decision needed

**Flagging explicitly rather than quietly assuming the amendment.** The ceiling
installed on this task
(`PACKAGE_10_LIVE_COMPLETION_EXECUTION_TASKS_2026-08-09.json` T11) grants:

```
modules/external_data/application/**            modules/external_data/workers/**
shared/infrastructure/persistence/**external_data**   apps/worker/oday_worker/**
apps/api/app/routes/external_data.py
tests/** (focused tests matching the diagnosed path)
docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/**
```

Two of the four files changed here are **not** in that list:

- `apps/api/oday_api/main.py`
- `delivery_toolchain/e2e/check_live_e2e_gate.py`

Both are named verbatim in the T10 §7 preamble — *"Hand to a task with write
access to `.github/workflows/**`, `apps/api/oday_api/main.py`,
`delivery_toolchain/e2e/check_live_e2e_gate.py`, deployment variables, and the
principal-map secret"* — and the coordinator's dispatch note directs "Execute
README section 7 exact T11 handoff". So the intent is on record; the ceiling
JSON simply predates the diagnosis. It was authored 2026-08-09 on the
expectation that the defect would sit inside `modules/external_data/**`. T10
proved it does not: the defect is in the enqueue route and the gate probe.

**There is no fix inside the ceiling.** The writable surfaces cannot express
one. `apps/worker/oday_worker/**` has no principal, so the worker cannot know
which tenant the operator credential reads. `apps/api/app/routes/external_data.py`
is the *readback*, and T10 §7 warns in bold that repointing the read side moves
every already-live operator surface in dev into an empty partition. The only
place the two sides can be reconciled is where the tenant enters the system.

**Deliberately left out of scope to hold the line where a choice existed:**

- `.github/workflows/**` — in T10 §7 (item 2) but forbidden by the ceiling
  ("deployment workflows unless coordinator amends exact scope"), and coupled to
  item 1 anyway (§5).
- `docs/evidence/ODP_LIVE_E2E_GATE.md` — outside the ceiling and *not* named in
  §7, so it was reverted rather than shipped. It is now stale in one way that a
  follow-up should close: its "Grants the smoke principal must carry" matrix
  needs a `POST /api/v1/jobs` (`external-fetch`) → `integration:create` →
  `data_owner` row, because the enqueue route now requires a grant it previously
  did not. This is documentation only — the live smoke principal already holds
  `data_owner`, so nothing is blocked by the gap.

If the reviewer or coordinator judges the two code files out of bounds despite
§7, this task cannot be completed as specified and needs the ceiling amended
before any correct fix can land.

## 7. Open: live-candidate confirmation (acceptance criterion 3)

"Real candidate produces both required provider runs with lineage" needs a live
Deploy Dev execution and cannot be closed from this worker: T10 §6 recorded that
`gcloud` on this host has no usable credentials, and that has not changed. What
this task delivers is the code that makes that run succeed; the confirming
artifact is the next `live-e2e-gate.json`.

**What to require on the next Deploy Dev**, at the SHA that carries this change:

- `data:ingestion_runs` non-zero, and `data:<provider>:run_exists` green for
  both `admin_boundary.official_dataset` and `poi.commercial_api`;
- `data:<provider>:snapshot_binding` green for both — this is the lineage
  assertion (`sourceSnapshots` non-empty **and** `canonicalSnapshot` present);
- `worker:idempotent_replay` still `sameJob=True created=False`;
- `blocking_dependencies` no longer contains `external-data` (`mlflow` remains,
  owned by the model-readiness lane).

Also worth resolving while that run has database access: T10's open
sub-question — whether the 2026-08-03 governed runs are absent from the operator
partition because of a dev database reset, a principal-map rotation, or a
different environment. It does not affect this fix (the gate creates its own
runs before reading), but it is cheapest to answer there.

## 8. Provenance

- Diagnosis: `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/README.md`
  (gate authority: Deploy Dev run `31316767710`, job `93253511518`, head
  `9c95ecc3e1f2d0885bb4078070a116e852487f69`).
- Design: `docs/design/EXTERNAL_PROVIDER_LIVE_REQUIRED_RECONCILIATION.md`.
- Gate contract: `docs/evidence/ODP_LIVE_E2E_GATE.md`.

No secret value, bearer token, or credential appears in this note. The tenant
identifiers quoted here are non-secret ids already published in the T10 record
and in `ODP-OPERATOR-SMOKE-RBAC-LIVE-002`.
