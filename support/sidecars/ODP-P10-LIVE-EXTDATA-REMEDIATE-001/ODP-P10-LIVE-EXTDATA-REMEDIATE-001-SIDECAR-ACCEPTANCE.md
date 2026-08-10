# Package 10 Live External Data Remediation Acceptance Packet

- Sidecar task: `ODP-P10-LIVE-EXTDATA-REMEDIATE-001-SIDECAR-ACCEPTANCE`
- Parent task: `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` (pack order `T11`)
- Helper kind: `acceptance_packet`
- Sidecar owner: Antigravity2
- Sidecar reviewer: Claude2
- Parent owner: Claude2 (application/persistence/config) or Antigravity5 (runtime-only config)
- Parent reviewer: Antigravity6
- Phase: `Package10LiveClosure`
- Prepared: `2026-08-10T00:52Z`
- Prepared at base: `7e6fab1afc6f20cd9225eb502f1fdcdca13d6098` (equal to `origin/dev`)
- Live status snapshot read at: `ai-status.json` `updated_at = 2026-08-04T02:04:00Z`

---

## 1. Scope Boundary & Intent

This document is a support-only acceptance packet, dependency map, and reviewer replay harness for task `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` (T11).

- **Non-mutating scope:** This sidecar does NOT modify L1 canonical architecture truth, core contract schemas, live task statuses, or primary runtime/registry/governance implementation files.
- **Support-only artifact:** Outputs are strictly confined to `support/sidecars/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/ODP-P10-LIVE-EXTDATA-REMEDIATE-001-SIDECAR-ACCEPTANCE.md`.
- **Handoff objective:** Provide the parent owner (`Claude2` / `Antigravity5`) and parent reviewer (`Antigravity6`) with an authoritative, independent acceptance contract and verification matrix to guide T11 execution and review.

---

## 2. Frozen Baseline & Pre-dispatch State Analysis

### 2.1 Parent Task Status Snapshot
- **Task ID:** `ODP-P10-LIVE-EXTDATA-REMEDIATE-001`
- **Order:** `T11` (Package 10 Live Closure chain)
- **Status:** `blocked`
- **Priority:** `P0`
- **Automation Class:** `CONDITIONAL_AUTO_REMEDIATION`
- **Upstream Dependency:** `ODP-P10-LIVE-EXTDATA-DIAG-001` (T10)

