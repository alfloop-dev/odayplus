# ODP-DEV-BUILD-ARTIFACT-HANDOFF-003 Build Dispatch Evidence

Task: ODP-DEV-BUILD-ARTIFACT-HANDOFF-003
Owner: Claude2 (reassigned from Antigravity3 on 2026-09-07T17:10:27Z)
Reviewer: Codex2
Repository: alfloop-dev/odayplus

> **Current state (2026-09-08): the handoff is COMPLETE.** Billing was restored
> by Human/Ops, run `34179791241` succeeded at candidate C, and all five
> previously undelivered artifact categories have been downloaded and verified.
> **Section 13 is the authoritative delivery record.** Sections 5–11 describe the
> earlier failed attempt (run `34140207274`) and are retained as history; their
> "blocked" conclusions are superseded by section 13 and must not be read as the
> current state.

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

12 variables are configured in the `dev-build` environment (enumerated via
`gh api --paginate repos/alfloop-dev/odayplus/environments/dev-build/variables`;
pagination matters — the unpaginated call caps at 30 and can silently undercount).
Names and existence only; no values are read or recorded here.

The build-phase checker requires **11** of them, and the downloaded
`release-environment-receipt.json` shows all 11 resolved with
`missing_variables: []`. Item 10 below is configured but is **not** in the
build-phase required set, so "12 configured" and "11 required and resolved"
are both true and must not be conflated:

1. `GCP_AR_REPO`
2. `GCP_PROJECT_ID`
3. `GCP_REGION`
4. `GCP_SERVICE_ACCOUNT`
5. `GCP_WORKLOAD_IDENTITY_PROVIDER`
6. `ODP_CLOUD_RUN_API_SERVICE`
7. `ODP_CLOUD_RUN_WEB_SERVICE`
8. `ODP_CLOUD_RUN_WORKER_JOB`
9. `ODP_CLOUD_RUN_SCHEDULER_JOB`
10. `ODP_CLOUD_RUN_MIGRATION_JOB` — configured; not required by the build phase
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

## 7. Build Artifacts — Registry-Dependent Categories Not Produced

> The build failed at `docker push` (step 19) on a GCP billing denial, so every
> artifact category that depends on the container registry is absent. Two
> categories do **not** depend on the registry and have now been produced and
> independently verified: the pre-failure CI receipts (with raw hashes) and the
> six-file egress contract digest, which is computed over checked-in sources.

### Pre-Failure CI Receipts — verified raw hashes

Downloaded and hashed independently on 2026-09-07T17:16Z by Claude2. For each
artifact the locally computed zip SHA-256 matched the GitHub API `digest` field
exactly, so these are verified raw hashes, not restated API metadata.

| Artifact ID | Artifact Name | Bytes | Raw zip SHA-256 (verified) | Inner file | Inner file SHA-256 |
|---|---|---|---|---|---|
| 10025584937 | `release-phase-receipt-dev-build` | 632 | `ac593cce60f9f70222f44319e30ca833810bce7e9fe4d5b0b59d3eac275738c4` | `release-phase-receipt.json` (765 B) | `9828536c66134ab076691fd7a6377e871acaa5283e6937b44938e4338ed5b316` |
| 10025591210 | `release-environment-receipt-dev-build` | 796 | `b034d6c29752231db469b29abc2d718651130b1d05e1af75ac96d9ee8b72efd6` | `release-environment-receipt.json` (1127 B) | `bbafd34d78e8fd8137f8f84505a450d15947dfcba5b75f73628907c27de28e21` |
| 10025603772 | `release-npm-audit-receipt-dev` | 508 | `25aa31578bcc8c3a4d5c004a5a97352b0d48afa7f9113a5c3091327e236f6134` | `npm-audit-receipt.json` (605 B) | `578d7533be844b90b1fe467d74e752d6505c70513bbb019b152a34c2ecb02858` |

Reproduction (read-only):

```bash
gh api repos/alfloop-dev/odayplus/actions/runs/34140207274/artifacts \
  --jq '.artifacts[] | "\(.id)\t\(.name)\t\(.digest)"'
gh api repos/alfloop-dev/odayplus/actions/artifacts/<ID>/zip > <ID>.zip
sha256sum <ID>.zip     # must equal the API digest above
```

These receipts are **not** release artifacts. They are admissible only as proof
that the dispatch stayed inside the granted build-only authority. Two fields are
load-bearing for that claim, both read back from the downloaded receipts:

- `release-phase-receipt.json`: `phase=build`, `environment=dev`,
  `release_sha=596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`,
  `task_id=ODP-DEV-BUILD-ARTIFACT-HANDOFF-003`, **`lease_supplied: false`**, and
  `image_handoff` / `manifest_handoff` all `null` — i.e. no lease was signed and
  no deploy-only input was supplied, as the acceptance requires.
- `release-environment-receipt.json`: `github_environment=dev-build` with 11
  variables resolved and `missing_variables: []`. Names and booleans only; the
  single non-secret value present in the receipt is
  `ODP_CLOUD_RUN_VPC_EGRESS=all-traffic`. No secret values are recorded here.

### Required Immutable Release Artifacts

