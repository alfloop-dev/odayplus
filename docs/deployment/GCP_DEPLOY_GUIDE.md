# GCP Cloud Run Deployment Reference Guide

This document defines the configuration model, variable scopes, environment protection rules, and credentials required for the automated deployment of ODay Plus API, Web, and background jobs to GCP Cloud Run via the unified Runtime Release GitHub Actions workflow (`.github/workflows/deploy-dev.yml`).

---

## 1. Environment Topology & Protection Matrix

The ODay Plus delivery lifecycle uses three GitHub deployment environments, each with specific protection boundaries:

| Environment | Purpose | Lifecycle | Protection Rule | Required Reviewers |
|---|---|---|---|---|
| `dev` | Continuous integration, contract verification, live E2E | Permanent | None (automated CI/CD deploy on `dev` merge) | N/A |
| `staging` | Ephemeral release rehearsal (migration, E2E, rollback drill) | Per-release ephemeral (auto-cleanup / 24h TTL) | `required_reviewers` | `Alien-alfaloop`, `ajoe734` |
| `production` | Blue-green serving (0% green smoke → 100% traffic switch) | Permanent | `required_reviewers` | `Alien-alfaloop`, `ajoe734` |

### GitHub Environment Reviewer Governance
- **Staging & Production Review Rules**: Both environments require explicit review approval by authorized human operators before deployment jobs execute.
- **Audited Reviewers**:
  - `Alien-alfaloop` (ID: `122770408`)
  - `ajoe734` (ID: `169176954`)
- **Self-Review Prevention**: Can be configured per compliance policy. Live audit receipts are preserved in [`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/github-environments-audit.json`](../evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/github-environments-audit.json).

---

## 2. Authentication Configuration (WIF Only)

All deployment and build environments strictly require **Workload Identity Federation (WIF)**. Long-lived service account keys (`GCP_SA_KEY`) are prohibited by security policy, and no `GCP_SA_KEY` fallback path exists in `.github/workflows/deploy-dev.yml` or `product_ops/deployment/deploy_cloud_run_waji.sh`.

### Keyless WIF Parameters (GitHub Variables)

| Variable Name | Scope | Description | Canonical Value |
|---|---|---|---|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Environment / Repo | Full resource name of the Workload Identity Provider. | `projects/767864276141/locations/global/workloadIdentityPools/github-actions/providers/odayplus` |
| `GCP_SERVICE_ACCOUNT` | Environment / Repo | The deployment service account email to impersonate. | `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com` |

### IAM Workload Identity Binding
The deployer service account binds `roles/iam.workloadIdentityUser` to the GitHub repository subject set:
```text
principalSet://iam.googleapis.com/projects/767864276141/locations/global/workloadIdentityPools/github-actions/attribute.repository/alfloop-dev/odayplus
```

---

## 3. Required Environment Variables & Secret References

The deployment pipeline is configured via GitHub Environment Variables and Secret Manager Secret References. If required configurations are missing, the pipeline will fail-closed immediately.

### 3.1 Target GCP Environment Variables

| Variable Name | Scope | Description | Example Value (`dev` / `staging`) |
|---|---|---|---|
| `GCP_PROJECT_ID` | Environment | GCP Project ID where resources are deployed. | `odayplus-runtime-20260825` |
| `GCP_REGION` | Environment | GCP target deployment region. | `asia-east1` |
| `GCP_AR_REPO` | Environment | GCP Artifact Registry Docker repository name. | `oday-plus-dev` |
| `GCP_CLOUD_SQL_INSTANCE` | Environment | Cloud SQL instance connection string. | `odayplus-runtime-20260825:asia-east1:oday-dev-sql` |
| `ODP_CLOUD_RUN_API_SERVICE` | Environment | API Cloud Run service name. | `oday-api` |
| `ODP_CLOUD_RUN_WEB_SERVICE` | Environment | Web Cloud Run service name. | `oday-web` |
| `ODP_CLOUD_RUN_MIGRATION_JOB` | Environment | Migration Cloud Run Job name. | `oday-migration` |
| `ODP_CLOUD_RUN_WORKER_JOB` | Environment | Worker Cloud Run Job name. | `oday-worker` |
| `ODP_CLOUD_RUN_SCHEDULER_JOB` | Environment | Scheduler Cloud Run Job name. | `oday-scheduler` |
| `ODP_FORECAST_ENGINE` | Environment | Time-series forecasting engine. | `statsforecast` |
| `ODP_FORECAST_MODEL` | Environment | Default forecasting model. | `seasonal_naive` |

### 3.2 Secret Reference Governance (Zero Plaintext Secrets in GitHub)

Secrets are never stored as plaintext strings in GitHub repository settings or workflow files. GitHub Environment Variables hold only the Secret Manager secret name/reference (`<secret-name>:latest`):

| Variable Name | Secret Manager Secret Name | Secret Purpose |
|---|---|---|
| `ODAY_DATABASE_URL_SECRET` | `oday-plus-dev-api-database-url-pg16` | PostgreSQL connection string (`postgresql://...`) |
| `ODP_AUTH_PRINCIPAL_MAP_SECRET` | `oday-plus-dev-auth-principal-map` | Subject & SA email to RBAC role mappings JSON |
| `ODP_WEB_SESSION_SECRET_SECRET` | `oday-plus-dev-web-session-secret` | Web application session signing key |
| `ODP_WEB_OIDC_CLIENT_SECRET_SECRET` | `oday-plus-dev-web-oidc-client-secret` | Google OAuth Web client secret |

