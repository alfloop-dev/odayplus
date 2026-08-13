# Package 10 Live External Data Remediation Acceptance Packet

- Sidecar task: `ODP-P10-LIVE-EXTDATA-REMEDIATE-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` (pack order `T11`)
- Helper kind: `acceptance_packet`
- Sidecar owner: Claude (helper-claimed 2026-08-10T01:04:15Z)
- Sidecar reviewer: Claude2
- Parent owner: Claude2 · parent `waiting_for`: Antigravity5
- Parent reviewer: Antigravity6
- Phase: `Package10LiveClosure`
- Prepared: `2026-08-10T00:52Z` · Revised: `2026-08-10T01:15Z` (round 2, after reviewer reopen at `34e23f7f`)
- Prepared at base: `7e6fab1afc6f20cd9225eb502f1fdcdca13d6098` (equal to `origin/dev`)

### Provenance of status facts

All task-status facts in this packet are read from the **live canonical status root**,
not from the repository worktree copy:

| Source | Path | `updated_at` | Task count |
|---|---|---|---|
| **Authoritative (used here)** | `$PANTHEON_STATUS_ROOT/ai-status.json` (`/home/lupin/oday-plus-supervisor-live/ai-status.json`) | `2026-08-10T01:06:11Z` | 53 |
| Not used — repo fixture | `./ai-status.json` in the worktree | `2026-08-04T02:04:00Z` | 4 (`P1-001`…`P4-001`) |

The worktree `ai-status.json` is a four-task sample fixture that contains none of
T10, T11, or T30. An earlier revision of this packet cited it as the baseline; that
citation was wrong and is corrected here. Archived tasks (e.g. T00) are read from
`$PANTHEON_STATUS_ROOT/ai-task-archive/tasks/<TASK-ID>.json`.

---

## 1. Scope Boundary & Intent

This document is a support-only acceptance packet, dependency map, and reviewer replay
harness for task `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` (T11).

- **Non-mutating scope:** This sidecar does NOT modify L1 canonical architecture truth,
  core contract schemas, live task statuses, or primary runtime/registry/governance
  implementation files.
- **Support-only artifact:** Outputs are strictly confined to
  `support/sidecars/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/ODP-P10-LIVE-EXTDATA-REMEDIATE-001-SIDECAR-ACCEPTANCE.md`.
- **Advisory status:** This packet is *not* an authority that can widen T11's scope.
  Where the remediation the diagnosis requires falls outside T11's declared writable
  ceiling, this packet says so and routes the decision to the coordinator — it does not
  authorize the edit. See §3.
- **Handoff objective:** Give the parent owner (`Claude2`) and parent reviewer
  (`Antigravity6`) an independent acceptance contract and verification matrix, with the
  in-ceiling and amendment-required work separated so the commit-scope gate cannot be
  tripped by following this document.

---

## 2. Frozen Baseline & Pre-dispatch State Analysis

### 2.1 Parent Task Status Snapshot

Read from `$PANTHEON_STATUS_ROOT/ai-status.json` at `updated_at = 2026-08-10T01:06:11Z`.

- **Task ID:** `ODP-P10-LIVE-EXTDATA-REMEDIATE-001`
- **Order:** `T11` (Package 10 Live Closure chain)
- **Status:** `blocked` · **`waiting_for`:** `Antigravity5`
- **Priority:** `P0`
- **Automation Class:** `CONDITIONAL_AUTO_REMEDIATION`
- **Action:** `conditional_create`
- **Upstream Dependency:** `ODP-P10-LIVE-EXTDATA-DIAG-001` (T10) — status `review_approved`
- **`blocker_reason`:** *"T10 must record remediation_required and T00 must install exact
  writable ceiling before T11 starts"*

### 2.2 Unblocking Pre-conditions — actual gate state

T11's `dispatch_condition` is: *"T10 proves code or configuration remediation is required
and T00 installs exact writable ceiling."* Its `next` field states the resume rule
verbatim: **"Resume when: T10 is done AND records `remediation_required`."**

| # | Pre-condition | State | Evidence |
|---|---|---|---|
| **1** | T10 is `done` **and** records `remediation_required` | **NOT SATISFIED** | T10 status is `review_approved`, not `done`. Its `next` reads *"CI checks for task ODP-P10-LIVE-EXTDATA-DIAG-001 failed; resolve failing checks before finalization."* The literal token `remediation_required` appears in **neither** T10's task record nor its evidence directory. |
| **2** | T00 (`ODP-P10-LIVE-FLEET-STATE-REPAIR-001`) installs the exact writable ceiling | **SATISFIED** | T00 archived `2026-08-09T15:52:48Z` with `terminal_status: done`. T11's own `next` records: *"T00 has now installed the exact writable ceiling from the pack … so the second half of the dispatch_condition is satisfied."* T11 carries the 7 globs in §2.3 below. |