| # | Required Artifact | Status | Notes |
|---|---|---|---|
| 1 | Immutable container images (4: api, web, worker, scheduler) | ❌ **Not produced** | `api` image was built locally only; its push was denied. `web`, `worker`, `scheduler` were never built. No image exists in Artifact Registry for this SHA. |
| 2 | Candidate release manifest (`RELEASE_MANIFEST.json`) | ❌ **Not produced** | Step 22 (publish candidate release manifest) was skipped |
| 3 | SBOM attestations (per-image CycloneDX) | ❌ **Not produced** | SBOM was generated locally (step 12) but never attested onto any image via Cosign |
| 4 | Cosign signatures (per-image) | ❌ **Not produced** | `cosign sign` was never executed for any image |
| 5 | Supply chain provenance refs (signature + SBOM digest refs) | ❌ **Not produced** | `resolve_supply_chain_ref` was never reached |
| 6 | Six-file egress contract digest | ✅ **Produced and verified** | Computed over checked-in sources at C; does not depend on the build. See below. |

Categories 1–5 are all registry-dependent and cannot be produced until the
billing blocker in section 10 is cleared.

> **Do not mistake the local image ID for an image identity.** The job log at
> `2026-09-07T15:53:04.9835923Z` shows `writing image
> sha256:ed7d32f14ab15235c606c96d865bc7aafc6dd3b265ab5ece8edd41c20a79dade`. That
> is a local Docker build ID for the `api` image on the runner. It was never
> pushed, has no registry manifest digest, and is **not** an immutable image
> identity. It must not be handed off or recorded as one.

### Six-File Egress Contract Digest — CORRECTED DEFINITION

An earlier revision of this document defined the "six-file egress contract" as
four image digests plus a manifest digest plus an SBOM digest, and claimed it
could not be computed until the manifest existed. **That was wrong on both
counts** and is corrected here.

The authoritative definition at candidate C is
`delivery_toolchain/release/release_manifest.py:243-250`,
`SOURCES_OFF_EGRESS_CONTRACT_FILES` — six **checked-in source files**, described
in that module as "the checked-in contract inputs for the one Runtime Release
path". The digest is produced by
`compute_sources_off_egress_contract_digest(root)` at lines 1606-1612, which
delegates to `compute_file_set_digest()`. It reads only files from the
repository tree, so it is fully computable at C with no build, no registry, and
no GCP access.

**Computed at C = `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`:**

```
egress_contract_digest = sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09
```

Constituent files and their content hashes at C:

| # | Path (at C) | SHA-256 of file content |
|---|---|---|
| 1 | `.github/workflows/deploy-dev.yml` | `bd326d199676f3d784fcff6a6be19b54a25876f9d1dce41435c5db889b2c36da` |
| 2 | `product_ops/deployment/deploy_cloud_run_waji.sh` | `caa9b00fec1c7823f875a36a6e008ef0d6457d8c85088c30726fcd2e751e1534` |
| 3 | `infra/terraform/cloud_run.tf` | `48c6d549084ee596e76f14707eab8ca81113767f10d7ff18e8099b4f2deafd37` |
| 4 | `infra/terraform/network.tf` | `61fc7a27d56431c37d85d950e72210532b133b6274082858f31d6d7d0d4b02db` |
| 5 | `product_ops/deployment/staging_lifecycle.py` | `7a0e6f6bd2f7a3ada7ad6dfadea4d3e519e0858f2da1a7c0a52412f49d71365d` |
| 6 | `product_ops/deployment/cloud_run_job_entrypoint.py` | `2dca1d31042b1aff4c8e105f2015f51e0c549a9203ef6cb394b3dc76b9ff400f` |

The per-file hashes are listed for auditability only. The contract digest is
**not** a hash of that list: `compute_file_set_digest` sorts the absolute paths,
then feeds `relative_path || NUL || file_bytes || NUL` per file into one SHA-256.
Recompute it with the function, never by rehashing the table.

Reproduction — uses C's own function, no reimplementation:

```bash
mkdir -p /tmp/odp-c-egress/root /tmp/odp-c-egress/mod
git archive 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12 \
  .github/workflows/deploy-dev.yml \
  product_ops/deployment/deploy_cloud_run_waji.sh \
  infra/terraform/cloud_run.tf infra/terraform/network.tf \
  product_ops/deployment/staging_lifecycle.py \
  product_ops/deployment/cloud_run_job_entrypoint.py \
  | tar -x -C /tmp/odp-c-egress/root
git show 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12:delivery_toolchain/release/release_manifest.py \
  > /tmp/odp-c-egress/mod/release_manifest.py
python3 -c "
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('rm_at_c', '/tmp/odp-c-egress/mod/release_manifest.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.compute_sources_off_egress_contract_digest(root=Path('/tmp/odp-c-egress/root')))
"
# => sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09
```

Run twice against two independent extractions on 2026-09-07T17:15Z; both
produced the digest above. `release_manifest.py` at C imports stdlib only, so it
loads standalone without the rest of the toolchain.

**Scope limit — this is a source-contract digest, not a runtime readback.** It
attests only that the six checked-in contract inputs at C hash to the value
above. It says nothing about the egress posture of any deployed runtime, and it
must never be presented as one. The runtime side of the sources-off posture is a
separate deploy-time artifact — the probe receipt at
`.odp_data/deployment/public-egress-probe.json`
(`SOURCES_OFF_RUNTIME_PROBE_RECEIPT`) — which this task neither produced nor is
authorised to produce.

## 8. Handoff to ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002

**Handoff status: SUPERSEDED — this section records the partial handoff as of
2026-09-07, when the build was still blocked. The complete handoff is in
section 13.**

