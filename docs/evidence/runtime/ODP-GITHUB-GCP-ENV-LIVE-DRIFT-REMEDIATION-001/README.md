# ODP-GITHUB-GCP-ENV-LIVE-DRIFT-REMEDIATION-001 — 修復三環境 GitHub Environment Live Drift

- **Task ID**: `ODP-GITHUB-GCP-ENV-LIVE-DRIFT-REMEDIATION-001`
- **Title**: 修復三環境 GitHub environment live drift
- **Owner**: Antigravity2
- **Reviewer**: Codex
- **Phase**: Wave 3 - Environment live drift remediation
- **Date**: 2026-08-27
- **Source Plan**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)

---

## 1. 任務背景與修復目標

本任務針對 2026-08-27 觀測到的 GitHub Environment live drift 進行徹底修復。依據 live GCP 資源即時讀取與契約驗證，逐項恢復 `dev`、`staging` 與 `production` 三個環境中被清除或漂移的非秘密 Runtime Release 環境變數與 Secret Manager references。

核心原則與保證：
1. **依當日 Live GCP 資源逐項重驗**：所有寫回的 GCP 資源（Cloud SQL、Cloud Storage、Cloud Run 服務、VPC connector、Service Accounts、Secret Manager secrets、WIF provider）均於 2026-08-27 執行即時 readback 確認存在，絕無盲目抄錄歷史或填入 placeholder。
2. **端點與 Audience 精確隔離**：
   - `dev`：`ODP_DEV_DEPLOY_URL` 嚴格僅使用 Cloud Run 臨時網址（`https://oday-web-767864276141.asia-east1.run.app`），`ODP_AUTH_AUDIENCES` 使用 dev Cloud Run API 網址（`https://oday-api-767864276141.asia-east1.run.app`）。
   - `staging`：Web 端點為 `https://console-staging.oday-plus.com.tw`，API 端點為 `https://api-staging.oday-plus.com.tw`，`ODP_AUTH_AUDIENCES` 使用 staging Cloud Run API 網址（`https://oday-staging-api-767864276141.asia-east1.run.app`）。
   - `production`：Web 端點為 `https://console.oday-plus.com.tw`，API 端點為 `https://api.oday-plus.com.tw`，`ODP_AUTH_AUDIENCES` 使用 prod Cloud Run API 網址（`https://oday-api-365886461656.asia-east1.run.app`），不修改 www 官方網站。
3. **OAuth 人工 Gate 保持 Fail-Closed**：
   - 三環境之 `ODP_WEB_OIDC_CLIENT_ID` 嚴格保持缺失。
   - 三環境之 OAuth client secret (`oday-dev-web-oidc-client-secret`, `oday-staging-web-oidc-client-secret`, `oday-prod-web-oidc-client-secret`) 均確認存在於 GCP Secret Manager 且維持 0 versions，等待 Human/Ops 提供憑證。
4. **零秘密讀取與洩漏**：所有收據與 audit 檔案均不包含任何 secret value，僅記錄 resource name、版本狀態與 presence/redaction 標記。
5. **Reviewer 保護維持不變**：`staging` 與 `production` 之 `required_reviewers` 保護規則（`Alien-alfaloop`, `ajoe734`）完整保留且不更動。

---

## 2. 三環境 Live GCP 資源讀取與變數映射

### 2.1 `dev` 環境（常駐整合）
- **GCP Project**: `odayplus-runtime-20260825` (`767864276141`)
- **Cloud SQL**: `odayplus-runtime-20260825:asia-east1:oday-dev-sql` (RUNNABLE)
- **Artifact Registry**: `oday-plus-dev` (`asia-east1`)
- **Cloud Storage**:
  - Snapshot: `oday-dev-source-snapshots-odayplus-runtime-20260825`
  - Release Leases: `gs://odayplus-runtime-20260825-release-leases/leases`
- **Cloud Run 服務**:
  - API: `oday-api` (`https://oday-api-767864276141.asia-east1.run.app`)
  - Web: `oday-web` (`https://oday-web-767864276141.asia-east1.run.app`)
  - MLflow: `oday-mlflow` (`https://oday-mlflow-767864276141.asia-east1.run.app`)
- **Service Accounts**:
  - Runtime: `gke-oday-dev-runtime@odayplus-runtime-20260825.iam.gserviceaccount.com`
  - Scheduler: `oday-dev-scheduler@odayplus-runtime-20260825.iam.gserviceaccount.com`
  - Smoke Operator: `oday-dev-smoke-operator@odayplus-runtime-20260825.iam.gserviceaccount.com` (Unique ID: `106898637126232088368`)
- **Secret Manager References**:
  - `ODAY_DATABASE_URL_SECRET`: `oday-plus-dev-api-database-url-pg16:latest` (Version 1 enabled)
  - `ODP_AUTH_PRINCIPAL_MAP_SECRET`: `oday-plus-dev-auth-principal-map:latest` (Version 1 enabled)
  - `ODP_WEB_SESSION_SECRET_SECRET`: `oday-plus-dev-web-session-secret:latest` (Version 1 enabled)
  - `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`: `oday-dev-web-oidc-client-secret` (0 versions, fail-closed)
- **變數總數**: 39 個

### 2.2 `staging` 環境（短生命週期預演）
- **GCP Project**: `odayplus-runtime-20260825` (`767864276141`)
- **Cloud SQL**: `odayplus-runtime-20260825:asia-east1:oday-staging-sql` (RUNNABLE)
- **VPC Connector**: `projects/odayplus-runtime-20260825/locations/asia-east1/connectors/oday-staging-vpc` (READY)
- **Cloud Storage**:
  - Snapshot: `oday-staging-source-snapshots-odayplus-runtime-20260825`
  - Release Leases: `gs://odayplus-runtime-20260825-release-leases/leases`