### 2.2 Unblocking Pre-conditions
T11 requires two explicit pre-conditions before active dispatch:
1. **Diagnosis proof:** Upstream task T10 (`ODP-P10-LIVE-EXTDATA-DIAG-001`) must record `remediation_required` with an exact, evidence-backed defect path. *(Satisfied: T10 diagnosis in PR #759 / `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/README.md` proved a write/read tenant-partition split).*
2. **Writable ceiling installation:** T00 (`ODP-P10-LIVE-FLEET-STATE-REPAIR-001`) must declare the exact writable path ceiling for T11 in `ai-status.json`. *(Satisfied: Writable ceiling declared).*

### 2.3 Writable and Forbidden Path Boundaries

#### Declared Writable Paths:
- `modules/external_data/application/**`
- `modules/external_data/workers/**`
- `shared/infrastructure/persistence/**external_data**`
- `apps/worker/oday_worker/**`
- `apps/api/app/routes/external_data.py`
- `tests/**` (limited to focused unit/integration tests matching the diagnosed path)
- `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/**`

#### Declared Forbidden Paths:
- `apps/web/**` (Package 10 Web UI)
- `docs/design/**` & `docs_archive/**`
- `models/**` (ML models and registries)
- Auth and RBAC surfaces
- Deployment workflow definitions (unless explicitly amended by coordinator)
- Direct production database manual patches
- Weakening live E2E assertions

---

## 3. Technical Remediation Blueprint (T10 Handoff Alignment)

### 3.1 Diagnosed Defect Summary
Task T10 established that the zero-ingestion-run failure (`data:ingestion_runs runs=0`, `data:admin_boundary.official_dataset:run_exists FAIL`, `data:poi.commercial_api:run_exists FAIL`) in live E2E gate run `31316767710` was caused by a **write/read tenant-partition split**:

1. **Write Path:** The worker enqueues and executes `external-fetch` probes under the deployment fallback tenant `tenant-dev` (`sha256 = 7c51172bedb79ef6b6d0d0eb675210470d2cc2e0a4947ab7221616199a9c01f6`).
2. **Read Path:** `live-e2e-gate` reads back authenticated ingestion runs under the smoke principal's tenant `a11ce505-70bc-56d9-8564-ad22efa23c9e` (`sha256 = da57d47ac40b5f8fa57ac349b3b1a154b3b64d4e807c142a1e9ba1bdef834b5b`).
3. **Partition Isolation:** `TenantScopedDocumentStore` appends the hashed tenant ID to collection names (`ingestion_runs.tenant.<hash>`). Because the writes and reads target different collections, the readback returns 200 OK with `runs=0`.

### 3.2 Required Action Items for T11

#### Item 1: Align Scheduled Ingestion Tenant with Operator Tenant (Config / Unblock Gate)
- Read the smoke service-account `tenant_id` from `ODP_AUTH_PRINCIPAL_MAP_SECRET` (documented as `a11ce505-70bc-56d9-8564-ad22efa23c9e`).
- Set dev environment variables `ODP_SCHEDULED_INGESTION_TENANT_ID` and `ODP_TENANT_ID` to match the operator tenant (`a11ce505-70bc-56d9-8564-ad22efa23c9e`).
- *Constraint:* Align ingestion to operator tenant, NOT vice versa (repointing principal map to `tenant-dev` would orphan existing operator data partitions).

#### Item 2: Eliminate Soft Fallback Defaults in Workflow Definitions
- In `.github/workflows/deploy-dev.yml` (L101-102) and `.github/workflows/deploy-staging.yml` (L84-85), remove `|| 'tenant-dev'` and `|| 'tenant-staging'`.
- Require unconfigured deployments to fail closed at `scripts/deploy_cloud_run_waji.sh:45-46` rather than silently inventing a synthetic tenant partition.

#### Item 3: Enforce Tenant Scope Binding on `external-fetch` API Endpoint
- In `POST /api/v1/jobs` (`apps/api/app/routes/external_data.py` or `apps/api/oday_api/main.py:764`), apply tenant scope verification for `external-fetch` payloads (matching the rule implemented for `forecast` jobs).
- Reject any payload specifying a `tenant_id` that differs from the authenticated principal with `TENANT_SCOPE_MISMATCH` (HTTP 400).
- Rewrite `payload["tenant_id"]` to match the active authenticated tenant ID before enqueueing.

#### Item 4: Remove Guessing Behavior from E2E Gate Script
- In `scripts/e2e/check_live_e2e_gate.py:1315` (`_enqueue_body`), eliminate the fallback chain (`ODP_SCHEDULED_INGESTION_TENANT_ID` / `ODP_TENANT_ID` / `"tenant-e2e"`).
- Derive the probe payload tenant strictly from the authenticated identity holding the gate credentials.

### 3.3 Regression Test Specifications
1. **API Scope Binding Unit Test:** Verify `POST /api/v1/jobs` rejects `external-fetch` payloads with a mismatched `tenant_id` and returns `TENANT_SCOPE_MISMATCH`.
2. **Partition Consistency Test:** Assert that `ExternalIngestionService` and `TenantScopedDocumentStore` write to and read from identical partition digests for all valid principal claims.

---

## 4. Dependency Map

| # | Upstream Dependency / Context | Parent Consumer | Expected Result / Output | Fail-Closed Condition |
|---|-------------------------------|-----------------|--------------------------|-----------------------|
| **D1** | T10 Evidence (`docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-DIAG-001/README.md`) | T11 Owner (`Claude2`/`Antigravity5`) | Root cause & 4-item remediation specification | T11 must not attempt arbitrary trial-and-error fixes outside T10 diagnosis |
| **D2** | T00 Writable Path Ceiling (`ai-status.json`) | T11 Commit Scope | Strict compliance with 7 declared writable globs | Commit leaking outside declared writable paths |
| **D3** | Deploy Dev Gate Authority (`live-e2e-gate.json`) | T11 Verification | `data:ingestion_runs`, `data:admin_boundary.official_dataset:run_exists`, `data:poi.commercial_api:run_exists` all PASS | Any `data:*` check remaining FAIL |
| **D4** | `ODP_AUTH_PRINCIPAL_MAP_SECRET` | Environment Config | Tenant ID resolved to `a11ce505-70bc-56d9-8564-ad22efa23c9e` | Hardcoding unverified tenant string |
| **D5** | API Endpoint (`apps/api/app/routes/external_data.py`) | Application Layer | `TENANT_SCOPE_MISMATCH` enforced on `external-fetch` | Unauthenticated tenant override allowed |
| **D6** | Worker Handler (`apps/worker/oday_worker/handlers.py`) | Execution Layer | `IngestionRunRecord` written to authenticated partition | Writes landing in `tenant-dev` or unscoped partition |
| **D7** | Workflow Manifests (`deploy-dev.yml`, `deploy-staging.yml`) | CI/CD Infrastructure | Removal of `\|\| 'tenant-dev'` fallback | Deployment silently falling back to synthetic tenant |
| **D8** | Gate Script (`scripts/e2e/check_live_e2e_gate.py`) | Validation Infrastructure | `_enqueue_body` deriving tenant from auth principal | Gate probing tenant partition different from readback |
| **D9** | Downstream Task T30 (`ODP-P10-DEV-REDEPLOY-VERIFY-001`) | Fleet Dispatch | T30 unblocked only after T11 completes and merges | T30 triggered before T11 candidate deployment |
| **D10**| Independent Reviewer (`Antigravity6`) | Quality Gate | Exact-head review & evidence validation pass | Merging without exact-head approval |

### Intended Composition & Flow Architecture

```text
  +-----------------------------------------------------------------------+
  | T10 Diagnosis (PR #759 Merged)                                        |
  | Root cause: Write/Read Tenant Split (tenant-dev vs a11ce505...)       |
  +-----------------------------------------------------------------------+
                                     |
                                     v
  +-----------------------------------------------------------------------+
  | T11 Remediation Execution (ODP-P10-LIVE-EXTDATA-REMEDIATE-001)        |
  | 1. Align ODP_SCHEDULED_INGESTION_TENANT_ID to a11ce505...             |
  | 2. Remove || 'tenant-dev' fallback in deploy-dev.yml                  |
  | 3. Enforce TENANT_SCOPE_MISMATCH in POST /api/v1/jobs                 |
  | 4. Remove fallback chain in check_live_e2e_gate.py                    |
  +-----------------------------------------------------------------------+
                                     |
                                     v
  +-----------------------------------------------------------------------+
  | Verification & Evidence Generation                                    |
  | - Focused Pytest regression pass                                      |
  | - Candidate Deploy Dev run executed                                   |
  | - live-e2e-gate.json confirms data:* checks PASS (runs >= 1)         |
  | - Evidence saved: docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/ |
  +-----------------------------------------------------------------------+
                                     |
                                     v
  +-----------------------------------------------------------------------+
  | Review & Task PR Finalization (Antigravity6 Reviewer)                 |
  | - Task PR merged into origin/dev                                      |
  | - T11 status moved to done                                            |
  | - Unblocks T30 (ODP-P10-DEV-REDEPLOY-VERIFY-001)                      |
  +-----------------------------------------------------------------------+
```

---

## 5. Acceptance Checklist & Replay Matrix

### 5.1 Acceptance Checklist

- [ ] **Item 1: Deterministic Regression Test**
  A test reproducing worker enqueue success with mismatched tenant readback failure exists and passes, proving the fix prevents regression.
- [ ] **Item 2: Scoped Code & Config Fix**
  `POST /api/v1/jobs` enforces tenant matching, workflow fallbacks are removed, and ingestion tenant is aligned with operator tenant `a11ce505-70bc-56d9-8564-ad22efa23c9e`.
- [ ] **Item 3: Real Candidate Gate Execution**
  Candidate deployment produces non-empty `SUCCEEDED` runs for both required providers (`admin_boundary.official_dataset` and `poi.commercial_api`), turning all `data:*` checks in `live-e2e-gate.json` green.
- [ ] **Item 4: Fail-Closed Invariants Intact**
  Retry, idempotency, DQ quarantine, tenant partition isolation, audit trail, and error classification remain strictly fail-closed.
- [ ] **Item 5: CI & Quality Gates Pass**
  Focused tests, integration tests, live-gate, Ruff linter, git diff scope check, and exact-head CI pass with zero violations.
- [ ] **Item 6: Independent Review & Evidence**
  Independent review by `Antigravity6` and rollback evidence directory (`docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/`) are committed and verified prior to PR merge.

### 5.2 Reviewer Replay Commands (For Antigravity6)

```bash
# 1. Verify working branch and HEAD commit
git branch --show-current
git log -n 1 --stat

# 2. Verify diff scope strictly adheres to T11 writable paths ceiling
git diff --name-only origin/dev...HEAD

# 3. Run focused regression tests for external_data tenant scope binding
pytest tests/unit/external_data/ -k "tenant_scope" -v

# 4. Inspect evidence artifact directory
ls -la docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/

# 5. Verify live E2E gate output from candidate run
jq '.checks[] | select(.name | startswith("data:"))' \
  docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/live-e2e-gate.json
```

---

## 6. Risk Register & Mitigation Strategy

| Risk ID | Risk Description | Severity | Mitigation Strategy |
|---------|------------------|----------|---------------------|
| **R1** | **Scope Creep / Writable Leak:** Modifications extending into UI, models, or core RBAC. | High | Strict enforcement of `worker_commit.py` with `--scope` matching T11 writable ceiling. |
| **R2** | **Tenant Partition Mutation:** Re-pointing principal map instead of ingestion variable, orphaning live data. | High | Explicitly follow Item 1 direction: align ingestion variable to operator tenant `a11ce505-70bc-56d9-8564-ad22efa23c9e`. |
| **R3** | **Unconfigured Deployment Failure:** Removing defaults without updating environment vars breaks staging/dev deploys. | Medium | Ensure environment variables are populated in Secret Manager / GitHub repository settings prior to triggering candidate deploy. |
| **R4** | **Uncovered CI Flakes:** Non-required flaky gates (e.g. `performance-gate`) emitting false CI failures. | Low | Verify required status checks (`orchestrator`, `product`, `product-e2e-gate`, `task-review-gate`) rather than non-blocking noise. |

---

## 7. Summary & Handoff Recommendation

This acceptance packet establishes the complete, verifiable specification for task `ODP-P10-LIVE-EXTDATA-REMEDIATE-001` (T11). Upon unblocking:
1. The parent owner (`Claude2` / `Antigravity5`) should implement the 4-item remediation blueprint within the declared writable ceiling.
2. Generate required runtime evidence under `docs/evidence/runtime/ODP-P10-LIVE-EXTDATA-REMEDIATE-001/`.
3. Submit for independent review to `Antigravity6`.
4. Merge task PR to `origin/dev` and finalize status via `ai-status.sh done`.