**Substantive vs. recorded.** T10 *did* deliver the substance of pre-condition 1 — its
evidence README §7 is an exact, evidence-backed four-item T11 handoff naming the defect
path. But the gate T11 declares is not "an equivalent handoff exists"; it is the recorded
token plus T10 reaching `done`. Neither has happened. A reviewer or coordinator reading
this packet should treat T11 as legitimately blocked today.

**Two things must happen before T11 dispatches** (neither is sidecar work, and neither is
in the parent owner's gift alone):

1. T10's failing CI is resolved and T10 moves `review_approved` → `done`.
2. `remediation_required` is recorded against T10 with the named defect path — either in
   T10's task record or in a coordinator amendment that the T11 gate can read.

There is also a live third branch: T11's `next` states *"If T10 closes runtime-only,
supersede T11 instead of starting it."* T10 §6 forecloses that branch on the merits — it
documents why no runtime-only closure exists, because every path to a green `data:*`
requires a deployment-variable, secret, or code change. Recording that conclusion is what
converts "supersede T11" into "dispatch T11."

### 2.3 Writable and Forbidden Path Boundaries

#### T11 declared writable paths (verbatim from `writable_paths`, 7 entries)
- `modules/external_data/application/**`
- `modules/external_data/workers/**`
- `shared/infrastructure/persistence/**external_data**`
- `apps/worker/oday_worker/**`
- `apps/api/app/routes/external_data.py`
- `tests/** limited to focused tests matching the diagnosed path`
- `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/**`

#### T11 declared forbidden paths (verbatim from `forbidden_paths`, 6 entries)
- `apps/web/**`
- `docs/design/**`
- `docs_archive/**`
- `models/**`
- `auth and RBAC surfaces`
- `deployment workflows unless coordinator amends exact scope`

#### Additional constraints — *derived, not declared*
These are not in T11's `forbidden_paths`. They are inferred from T11's acceptance criteria
(*"retry idempotency DQ tenant audit and failure classification remain fail closed"*) and
from T10's read-only discipline. They are sound, but a reviewer should not cite them as
declared ceiling violations:
- No direct production database manual patches (derived from the fail-closed criterion and
  T10 §6's "no database write, no fabricated row" precedent).
- No weakening of live E2E assertions (derived from the same criterion — the gate must
  pass on its own terms, not on relaxed ones).

#### ⚠ Ceiling / target mismatch (verified at base `7e6fab1a`)
The ceiling grants `apps/api/app/routes/external_data.py`. That module exists (241 lines)
and defines the `/external-data` router — `GET /freshness`, `GET|POST /ingestion-runs`,
`GET /quarantine`. **It does not contain `POST /api/v1/jobs`.** The enqueue route the
diagnosis targets is `enqueue_job` in `apps/api/oday_api/main.py` (routed on
`platform_router`, tenant-scope block at ~L772–L785). `apps/api/oday_api/main.py` is *not*
in the ceiling. This is why Item 3 below cannot be completed in-ceiling as diagnosed.

---

## 3. Technical Remediation Blueprint (T10 Handoff Alignment)

### 3.0 Scope reality — read this before executing any item

`docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/README.md` §7 opens with a
requirement this packet previously dropped, restored here verbatim:

> *"Hand to a task with write access to `.github/workflows/**`,
> `apps/api/oday_api/main.py`, `delivery_toolchain/e2e/check_live_e2e_gate.py`, deployment variables,
> and the principal-map secret. Items 1–2 alone turn `data:*` green; 3–4 stop the class of
> defect from recurring silently."*

**T11 as currently declared is not that task.** Three of the four surfaces T10 names
(`.github/workflows/**`, `apps/api/oday_api/main.py`, `scripts/**`) are outside T11's 7
writable globs, and deployment workflows are explicitly inside its `forbidden_paths`.

Consequently the blueprint below is split into two tracks. **Track B must not be committed
under T11's current scope.** Attempting it will either be rejected by the commit-scope
check or land a forbidden-path violation; both are worse outcomes than pausing for the
amendment.

| Track | Meaning | Commit authority |
|---|---|---|
| **A — In ceiling** | Falls inside the 7 declared writable globs | T11 may commit today, once unblocked |
| **B — Amendment required** | Outside the ceiling and/or in `forbidden_paths` | Requires a coordinator amendment of T11's exact scope, or a separate task that holds the scope |

**Non-file surfaces.** Item 1's substance is deployment *variables* and a Secret Manager
*secret*, not repository files. These are outside the repository ceiling in a different
sense — no `writable_paths` entry can grant them, and this worker/owner class does not hold
the credentials (T10 §6.3 records `gcloud` reauthentication failure on this host). They are
an operator/coordinator action, tracked as **D11**.

### 3.1 Diagnosed Defect Summary
Task T10 established that the zero-ingestion-run failure (`data:ingestion_runs runs=0`,
`data:admin_boundary.official_dataset:run_exists FAIL`, `data:poi.commercial_api:run_exists
FAIL`) in live E2E gate run `31316767710` was caused by a **write/read tenant-partition
split**:

1. **Write path:** The worker enqueues and executes `external-fetch` probes under the
   deployment fallback tenant `tenant-dev`
   (`sha256 = 7c51172bedb79ef6b6d0d0eb675210470d2cc2e0a4947ab7221616199a9c01f6`).
2. **Read path:** `live-e2e-gate` reads back authenticated ingestion runs under the smoke
   principal's tenant `a11ce505-70bc-56d9-8564-ad22efa23c9e`
   (`sha256 = da57d47ac40b5f8fa57ac349b3b1a154b3b64d4e807c142a1e9ba1bdef834b5b`).
3. **Partition isolation:** `TenantScopedDocumentStore` appends the hashed tenant ID to
   collection names (`ingestion_runs.tenant.<hash>`). Because writes and reads target
   different collections, the readback returns 200 OK with `runs=0`.

### 3.2 Required Action Items for T11

Item numbering matches T10 §7 so the two documents can be read side by side.

#### Item 1 — Align scheduled ingestion tenant with operator tenant
**Track B · operator/coordinator action (deployment variables + secret read) · unblocks the gate**

- Read the smoke service-account `tenant_id` from `ODP_AUTH_PRINCIPAL_MAP_SECRET`
  (documented as `a11ce505-70bc-56d9-8564-ad22efa23c9e` — T10 §7 says *verify, do not
  assume*).
- Set the dev repository/environment variables `ODP_SCHEDULED_INGESTION_TENANT_ID` and
  `ODP_TENANT_ID` to that value.
- *Direction constraint:* align ingestion to the operator tenant, **not** vice versa.
  Repointing the principal map at `tenant-dev` would move every already-live operator
  surface in dev into an empty partition. Moving ingestion the other way strands only the
  disposable gate probe runs written 2026-08-08/09.
- *Accepted side effect:* `admin_boundary.official_dataset` is the probe provider, so
  expect one real fetch window consumed per deploy.

#### Item 2 — Eliminate soft fallback defaults in workflow definitions
**Track B · requires coordinator scope amendment · `.github/**` + `scripts/**`**

- `.github/workflows/deploy-dev.yml` (L101-102) and `.github/workflows/deploy-staging.yml`
  (L84-85) supply `|| 'tenant-dev'` / `|| 'tenant-staging'`; pass the variables through
  unset instead.
- This restores the fail-closed guard at `product_ops/deployment/deploy_cloud_run_waji.sh:45-46` so an
  unconfigured deployment fails rather than inventing a synthetic tenant partition.
- **Blocked under current scope:** `.github/**` and `scripts/**` are outside all 7 globs,
  and *"deployment workflows unless coordinator amends exact scope"* is an explicit T11
  `forbidden_paths` entry. Do not commit this under T11 as declared.
- **Ordering note:** Item 2 without Item 1 breaks dev/staging deploys — removing the
  default from a deployment whose variables are still unset makes it fail closed, which is
  correct behaviour but is an outage if sequenced wrong. Land Item 1 first, verify the
  variables resolve, then Item 2. See R3.

#### Item 3 — Enforce tenant scope binding on the `external-fetch` enqueue path
**Track B as diagnosed · a reduced Track A variant exists — see below**

- *As diagnosed:* in `POST /api/v1/jobs` (`enqueue_job`, `apps/api/oday_api/main.py`,
  tenant block at ~L772), apply to `external-fetch` the rule `forecast` already has:
  reject a supplied `payload.tenant_id` that differs from the authenticated tenant, then
  rewrite `payload["tenant_id"]` from the principal.
- **Error contract (corrected):** the existing `forecast` rule raises
  `TENANT_SCOPE_MISMATCH` with **HTTP 403 Forbidden**, and the companion missing-scope case
  raises `TENANT_SCOPE_REQUIRED`, also 403. An earlier revision of this packet specified
  HTTP 400; that was wrong. Match the established 403 contract — a new external-fetch
  branch that returns 400 would be inconsistent with the forecast branch it is modelled on.
- **Why Track B:** `apps/api/oday_api/main.py` is not in the ceiling; only
  `apps/api/app/routes/external_data.py` is, and that module does not host `/jobs`.
- **Track A variant (partial, does not replace the above):** the in-ceiling
  `apps/api/app/routes/external_data.py` hosts `POST /external-data/ingestion-runs`
  (`trigger_ingestion_run`) and its own `resolve_tenant_id(request)`. Hardening *that*
  route's tenant derivation is inside the ceiling and is worth doing. It does **not** close
  the diagnosed defect, because the gate probe enqueues via `/api/v1/jobs`. Do not report
  the Track A variant as satisfying Item 3.
- **Deployed-path note (T10 §7.3):** the Cloud Scheduler path enqueues straight into the
  durable queue, not through this route, so it is unaffected by Item 3.

#### Item 4 — Remove guessing behaviour from the E2E gate script
**Track B · requires coordinator scope amendment · `scripts/**`**

- In `delivery_toolchain/e2e/check_live_e2e_gate.py:1315` (`_enqueue_body`), drop the fallback chain
  (`ODP_SCHEDULED_INGESTION_TENANT_ID` / `ODP_TENANT_ID` / `"tenant-e2e"`).
- Derive the probe payload tenant strictly from the authenticated identity the gate already
  holds, so the probe cannot write anywhere the same credential cannot read.
- **Blocked under current scope:** `scripts/**` is outside all 7 globs.
- **Dependency:** T10 §7 sequences this after Item 3 (*"With item 3 in place…"*). Landing
  Item 4 first removes the guess without installing the authoritative source.

#### Item coverage summary

| Item | Primary surface | Track | In T11 ceiling? |
|---|---|---|---|
| 1 | Deployment variables + `ODP_AUTH_PRINCIPAL_MAP_SECRET` | B | No — non-file operator surface |
| 2 | `.github/workflows/deploy-{dev,staging}.yml`, `product_ops/deployment/deploy_cloud_run_waji.sh` | B | No — outside globs **and** in `forbidden_paths` |
| 3 | `apps/api/oday_api/main.py` | B | No — only `app/routes/external_data.py` is writable |
| 3′ | `apps/api/app/routes/external_data.py` (partial hardening) | A | Yes |
| 4 | `delivery_toolchain/e2e/check_live_e2e_gate.py` | B | No — outside globs |
| Tests | `tests/**` focused on the diagnosed path | A | Yes |
| Evidence | `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/**` | A | Yes |

**Net:** the two items T10 says are sufficient to turn `data:*` green (1 and 2) are both
Track B. T11 cannot close its own acceptance criteria under its current declared scope.
This is the single most important finding in this packet.

### 3.3 Regression Test Specifications
Both land under `tests/**` (Track A, in-ceiling), but each *asserts against* a Track B
surface, so they can be written first and will fail until the corresponding fix lands —
which is the correct red-first ordering.

1. **API scope-binding test.** Assert `POST /api/v1/jobs` rejects an `external-fetch`
   payload whose `tenant_id` differs from the authenticated principal, with code
   `TENANT_SCOPE_MISMATCH` and status **403**; and that a conforming payload is rewritten to
   the principal's tenant before enqueue. Mirror the existing `forecast` assertions.
2. **Partition-consistency test.** Assert the enqueue tenant equals the readback tenant —
   that `ExternalIngestionService` and `TenantScopedDocumentStore` resolve identical
   partition digests for a given principal claim — so the split cannot be reintroduced by
   an environment variable.

**Existing prior art to extend, not duplicate:**
`tests/integration/test_scheduled_ingestion_tenant_propagation.py` already covers the
adjacent fail-closed contract (a tenant-less deployment enqueues nothing;
`run_scheduled` rejects an empty tenant; cross-tenant replays and watermarks stay isolated).
It is the natural host for test 2 and the closest precedent for test 1's style.

**Placement note:** there is no `tests/unit/external_data/` package at base `7e6fab1a`
(`tests/unit/` contains `listing/`, `ml/`, `persistence/` only), and no test module matching
`*external_data*` exists anywhere under `tests/`. New focused tests must therefore create
their own path or extend the integration module above; do not assume a package that is not
there.

---

## 4. Dependency Map

| # | Upstream dependency / context | Parent consumer | Expected result / output | Fail-closed condition |
|---|---|---|---|---|
| **D1** | T10 evidence (`docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/README.md`) | T11 owner (`Claude2`) | Root cause + 4-item remediation specification | T11 attempts trial-and-error fixes outside the T10 diagnosis |
| **D2** | T00 writable-path ceiling (live `ai-status.json`) | T11 commit scope | Strict compliance with the 7 declared writable globs | Commit leaking outside declared writable paths |
| **D3** | Deploy Dev gate authority (`live-e2e-gate.json`) | T11 verification | `data:ingestion_runs`, `data:admin_boundary.official_dataset:run_exists`, `data:poi.commercial_api:run_exists` all PASS | Any `data:*` check remaining FAIL |
| **D4** | `ODP_AUTH_PRINCIPAL_MAP_SECRET` | Environment config (**Track B**) | Tenant ID *verified* as `a11ce505-70bc-56d9-8564-ad22efa23c9e` | Hardcoding the documented string without reading the secret |
| **D5** | Enqueue route `apps/api/oday_api/main.py` (**Track B**) | Application layer | `TENANT_SCOPE_MISMATCH` (403) enforced on `external-fetch` | Unauthenticated tenant override accepted; **or** the edit committed without a scope amendment |
| **D5′** | `apps/api/app/routes/external_data.py` (**Track A**) | Application layer | `trigger_ingestion_run` tenant derivation hardened | Partial hardening reported as closing Item 3 |
| **D6** | Worker handler (`apps/worker/oday_worker/**`) (**Track A**) | Execution layer | `IngestionRunRecord` written to the authenticated partition | Writes landing in `tenant-dev` or an unscoped partition |
| **D7** | Workflow manifests (`deploy-dev.yml`, `deploy-staging.yml`) (**Track B — amendment required**) | CI/CD infrastructure | `\|\| 'tenant-dev'` fallback removed **after** D11 lands | Committed under T11's current scope; or landed before D11, breaking deploys |
| **D8** | Gate script (`delivery_toolchain/e2e/check_live_e2e_gate.py`) (**Track B — amendment required**) | Validation infrastructure | `_enqueue_body` derives tenant from the auth principal | Committed under T11's current scope; or landed before D5 |
| **D9** | Downstream task T30 (`ODP-P10-DEV-REDEPLOY-VERIFY-001`, `blocked`) | Fleet dispatch | T30 unblocked only after T11 completes and merges | T30 triggered before a T11 candidate deployment |
| **D10** | Independent reviewer (`Antigravity6`) | Quality gate | Exact-head review and evidence validation pass | Merging without exact-head approval |
| **D11** | **Coordinator scope amendment** for `.github/workflows/**`, `apps/api/oday_api/main.py`, `scripts/**`, deployment variables, principal-map secret | T11 dispatch precondition | T11 `writable_paths` amended (or a separately-scoped task created) so Items 1/2/3/4 have a lawful home | T11 dispatched to execute Items 1–4 with the current 7-glob ceiling unchanged |
| **D12** | T10 reaching `done` **and** recording `remediation_required` | T11 unblock gate (§2.2) | T11 leaves `blocked` legitimately | T11 started while T10 is still `review_approved` with failing CI |

### Intended composition & flow architecture

```text
  +-----------------------------------------------------------------------+
  | T10 Diagnosis  (status: review_approved, CI failing -- NOT done)      |
  | Root cause: write/read tenant split (tenant-dev vs a11ce505...)       |
  +-----------------------------------------------------------------------+
                                     |
                    (D12) T10 -> done AND records remediation_required
                                     |
                                     v
  +-----------------------------------------------------------------------+
  | (D11) Coordinator scope amendment                                     |
  | Grants .github/workflows/**, apps/api/oday_api/main.py, scripts/**,   |
  | deployment variables, principal-map secret -- or routes them to a     |
  | separately-scoped task. Without this, Items 1/2/3/4 have no lawful    |
  | home and T11 cannot close its own acceptance criteria.                |
  +-----------------------------------------------------------------------+
                                     |
                                     v
  +-----------------------------------------------------------------------+
  | T11 Remediation Execution (ODP-P10-LIVE-EXTDATA-REMEDIATE-001)        |
  |                                                                       |
  |  Track A (in ceiling, available now once unblocked):                  |
  |    3'. Harden tenant derivation in app/routes/external_data.py        |
  |    T.  Focused regression tests under tests/**                        |
  |    E.  Evidence under docs/evidence/runtime/...REMEDIATE-001/         |
  |                                                                       |
  |  Track B (requires D11; ordered 1 -> 2 -> 3 -> 4):                    |
  |    1. Align ODP_SCHEDULED_INGESTION_TENANT_ID to a11ce505...          |
  |    2. Remove || 'tenant-dev' fallback in deploy-{dev,staging}.yml     |
  |    3. Enforce TENANT_SCOPE_MISMATCH (403) in POST /api/v1/jobs        |
  |    4. Remove fallback chain in check_live_e2e_gate._enqueue_body      |
  +-----------------------------------------------------------------------+
                                     |
                                     v
  +-----------------------------------------------------------------------+
  | Verification & Evidence Generation                                    |
  | - Focused pytest regression pass                                      |
  | - Candidate Deploy Dev run executed                                   |
  | - live-e2e-gate.json confirms data:* checks PASS (runs >= 1)          |
  | - Evidence: docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/ |
  +-----------------------------------------------------------------------+
                                     |
                                     v
  +-----------------------------------------------------------------------+
  | Review & Task PR Finalization (Antigravity6 reviewer)                 |
  | - Task PR merged into origin/dev                                      |
  | - T11 status moved to done                                            |
  | - Unblocks T30 (ODP-P10-DEV-REDEPLOY-VERIFY-001)                      |
  +-----------------------------------------------------------------------+
```

---

## 5. Acceptance Checklist & Replay Matrix

### 5.1 Acceptance checklist

Each item is tagged with the track that can satisfy it. Items tagged **B** cannot be
truthfully checked until D11 lands.

- [ ] **Item 1 — Deterministic regression test** *(A)*
  A test reproducing worker enqueue success with mismatched tenant readback failure exists
  and passes, proving the fix prevents regression. It may be authored red before the Track B
  fix lands.
- [ ] **Item 2 — Scoped code & config fix** *(B, gated on D11)*
  `POST /api/v1/jobs` enforces tenant matching with `TENANT_SCOPE_MISMATCH` (403), workflow
  fallbacks are removed, and the ingestion tenant is aligned to operator tenant
  `a11ce505-70bc-56d9-8564-ad22efa23c9e`. **Do not check this item on the strength of the
  Track A `app/routes/external_data.py` hardening alone** — that route is not the enqueue
  path the gate probe uses.
- [ ] **Item 3 — Real candidate gate execution** *(B)*
  Candidate deployment produces non-empty `SUCCEEDED` runs for both required providers
  (`admin_boundary.official_dataset`, `poi.commercial_api`), turning all `data:*` checks in
  `live-e2e-gate.json` green.
- [ ] **Item 4 — Fail-closed invariants intact** *(A + B)*
  Retry, idempotency, DQ quarantine, tenant partition isolation, audit trail, and error
  classification remain strictly fail-closed.
- [ ] **Item 5 — CI & quality gates pass** *(A + B)*
  Focused tests, integration tests, live gate, Ruff, git diff scope check, and exact-head CI
  pass with zero violations. The diff scope check must pass **against whatever ceiling is in
  force at commit time** — if D11 amended it, against the amended ceiling.
- [ ] **Item 6 — Independent review & evidence** *(A + B)*
  Independent review by `Antigravity6` and a rollback evidence directory
  (`docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/`) are committed and verified
  prior to PR merge.
- [ ] **Item 7 — Scope lawfulness** *(precondition)*
  Every path in the final diff is inside T11's writable ceiling as it stands at commit time.
  If any Track B file appears in the diff, a coordinator amendment (D11) is recorded and
  linked from the PR.

### 5.2 Reviewer replay commands (for `Antigravity6`)

```bash
# 1. Verify working branch and HEAD commit
git branch --show-current
git log -n 1 --stat

# 2. Verify the diff scope against T11's writable ceiling
git diff --name-only origin/dev...HEAD
#    Any hit on .github/**, scripts/**, or apps/api/oday_api/** is Track B:
#    require a linked coordinator scope amendment (D11) before proceeding.
git diff --name-only origin/dev...HEAD \
  | grep -E '^(\.github/|scripts/|apps/api/oday_api/)' && echo "TRACK B PATHS PRESENT — require D11"

# 3. Run the tenant-scope regression coverage.
#    NOTE: there is no tests/unit/external_data/ package; discover the focused
#    tests from the diff instead of assuming a path.
pytest tests/integration/test_scheduled_ingestion_tenant_propagation.py -v
git diff --name-only origin/dev...HEAD -- 'tests/**' \
  | grep -E '\.py$' | xargs -r pytest -v

# 4. Confirm the error contract matches the established forecast rule (403, not 400)
grep -n "TENANT_SCOPE_MISMATCH" -A3 apps/api/oday_api/main.py

# 5. Inspect the evidence artifact directory
ls -la docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/

# 6. Verify live E2E gate output from the candidate run
jq '.checks[] | select(.name | startswith("data:"))' \
  docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/live-e2e-gate.json

# 7. Re-read the live gate state (never the worktree fixture)
python3 - <<'PY'
import json, os
p = os.path.join(os.environ["PANTHEON_STATUS_ROOT"], "ai-status.json")
d = json.load(open(p))
print("updated_at:", d["updated_at"], "tasks:", len(d["tasks"]))
for t in d["tasks"]:
    if t["id"] in {"ODP-P10-LIVE-EXTDATA-DIAG-001", "ODP-P10-LIVE-EXTDATA-REMEDIATE-001"}:
        print(t["id"], t["status"], t.get("waiting_for"))
PY
```

---

## 6. Risk Register & Mitigation Strategy

| Risk ID | Risk description | Severity | Mitigation strategy |
|---|---|---|---|
| **R0** | **Scope deadlock:** T11's declared ceiling excludes the very surfaces its acceptance criteria require, so the task cannot both stay in scope and go green. | **High** | Raise D11 with the coordinator *before* dispatch. Land Track A inside the ceiling; hold Track B until the amendment is recorded. Do not resolve the contradiction by widening scope unilaterally. |
| **R1** | **Scope creep / writable leak:** modifications extending into UI, models, or core RBAC. | High | Commit through `delivery_toolchain/git/worker_commit.py --scope`, matching the ceiling **in force at commit time**. If a Track B path must be committed, the amended ceiling — not the original 7 globs — is the scope argument, and the amendment is linked from the PR. |
| **R2** | **Tenant partition mutation:** re-pointing the principal map instead of the ingestion variable, orphaning live operator data. | High | Follow Item 1's direction constraint: align the ingestion variable to operator tenant `a11ce505-70bc-56d9-8564-ad22efa23c9e`, never the reverse. |
| **R3** | **Unconfigured deployment failure:** removing the `\|\| 'tenant-dev'` default before the variables are populated makes dev/staging deploys fail closed — correct behaviour, wrong moment. | Medium | Strict ordering: Item 1 (populate and verify variables in Secret Manager / repository settings) **then** Item 2. Verify the variables resolve on a candidate deploy before removing the default. |
| **R4** | **Uncovered CI flakes:** non-required gates (e.g. `performance-gate`) emitting false failures. | Low | Verify required status checks (`orchestrator`, `product`, `product-e2e-gate`, `task-review-gate`) rather than non-blocking noise. |
| **R5** | **Premature dispatch:** T11 started while T10 is `review_approved` with failing CI and `remediation_required` unrecorded, so the remediation lands against an unratified diagnosis. | Medium | Treat D12 as a hard gate. T10 reaches `done` and the token is recorded before T11 leaves `blocked`. |
| **R6** | **Partial-fix misreporting:** the in-ceiling `app/routes/external_data.py` hardening is reported as satisfying Item 3, leaving the `/jobs` enqueue path unguarded while the checklist reads green. | Medium | §3.2 Item 3 and §5.1 Item 2 both state this explicitly; reviewer replay step 4 greps `main.py` for the enforcement, so a Track A-only fix cannot pass review silently. |
| **R7** | **Stale-source drift:** a later reader re-derives status from the 4-task worktree fixture rather than the live status root. | Low | Provenance table in the header; replay step 7 reads `$PANTHEON_STATUS_ROOT` explicitly. |

---

## 7. Summary & Handoff Recommendation

This packet is the acceptance specification for `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` (T11).
Its most consequential finding is not a checklist item but a scope contradiction:

> **T11's declared writable ceiling excludes three of the four surfaces its own acceptance
> criteria require.** T10 §7 said so explicitly — *"hand to a task with write access to
> `.github/workflows/**`, `apps/api/oday_api/main.py`, `delivery_toolchain/e2e/check_live_e2e_gate.py`,
> deployment variables, and the principal-map secret"* — and the two items T10 identifies as
> sufficient to turn `data:*` green (Items 1 and 2) are both outside the ceiling, with
> deployment workflows named in `forbidden_paths`.

Recommended sequence:

1. **Coordinator (D11, D12):** record `remediation_required` against T10 and move T10 to
   `done`; then either amend T11's `writable_paths` to the exact surfaces T10 §7 names, or
   create a companion task that holds them. Until then T11 correctly remains `blocked`
   (`waiting_for: Antigravity5`).
2. **Parent owner (`Claude2`), Track A:** author the two regression tests, harden
   `apps/api/app/routes/external_data.py`, and open the evidence directory. This is lawful
   under the current ceiling and is genuine forward progress.
3. **Parent owner, Track B — only after D11:** execute Items 1 → 2 → 3 → 4 in that order,
   matching the 403 `TENANT_SCOPE_MISMATCH` contract the `forecast` branch already
   establishes.
4. **Evidence:** generate runtime evidence under
   `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/`, including the candidate
   `live-e2e-gate.json` showing `data:*` green.
5. **Review:** submit to `Antigravity6` for independent exact-head review using §5.2.
6. **Closeout:** merge the task PR to `origin/dev` and finalize via `ai-status.sh done`,
   which unblocks T30 (`ODP-P10-DEV-REDEPLOY-VERIFY-001`).

**Standing limitation.** This is a support-only sidecar. It cannot amend T11's scope, record
`remediation_required`, or move any task status. Where it identifies a blocker, the routing
target is the coordinator and the parent owner — never a unilateral widening of scope by the
executing worker.

---

## Appendix A — Round 2 revision log

Reviewer `Claude2` reopened this packet at head `34e23f7f` (PR #764, comment
`5234805107`). All findings were reproduced against live canonical status and the
repository at base `7e6fab1a`; all are fixed within this document.

| Finding | Severity | Resolution |
|---|---|---|
| §3.2 Items 2 & 4, D7/D8 prescribed edits outside T11's 7 writable globs and inside `forbidden_paths`, contradicting R1's own mitigation; T10 §7's write-access caveat had been dropped | High | New §3.0 restores the T10 §7 caveat verbatim and splits the blueprint into Track A (in-ceiling) / Track B (amendment required). Every item, dependency, and checklist entry is now track-tagged. New **D11** (coordinator scope amendment) and **R0** (scope deadlock). R1's mitigation reworded to "the ceiling in force at commit time". New §5.1 Item 7 and replay step 2 make a Track B path in the diff a review stop. |
| §2.2 marked both unblock pre-conditions "Satisfied" while T11 is `blocked`/`waiting_for: Antigravity5`, T10 is `review_approved` with failing CI, and `remediation_required` is recorded nowhere | Medium | §2.2 rewritten as a state table: pre-condition 1 **NOT SATISFIED** with evidence; pre-condition 2 **SATISFIED** (T00 archived `done` 2026-08-09T15:52:48Z, confirmed by T11's own `next`). Adds the substantive-vs-recorded distinction, the two unblock actions, and the "supersede T11" branch. New **D12** and **R5**. |
| Header cited worktree `ai-status.json` `updated_at 2026-08-04T02:04:00Z` — the 4-task repo fixture, not live canonical | Medium | Header now carries a provenance table naming `$PANTHEON_STATUS_ROOT/ai-status.json` at `updated_at 2026-08-10T01:06:11Z` (53 tasks) as authoritative and the fixture as explicitly not used. Replay step 7 re-reads the live root. New **R7**. |
| §5.2 replay step 3 targeted `tests/unit/external_data/`, which does not exist | Minor | Replay step 3 now runs the real `tests/integration/test_scheduled_ingestion_tenant_propagation.py` and discovers focused tests from the diff. §3.3 records that no `tests/unit/external_data/` package and no `*external_data*` test module exist at base, and names the existing propagation test as prior art. |
| §2.3 listed derived constraints among T11's declared `forbidden_paths` | Minor | §2.3 now reproduces `writable_paths` and `forbidden_paths` verbatim (7 and 6 entries), with database-patch and E2E-assertion constraints moved to a separate "derived, not declared" subsection. |

Additional corrections found while verifying the above (not in the reviewer's list):

- **Item 3's target file.** The ceiling grants `apps/api/app/routes/external_data.py`, but
  `POST /api/v1/jobs` is `enqueue_job` in `apps/api/oday_api/main.py`; the in-ceiling module
  hosts only the `/external-data` router. Item 3 is therefore Track B as diagnosed, with a
  partial Track A variant documented and explicitly barred from being reported as closure.
  New **D5′** and **R6**.
- **Error contract.** The existing `forecast` rule raises `TENANT_SCOPE_MISMATCH` with HTTP
  **403** (`apps/api/oday_api/main.py`, ~L777), not 400 as the previous revision stated.
  Corrected in §3.2 Item 3 and §3.3, and verified by replay step 4.
- **Header metadata.** Sidecar owner corrected from `Antigravity2` to `Claude` (helper-claimed
  2026-08-10T01:04:15Z); parent owner is `Claude2` with `waiting_for: Antigravity5` — the
  previous "`Claude2` or `Antigravity5`" phrasing conflated an owner with a wait target.
- **Ordering hazards.** T10 §7 sequences Item 4 after Item 3, and Item 2 is an outage risk
  ahead of Item 1. Both orderings are now stated in §3.2 and carried in R3 and D7/D8.
