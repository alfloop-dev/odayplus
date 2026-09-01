# ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 — 以真實 artifact 完成 dev live rollout 並取代 false-done 前提

- Task ID: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`
- Phase: `Wave 3 - Dev Live Rollout Remediation`
- Owner: `Antigravity3`
- Reviewer: `Claude`
- 產出日期: 2026-09-01
- 結論: **Fail-Closed 停步。已完成真實 hosted build 產出 4 個全新 exact-digest signed images；發現 build handoff 工具鏈 Schema v2 缺陷並依驗收條件 10 fail closed，不擴大修 code；已完成 GCP 現場 readback 審計並明確推翻歷史假收據。**

---

## 1. 任務背景與驗收範圍

本任務旨在重用唯一 Runtime Release deploy phase，在 `odayplus-runtime-20260825` 實際部署 data platform、ODay Plus API/Web、migration/worker/scheduler；只接受 GCP live readback 與 hosted workflow receipts，不修改歷史假收據。

依據 Task Brief 驗收條件與全系統部署原則：
1. **權威 Manifest 驗證**：檢查最新 manifest 之 candidate SHA、image digests、SBOM、Cosign 與 registry 參照。
2. **Candidate 漂移重新 Build-Once**：若 candidate 到 `origin/dev` 之間含 product 或 build input 變更，必須建立新 release 並重新 build once，不得沿用舊 digest。
3. **單一管線與簽章 Lease**：以 signed Supervisor lease 只執行既有 Runtime Release deploy phase，不得建立第二套 workflow。
4. **現場 Readback 驗證**：檢查 GKE data platform、Cloud Run services（`oday-api`、`oday-web`）、Cloud Run jobs（`oday-migration`、`oday-worker`、`oday-scheduler`）與 Cloud Scheduler triggers。
5. **端點網址政策**：dev Web 只使用 Cloud Run 自動產生網址，不建立 DNS 或自訂網域憑證。
6. **缺陷處理原則（驗收條件 10）**：若 workflow 或部署程式有缺陷則 fail closed 並另建獨立 remediation task，不得在 rollout task 內擴大修 code。
7. **歷史收據處理**：歷史 `ODP-DEV-ROLLOUT-001` 收據保持不變，由新 evidence 明確標示已被 live reconciliation 推翻。

---

## 2. Candidate 漂移與 Hosted Build-Once 執行

### 2.1 Candidate 漂移分析

- Repo 內權威 manifest（`docs/evidence/gates/RELEASE_MANIFEST.json`）綁定之 candidate 為 `ebc4fca5c2dd5871275aee39a18406dd67464f04`。
- 目前 `origin/dev` HEAD 為 `ae03490480e5a1313d3fdb992172c9b5793053e2`（落差 251 個 commits）。
- 產品程式碼 diff：`apps/` 異動 43 個檔案（新增 9,337 行，包含本機 password-first auth、login throttling、operator console、forecastops 與 model routes）。
- 依驗收條件 2，不能沿用舊 candidate `ebc4fca5` 的舊 digests，必須對最新 `origin/dev` SHA `ae03490480e5` 重新執行 build-once。

### 2.2 Hosted Build Phase 執行結果

透過 GitHub Actions 觸發唯一的 Runtime Release workflow（Run ID: [33509435127](https://github.com/alfloop-dev/odayplus/actions/runs/33509435127)），參數為：
- `phase`: `build`
- `environment`: `dev`（綁定 `dev-build` environment）
- `release_sha`: `ae03490480e5a1313d3fdb992172c9b5793053e2`
- `task_id`: `ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001`

**通過項目：**
- Checkout exact release SHA `ae03490480e5`
- Environment binding 與 9 個 build vars 驗證（`check_release_environment.py`）
- Secret scan、Python SAST scan、CycloneDX SBOM generation
- Locked project dependencies sync（`uv sync --frozen`）
- E2E deployment health, backup, restore, and rollback proof（`verify_deployment_health_backup_rollback.py`）
- WIF 雲端身份驗證與 Cloud SDK setup
- 4 個 container images 成功建置並推播至 Artifact Registry，且通過 Cosign keyless 簽章與 CycloneDX SBOM 認證：

| Component | Exact Image Digest | Signature Reference | SBOM Reference |
|---|---|---|---|
| **api** | `oday-api@sha256:0d23643eb58ad161b2a9ebcdf1f30aa329f70603a4db30f92f39a682abe33730` | `oday-api@sha256:5db3089a0616c2a24dc81585cae29bfff5cd64c728d004f2ade1428f18f8bc7f` | `oday-api@sha256:c707428bcd9d2d66f204f54f4a768ee3701547dfe96a6d21a8d43af2bf185780` |
| **web** | `oday-web@sha256:a00ad85a4fa3bd89a27e6066074bb3c3425ccaf8c72bc9bac061473aaff708f2` | `oday-web@sha256:4f84d23f7fbae75c760fca0b57f048de2b60d7a0e8628cb0a9b44c00f7c57407` | `oday-web@sha256:00f56581d986806d9a6197309f2c96a43d8081629f1ebe5eccaf51e37e066530` |
| **worker** | `oday-worker@sha256:6dc92f01d67b224f4583b26355d2c395cebe6fe12b08c6adb138a8afb54bc503` | `oday-worker@sha256:81959a34c4a06911615bc9a1f2a9aa91f190353580689f9f7d9317000aa495e0` | `oday-worker@sha256:0a68d983dc08f12d1101bf8226662e8b51b8a20f9407587d656b3fd11f916109` |
| **scheduler** | `oday-scheduler@sha256:1bf9c45898e2446aeae39c7717e2c6c0920fce5a0845cf9cdf6142d8d8f81b41` | `oday-scheduler@sha256:94a7be2a3bf4dc4213865f6278e8971fb0837573cb2f5f4e54fac5ff3e43272e` | `oday-scheduler@sha256:518bea61190c2ded1d77a5daee84f188b7dca3168dafc23ac9dd93035957f462` |

### 2.3 發現工具鏈缺陷並 Fail Closed

在 `Write the build-once artifact handoff` 步驟中，`build_release_handoff.py` 退出 code 1：
```text
build-once artifact handoff 無法產生：
- 缺少 masked data snapshot 參照；build 階段必須綁定本次核准的 masked snapshot。
- 缺少 rollback release 參照；build 階段必須綁定上一核准 release 與 snapshot pointer。
```

**根本原因分析：**
1. `delivery_toolchain/release/build_release_handoff.py` 預設使用 Schema Version 2，嚴格要求 `data_snapshot` 與 `rollback_release` 參照。
2. 然而呼叫端 `.github/workflows/deploy-dev.yml` 的 `Write the build-once artifact handoff` 步驟僅傳入 image/sbom/signature 參照，未傳入 snapshot/rollback 參數，且 `build_release_handoff.py` CLI 未暴露 `--schema-version 1` 選項。
3. 依據驗收條件 10：*「若 workflow 或部署程式有缺陷則 fail closed 並另建獨立 remediation task，不得在 rollout task 內擴大修 code」*，且 `.github/workflows/` 與 `delivery_toolchain/release/` 皆為本 rollout 任務之 forbidden paths，因此本任務**嚴格守門、fail closed 停步**，不私自修改 workflow 或 toolchain code。

---

## 3. GCP 現場 Runtime 讀取與審計（Readback Audit）

本次透過 `gcloud` 與 `kubectl` 進行即時現場唯讀查核，結果如下：

| 資源類別 | 資源名稱 / 位置 | 現況 | 說明 |
|---|---|---|---|
| **GKE Cluster** | `oday-emgi-gke` (`asia-east1-a`) | **RUNNING** | 1 node（`e2-standard-2`），K8s v1.35.7 |
| **GKE Workloads** | `oday-emgi/deployment.apps/oday-emgi-daemon`<br>`oday-emgi/deployment.apps/oday-emgi-webserver` | **1/1 READY** | Data platform 已部署完成（`DPF-EMGI-LIVE-ROLLOUT-001` done），第三方來源 disabled，default-deny egress 生效 |
| **Cloud Run Services** | `oday-mlflow`<br>`oday-staging-mlflow` | **READY** | 僅 MLflow tracking server 存在；`oday-api` 與 `oday-web` 尚未部署 |
| **Cloud Run Jobs** | 無 | **0 個** | `oday-migration`、`oday-worker`、`oday-scheduler` 尚未部署 |
| **Cloud Scheduler** | 無 | **0 個** | `oday-worker-trigger`、`oday-scheduler-trigger` 尚未部署 |
| **Cloud SQL** | `oday-dev-sql`<br>`oday-staging-sql` | **RUNNABLE** | PostgreSQL 16 執行中 |
| **Artifact Registry** | `asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev` | **READY** | 包含候選版本 `ebc4fca5` 與全新建置之 `ae034904` exact-digest signed images |
| **Secret Manager** | `oday-dev-web-oidc-client-secret`<br>`oday-plus-dev-api-database-url-pg16`<br>`oday-plus-dev-auth-principal-map`<br>`oday-plus-dev-mlflow-backend-uri`<br>`oday-plus-dev-web-session-secret` | **READY** | 5 個 dev secrets 容器存在且 version 啟用（僅讀取 metadata 名稱，未讀取 secret payload） |

---

## 4. 歷史收據審查

- 歷史 `docs/evidence/runtime/ODP-DEV-ROLLOUT-001/` 收據文件**保持原始位元組不變**，未被修改、移動或刪除。
- 依據 `docs/evidence/runtime/ODP-LIVE-RUNTIME-EVIDENCE-RECONCILE-001/live-runtime-reconciliation-audit.json` 與本審計報告，該歷史收據中宣稱的 Cloud Run services/jobs 執行、fake repeated-nibble digests（`sha256:1111...`）及 `.odp_data/deployment/` 報告皆已被現場 readback 確實驗證為無效。

---

## 5. 解阻與後續步驟（Unblock Requirements）

1. **建立獨立 Toolchain Remediation Task**：
   - 修訂 `delivery_toolchain/release/build_release_handoff.py` 與 `.github/workflows/deploy-dev.yml`，使 Schema v1 / v2 manifest 產出與 snapshot / rollback 參數傳遞一致。
2. **重新執行 Runtime Release Build Phase**：
   - 產生 `ae03490480e5a1313d3fdb992172c9b5793053e2`（或最新 dev tip）之 byte-exact `RELEASE_MANIFEST.json` 與 `runtime-release-images.json`。
3. **Supervisor 簽發 Release Lease**：
   - 依據產出之 `manifest_digest` 簽發 Supervisor Ed25519 lease 並寫入 GCS CAS state。
4. **執行 Deploy Phase 並完成 Live Readback**：
   - 透過 hosted workflow 部署 `oday-api`、`oday-web`、`oday-migration`、`oday-worker`、`oday-scheduler`，取得真實 Cloud Run URLs 與執行收據。
