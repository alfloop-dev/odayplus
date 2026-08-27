# ODP-GITHUB-GCP-ENV-LIVE-DRIFT-REMEDIATION-001 — 修復三環境 GitHub Environment Live Drift

- **Task ID**: `ODP-GITHUB-GCP-ENV-LIVE-DRIFT-REMEDIATION-001`
- **Title**: 修復三環境 GitHub environment live drift
- **Owner**: Antigravity2
- **Reviewer**: Codex
- **Phase**: Wave 3 - Environment live drift remediation
- **Date**: 2026-08-27
- **Source Plan**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)

---

## 1. 任務背景與修復保證

本任務針對 2026-08-27 觀測到的 GitHub Environment live drift 進行修復。依據 live GCP 資源即時讀取與契約驗證，逐項恢復 `dev`、`staging` 與 `production` 三個環境中被清除或漂移的非秘密 Runtime Release 環境變數與 Secret Manager references。

核心原則與保證：
1. **依 Live GCP 資源逐項重驗**：所有寫回的 GCP 資源（Cloud SQL、Cloud Storage、Cloud Run 服務、VPC connector、Service Accounts、Secret Manager secrets、WIF provider）均已於 2026-08-27 執行即時 readback 確認存在，絕無盲目抄錄歷史或填入 placeholder。
2. **端點與 Audience 精確隔離**：
   - `dev`：`ODP_DEV_DEPLOY_URL` 嚴格僅使用 Cloud Run 臨時網址（`https://oday-web-767864276141.asia-east1.run.app`），`ODP_AUTH_AUDIENCES` 使用 dev Cloud Run API 網址（`https://oday-api-767864276141.asia-east1.run.app`）。
   - `staging`：Web 端點為 `https://console-staging.oday-plus.com.tw`，API 端點為 `https://api-staging.oday-plus.com.tw`，`ODP_AUTH_AUDIENCES` 使用 staging Cloud Run API 網址（`https://oday-staging-api-767864276141.asia-east1.run.app`）。
   - `production`：Web 端點為 `https://console.oday-plus.com.tw`，API 端點為 `https://api.oday-plus.com.tw`，`ODP_AUTH_AUDIENCES` 使用 prod Cloud Run API 網址（`https://oday-api-365886461656.asia-east1.run.app`），不修改 www 官方網站。
3. **OAuth 人工 Gate 保持 Fail-Closed**：
   - 三環境之 `ODP_WEB_OIDC_CLIENT_ID` 嚴格保持缺失。
   - 三環境之 OAuth client secret (`oday-dev-web-oidc-client-secret`, `oday-staging-web-oidc-client-secret`, `oday-prod-web-oidc-client-secret`) 均確認存在於 GCP Secret Manager 且維持 0 versions，等待 Human/Ops 提供憑證。
4. **零秘密讀取與洩漏**：所有收據與 audit 檔案均不包含任何 secret value，僅記錄 resource name、presence 狀態、value SHA256 雜湊與 classification 分類。
5. **Reviewer 保護維持不變**：`staging` 與 `production` 之 `required_reviewers` 保護規則（`Alien-alfaloop`, `ajoe734`）完整保留且不更動。

---

## 2. GitHub API 分頁說明（重要）

GitHub API `/repos/alfloop-dev/odayplus/environments/{environment}/variables` 預設具備分頁限制（每頁預設 10 至 30 筆）。

進行 live readback 或驗證時，**必須加上 `--paginate` 參數**（例如 `gh api --paginate "repos/alfloop-dev/odayplus/environments/dev/variables?per_page=100"`），否則 API 只會返回第一頁（前 10 筆變數），導致後續變數（如 `ODP_DEV_DEPLOY_URL`、`ODP_RELEASE_LEASE_STATE_URI`、`ODP_WEB_OIDC_CLIENT_SECRET_SECRET` 等）在未分頁查詢時看似缺失。

---

## 3. 三環境 Live 變數與 Secret Manager 參考現況

### 3.1 `dev` 環境（共 41 個變數）
- **GCP Project**: `odayplus-runtime-20260825` (`767864276141`)
- **Secret Manager References**:
  - `ODAY_DATABASE_URL_SECRET`: `oday-plus-dev-api-database-url-pg16:latest` (Version 1 ENABLED)
  - `ODP_AUTH_PRINCIPAL_MAP_SECRET`: `oday-plus-dev-auth-principal-map:latest` (Version 1 ENABLED)
  - `ODP_WEB_SESSION_SECRET_SECRET`: `oday-plus-dev-web-session-secret:latest` (Version 1 ENABLED)
  - `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`: `oday-dev-web-oidc-client-secret` (0 versions, fail-closed)
- **OAuth Client ID**: `ODP_WEB_OIDC_CLIENT_ID` 保持缺失 (fail-closed)
- **Deploy URL**: `https://oday-web-767864276141.asia-east1.run.app`

