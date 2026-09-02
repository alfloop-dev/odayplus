# ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001

## 結論

**Fail-closed，尚未部署。** 本輪以最新 `origin/dev` 的 exact SHA
`38de35a7bac2d6c6f6d8d079ffaf16abf6163c29` 重跑唯一 `Runtime Release` build
phase。Hosted run `33627271466` 實際 build、push、Cosign sign、SBOM attest 了
四個 immutable images，但在產生 release handoff 時因缺少上一個可 admission 的
rollback manifest 而停止。

因此本 evidence **不宣稱 release manifest、signed Supervisor lease、Cloud Run
deploy、live smoke 或成功收據已存在**。四個 image 的 refs 只作為 hosted build
run 的真實產物紀錄，不可在沒有 handoff manifest 與 lease 時直接部署。

## 1. Candidate 與 authoritative manifest

| 項目 | 結果 |
|---|---|
| 最新 `origin/dev` | `38de35a7bac2d6c6f6d8d079ffaf16abf6163c29` |
| repository manifest candidate | `ebc4fca5c2dd5871275aee39a18406dd67464f04` |
| repository manifest | `schema_version: 1`, `release_status: ready`, digest `sha256:fa2f52220951dc89c56b41b7f0fd61280ce00a028709d2124ceefcdc55f24de9` |
| manifest admission | **拒絕**：缺 `sources_off_attestation`／snapshot binding，且缺 `rollback_release` |
| candidate drift | **是**；不能沿用 `ebc4fca5` 的 images 或 manifest |

最新 manifest 的 immutable image syntax 與 canonical digest 可解析，但它不是目前
candidate 的 manifest，也不是可供 Schema v2 handoff 使用的上一個 release。這裡沒有
改寫 `docs/evidence/gates/RELEASE_MANIFEST.json`。

## 2. Hosted build-once receipt

唯一入口是 `.github/workflows/deploy-dev.yml` 的 `Runtime Release`，沒有新增
workflow 或 deployment path。

