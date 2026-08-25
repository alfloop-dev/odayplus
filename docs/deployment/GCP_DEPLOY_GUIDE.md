# GCP Cloud Run Deployment Reference Guide

This document defines the configuration model, variable scopes, and credentials required for the automated deployment of ODay Plus API and Web services to GCP Cloud Run via GitHub Actions.

## Required Variables and Secrets

The deployment pipeline is configured via GitHub Variables and Secrets, falling back to local environment variables during manual execution. If required configurations are missing, the pipeline will fail-closed immediately.

### 1. Target Environment Variables (GitHub Variables)

| Variable Name | Scope | Description | Example Value |
|---|---|---|---|
| `GCP_PROJECT_ID` | Repository / Environment | The GCP Project ID where resources are deployed. | `odayplus-runtime-20260825` |
| `GCP_REGION` | Repository / Environment | The GCP target region. | `asia-east1` |
| `GCP_AR_REPO` | Repository / Environment | The name of the GCP Artifact Registry repository. | `oday-plus-dev` |

### 2. Authentication Configuration (WIF only)

All deployment environments strictly require **Workload Identity Federation (WIF)**. Long-lived service account keys (`GCP_SA_KEY`) are prohibited by security policy, and no `GCP_SA_KEY` fallback path exists in `.github/workflows/deploy-dev.yml` or `product_ops/deployment/deploy_cloud_run_waji.sh`.

Configure the following **GitHub Variables**:

| Variable Name | Description | Example Value |
|---|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | The full resource name of the Workload Identity Provider. | `projects/767864276141/locations/global/workloadIdentityPools/github-actions/providers/odayplus` |
| `GCP_SERVICE_ACCOUNT` | The service account email to impersonate. | `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com` |

---

## Fail-Closed Mechanics

目前正式的部署目標是 `odayplus-runtime-20260825`。`alfaloop-data-project`
及其 service account、Cloud SQL、bucket、OAuth client、provider gateway URL
均不得作為新 release 的 fallback。若新專案缺少必要資源，部署必須停止並回報
缺口，不得跨專案讀取舊資源。

第三方 provider 不隨 runtime project 搬遷。新專案的來源啟用清單預設為空、
不建立 provider credentials，也不開放 provider egress；後續只能透過逐來源核准
流程啟用。

### 新 project 的 Web OAuth 人工作業

一般 Google Auth Platform Web OAuth client 不能由部署 CLI 自動建立。請在
`odayplus-runtime-20260825` 的 Google Auth Platform 建立「Web application」client，
並設定：

- Authorized JavaScript origin：`https://oday-web-767864276141.asia-east1.run.app`
- Authorized redirect URI：`https://oday-web-767864276141.asia-east1.run.app/auth/callback`

client ID 寫入 GitHub `dev` environment variable `ODP_WEB_OIDC_CLIENT_ID`；client
secret 只能寫入新 project Secret Manager 的
`oday-plus-dev-web-oidc-client-secret`，GitHub 只保存
`oday-plus-dev-web-oidc-client-secret:latest` 這個 reference。不得把 client secret
貼進 issue、PR、workflow log 或 repository。

If the deployment runs and WIF variables (`GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT`) are not populated, or if any required target environment variables are missing, the deployment script and the CI/CD pipeline will fail-closed immediately:

1. **Pre-flight Validation**: The workflow contains a `Validate GCP Deployment Variables` step that performs sanity checks and prints clear diagnostics.
2. **Local Script Safety**: The script `product_ops/deployment/deploy_cloud_run_waji.sh` checks the same environment variables and aborts execution before building any Docker images.

---

## Deployment Process Details

1. **Build & Deploy API (`oday-api`)**:
   - The API docker image is built using `infra/docker/api.Dockerfile` for the `linux/amd64` platform.
   - The image is pushed to Artifact Registry: `${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GCP_AR_REPO}/oday-api:dev-<sha>`
   - The service is deployed to Cloud Run with `ODP_ENV=dev`.
2. **Build & Deploy Web (`oday-web`)**:
   - The API Cloud Run URL is retrieved.
   - The Web docker image is built using `infra/docker/web.Dockerfile`, baking the retrieved API URL into Next.js configuration using the `ODP_API_BASE_URL` build argument.
   - The image is pushed to Artifact Registry: `${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GCP_AR_REPO}/oday-web:dev-<sha>`
   - The service is deployed to Cloud Run.
3. **Automated Smoke Checks**:
   - Verify API `/health` returns `200`.
   - Verify Web `/operator` returns `200`.
