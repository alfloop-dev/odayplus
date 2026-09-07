# ODP-DEV-BUILD-ARTIFACT-HANDOFF-003 Build Dispatch Evidence

Task: ODP-DEV-BUILD-ARTIFACT-HANDOFF-003
Owner: Antigravity3
Reviewer: Codex2
Repository: alfloop-dev/odayplus

## 1. Candidate Selection

### Exact Candidate SHA (C)

```
596b9c9a1788d952811a2bf8d4bba8a4e4d76b12
```

Source: `origin/dev` at dispatch time 2026-09-07T15:48:54Z.

### Dependency Verification

All three upstream fixes merged into C via first-parent ancestry:

| Dependency Task | PR | Merge Commit in dev |
|---|---|---|
| ODP-RUNTIME-RELEASE-DISPATCH-CLI-INTEGRATION-001 | #1206 | `74530caf` |
| ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001 | #1208 | `b9dd2e7b` |
| ODP-RELEASE-GATE-FIXTURE-STAGING-002 | #1212 | `17393dd4` |

Ancestry confirmed: `04e1572f` (old candidate from run 33942097235) is ancestor of `596b9c9a`.

### Not Included in C (AVM Waiting Scope)

| Task | PR | Status | Reason |
|---|---|---|---|
| ODP-AVM-QUALITY-NULLABLE-001 | #1149 | Open / In Review | AVM quality_score nullable handling; not blocking build gate |

## 2. Existing Build Run Check

No existing Runtime Release build run found for SHA `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`.

Previous runs examined:
- Run 33942097235 (2026-09-05) → SHA `04e1572f` → success (old candidate, pre-fix merges)
- No run for `596b9c9a`

Decision: dispatch a new build.

## 3. Workflow Inputs Audit

### Workflow File

`.github/workflows/deploy-dev.yml` (name: "Runtime Release" / "Deploy Dev")
Workflow ID: 302984644

### Dispatch Inputs

| Input | Value | Rationale |
|---|---|---|
| `phase` | `build` | Build-only, no deploy |
| `environment` | `dev` | Target environment |
| `release_sha` | `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` | Latest origin/dev |
| `task_id` | `ODP-DEV-BUILD-ARTIFACT-HANDOFF-003` | This task |
| `initial_release_recovery` | `true` | First dev release; no previous approved release exists |
| `release_lease` | (empty) | Build phase; lease not required |
| `manifest_run_id` | (empty) | Build phase |
| `manifest_digest` | (empty) | Build phase |
| `api_image` | (empty) | Build phase |
| `web_image` | (empty) | Build phase |
| `worker_image` | (empty) | Build phase |
| `scheduler_image` | (empty) | Build phase |
| `data_snapshot_*` | (empty) | No approved snapshot in dev-build vars |
| `external_sources_enabled` | (empty) | Sources-off standing posture |

### dev-build Environment Variables (names only)

All 12 required variables present:

1. `GCP_AR_REPO`
2. `GCP_PROJECT_ID`
3. `GCP_REGION`
4. `GCP_SERVICE_ACCOUNT`
5. `GCP_WORKLOAD_IDENTITY_PROVIDER`
6. `ODP_CLOUD_RUN_API_SERVICE`
7. `ODP_CLOUD_RUN_WEB_SERVICE`
8. `ODP_CLOUD_RUN_WORKER_JOB`
9. `ODP_CLOUD_RUN_SCHEDULER_JOB`
10. `ODP_CLOUD_RUN_MIGRATION_JOB`
11. `ODP_CLOUD_RUN_VPC_CONNECTOR`
12. `ODP_CLOUD_RUN_VPC_EGRESS`

No `ODP_APPROVED_DATA_SNAPSHOT_*` or `ODP_PREVIOUS_RELEASE_MANIFEST_PATH` variables configured (consistent with initial_release_recovery).

## 4. Build Run

- **Run ID**: 34140207274
- **URL**: https://github.com/alfloop-dev/odayplus/actions/runs/34140207274
- **Dispatched at**: 2026-09-07T15:48:54Z
- **Completed at**: ~2026-09-07T15:53:12Z
- **Head SHA**: 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12
- **Ref**: dev
- **Overall status**: ❌ **failure**

### Job Results

| Job | Status |
|---|---|
| Validate release phase inputs | ✅ success |
| Build once and publish the immutable artifact handoff | ❌ **failure** (step 19/27) |
| Verify the Supervisor lease authorises this deploy | ⏭️ skipped (build phase, expected) |

### Build Job Step-by-Step Results