---

## 4. Fail-Closed Mechanics & Human Authority Prerequisites

### Current Project Baseline (`odayplus-runtime-20260825`)
The active shared non-production runtime project is `odayplus-runtime-20260825`. Legacy projects (`alfaloop-data-project`, `alfaloop-data-project-2`) and their service accounts, Cloud SQL instances, buckets, and OAuth clients are deprecated and must not be used as fallback. If target project resources are missing, deployment aborts immediately.

### Third-Party Provider Gate
Third-party external providers are disabled by default. The active provider list is empty, provider credentials are not provisioned, and default-deny egress is enforced. External provider enablement requires explicit source approval receipts.

### Human Authority Prerequisites for Production
In accordance with Rollout Plan §16, Auto-Workers must **fail-closed** and not invent fictitious or placeholder configurations for production. Live production deployment is blocked until human operators provision:
1. **Dedicated Production GCP Project & Region**: Project creation, billing, and resource naming signoff.
2. **Production WIF & Service Accounts**: Workload Identity Pool provider mapping and production runtime/deployer SAs.
3. **Production Cloud SQL & Secrets**: Dedicated production database instance and populated Secret Manager secrets.
4. **Production Web OAuth Client & Domains**: Google Auth Platform client with production authorized redirect URIs and custom domain URLs (`ODP_PROD_DEPLOY_URL`, `ODP_PROD_API_URL`).
5. **Production Operations Policy**: Watch window duration, SLO error/latency rollback thresholds, and on-call operator assignment.

---

## 5. Deployment Process Details

1. **Build Once (`build` job)**:
   - Container images for API (`infra/docker/api.Dockerfile`), Worker (`infra/docker/worker.Dockerfile`), Scheduler (`infra/docker/scheduler.Dockerfile`), and Web (`infra/docker/web.Dockerfile`) are built for `linux/amd64` using the exact release SHA.
   - Images are pushed to Artifact Registry: `${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GCP_AR_REPO}/<service>:release-<sha>`
   - The job resolves each pushed tag to its immutable `repo/service@sha256:<64-hex>` reference and publishes the four-reference handoff artifact `runtime-release-images-<sha>`.
   - Images are signed with Cosign and verified against OCI signatures.
   - Secret scan, SAST (Bandit), SBOM generation, and baseline E2E backup/restore proofs run in the build stage.

2. **Deploy-by-Digest (`deploy` job)**:
   - The first environment dispatch leaves `api_image`, `web_image`, `worker_image`, and `scheduler_image` empty so the build job runs. Staging and production dispatches must pass all four exact references from the handoff artifact; supplying only some, a mutable tag, or a different reference is rejected before admission.
   - When the handoff is supplied, the build job is skipped and the deploy job passes those exact digest references to Cloud Run. The deploy script rejects tags in this mode and never rebuilds.
   - For `dev`: Deploys API/Web services, Migration/Worker/Scheduler jobs, updates scheduler triggers, and runs live E2E acceptance gate.
   - For `staging`: 透過 `product_ops/deployment/staging_lifecycle.py create` 建立短生命週期 release-scoped 隔離資源（DB、Bucket、Tenant、Service Accounts、Cloud Run Services/Jobs、Paused 排程），並將 Terraform output handoff 作為唯一 runtime authority；`staging_lifecycle.py verify` 執行 8 階段完整演練（Migration compatibility, snapshot materialization, authenticated smoke, worker idempotency, scheduler one-shot, backup/restore drill, rollback rehearsal, external providers disabled），產生 secret-free 收據上傳。成功 closeout 後精確 cleanup；失敗時僅在 create state、ownership inventory 與 lifecycle marker 可讀時執行 `staging_lifecycle.py hold`，依 TTL（24h）保留供除錯。
   - For `production`: Deploys green candidate revisions (0% public traffic), executes green smoke checks, executes blue→green 100% traffic switch via `product_ops/deployment/bluegreen_release.py`, and updates scheduler trigger digests.

3. **Automated Smoke Checks & Receipts**:
   - Verify API authenticated health checks with release-scoped least-privilege identity.
   - Verify Web operator console loads and authentication flows succeed.
   - Redacted JSON validation receipts are published as workflow artifacts (`runtime-release-${environment}-validation`).

Staging 的 Terraform state 必須使用受保護的 GCS backend，prefix 固定包含完整 release id（`oday-plus/ephemeral-staging/<release_id>`）；state 內含 generated credentials，不能放在 runner `/tmp`、git 或一般 artifact。`create` 產生的 tfvars/inventory/lifecycle sidecar 與 output handoff 會寫入同一 release recovery bundle，供 failure hold、orphan recovery 及 production closeout 使用。Staging 驗證成功後不立即 cleanup；必須先驗證 production watch-window durable closeout receipt，再由同一 lifecycle cleanup 以 exact release labels 執行銷毀。

---

## 6. Audit & Evidence Receipts

Live configuration readbacks and proof artifacts for environment bootstrap are preserved under [`docs/evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/`](../evidence/runtime/ODP-GITHUB-GCP-ENV-BOOTSTRAP-001/):
- `github-environments-audit.json`: Environment protection rules and reviewer user IDs.
- `github-variables-audit.json`: Redacted audit of environment variables across `dev`, `staging`, and `production`.
- `gcp-wif-iam-audit.json`: GCP WIF provider, IAM policy, and Secret Manager references.
- `production-authority-prerequisites.json`: Human authority blocking condition checklist.