| 欄位 | 值 |
|---|---|
| run | [33627271466](https://github.com/alfloop-dev/odayplus/actions/runs/33627271466) |
| phase / environment | `build` / `dev-build` |
| release SHA | `38de35a7bac2d6c6f6d8d079ffaf16abf6163c29` |
| result | `failure`，停在 `Write the build-once artifact handoff` |
| steps before failure | phase validation、environment binding、secret scan、SAST、SBOM、locked dependencies、E2E deployment health/backup/restore/rollback proof、WIF、Cloud SDK、Cosign、四 image build/push/sign/attest |
| uploaded artifacts | 僅 `release-phase-receipt-dev-build` 與 `release-environment-receipt-dev-build`；沒有 candidate manifest 或 image handoff artifact |

Hosted run 實際解析到的四個 image、signature、SBOM refs 如下。這些 refs 來自同一
run 的 build log；後續 handoff failure 代表它們尚未形成可 admission 的 release。

| component | image digest | Cosign signature ref | SBOM attestation ref |
|---|---|---|---|
| api | `oday-api@sha256:312a0356e06ce6cfbaaebba4886fe63cd07242dd6150d95e6fd2c3f6000031ef` | `oday-api@sha256:60092fd70555d0c505ae1ee1bb99d2fef0679934d427a66f311189e388358f42` | `oday-api@sha256:203f33a37f2002280f439fa7ed866527564770f2f72cf22de9f4be6964d06a6e` |
| web | `oday-web@sha256:cc8d6bc09c9cc693bfc7223b764d535d5b8d1d3d8609b91dcf5c30467f3f74b9` | `oday-web@sha256:ddb4ac99d20b3f0a63a8c99c56c9a65c7104fec2fbe91230b524a78aa4327d11` | `oday-web@sha256:1530edcec3a7beb154f9bab8c1c3d09edd2612333a10c4e4b2627dfcf0f13592` |
| worker | `oday-worker@sha256:54669b8e38fbd4193a7d2866f292fbedb22903eff6a9482e444151cce6c9c51c` | `oday-worker@sha256:e808c125ba6130219eb372ba2b0f28f7c3a2209a346322c10e5e33ff950f85cf` | `oday-worker@sha256:9e60ed0cee4d65a3b77a3bf02059f791586986fc06866540c5da9fd61895f937` |
| scheduler | `oday-scheduler@sha256:f0b5d03e0f1ab9310682a0a9e08cc6bfd0e27ba254f6a26e226025feefe5c813` | `oday-scheduler@sha256:cf62721d4eaa7d01e160b5ff71a49feacd8803609e74846478a723ab2e17b44d` | `oday-scheduler@sha256:d0e89ef16e1e4f7538612b6980604de6ced00d33c23f32ed9912b002a0c97900` |

The four refs were resolved by the hosted build's `gcloud artifacts` resolver before
handoff. A local post-run re-read was attempted with the available gcloud accounts but
was denied `artifactregistry.repositories.get`; that denial is recorded rather than
converted into a local success claim.

## 3. Current blockers

### 3.1 No admissible rollback baseline

`build_release_handoff.py` correctly refused the sources-off build with:

```text
缺少 rollback release 參照；build 階段必須綁定上一核准 release 與 snapshot pointer。
```

The only repository manifest is Schema v1 and the manifest validator reports that it
cannot be admitted because it has neither sources-off evidence nor `rollback_release`.
The current build environment has no `ODP_PREVIOUS_RELEASE_MANIFEST_PATH` variable, and
no valid previous Schema v2 manifest was found in the task-scoped repository evidence.
This task does not manufacture a predecessor manifest or retrofit an approval claim onto
the historical file.

### 3.2 No dev admission lease configuration

`gh api repos/alfloop-dev/odayplus/environments/dev/variables` confirms that `dev` has
no `ODP_RELEASE_LEASE_PUBLIC_KEY` or `ODP_RELEASE_LEASE_STATE_URI`; repository variables
also provide neither. The worker shell has no `ODP_RELEASE_LEASE_PRIVATE_KEY`, and the
available gcloud identities cannot read or administer the target project's release state.
Consequently no Supervisor-issued lease can be minted or admitted here.

### 3.3 GCP readback authority unavailable

The current worker attempted read-only Cloud Run, Cloud Run Jobs, Cloud Scheduler, GKE,
Cloud SQL, Secret Manager, and Artifact Registry probes. The available identities either
required reauthentication or returned IAM `PERMISSION_DENIED`; no deployment command was
run. The last successful readback already present in the preceding reconciliation audit
showed only the EMGI data-platform workloads and MLflow services, with API/Web/jobs/
Scheduler absent. It is retained as historical pre-deploy evidence, not relabeled as a
new post-deploy receipt.

## 4. Sources-off and endpoint policy

The build run received the configured `dev-build` VPC connector and `all-traffic` value.
The missing build environment variables observed in run `33627106252` were corrected in
the GitHub `dev-build` environment:

```text
ODP_CLOUD_RUN_VPC_CONNECTOR=projects/odayplus-runtime-20260825/locations/asia-east1/connectors/oday-staging-vpc
ODP_CLOUD_RUN_VPC_EGRESS=all-traffic
```

This is configuration readiness only. Since deploy never ran, there is no runtime
public-egress probe receipt, authenticated API/Web smoke, Cloud Run revision, job
execution, or 16-source live readback to claim. The dev Web URL policy remains
Cloud-Run-generated only; no DNS, custom domain, or certificate was created.

## 5. Historical receipt protection

No file under `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` was edited, moved, or deleted.
The pre-existing file hashes at this audit are:

```text
fc9cfc042068d56e00dbd88a3e6f27e3f550c0cba7aa11b09989660aa864b477  README.md
a35d1e15a04e887cf4eb83ca4db9faa0a69ab9f5416a386e26190864ecf61a70  data-platform-dev-deployment.json
25add1cce139170c260cc1793f726c450549bffb1c4d6af572f4d151ca8072  dev-integration-readback.json
223b35cb0a2da874c50172bf2561f378034cd7b73622441e85134fd4026e621a  dev-rollout-manifest-binding.json
72cd51e8dcdf7ef77e3fcd4c0e68fed09b371c4189b06eac26465c52e6985f75  external-sources-provider-off-audit.json
2ac6638467ccf4b260df1bed51fddee59d579e6c43edb6ec21fa86dc40775e26  odayplus-dev-deployment.json
5b1e41015c9291e2fe6dda26de681f456a90672a325aa7e9ce868e9e96e09449  release-receipts-index.json
```

The new audit explicitly marks those historical receipts as superseded by live
reconciliation evidence; it does not alter their bytes.

## 6. Resolution requirements

1. Provide a real previous approved release manifest, or complete the separately governed
   release-baseline/bootstrap remediation; do not construct one from placeholder data.
2. Configure the dev environment's `ODP_RELEASE_LEASE_STATE_URI` and
   `ODP_RELEASE_LEASE_PUBLIC_KEY` against an existing Supervisor-owned durable CAS store.
3. Have the Supervisor issue a lease bound to the newly generated manifest digest.
4. Dispatch the existing Runtime Release `deploy` phase with that lease and all four exact
   image refs, then collect hosted receipts and authorized GCP readback.
5. Only after deployment, record Cloud Run URLs/revisions, one-shot executions,
   authenticated smoke, provider-off/default-deny egress, and exact manifest binding.

Until these requirements are satisfied, the release remains **NO-GO** and no deployment
success receipt may be emitted.