### 3.2 `staging` 環境（共 47 個變數）
- **GCP Project**: `odayplus-runtime-20260825` (`767864276141`)
- **Secret Manager References**:
  - `ODAY_DATABASE_URL_SECRET`: `oday-staging-database-url:1` (Version 1 ENABLED)
  - `ODP_AUTH_PRINCIPAL_MAP_SECRET`: `oday-staging-auth-principal-map:1` (Version 1 ENABLED)
  - `ODP_WEB_SESSION_SECRET_SECRET`: `oday-staging-web-session-secret:1` (Version 1 ENABLED)
  - `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`: `oday-staging-web-oidc-client-secret` (0 versions, fail-closed)
- **OAuth Client ID**: `ODP_WEB_OIDC_CLIENT_ID` 保持缺失 (fail-closed)
- **Deploy URL**: `https://console-staging.oday-plus.com.tw`
- **API URL**: `https://api-staging.oday-plus.com.tw`
- **Required Reviewers**: `Alien-alfaloop`, `ajoe734`

### 3.3 `production` 環境（共 44 個變數）
- **GCP Project**: `odayplus-prod-20260826` (`365886461656`)
- **Secret Manager References**:
  - `ODAY_DATABASE_URL_SECRET`: `oday-prod-database-url:1` (Version 1 ENABLED)
  - `ODP_AUTH_PRINCIPAL_MAP_SECRET`: `oday-prod-auth-principal-map:1` (Version 1 ENABLED)
  - `ODP_WEB_SESSION_SECRET_SECRET`: `oday-prod-web-session-secret:1` (Version 1 ENABLED)
  - `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`: `oday-prod-web-oidc-client-secret` (0 versions, fail-closed)
- **OAuth Client ID**: `ODP_WEB_OIDC_CLIENT_ID` 保持缺失 (fail-closed)
- **Deploy URL**: `https://console.oday-plus.com.tw`
- **API URL**: `https://api.oday-plus.com.tw`
- **Required Reviewers**: `Alien-alfaloop`, `ajoe734`

---

## 4. 稽核證據檔案

| 檔案 | 格式 | 說明 |
|---|---|---|
| [`README.md`](README.md) | Markdown | 任務背景、分頁說明、各環境變數現況與宣告之驗證指令 |
| [`github-environments-audit.json`](github-environments-audit.json) | JSON | `dev` (41)、`staging` (47)、`production` (44) 之 `name`, `present`, `value_sha256`, `classification` 及 Secret Manager reference metadata，零 raw secret values |

---

## 5. 任務宣告之驗證指令與執行結果

依 Task Brief 所宣告之驗證指令執行 live readback：

### 5.1 `dev` 環境變數列表驗證 (41 個)
```bash
gh api --paginate repos/alfloop-dev/odayplus/environments/dev/variables?per_page=100 --jq .variables[].name
```
**輸出清單**：
- `GCP_AR_REPO`
- `GCP_CLOUD_SQL_INSTANCE`
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_SERVICE_ACCOUNT`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `MLFLOW_TRACKING_URI`
- `ODAY_DATABASE_URL_SECRET`
- `ODP_AUTH_AUDIENCES`
- `ODP_AUTH_ISSUER`
- `ODP_AUTH_JWKS_URI`
- `ODP_AUTH_PRINCIPAL_MAP_SECRET`
- `ODP_AUTH_SUBJECT_ROLE_BINDINGS`
- `ODP_CLOUD_RUN_API_SERVICE`
- `ODP_CLOUD_RUN_MIGRATION_JOB`
- `ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`
- `ODP_CLOUD_RUN_SCHEDULER_JOB`
- `ODP_CLOUD_RUN_WEB_SERVICE`
- `ODP_CLOUD_RUN_WORKER_JOB`
- `ODP_CLOUD_SCHEDULER_SCHEDULER_TRIGGER`
- `ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT`
- `ODP_CLOUD_SCHEDULER_WORKER_TRIGGER`
- `ODP_DEV_DEPLOY_URL`
- `ODP_EXTERNAL_PROVIDER_PROBE_TIMEOUT_SECONDS`
- `ODP_FORECAST_ENGINE`
- `ODP_FORECAST_MODEL`
- `ODP_MLFLOW_CLOUD_RUN_AUDIENCE`
- `ODP_OPERATOR_SMOKE_ROLE`
- `ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT`
- `ODP_OPERATOR_SMOKE_SUBJECT`
- `ODP_RELEASE_LEASE_PUBLIC_KEY`
- `ODP_RELEASE_LEASE_STATE_URI`
- `ODP_SCHEDULED_INGESTION_TENANT_ID`
- `ODP_SCHEDULER_CRON`
- `ODP_SCHEDULER_TIME_ZONE`
- `ODP_SNAPSHOT_BUCKET`
- `ODP_TENANT_ID`
- `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`
- `ODP_WEB_OIDC_ISSUER`
- `ODP_WEB_SESSION_SECRET_SECRET`
- `ODP_WORKER_CRON`

### 5.2 `staging` 環境變數列表驗證 (47 個)
```bash
gh api --paginate repos/alfloop-dev/odayplus/environments/staging/variables?per_page=100 --jq .variables[].name
```
**輸出清單**：
- `GCP_AR_REPO`
- `GCP_CLOUD_SQL_INSTANCE`
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_SERVICE_ACCOUNT`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `MLFLOW_TRACKING_URI`
- `ODAY_DATABASE_URL_SECRET`
- `ODP_AUTH_AUDIENCES`
- `ODP_AUTH_ISSUER`
- `ODP_AUTH_JWKS_URI`
- `ODP_AUTH_PRINCIPAL_MAP_SECRET`
- `ODP_AUTH_SUBJECT_ROLE_BINDINGS`
- `ODP_CLOUD_RUN_API_SERVICE`
- `ODP_CLOUD_RUN_MIGRATION_JOB`
- `ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`
- `ODP_CLOUD_RUN_SCHEDULER_JOB`
- `ODP_CLOUD_RUN_VPC_CONNECTOR`
- `ODP_CLOUD_RUN_VPC_EGRESS`
- `ODP_CLOUD_RUN_WEB_SERVICE`
- `ODP_CLOUD_RUN_WORKER_JOB`
- `ODP_CLOUD_SCHEDULER_SCHEDULER_TRIGGER`
- `ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT`
- `ODP_CLOUD_SCHEDULER_WORKER_TRIGGER`
- `ODP_EXTERNAL_PROVIDER_MODE`
- `ODP_FORECAST_ENGINE`
- `ODP_FORECAST_MODEL`
- `ODP_LIVE_E2E_DEPLOYMENT_MODE`
- `ODP_LIVE_E2E_WORKER_DEADLINE_SECONDS`
- `ODP_MLFLOW_CLOUD_RUN_AUDIENCE`
- `ODP_OPERATOR_SMOKE_ROLE`
- `ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT`
- `ODP_OPERATOR_SMOKE_SUBJECT`
- `ODP_RELEASE_LEASE_PUBLIC_KEY`
- `ODP_RELEASE_LEASE_STATE_URI`
- `ODP_SCHEDULED_INGESTION_TENANT_ID`
- `ODP_SCHEDULER_CRON`
- `ODP_SCHEDULER_TIME_ZONE`
- `ODP_SNAPSHOT_BUCKET`
- `ODP_STAGING_API_URL`
- `ODP_STAGING_DEPLOY_URL`
- `ODP_STAGING_SECRET_OWNER`
- `ODP_TENANT_ID`
- `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`
- `ODP_WEB_OIDC_ISSUER`
- `ODP_WEB_SESSION_SECRET_SECRET`
- `ODP_WORKER_CRON`

