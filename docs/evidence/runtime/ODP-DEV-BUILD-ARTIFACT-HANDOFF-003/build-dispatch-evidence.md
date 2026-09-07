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

### What Executed Successfully Before Failure

The following pre-image-push gates executed and completed successfully:
- Secret scan ✅ (step 8)
- Python SAST ✅ (step 9)
- npm audit gate ✅ (step 10)
- SBOM generation ✅ (step 12 — local CycloneDX file only; never attested onto any image)
- E2E deployment health/backup/restore/rollback ✅ (step 14)
- WIF authentication ✅ (step 15)
- Cloud SDK setup ✅ (step 16)
- Initial release target absence readback ✅ (step 17)
- Cosign installation ✅ (step 18)
- Docker build of API image ✅ (step 19 — local build succeeded)
- Docker push of API image ❌ (step 19 — `denied: billing not enabled` on project #767864276141)

### What Was NEVER Executed (Skipped Due to Prior Failure)

The following operations within step 19 and all subsequent steps were **never executed**:
- Cosign signing of API image ❌ (not executed — push failed before signing)
- Cosign signature verification of API image ❌ (not executed)
- SBOM attestation of API image ❌ (not executed)
- Build/push/sign/attest of Worker image ❌ (not executed — `build_publish_sign_attest` loop exited at API)
- Build/push/sign/attest of Scheduler image ❌ (not executed)
- Build/push/sign/attest of Web image ❌ (not executed)
- Supply chain ref resolution (signature + SBOM digest refs) ❌ (not executed)
- Write build-once artifact handoff (step 20) ❌ (skipped by GitHub Actions)
- Publish immutable image handoff (step 21) ❌ (skipped)
- Publish candidate release manifest (step 22) ❌ (skipped)
- Publish initial-release target absence readback (step 23) ❌ (skipped)

**Assessment**: The billing failure at `docker push` is a GCP infrastructure issue, not a code defect. However, all operations downstream of `docker push` — including Cosign signing, signature verification, SBOM attestation, three additional image builds, manifest generation, and artifact publication — were never executed and remain **unverified**.

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
- No code, workflow, or toolchain change can resolve the billing block
- The candidate SHA `596b9c9a` and dispatch inputs are correct for a re-run, but the build must complete *all* steps (push, signing, attestation, manifest, supply chain refs) to produce verifiable artifacts
- Only the GCP infrastructure block must be cleared before a re-run is attempted

## 7. Build Artifacts — NONE PRODUCED

> **No immutable release artifacts were produced by this build run.** The build
> failed at `docker push` (step 19) due to GCP billing denial. All six required
> egress contract artifact categories remain undelivered.

### Pre-Failure CI Receipts (informational only; not release artifacts)

| Artifact ID | Artifact Name | Status |
|---|---|---|
| 10025584937 | `release-phase-receipt-dev-build` | Uploaded (phase validation receipt) |
| 10025591210 | `release-environment-receipt-dev-build` | Uploaded (environment binding receipt) |
| 10025603772 | `release-npm-audit-receipt-dev` | Uploaded (npm audit receipt) |

### Required Immutable Release Artifacts — ALL MISSING

| # | Required Artifact | Status | Notes |
|---|---|---|---|
| 1 | Immutable container images (4: api, web, worker, scheduler) | ❌ **Not produced** | Only API image was built locally; push failed, no image exists in Artifact Registry for this SHA. Worker, scheduler, web images were never built. |
| 2 | Candidate release manifest (`RELEASE_MANIFEST.json`) | ❌ **Not produced** | Step 20 (write handoff) was skipped by GitHub Actions |
| 3 | SBOM attestations (per-image CycloneDX) | ❌ **Not produced** | SBOM was generated locally (step 12) but never attested onto any image via Cosign |
| 4 | Cosign signatures (per-image) | ❌ **Not produced** | `cosign sign` was never executed for any image |
| 5 | Supply chain provenance refs (signature + SBOM digest refs) | ❌ **Not produced** | `resolve_supply_chain_ref` was never reached |
| 6 | Six-file egress contract digest set | ❌ **Not produced** | Requires manifest which was not generated |

### Egress Contract Digests — NOT AVAILABLE

No egress contract digests can be reported because the manifest was never generated.
The six required files (4 image identity digests + manifest digest + SBOM digest) do not exist.

## 8. Handoff to ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002

**Handoff status: BLOCKED — cannot complete.**

The build did not produce any of the required immutable artifacts:
- ❌ No immutable container image digests (4 images required)
- ❌ No candidate release manifest (`RELEASE_MANIFEST.json`)
- ❌ No SBOM attestation refs
- ❌ No Cosign signature refs
- ❌ No supply chain provenance digest refs
- ❌ No egress contract digest set (6 files)

**Acceptance criterion**: "未交接真實可驗證產物不可 done" — this task cannot be marked done
without delivering real, verifiable artifacts. A failure RCA checkpoint alone
does not satisfy the task acceptance.

The following pre-staged information is ready for immediate use once a successful
build completes:

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
- ❌ Did not declare done without verifiable artifacts

## 10. Canonical Blocker

**Blocker**: GCP billing disabled on project `odayplus-runtime-20260825` (project number `767864276141`)

| Field | Value |
|---|---|
| Blocker type | Infrastructure — requires Human/Ops authority |
| Blocking action | `docker push` to Artifact Registry denied |
| GCP project | `odayplus-runtime-20260825` (project #767864276141) |
| Error | `denied: This API method requires billing to be enabled.` |
| Restoration URL | https://console.developers.google.com/billing/enable?project=767864276141 |
| Last known working | 2026-09-05T03:41:13Z (run 33942097235, SHA `04e1572f`, different candidate) |
| Failed at | 2026-09-07T15:53:11.768Z (run 34140207274, SHA `596b9c9a`) |
| Evidence | https://github.com/alfloop-dev/odayplus/actions/runs/34140207274/job/101800303595 |

**What must happen before this task can proceed**:
1. Human/Ops restores billing on GCP project #767864276141
2. Billing restoration is evidenced (e.g., successful Artifact Registry push test)
3. Owner (Antigravity3) checks whether the existing failed run can be reused, or dispatches a new build-only run
4. Successful build produces all 6 artifact categories
5. Owner downloads and verifies exact artifacts, raw hashes, 4 image identities, supply chain refs, and egress digest
6. Owner completes handoff to PR#1205 (Antigravity5/Codex2)

**What must NOT happen**:
- ❌ Do not re-run the build without evidence of billing restoration
- ❌ Do not declare done without delivering real verifiable artifacts
- ❌ Do not deploy, sign a lease, or enable external sources
- ❌ Do not force push or overwrite task history

## 11. Review History

| Date | Actor | Action | Summary |
|---|---|---|---|
| 2026-09-07T15:48:54Z | Antigravity3 | Dispatch | Build dispatched for C=`596b9c9a`, run 34140207274 |
| 2026-09-07T15:53:12Z | GitHub Actions | Failure | Step 19 failed: billing denied on docker push |
| 2026-09-07T~16:20Z | Antigravity3 | PR submission | PR#1237 submitted with evidence (head `92203b2f`) |
| 2026-09-07T16:27:07Z | Codex2 | Review rejection | P1: no artifacts delivered; P2: evidence overstates verification scope |
| 2026-09-07T16:32Z | Antigravity3 | Evidence correction | Corrected P1/P2 findings; set canonical blocker for billing restoration |