| # | Step | Result |
|---|---|---|
| 1 | Set up job | ✅ success |
| 2 | Run actions/checkout@v4 | ✅ success |
| 3 | Assert exact release SHA is checked out | ✅ success |
| 4 | 確認 build 階段已綁定 environment 且變數齊備 | ✅ success |
| 5 | Publish build environment binding receipt | ✅ success |
| 6 | Install uv | ✅ success |
| 7 | Set up Python | ✅ success |
| 8 | Run Secret Scan | ✅ success |
| 9 | Run Python SAST Scan | ✅ success |
| 10 | Run production npm audit gate | ✅ success |
| 11 | Publish production npm audit receipt | ✅ success |
| 12 | Generate SBOM | ✅ success |
| 13 | Install locked project dependencies | ✅ success |
| 14 | Run E2E deployment health, backup, restore, and rollback proof | ✅ success |
| 15 | Authenticate to Google Cloud (WIF) | ✅ success |
| 16 | Set up Cloud SDK | ✅ success |
| 17 | 讀回部署 target 以確認沒有既有已核准 release | ✅ success |
| 18 | Install Cosign | ✅ success |
| 19 | **Build, publish, sign, and attest immutable container images** | ❌ **failure** |
| 20 | Write the build-once artifact handoff | ⏭️ skipped |
| 21 | Publish immutable image handoff | ⏭️ skipped |
| 22 | Publish candidate release manifest | ⏭️ skipped |
| 23 | Publish initial-release target absence readback | ⏭️ skipped |

## 5. Root Cause Analysis

### Failure Point

Step 19: "Build, publish, sign, and attest immutable container images"

The API container image was **built successfully** locally on the runner:
```
#15 naming to asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev/oday-api:release-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12 done
```

The `docker push` to Artifact Registry then **failed**:
```
denied: This API method requires billing to be enabled.
Please enable billing on project #767864276141
by visiting https://console.developers.google.com/billing/enable?project=767864276141
then retry.
```

### Root Cause

**GCP project billing is disabled or unlinked.** The Artifact Registry API on project
`odayplus-runtime-20260825` (project number `767864276141`) does not accept push
operations because billing is not enabled.

### Previous Run Comparison

The last successful run (33942097235) completed on **2026-09-05T03:41:13Z** with full
image push and signing. Billing was working at that time. Between 2026-09-05 and
2026-09-07, billing on this GCP project was disabled or the billing account was
unlinked.

### What Passed Before Failure

All security, quality, and deployment validation gates passed:
- Secret scan ✅
- Python SAST ✅
- npm audit gate ✅
- SBOM generation ✅
- E2E deployment health/backup/restore/rollback ✅
- WIF authentication ✅
- Cloud SDK setup ✅
- Initial release target absence readback ✅
- Cosign installation ✅
- Docker build ✅ (image built locally)

Only the `docker push` failed — the issue is purely GCP infrastructure, not code.

## 6. Minimal Fix Required

### Human/Ops Action

1. **Re-enable billing** on GCP project `odayplus-runtime-20260825` (project number `767864276141`):
   - Visit https://console.developers.google.com/billing/enable?project=767864276141
   - Link an active billing account
   - Wait a few minutes for propagation

2. **After billing is restored**, re-run the same dispatch:
   ```
   gh workflow run deploy-dev.yml \
     --repo alfloop-dev/odayplus \
     --ref dev \
     -f phase=build \
     -f environment=dev \
     -f release_sha=596b9c9a1788d952811a2bf8d4bba8a4e4d76b12 \
     -f task_id=ODP-DEV-BUILD-ARTIFACT-HANDOFF-003 \
     -f initial_release_recovery=true
   ```

### What Cannot Be Fixed by Auto Worker

- GCP billing configuration requires Human/Ops authority
- No code, workflow, or toolchain change can resolve this
- The candidate SHA 596b9c9a is correct; the dispatch inputs are correct
- Only the GCP infrastructure block must be cleared

## 7. Build Artifacts

### Published Artifacts (partial — only pre-failure receipts)

| Artifact | Status |
|---|---|
| `release-phase-receipt-dev-build` | ✅ Published (phase validation passed) |
| `release-environment-receipt-dev-build` | ✅ Published (environment binding passed) |
| `release-npm-audit-receipt-dev` | ✅ Published (npm audit passed) |
| `runtime-release-images-596b9c9a...` | ❌ Not published (build failed at push) |
| `runtime-release-manifest-596b9c9a...` | ❌ Not published (handoff step skipped) |
| `initial-release-absence-readback-596b9c9a...` | ❌ Not published (upload step skipped) |

### Egress Contract Digests

Not available — manifest was not generated due to image push failure.

## 8. Handoff to ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002

**Cannot complete handoff.** The build did not produce the immutable artifact handoff
(manifest, image digests, SBOM refs, signature refs) required for the downstream
task. A successful build is required before handoff.

The following information is pre-staged for immediate use once billing is restored
and a successful build completes:

- **Candidate C**: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- **Target task**: ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002
- **Target task PR**: #1205 (open, branch `task/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`)
- **Target task owner**: Antigravity5
- **Target task reviewer**: Codex2

## 9. Prohibitions Confirmed

- ❌ Did not modify `.github/workflows/` or any workflow file
- ❌ Did not sign a lease or dispatch a deploy
- ❌ Did not enable external sources or switch traffic
- ❌ Did not output secret values (only variable names recorded)
- ❌ Did not forge Human/Ops or reviewer approval
- ❌ Did not re-run the same failing run (billing issue requires Human/Ops fix first)