### 5.3 `production` 環境變數列表驗證 (44 個)
```bash
gh api --paginate repos/alfloop-dev/odayplus/environments/production/variables?per_page=100 --jq .variables[].name
```
**輸出清單**：
- `GCP_AR_REPO`
- `GCP_CLOUD_SQL_INSTANCE`
- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_SERVICE_ACCOUNT`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `MLFLOW_TRACKING_URI`
- `ODAY_DATABASE_URL_SECRET`
- `ODP_AUTH_AUDIENCES`
- `ODP_AUTH_ISSUER`
- `ODP_AUTH_JWKS_URI`
- `ODP_AUTH_PRINCIPAL_MAP_SECRET`
- `ODP_AUTH_SUBJECT_ROLE_BINDINGS`
- `ODP_CLOUD_RUN_API_SERVICE`
- `ODP_CLOUD_RUN_MIGRATION_JOB`
- `ODP_CLOUD_RUN_RUNTIME_SERVICE_ACCOUNT`
- `ODP_CLOUD_RUN_SCHEDULER_JOB`
- `ODP_CLOUD_RUN_WEB_SERVICE`
- `ODP_CLOUD_RUN_WORKER_JOB`
- `ODP_CLOUD_SCHEDULER_SCHEDULER_TRIGGER`
- `ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT`
- `ODP_CLOUD_SCHEDULER_WORKER_TRIGGER`
- `ODP_EXTERNAL_PROVIDER_MODE`
- `ODP_FORECAST_ENGINE`
- `ODP_FORECAST_MODEL`
- `ODP_LIVE_E2E_DEPLOYMENT_MODE`
- `ODP_LIVE_E2E_WORKER_DEADLINE_SECONDS`
- `ODP_MLFLOW_CLOUD_RUN_AUDIENCE`
- `ODP_OPERATOR_SMOKE_ROLE`
- `ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT`
- `ODP_OPERATOR_SMOKE_SUBJECT`
- `ODP_PROD_API_URL`
- `ODP_PROD_DEPLOY_URL`
- `ODP_RELEASE_LEASE_PUBLIC_KEY`
- `ODP_RELEASE_LEASE_STATE_URI`
- `ODP_SCHEDULED_INGESTION_TENANT_ID`
- `ODP_SCHEDULER_CRON`
- `ODP_SCHEDULER_TIME_ZONE`
- `ODP_SNAPSHOT_BUCKET`
- `ODP_TENANT_ID`
- `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`
- `ODP_WEB_OIDC_ISSUER`
- `ODP_WEB_SESSION_SECRET_SECRET`
- `ODP_WORKER_CRON`