Delivered now, directly usable by the downstream evidence-only C→E task:

- ✅ **Candidate C**: `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`
- ✅ **Six-file egress contract digest**: `sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09`, with the corrected source definition, the six constituent files, their content hashes, and a reproduction that calls C's own function (section 7)
- ✅ **Build run identity**: run `34140207274`, attempt 1, job `101800303595`, `completed/failure`; confirmed as the only Runtime Release run for C
- ✅ **Three CI receipt artifacts** with verified raw zip and inner-file hashes (section 7)
- ✅ **Authority proof**: `lease_supplied: false` and null image/manifest handoff in the phase receipt — build-only scope was not exceeded

Still blocked, and required before the downstream task can close:

- ❌ Four immutable image identities (api, web, worker, scheduler)
- ❌ Candidate release manifest (`RELEASE_MANIFEST.json`)
- ❌ SBOM attestation refs
- ❌ Cosign signature refs
- ❌ Supply chain provenance digest refs

**Acceptance criterion**: "未交接真實可驗證產物不可 done" — this task is **not**
done. One artifact category has been delivered; five have not. The remaining
five are gated on the Human/Ops billing restoration in section 10, not on any
code, workflow, or toolchain change.

Downstream target, unchanged:

- **Target task**: ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002
- **Target task PR**: #1205 (open, branch `task/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`)
- **Target task owner**: Antigravity5
- **Target task reviewer**: Codex2

## 9. Prohibitions Confirmed

- ❌ Did not modify `.github/workflows/` or any workflow file
- ❌ Did not sign a lease or dispatch a deploy (`lease_supplied: false`, verified in the downloaded phase receipt)
- ❌ Did not enable external sources or switch traffic
- ❌ Did not output secret values (only variable names and booleans recorded)
- ❌ Did not forge Human/Ops or reviewer approval
- ❌ Did not re-run the failing build (billing restoration is unevidenced; a re-run would fail identically)
- ❌ Did not declare done without verifiable artifacts
- ❌ Did not reimplement the egress digest algorithm; C's own function was executed
- ❌ Did not force push or rewrite task history

## 10. Canonical Blocker (RESOLVED 2026-09-08 — historical)

> This blocker was cleared. Human/Ops restored billing on project
> `767864276141`; the first successful push and signature under restored
> billing is timestamped `2026-09-08T02:17:01Z` in the Rekor transparency
> log (section 13.3). The record below is kept because it documents what
> was blocked and why; it is no longer the task's state.

**Blocker**: GCP billing disabled on project `odayplus-runtime-20260825` (project number `767864276141`)