- **Cloud Run 服務**:
  - API: `oday-staging-api` (`https://oday-staging-api-767864276141.asia-east1.run.app`)
  - Web: `oday-staging-web` (`https://console-staging.oday-plus.com.tw`)
  - MLflow: `oday-staging-mlflow` (`https://oday-staging-mlflow-767864276141.asia-east1.run.app`)
- **Service Accounts**:
  - Runtime: `oday-staging-runtime@odayplus-runtime-20260825.iam.gserviceaccount.com`
  - Scheduler: `oday-staging-scheduler@odayplus-runtime-20260825.iam.gserviceaccount.com`
  - Smoke Operator: `oday-dev-smoke-operator@odayplus-runtime-20260825.iam.gserviceaccount.com` (Unique ID: `106898637126232088368`)
- **Secret Manager References**:
  - `ODAY_DATABASE_URL_SECRET`: `oday-staging-database-url:1` (Version 1 enabled)
  - `ODP_AUTH_PRINCIPAL_MAP_SECRET`: `oday-staging-auth-principal-map:1` (Version 1 enabled)
  - `ODP_WEB_SESSION_SECRET_SECRET`: `oday-staging-web-session-secret:1` (Version 1 enabled)
  - `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`: `oday-staging-web-oidc-client-secret` (0 versions, fail-closed)
- **變數總數**: 47 個

### 2.3 `production` 環境（正式 Blue-Green 運行）
- **GCP Project**: `odayplus-prod-20260826` (`365886461656`)
- **Cloud SQL**: `odayplus-prod-20260826:asia-east1:oday-prod-sql` (RUNNABLE)
- **Artifact Registry**: `oday-plus` (`asia-east1`)
- **Cloud Storage**:
  - Snapshot: `odayplus-prod-20260826-source-snapshots`
  - Release Leases: `gs://odayplus-prod-20260826-release-leases/leases`
- **Cloud Run 服務**:
  - API: `oday-api` (`https://oday-api-365886461656.asia-east1.run.app`)
  - Web: `oday-web` (`https://console.oday-plus.com.tw`)
  - MLflow: `oday-prod-mlflow` (`https://oday-prod-mlflow-icm6xhajsa-de.a.run.app`)
- **Service Accounts**:
  - Runtime: `oday-prod-runtime@odayplus-prod-20260826.iam.gserviceaccount.com`
  - Scheduler: `oday-prod-scheduler@odayplus-prod-20260826.iam.gserviceaccount.com`
  - Smoke Operator: `oday-prod-smoke-operator@odayplus-prod-20260826.iam.gserviceaccount.com` (Unique ID: `115219773121101906030`)
- **Secret Manager References**:
  - `ODAY_DATABASE_URL_SECRET`: `oday-prod-database-url:1` (Version 1 enabled)
  - `ODP_AUTH_PRINCIPAL_MAP_SECRET`: `oday-prod-auth-principal-map:1` (Version 1 enabled)
  - `ODP_WEB_SESSION_SECRET_SECRET`: `oday-prod-web-session-secret:1` (Version 1 enabled)
  - `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`: `oday-prod-web-oidc-client-secret` (0 versions, fail-closed)
- **變數總數**: 44 個

---

## 3. GitHub 環境保護規則現況

`staging` 與 `production` 環境均設有嚴格的 `required_reviewers` 保護規則：
- `staging`: Reviewers: `Alien-alfaloop` (`122770408`), `ajoe734` (`169176954`)
- `production`: Reviewers: `Alien-alfaloop` (`122770408`), `ajoe734` (`169176954`)
- `prevent_self_review`: `false`

本修復任務完全未變更任何保護規則或審核者清單。

---

## 4. 稽核產出索引

| 檔案 | 格式 | 說明 |
|---|---|---|
| [`README.md`](README.md) | Markdown | 本任務之修復說明、架構對齊與驗證摘要 |
| [`github-environments-audit.json`](github-environments-audit.json) | JSON | `dev`, `staging`, `production`, `dev-build`, `staging-build`, `production-build` 六個 GitHub environment 之保護規則與完整變數讀取稽核 |
| [`gcp-resources-audit.json`](gcp-resources-audit.json) | JSON | `odayplus-runtime-20260825` 與 `odayplus-prod-20260826` 兩專案之 Cloud SQL、Cloud Run、Cloud Storage、VPC、IAM SA、AR、WIF 及 Secret Manager 完整讀取記錄 |
| [`live-readback-transcript.txt`](live-readback-transcript.txt) | Plain Text | 執行 gcloud 與 gh 命令之逐字命令輸出記錄（機密已遮蔽） |

---

## 5. 驗證指令與結果

### 5.1 GitHub API 變數列表驗證
```bash
# 驗證 dev 環境變數
gh api --paginate repos/alfloop-dev/odayplus/environments/dev/variables?per_page=100 --jq .variables[].name | wc -l
# 結果：39

# 驗證 staging 環境變數
gh api --paginate repos/alfloop-dev/odayplus/environments/staging/variables?per_page=100 --jq .variables[].name | wc -l
# 結果：47

# 驗證 production 環境變數
gh api --paginate repos/alfloop-dev/odayplus/environments/production/variables?per_page=100 --jq .variables[].name | wc -l
# 結果：44
```

### 5.2 部署契約與作業測試
```bash
uv run --python 3.12 pytest tests/ops/
```
**執行結果**：`664 passed, 1 warning, 14 subtests passed in 95.50s`