| Field | Value |
|---|---|
| Blocker type | Infrastructure — requires Human/Ops authority |
| Blocking action | `docker push` to Artifact Registry denied |
| GCP project | `odayplus-runtime-20260825` (project #767864276141) |
| Error | `denied: This API method requires billing to be enabled.` |
| Restoration URL | https://console.developers.google.com/billing/enable?project=767864276141 |
| Last known working | run 33942097235, SHA `04e1572f` (different candidate): created 2026-09-05T03:31:38Z, completed `success` 2026-09-05T03:41:13Z |
| Failed at | 2026-09-07T15:53:11.7685020Z (run 34140207274, SHA `596b9c9a`), exit code 1 at 15:53:11.7713620Z |
| Evidence | https://github.com/alfloop-dev/odayplus/actions/runs/34140207274/job/101800303595 |
| Current blocker owner | Claude2 (reassigned from Antigravity3 on 2026-09-07T17:10:27Z) |

**Billing state is not directly observable from this worker.** `gcloud` is not
available to the auto worker, and the only other way to test the project's
billing state is to run a build — which is exactly what the reviewer prohibited
without prior restoration evidence. The billing status is therefore reported as
**last-observed-failed at 2026-09-07T15:53:11Z**, not as "still failing today".
Whoever acts on this blocker must establish the current state independently.

This is corroborated by an independent task rather than asserted. PR#1239
(ODP-GCP-STAGING-EXECUTION-PREFLIGHT-002), merged into `dev` at
`c4bf87d81d55` and composed into this branch by the base advance below, ran its
own read-only GCP probes from the same host. Its captured stderr at
`docs/evidence/runtime/ODP-GCP-STAGING-EXECUTION-PREFLIGHT-002/raw/projects-describe-runtime.err`
shows `gcloud projects describe` failing with `Reauthentication failed. cannot
prompt during non-interactive execution`, and no file in that evidence set
records any billing state. Two separate tasks therefore reached the same limit:
GCP billing cannot be read from an auto worker, and the only remaining probe is
a build dispatch, which is prohibited here until restoration is evidenced.

**What must happen before this task can proceed**:
1. Human/Ops restores billing on GCP project #767864276141
2. Billing restoration is evidenced independently of this task
3. Owner re-checks for a reusable successful run at C before dispatching anything
4. If none exists, owner dispatches one build-only run under the existing authority
5. Successful build produces the five remaining artifact categories
6. Owner downloads and verifies exact artifacts, raw hashes, 4 image identities, and supply chain refs, then completes the handoff to PR#1205 (Antigravity5/Codex2)

**What must NOT happen**:
- ❌ Do not re-run the build without evidence of billing restoration
- ❌ Do not declare done without delivering the remaining artifact categories
- ❌ Do not deploy, sign a lease, or enable external sources
- ❌ Do not force push or overwrite task history
- ❌ Do not present the section 7 source-contract digest as a runtime egress readback

## 11. Independent Re-Verification (2026-09-07, Claude2)

Ownership moved to Claude2 after two reviewer reopens. Every load-bearing claim
inherited from the previous owner was re-measured rather than restated. All
checks below are read-only; no build, deploy, or test suite was run.

| Claim | Method | Result |
|---|---|---|
| Run 34140207274 is the only Runtime Release run for C | `gh run list --workflow deploy-dev.yml --limit 15 --json headSha,conclusion` | Confirmed — single run at C, `completed/failure`; no newer run exists, so there is nothing reusable |
| Failure was a billing denial, not a code defect | Fetched job log, grepped for the denial | Confirmed at `2026-09-07T15:53:11.7685020Z`, exit code 1 at `15:53:11.7713620Z` |
| Steps after the push never executed | `gh api .../runs/34140207274/jobs`, step conclusions | Confirmed — step 19 `failure`; steps 20–23 (handoff, image handoff, manifest, target readback) `skipped`; steps 1–18 `success` |
| Only the `api` image reached a local build | Job log around the failure | Confirmed — `Building immutable api image...` then push denied; `web`/`worker`/`scheduler` never started |
| The three CI artifacts exist and are intact | Downloaded each zip, compared SHA-256 to the API digest | Confirmed — all three matched exactly |
| "All 12 required variables present" | Compared the paginated `dev-build` variable list against `variables_resolved` in the downloaded receipt | **Corrected** — 12 are configured, but the build phase requires 11; `ODP_CLOUD_RUN_MIGRATION_JOB` is configured and not required. Section 3 now states both separately |
| Local image ID is not an image identity | Read the job log line at `15:53:04.9835923Z` | Confirmed — it is a local build ID, never pushed; flagged in section 7 so it cannot be handed off by mistake |
| Reviewer's P2 (egress contract misdefined) | Read `release_manifest.py:243-250` and `:1606-1612` at C | Confirmed — reviewer was correct; definition corrected and the digest computed in section 7 |

## 12. Review History

| Date | Actor | Action | Summary |
|---|---|---|---|
| 2026-09-07T15:48:54Z | Antigravity3 | Dispatch | Build dispatched for C=`596b9c9a`, run 34140207274 |
| 2026-09-07T15:53:12Z | GitHub Actions | Failure | Step 19 failed: billing denied on docker push |
| 2026-09-07T~16:20Z | Antigravity3 | PR submission | PR#1237 submitted with evidence (head `92203b2f`) |
| 2026-09-07T16:27:07Z | Codex2 | Reopen 1 | P1: no artifacts delivered; P2: evidence overstated verification scope |
| 2026-09-07T16:32Z | Antigravity3 | Evidence correction | Corrected executed-vs-skipped framing; set billing blocker (head `e9a95f43`) |
| 2026-09-07T17:06:31Z | Codex2 | Reopen 2 | P1 unresolved: still no delivered artifacts. P2 (new): six-file egress contract misdefined as image/manifest/SBOM digests; real definition is six checked-in files |
| 2026-09-07T17:10:27Z | Orchestrator | Reassignment | Ownership moved Antigravity3 → Claude2 after 2 reopens; blocker cleared by reopen |
| 2026-09-07T17:17Z | Claude2 | Evidence correction | Fixed P2 definition and computed the real egress digest; verified raw hashes for the three CI artifacts; re-verified all inherited claims (section 11); reset the canonical billing blocker |
| 2026-09-08T02:11:41Z | ajoe734 (Human/Ops) | Dispatch | Build re-dispatched for C under restored billing, run `34179207603`; step 19 built/pushed/signed/attested all four images, step 20 failed on the missing rollback reference (`INITIAL_RELEASE_RECOVERY: false`) |
| 2026-09-08T02:17:01Z | GitHub Actions | Billing restored | First successful push + Rekor-logged signature at C, where the same push was denied on 2026-09-07 |
| 2026-09-08T02:21:49Z | ajoe734 (Human/Ops) | Dispatch | Re-dispatched with the initial-release readback enabled, run `34179791241`; images reused, all 23 steps `success`, manifest and handoff artifacts published |
| 2026-09-08T02:35:29Z | ajoe734 | Retry | `/retry` via ops issue #1236, announcing the successful run for the owner to consume |
| 2026-09-08 | Claude2 | Delivery | Re-measured the announced run independently, downloaded and verified all six artifacts, validated the manifest with C's own validator, recomputed the egress digest, and completed the handoff (section 13) |

## 13. Delivered Artifact Handoff (2026-09-08, Claude2)

Billing was restored by Human/Ops and a successful build now exists at candidate
C. This section is the authoritative delivery record and supersedes the blocked
conclusions in sections 5–11. Every value below was measured by this owner from
the GitHub API, the downloaded artifact bytes, or candidate C's own code — not
copied from the ops-bus notification that announced the run.

### 13.1 How this round started, and what this owner did NOT do

The GitHub ops issue #1236 asserted that billing was restored and that run
`34179791241` had succeeded. That assertion was treated as a lead, not as
evidence, and was re-measured independently.

**Neither run in this round was dispatched by this owner.** Both were dispatched
by `ajoe734` (Human/Ops). This owner did not dispatch a build, because the
acceptance requires re-checking for a reusable successful run at C first, and one
already existed. No 20-minute build was repeated.

### 13.2 Run identity and terminal state

| Field | Value |
|---|---|
| Run ID | `34179791241` |
| URL | https://github.com/alfloop-dev/odayplus/actions/runs/34179791241 |
| Workflow | Runtime Release (`302984644`, `.github/workflows/deploy-dev.yml`) |
| Event / actor | `workflow_dispatch` / `ajoe734` |
| Run attempt | 1 |
| Created / updated | `2026-09-08T02:21:49Z` / `2026-09-08T02:26:33Z` |
| Status / conclusion | `completed` / **`success`** |
| `head_sha` of the run | `8c570a56353abdcc8ba70fe0a3fdd9b963902391` |
| Candidate actually built (C) | `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` |

`head_sha` is the `dev` tip that supplied the *workflow definition*; it is not
the built candidate. The built tree is pinned by the `release_sha` input and
proved by step 3, `Assert exact release SHA is checked out`, which runs
`git rev-parse HEAD` and exits non-zero unless it equals `ODAY_RELEASE_SHA`.
That step passed with `ODAY_RELEASE_SHA: 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12`.

Job results — build ran, nothing else did:

| Job ID | Name | Conclusion |
|---|---|---|
| `101916378520` | Validate release phase inputs | `success` |
| `101916404681` | Build once and publish the immutable artifact handoff | `success` |
| `101917162346` | Verify the Supervisor lease authorises this deploy | `skipped` |
| `101917162318` | Deploy the admitted artifact by immutable digest | `skipped` |
| `101917162350` | Verify production watch and clean up ephemeral staging | `skipped` |

All 23 steps of job `101916404681` concluded `success`, including the four steps
that never executed in the failed attempt: step 20 `Write the build-once artifact
handoff`, step 21 `Publish immutable image handoff`, step 22 `Publish candidate
release manifest`, step 23 `Publish initial-release target absence readback`.

### 13.3 Provenance of the images: they were built by run `34179207603`

This is a material fact that the ops-bus summary did not state, and it changes
how the run must be read. Run `34179791241` did **not** build or push the images.
Its step 19 log reads:

```
Immutable images already exist for 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12; reusing their digests.
```

The images were built, pushed, signed and attested by an **earlier** run:

| Field | Value |
|---|---|
| Run ID | `34179207603` |
| Created | `2026-09-08T02:11:41Z` |
| Conclusion | `completed` / `failure` |
| Build job | `101914686431` |
| Step 19 `Build, publish, sign, and attest immutable container images` | `success` |
| Step 20 `Write the build-once artifact handoff` | **`failure`** |
| Steps 21–23 | `skipped` |
| Step 17 `讀回部署 target 以確認沒有既有已核准 release` | `skipped` |

**First real root cause of `34179207603`** (log tail of job `101914686431`):

```
build-once artifact handoff 無法產生：
- 缺少 rollback release 參照；build 階段必須綁定上一核准 release 與 snapshot pointer。
  若這是該 target 的首次部署，請改以 --initial-release-readback 提供對 target 的...
```

with `INITIAL_RELEASE_RECOVERY: false` in that step's environment. This is the
first release to the dev target, so there is no prior approved release to bind a
rollback pointer to. **Minimal fix, and the one that was applied:** re-dispatch
with the initial-release readback enabled. The successful run's step 20
environment carries `INITIAL_RELEASE_RECOVERY: true`, which also un-skips step 17
and produces the absence readback artifact. No workflow file was modified.

Corroboration that the push/sign happened at `02:17–02:19Z` inside
`34179207603`, from the Rekor transparency log entries embedded in the
signatures (log ID `c0d23d6ad406973f9559f3ba2d1ca01f84147d8ffc5b8445c224f98b9591801d`):

| Component | Rekor `logIndex` | `integratedTime` | UTC |
|---|---|---|---|
| api | `2754425715` | `1788833821` | `2026-09-08T02:17:01Z` |
| worker | `2754426118` | `1788833858` | `2026-09-08T02:17:38Z` |
| scheduler | `2754426482` | `1788833890` | `2026-09-08T02:18:10Z` |
| web | `2754427686` | `1788833982` | `2026-09-08T02:19:42Z` |

Those timestamps fall inside `34179207603` (02:11:41Z→) and before
`34179791241` started (02:21:49Z). They are also the earliest evidence of
**restored billing**: the same `docker push` that was denied at
`2026-09-07T15:53:11Z` succeeded at `2026-09-08T02:17:01Z`.

This reuse is not a gap — the workflow is deliberately built this way. Its own
comment states that re-running the phase must reproduce the first run's handoff,
and that it must not sign or attest a second time, "because a second attestation
would change the attestation digest and with it the manifest digest an issued
lease is bound to". The reuse path is also fail-closed on partial state: if the
four release tags are not all present, it aborts with `release image tag set is
incomplete; refusing to rebuild or move an existing tag`.

### 13.4 Four immutable image identities

Registry `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev`,
all digest-pinned, no tags:

| Component | Immutable identity |
|---|---|
| api | `oday-api@sha256:5e1a152e839cbfa7a2bf422b924928b56fad89f35e44243619a3f2fe98802cee` |
| web | `oday-web@sha256:38c716462b569b7420fe95788a84f8a1b3778e2e7c938d535a1dc13288a78971` |
| worker | `oday-worker@sha256:b2c0e4473ad529ad71f215b76fc125115d8ba0b4e845a87e532d10ebdb8ddba3` |
| scheduler | `oday-scheduler@sha256:f3fd22c00478d730273494c23c512a87c807de645a8b759d0af4908d82cd4cc9` |

The manifest carries a fifth component, `migration`, which is not a fifth image:
it declares `shares_image_with: worker` and reuses the worker digest.

### 13.5 `RELEASE_MANIFEST.json` — verified with candidate C's own validator

| Field | Value |
|---|---|
| Artifact ID | `10038569478` |
| Artifact name | `runtime-release-manifest-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` |
| Inner file SHA-256 | `8bb6e72ed1306862ddb4c40d48c851b31ecf8c4ab5f361ac5cc7e1856537f56d` |
| `release_id` | `odp-596b9c9a1788` |
| `release_status` | `ready` |
| `schema_version` | `2` |
| `manifest_digest` | `sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c` |
| `data_contract_digest` | `sha256:05e2cb05619f1c524b0f9578e4ceba9ec863d143d5e64b0eeac97539ce8e7c73` |
| `source_policy_digest` | `sha256:0a34bb128b5b5b26201b7f014f4b4f8e631e841c8f205f38dfc09c9eb682d824` |
| `migration_digest` | `sha256:17794de9afb84681aabff9ed0966dedde83d950aef132de519fdc193099e620b` |

Reproduction — candidate C extracted read-only via `git archive`, then C's own
manifest code applied to the downloaded bytes:

```python
from delivery_toolchain.release.release_manifest import compute_manifest_digest, validate_manifest
m = json.load(open("RELEASE_MANIFEST.json"))
compute_manifest_digest(m)
# => sha256:1b5348d907643e3f3087642d8674002beec016bd16ef38b00e261e88da026e6c  (matches recorded)
validate_manifest(m, expected_candidate_sha="596b9c9a1788d952811a2bf8d4bba8a4e4d76b12",
                  expected_digest=m["manifest_digest"])
# => []  (no errors)
```

The manifest is therefore self-consistent, digest-stable, and validates against
candidate C as the expected candidate.

**One apparent discrepancy, resolved:** the manifest records
`created_at: 2026-09-07T15:29:32+00:00`, which is ~11 hours *before* the run that
uploaded it. That is not a stale artifact — it is exactly the committer timestamp
of candidate C (`git show -s --format=%cI 596b9c9a` → `2026-09-07T15:29:32+00:00`).
The field is derived from the candidate, not from wall-clock time, which is what
keeps `manifest_digest` reproducible across re-runs.

### 13.6 Supply chain: SBOM attestations, Cosign signatures, provenance

Cosign `v2.5.2`, installed by `sigstore/cosign-installer@v3`
(`398d4b0eeef1380460a10c8013a76f728fb906ac`). The SBOM generated in-run has
content digest `sha256:2b6cb89ce1e138d6b835cbabf9dd88572e6a7b2b84e78774425f5624bb7366c0`
and is attested with `cosign attest --type cyclonedx`.

SBOM attestation refs (each resolved from the `.att` tag to its own immutable digest):

| Component | SBOM attestation |
|---|---|
| api | `oday-api@sha256:c962987fdaeaab721b5fe7f08fd70af9ee730271aff365f2d604a3f8c6ad8980` |
| web | `oday-web@sha256:359ccfd3a2d2e41e4fd44733d5ac7138dd92acec58eff3d2be02cc1dd358f598` |
| worker | `oday-worker@sha256:e6f6b390e9a143745fcbdd18f2e78dc7364a75d3180f2d975d3916c1f2036915` |
| scheduler | `oday-scheduler@sha256:c6f75a41ca6433441a1d6e31de24985bae16087a470a7889fc71960bc90ae0ff` |

Cosign signature refs (resolved from the `.sig` tag to its own immutable digest):

| Component | Signature |
|---|---|
| api | `oday-api@sha256:1d27d5394eec6436b74acffac2acea3285f0bb58cdc53e8243f38ebd64c82258` |
| web | `oday-web@sha256:7ba935dddad224e6b466fae54e8318f71ead2f352a2fcd8c8fc3d6ff125e0b3f` |
| worker | `oday-worker@sha256:3d70e5873e12c0ab121bedd48caaf3f03292c9f36f1cbea04e621257d5386b6c` |
| scheduler | `oday-scheduler@sha256:b57a2e39e35d23f69b926371f0ba32282c2cc0dfcfe9a07321fccdce46d65fc7` |

The workflow resolves both refs itself and **fails closed** if either tag does
not resolve (`no Cosign signature artifact resolves for ...` /
`no SBOM attestation artifact resolves for ...`). So these are fetchable objects,
not free-text claims.

**These signatures are real, not a passing print.** This repository has a history
of supply-chain gates that print `PASSED` when the tool is absent, so the log was
read rather than trusted. All four `cosign verify` invocations ran

```
cosign verify --certificate-identity-regexp 'https://github.com/alfloop-dev/.*' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' <digest-ref>
```

against the live registry and each returned a full signature payload with a
Fulcio certificate and a Rekor bundle (`SignedEntryTimestamp`, `logIndex`,
`integratedTime`, `logID`) — the transparency-log entries tabulated in 13.3.
An absent-tool stub cannot fabricate those.

**Provenance — stated precisely.** The manifest schema at C has no `provenance`
field, and no separate SLSA provenance attestation was produced. What exists,
and all that may be claimed, is the provenance carried inside each signature's
Fulcio certificate claims:

| Certificate claim | Value |
|---|---|
| Issuer | `https://token.actions.githubusercontent.com` |
| Subject | `https://github.com/alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@refs/heads/dev` |
| `githubWorkflowRepository` | `alfloop-dev/odayplus` |
| `githubWorkflowName` | `Runtime Release` |
| `githubWorkflowRef` | `refs/heads/dev` |
| `githubWorkflowSha` | `8c570a56353abdcc8ba70fe0a3fdd9b963902391` |
| `githubWorkflowTrigger` | `workflow_dispatch` |

Note that `githubWorkflowSha` binds the *workflow definition* commit, not the
built candidate. The binding of the image content to candidate C rests on step 3's
`git rev-parse HEAD` assertion (13.2) and on the
`org.opencontainers.image.revision=596b9c9a...` label applied at build time —
not on the certificate. A downstream consumer must not read `8c570a56` from these
certificates as the built candidate.

### 13.7 Artifact IDs with verified raw hashes

All six artifacts were downloaded via
`gh api repos/alfloop-dev/odayplus/actions/artifacts/<id>/zip`. For each one the
SHA-256 of the received zip matches the digest GitHub reports in its artifacts
API **and** the digest the runner printed at upload time in the job log.

| Artifact ID | Name | Bytes | Raw zip SHA-256 | Inner file SHA-256 |
|---|---|---|---|---|
| `10038482325` | `release-phase-receipt-dev-build` | 631 | `bcfbf76667f7c5892dd4832851ec30d966aea346e054ac4b9832b84dbe3e11d5` | `af1f1d5d1be169187a054a83d09b47e5978d4018d1c366144a99fe8f88deb8c7` |
| `10038486296` | `release-environment-receipt-dev-build` | 795 | `9180ed873afc9d08b05ff948b7d358d6bf10fc8416e2d8864c1f554c9a13a6cf` | `a5fc3cc7847bbbec3bb0dbd651428b9a6fcebb18db770ff2abd09cd2b850f9bb` |
| `10038492941` | `release-npm-audit-receipt-dev` | 508 | `fb61bd06cfc8da6d4c4c5a495b96c7bdf5d92f5743d68c7a568a8147fd3d921a` | `49c5c659e9b08dab23ec0e9aee390d814f8d8e2c0f78c3a4d922cedb4de96224` |
| `10038569219` | `runtime-release-images-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` | 453 | `7f0515c6d92b4351da4a5a425d0b20cc1f49c519eeda1754adcbfe996e35d05b` | `612507f59edb8e09807dfa5a9f15fafaa622fb03473e2eff4015dda64494fa12` |
| `10038569478` | `runtime-release-manifest-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` | 2440 | `fab4a3e97c20a0fc89481a6055d97959fea37cbdc1d647cd2f3dad7d0c1fdae4` | `8bb6e72ed1306862ddb4c40d48c851b31ecf8c4ab5f361ac5cc7e1856537f56d` |
| `10038569730` | `initial-release-absence-readback-596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` | 508 | `b76ce7a65f97baa306e4ec9bc96c5cc7e49f753143e2c1238c3604daa888aa95` | `ec316739eecd6c0438582a29c3803acad38fe283aa4167ca30db9dc481ffa283` |

None are expired. Download form, usable as-is:

```bash
gh api repos/alfloop-dev/odayplus/actions/artifacts/10038569478/zip > manifest.zip
```

### 13.8 Six-file egress contract digest — recomputed at C

The reviewer's P2 correction is carried forward and re-verified in this round.
The digest is over six **checked-in source files** at C; it is a source-contract
digest and is **not** a runtime egress readback.

```
sha256:a9ab95a01d310eb1f79e71dad74e636058d5d1f3e9150602831974e7193bba09
```

Recomputed independently by extracting C with `git archive` and calling C's own
`compute_sources_off_egress_contract_digest(root=<C worktree>)` from
`delivery_toolchain/release/release_manifest.py`. All six files present:
`.github/workflows/deploy-dev.yml`, `product_ops/deployment/deploy_cloud_run_waji.sh`,
`infra/terraform/cloud_run.tf`, `infra/terraform/network.tf`,
`product_ops/deployment/staging_lifecycle.py`,
`product_ops/deployment/cloud_run_job_entrypoint.py`.

**This is now a two-sided match.** The value computed locally from C's sources is
byte-identical to `sources_off_attestation.egress_evidence.contract_digest` in the
manifest the build independently produced. The delivered manifest and the checked-in
sources agree on the egress contract.

Related digests recorded in the manifest's `sources_off_attestation`:
`binding_digest sha256:ada473d051cdfdd9ddbb1cbee0b4a0abc222f2b6a61812b7a216ea73a71441fe`,
16 sources audited, all `disabled`, `zero_credentials_present: true`,
`egress_posture: default-deny`. The `initial_release_recovery` block carries
`binding_digest sha256:77ffa2570d34fca0ddd1cc1fb87aecad4b3db0dbd85e185c8da2cfdc90b04235`
and `prior_release_absent: true`.

### 13.9 Authority proof — build-only scope was not exceeded

From the downloaded phase receipt (`10038482325`), not from the log:

| Field | Value |
|---|---|
| `phase` | `build` |
| `environment` | `dev` |
| `release_sha` | `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` |
| `task_id` | `ODP-DEV-BUILD-ARTIFACT-HANDOFF-003` |
| `lease_supplied` | **`false`** |
| `image_handoff` | all four `null` |
| `manifest_handoff` | `run_id: null`, `manifest_digest: null` |
| `secret_values_redacted` | `true` |

The run is bound to *this* task id. No lease was supplied, and the three
deploy-side jobs are `skipped` (13.2). The environment receipt (`10038486296`)
confirms binding to GitHub environment `dev-build` with 11 required variables
resolved and `missing_variables: []`; only names and booleans are recorded, plus
the one non-secret value the receipt itself exposes
(`ODP_CLOUD_RUN_VPC_EGRESS: all-traffic`). The npm audit receipt (`10038492941`)
is `pass` with 0 findings at or above `high`.

The absence readback (`10038569730`) shows all five Cloud Run targets with
`exists: false` and `serving_traffic: false`. That is a readback proving **no
deploy has occurred** — it must not be read as a deploy result.

### 13.10 Limits of this evidence — what is NOT claimed

- **The images were not fetched from the registry by this owner.** An auto worker
  has no Artifact Registry credentials and `gcloud` reauthentication fails
  non-interactively (independently reproduced by PR#1239). Fetchability is
  evidenced by the in-run `cosign verify` calls, which read the live registry with
  the workflow's WIF credentials at `02:17–02:19Z`, and by the workflow's
  fail-closed ref resolution. It is not evidenced by a local pull.
- **No SLSA provenance attestation exists** — see 13.6. Only Fulcio certificate
  claims plus CycloneDX SBOM attestations.
- **No deploy, lease, GO, or traffic switch was performed or authorised.**
- **This owner dispatched no build.** Both runs were dispatched by Human/Ops.
- **Billing restoration was not observed directly**, only inferred from the push
  and signature succeeding at `02:17:01Z` where the same operation was denied on
  2026-09-07. That inference is sound for the artifacts at hand and is not
  extended to any claim about the project's current billing configuration.

### 13.11 Complete handoff to ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 (PR#1205)

**Handoff status: COMPLETE.** All five previously undelivered artifact categories
are delivered, verified, and fetchable.

| Target | Value |
|---|---|
| Task | ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002 |
| PR | #1205 (open, `task/ODP-DEV-CANDIDATE-GATE-RECONCILIATION-002`) |
| Owner | Antigravity5 |
| Reviewer | Codex2 |
| Scope handed over | evidence-only C→E; the old PR is preserved |

| Category | Status | Where |
|---|---|---|
| Candidate C | ✅ | `596b9c9a1788d952811a2bf8d4bba8a4e4d76b12` |
| Successful build run | ✅ | run `34179791241`, build job `101916404681` |
| Four immutable image identities | ✅ | 13.4; artifact `10038569219` |
| `RELEASE_MANIFEST.json` | ✅ | 13.5; artifact `10038569478`, SHA-256 `8bb6e72e…` |
| SBOM attestation refs | ✅ | 13.6 |
| Cosign signature refs | ✅ | 13.6 |
| Provenance | ✅ (scoped) | 13.6 — Fulcio claims + Rekor entries; no separate SLSA attestation |
| Artifact IDs + raw hashes | ✅ | 13.7, all six verified |
| Six-file egress contract digest | ✅ | 13.8, `sha256:a9ab95a0…`, two-sided match |
| Initial-release absence readback | ✅ | artifact `10038569730` |
| Authority proof | ✅ | 13.9, `lease_supplied: false` |

Carry-forward constraints for the downstream owner: this is an **evidence-only**
C→E continuation. Do not deploy, sign a lease, issue a GO, or switch traffic. Do
not force push. If the PR#1205 ancestry is contaminated, handle it through a
normal new branch/PR, preserving the existing PR.

### 13.12 Verification commands actually run this round

All read-only. No build, deploy, lease, test suite, lint, or security scan was
run by this owner.

```bash
gh api repos/alfloop-dev/odayplus/actions/runs/34179791241
gh api repos/alfloop-dev/odayplus/actions/runs/34179791241/jobs
gh api repos/alfloop-dev/odayplus/actions/runs/34179791241/artifacts
gh api repos/alfloop-dev/odayplus/actions/jobs/101916404681
gh api repos/alfloop-dev/odayplus/actions/jobs/101916404681/logs --allow-escape-sequences
gh api repos/alfloop-dev/odayplus/actions/runs/34179207603/jobs
gh api repos/alfloop-dev/odayplus/actions/jobs/101914686431
gh api repos/alfloop-dev/odayplus/actions/jobs/101914686431/logs --allow-escape-sequences
gh api "repos/alfloop-dev/odayplus/actions/workflows/302984644/runs?per_page=20"
gh api repos/alfloop-dev/odayplus/actions/artifacts/<id>/zip   # each of the 6
sha256sum <each downloaded zip and each extracted inner file>
git show -s --format=%cI 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12
git archive 596b9c9a1788d952811a2bf8d4bba8a4e4d76b12 | tar -x -C <tmp>   # read-only C
python3 -c "...compute_sources_off_egress_contract_digest(root=<C>)..."
python3 -c "...compute_manifest_digest / validate_manifest on the downloaded manifest..."
```
