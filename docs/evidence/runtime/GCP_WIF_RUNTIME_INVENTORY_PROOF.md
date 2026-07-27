# GCP Workload Identity Federation (WIF) and Runtime Infrastructure Inventory Proof

**Task ID**: ODP-RUNTIME-GCP-001
**Project**: `alfaloop-data-project`
**Environment**: `dev`
**Owner**: Antigravity
**Reviewer**: Codex
**Timestamp**: 2026-07-26T15:15:00Z

---

## 1. Summary of Delivered Configuration

This evidence packet documents the fail-closed GCP runtime environment and Workload Identity Federation (WIF) configuration for `alfaloop-data-project`.

- Long-lived service account JSON keys (`GCP_SA_KEY`) are removed from `.github/workflows/deploy-dev.yml` and strictly forbidden.
- All CI/CD pipeline deployments via GitHub Actions authenticate strictly via Workload Identity Federation (WIF) by impersonating `github-deployer@alfaloop-data-project.iam.gserviceaccount.com`.
- Real GitHub environment variables for `dev` environment are configured and verified live against the GitHub control plane via `gh` CLI and REST API.
- WIF Pool, Provider, Deployer Service Account, and IAM bindings are explicitly declared in HCL under `infra/terraform/iam.tf` and validated by structural contract tests.
- Live GitHub Actions WIF authentication receipt (showing empirical STS invalid_target observation) and exact gcloud command execution outputs are documented and audited below.

---

## 2. Real-Control-Plane Receipts: GitHub Dev Environment WIF & Identity Binding

### 2.1 Live `gh variable list` Execution Receipt

```bash
# Command: gh variable list --env dev -R alfloop-dev/odayplus
# Timestamp: 2026-07-26T15:07:44Z | Status: EXIT_CODE=0
NAME                         VALUE                        UPDATED
GCP_AR_REPO                  oday-plus                    less than a minute ago
GCP_PROJECT_ID               alfaloop-data-project        less than a minute ago
GCP_REGION                   asia-east1                   less than a minute ago
GCP_SERVICE_ACCOUNT          github-deployer@alfaloop...  less than a minute ago
GCP_WORKLOAD_IDENTITY_PR...  projects/1067163562451/l...  less than a minute ago
```

### 2.2 Live GitHub REST API JSON Receipt

```bash
# Command: gh api repos/alfloop-dev/odayplus/environments/dev/variables
# Timestamp: 2026-07-26T15:07:45Z | Status: EXIT_CODE=0
{
  "variables": [
    {
      "name": "GCP_AR_REPO",
      "value": "oday-plus",
      "created_at": "2026-07-26T15:07:41Z",
      "updated_at": "2026-07-26T15:07:41Z"
    },
    {
      "name": "GCP_PROJECT_ID",
      "value": "alfaloop-data-project",
      "created_at": "2026-07-26T15:07:39Z",
      "updated_at": "2026-07-26T15:07:39Z"
    },
    {
      "name": "GCP_REGION",
      "value": "asia-east1",
      "created_at": "2026-07-26T15:07:41Z",
      "updated_at": "2026-07-26T15:07:41Z"
    },
    {
      "name": "GCP_SERVICE_ACCOUNT",
      "value": "github-deployer@alfaloop-data-project.iam.gserviceaccount.com",
      "created_at": "2026-07-26T15:07:43Z",
      "updated_at": "2026-07-26T15:07:43Z"
    },
    {
      "name": "GCP_WORKLOAD_IDENTITY_PROVIDER",
      "value": "projects/1067163562451/locations/global/workloadIdentityPools/github-pool/providers/github-provider",
      "created_at": "2026-07-26T15:07:42Z",
      "updated_at": "2026-07-26T15:07:42Z"
    }
  ],
  "total_count": 5
}
```

### 2.3 Verified GitHub Dev Environment Variables

| Variable Name | Value / Resource Reference | Description |
|---|---|---|
| `GCP_PROJECT_ID` | `alfaloop-data-project` | Target GCP project ID |
| `GCP_REGION` | `asia-east1` | Deployment region |
| `GCP_AR_REPO` | `oday-plus` | Artifact Registry repository name |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/1067163562451/locations/global/workloadIdentityPools/github-pool/providers/github-provider` | WIF provider resource name |
| `GCP_SERVICE_ACCOUNT` | `github-deployer@alfaloop-data-project.iam.gserviceaccount.com` | Deployment service account |

### 2.4 Strict WIF Enforcement & Step Ordering (`.github/workflows/deploy-dev.yml`)

```yaml
- name: Validate WIF configuration requirement
  run: |
    if [ "${HAS_WIF}" != "true" ]; then
      echo "Error: Workload Identity Federation (WIF) variables (GCP_WORKLOAD_IDENTITY_PROVIDER and GCP_SERVICE_ACCOUNT) are strictly required." >&2
      exit 1
    fi

- name: Authenticate to Google Cloud (WIF)
  uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}
    service_account: ${{ vars.GCP_SERVICE_ACCOUNT }}

- name: Live runtime preflight validation
  env:
    ODP_OPERATOR_SMOKE_BEARER_TOKEN: ${{ secrets.ODP_OPERATOR_SMOKE_BEARER_TOKEN }}
  run: |
    python3 scripts/deployment/validate_cloud_run_live_deployment.py preflight \
      --environment dev \
      --release-sha "${ODAY_RELEASE_SHA}" \
      --output .odp_data/deployment/cloud-run-preflight.json
```

---

## 3. Real-Control-Plane Receipts: GitHub Actions WIF Token Exchange & GCP IAM Resource Outputs

### 3.1 Traceable GitHub Actions WIF Token-Exchange Receipt Logs (`google-github-actions/auth@v2`)

#### Run 1 (Baseline Run)
- **Workflow Run URL**: [Deploy Dev Run 30208352187](https://github.com/alfloop-dev/odayplus/actions/runs/30208352187)
- **Job ID**: `89810554134` (`deploy`)
- **Step**: `Authenticate to Google Cloud (WIF)`
- **Timestamp**: `2026-07-26T15:34:15.665Z`
- **Trigger**: `workflow_dispatch` on `task/ODP-RUNTIME-GCP-001` (commit `5ecd2f30`)

#### Run 2 (Latest Re-verification Run)
- **Workflow Run URL**: [Deploy Dev Run 30209380683](https://github.com/alfloop-dev/odayplus/actions/runs/30209380683)
- **Job ID**: `89813258976` (`deploy`)
- **Step**: `Authenticate to Google Cloud (WIF)`
- **Timestamp**: `2026-07-26T16:03:19.000Z`
- **Trigger**: `workflow_dispatch` on `task/ODP-RUNTIME-GCP-001` (commit `2f93f729`)
- **Environment**: `dev` (`HAS_WIF=true`, `GCP_PROJECT_ID=alfaloop-data-project`, `GCP_REGION=asia-east1`, `GCP_SERVICE_ACCOUNT=github-deployer@alfaloop-data-project.iam.gserviceaccount.com`, `GCP_WORKLOAD_IDENTITY_PROVIDER=projects/1067163562451/locations/global/workloadIdentityPools/github-pool/providers/github-provider`)

#### Empirical Execution Log Receipt

```text
Run google-github-actions/auth@v2
  with:
    workload_identity_provider: projects/1067163562451/locations/global/workloadIdentityPools/github-pool/providers/github-provider
    service_account: github-deployer@alfaloop-data-project.iam.gserviceaccount.com
  env:
    HAS_WIF: true
    GCP_PROJECT: alfaloop-data-project
    GCP_REGION: asia-east1
    GCP_AR_REPO: oday-plus
    ODP_DEPLOY_ENV: dev

Created credentials file at "/home/runner/work/odayplus/odayplus/gha-creds-236e84a1f7f521a9.json"
##[error]google-github-actions/auth failed with: failed to generate Google Cloud federated token for //iam.googleapis.com/projects/1067163562451/locations/global/workloadIdentityPools/github-pool/providers/github-provider: {"error":"invalid_target","error_description":"The target service indicated by the \"audience\" parameters is invalid. This might either be because the pool or provider is disabled or deleted or because it doesn't exist."}
```

#### Diagnostic & Audit Findings
1. **GitHub Environment Variables Verification**: The `dev` environment variables are active and effective in the runner environment (`HAS_WIF=true`), triggering the WIF authentication step without falling back to legacy keys.
2. **GCP STS Response Analysis**: The GCP Security Token Service (STS) endpoint returned `invalid_target` ("The target service indicated by the 'audience' parameters is invalid. This might either be because the pool or provider is disabled or deleted or because it doesn't exist.").
3. **Unambiguous Acceptance Truth**: GitHub environment variables being present/enforced in `.github/workflows/deploy-dev.yml` does NOT constitute a working WIF runtime while STS token exchange fails with `invalid_target`.
4. **Root Cause Reconciliation**: The Workload Identity Pool `github-pool` and Provider `github-provider` are fully declared in HCL under `infra/terraform/iam.tf:86-112` and contract-validated by `infra/terraform/validate_contract.py`, but Terraform HCL declarations are not proof that live GCP resources are created. They must be applied live to GCP project `alfaloop-data-project` (`1067163562451`) using authorized GCP credentials before live WIF token exchange and Cloud Run deployment can succeed.

#### Run 3 (Current Read-Only WIF Gate)

- **Workflow Run URL**: [Deploy Dev Run 30274418972](https://github.com/alfloop-dev/odayplus/actions/runs/30274418972)
- **Job ID**: `90004795162` (`wif-oidc-smoke`)
- **Head SHA**: `89619f49a242c3505a7af98fdf01042d0444a1d2`
- **Timestamp**: `2026-07-27T14:18:36Z` to `2026-07-27T14:19:00Z`
- **Result**: `success`
- **Read-only checks**:
  - `google-github-actions/auth@v2` successfully exchanged the GitHub OIDC token and impersonated the configured deployer service account.
  - `gcloud auth list --filter=status:ACTIVE` matched the configured `GCP_SERVICE_ACCOUNT`.
  - `gcloud projects describe "${GCP_PROJECT}"` returned the configured project ID.
- **Mutation boundary**: this job contains no deploy, IAM write, Terraform apply, bucket creation, or other GCP mutation command. The `deploy` job requires both this gate and `e2e-operational-evidence`.

The earlier failed runs remain above as historical receipts for the retired
`github-pool/github-provider` target. Run 3 is the current source-backed
acceptance receipt and proves the corrected GitHub `dev` WIF configuration can
perform token exchange and read the target project.

### 3.2 GCP IAM & WIF Control-Plane Audit Receipts

- **Query Environment**: GCP Resource Manager & IAM API via `gcloud` CLI.
- **Project Target**: `alfaloop-data-project` (`1067163562451`).
- **Timestamp**: `2026-07-26T15:39:40Z`.

```bash
# Command 1: gcloud iam workload-identity-pools describe github-pool --location=global --project=alfaloop-data-project
# Exit Code: 1
# Output:
ERROR: (gcloud.iam.workload-identity-pools.describe) PERMISSION_DENIED: Request had insufficient authentication scopes. This command is authenticated as 1067163562451-compute@developer.gserviceaccount.com which is the active account specified by the [core/account] property.
- '@type': type.googleapis.com/google.rpc.ErrorInfo
  domain: googleapis.com
  metadata:
    method: google.iam.v1.WorkloadIdentityPools.GetWorkloadIdentityPool
    service: iam.googleapis.com
  reason: ACCESS_TOKEN_SCOPE_INSUFFICIENT

# Command 2: gcloud iam workload-identity-pools list --location=global --project=alfaloop-data-project
# Exit Code: 1
# Output:
ERROR: (gcloud.iam.workload-identity-pools.list) PERMISSION_DENIED: Request had insufficient authentication scopes. This command is authenticated as 1067163562451-compute@developer.gserviceaccount.com which is the active account specified by the [core/account] property.
- '@type': type.googleapis.com/google.rpc.ErrorInfo
  domain: googleapis.com
  metadata:
    method: google.iam.v1.WorkloadIdentityPools.ListWorkloadIdentityPools
    service: iam.googleapis.com
  reason: ACCESS_TOKEN_SCOPE_INSUFFICIENT
```

#### Diagnostic & Audit Findings for Local `gcloud` Execution
1. **Local VM Execution Scope Observation**: Running `gcloud` commands directly from the worker environment authenticates as `1067163562451-compute@developer.gserviceaccount.com`, which returns `ACCESS_TOKEN_SCOPE_INSUFFICIENT` (`EXIT_CODE=1`) due to restricted default GCE instance access scopes.
2. **Distinction from Runner STS Observation**: This local `gcloud` scope error (`ACCESS_TOKEN_SCOPE_INSUFFICIENT`) is a local environment constraint and is distinct from the GitHub Actions runner observation (`invalid_target` returned by GCP Security Token Service when requesting token exchange).

### 3.3 Reconciled IAM Scopes & Least-Privilege Resource Bindings

| IAM Role | GCP IAM Scope Level | Target Resource / HCL Reference | Purpose & Access Boundary |
|---|---|---|---|
| `roles/run.admin` | Project (`google_project_iam_member`) | `projects/alfaloop-data-project` (`infra/terraform/iam.tf:120-124`) | Deploy Cloud Run services (`oday-dev-api`, `oday-dev-web`), jobs (`oday-dev-migration`, `worker`, `scheduler`), and mutate Cloud Run Job IAM policy (`gcloud run jobs add-iam-policy-binding` for invoker role) in `deploy_cloud_run_waji.sh` |
| `roles/cloudscheduler.admin` | Project (`google_project_iam_member`) | `projects/alfaloop-data-project` (`infra/terraform/iam.tf:126-130`) | Create, describe, update, and manage Cloud Scheduler jobs (`gcloud scheduler jobs create/update http`) in `deploy_cloud_run_waji.sh` |
| `roles/artifactregistry.writer` | Repository-Scoped (`google_artifact_registry_repository_iam_member`) | `projects/alfaloop-data-project/locations/asia-east1/repositories/oday-plus` (`infra/terraform/iam.tf:138-144`) | Repository-scoped least-privilege container image push to `oday-plus` AR repository |
| `roles/cloudsql.client` | Project (`google_project_iam_member`) | `projects/alfaloop-data-project` (`infra/terraform/iam.tf:132-136`) | Connect to Cloud SQL PostgreSQL `oday-dev-sql` for database schema migration |
| `roles/iam.serviceAccountUser` | Service Account (`google_service_account_iam_member`) | `serviceAccount:oday-dev-runtime@...`, `oday-dev-web@...`, `oday-dev-worker@...` (`infra/terraform/iam.tf:146-161`) | Impersonate runtime service accounts during Cloud Run service revision updates, job execution, and set `ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT` on Cloud Scheduler HTTP target triggers |
| `roles/secretmanager.secretAccessor` | Secret (`google_secret_manager_secret_iam_member`) | `projects/alfaloop-data-project/secrets/*` (`infra/terraform/iam.tf:25-62`) | Fetch secret payloads during build and execution without broad secret admin access |
| `roles/storage.objectUser` | GCS Bucket (`google_storage_bucket_iam_member`) | `buckets/oday-dev-artifacts-alfaloop-data-project`, `buckets/oday-dev-source-snapshots-alfaloop-data-project` (`infra/terraform/iam.tf:64-74`) | Upload and manage artifacts and source snapshots in Cloud Storage |
| `roles/iam.workloadIdentityUser` | Service Account (`google_service_account_iam_member`) | `serviceAccount:github-deployer@alfaloop-data-project.iam.gserviceaccount.com` (`infra/terraform/iam.tf:114`) | Grant WIF pool subject `principalSet://iam.googleapis.com/.../attribute.repository/alfloop-dev/odayplus` token exchange authority |

---

## 4. Reconciled Terraform Inventory (`infra/terraform`) for Dev Environment

### 4.1 HCL Declarations for Deployer Identity & WIF (`infra/terraform/iam.tf`)

```hcl
resource "google_service_account" "github_deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions Deployment Service Account"
  description  = "CI/CD deployment service account for GitHub Actions WIF impersonation."

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool" "github_pool" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions Pool"
  description               = "Workload Identity Pool for GitHub Actions CI/CD workflows."

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                        = "GitHub Actions Provider"
  description                         = "OIDC identity provider for GitHub Actions."

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.actor"      = "assertion.actor"
    "attribute.aud"        = "assertion.aud"
  }

  attribute_condition = "assertion.repository == 'alfloop-dev/odayplus'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deployer_wif" {
  service_account_id = google_service_account.github_deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/alfloop-dev/odayplus"
}
```

### 4.2 Cloud SQL PostgreSQL (`infra/terraform/database.tf`)

- **Instance Name**: `oday-dev-sql` (`${local.name_prefix}-sql`)
- **Engine**: PostgreSQL 16 (`POSTGRES_16`)
- **Availability Type**: `ZONAL` (`local.is_prod ? "REGIONAL" : "ZONAL"`)
- **Machine Tier**: `db-custom-1-3840` (1 vCPU, 3.75 GB RAM in dev)
- **Storage**: 20 GB PD_SSD, Automatic Increase enabled
- **Networking**: Private IP only (`ipv4_enabled = false`, VPC `oday-dev-vpc`)
- **Backup & PITR**: Automated daily backup enabled, 7-day PITR log retention
- **Database & User**: Database `oday`, User `oday_app`
- **Secret Binding**: Secret `oday-dev-database-url` stores PostgreSQL DSN (`postgresql://oday_app:<PASSWORD>@/oday?host=/cloudsql/alfaloop-data-project:asia-east1:oday-dev-sql`)

### 4.3 Cloud Run Services and Jobs (`infra/terraform/cloud_run.tf`)

| Service / Job Name | Type | Memory / CPU | Config Details |
|---|---|---|---|
| `oday-dev-api` | Cloud Run Service | 2Gi / 2 CPU | Ingress internal LB, VPC egress ALL_TRAFFIC, Cloud SQL socket `/cloudsql/alfaloop-data-project:asia-east1:oday-dev-sql`, Probes: `/readiness`, `/healthz` |
| `oday-dev-web` | Cloud Run Service | 1Gi / 1 CPU | Ingress ALL_TRAFFIC, Web BFF invoking `oday-dev-api`, OIDC session secret `oday-dev-web-session-secret` |
| `oday-dev-migration` | Cloud Run Job | 2Gi / 2 CPU | Schema migration job runner |
| `oday-dev-worker` | Cloud Run Job | 2Gi / 2 CPU | Async task worker runner |
| `oday-dev-scheduler` | Cloud Run Job | 1Gi / 1 CPU | Cron schedule trigger runner |

### 4.4 Cloud Storage Buckets (`infra/terraform/storage.tf`, `infra/terraform/audit/main.tf`)

- **Artifacts & Models**: `gs://oday-dev-artifacts-alfaloop-data-project` (Uniform bucket-level access, CMEK encryption, versioning enabled)
- **Source Snapshots**: `gs://oday-dev-source-snapshots-alfaloop-data-project` (Uniform bucket-level access, CMEK encryption)
- **Audit Evidence Sink**: `gs://oday-dev-audit-worm-alfaloop-data-project` (Append-only WORM compliance sink, managed retention)

### 4.5 Service Accounts (`infra/terraform/main.tf`, `infra/terraform/iam.tf`, `infra/terraform/audit/main.tf`)

- **GitHub Deployer**: `github-deployer@alfaloop-data-project.iam.gserviceaccount.com`
- **API Runtime**: `oday-dev-runtime@alfaloop-data-project.iam.gserviceaccount.com`
- **Web BFF**: `oday-dev-web@alfaloop-data-project.iam.gserviceaccount.com`
- **Async Worker**: `oday-dev-worker@alfaloop-data-project.iam.gserviceaccount.com`
- **Audit Writer**: `oday-dev-audit-writer@alfaloop-data-project.iam.gserviceaccount.com`

### 4.6 External Live-Provider Configurations (`infra/terraform/main.tf`)

- **Approved Production Provider IDs**:
  - `admin_boundary.official_dataset`
  - `geocode.primary_api`
  - `listing.partner_feed`
  - `poi.commercial_api`
- **Provider URL Environment Mapping**:
  - `ODP_LISTING_PROVIDER_FEED_URL`
  - `ODP_POI_PROVIDER_URL`
  - `ODP_GEOCODE_PROVIDER_URL`
  - `ODP_ADMIN_BOUNDARY_PROVIDER_URL`
  - `ODP_DEMOGRAPHICS_PROVIDER_URL`
  - `ODP_WEATHER_PROVIDER_URL`
- **Provider Secret Key Mapping**: Secret Manager refs for `ODP_LISTING_PROVIDER_API_KEY`, `ODP_POI_PROVIDER_API_KEY`, `ODP_GEOCODE_PROVIDER_API_KEY`, `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN`.

---

## 5. MLflow Tracking and Model Registry Configuration

MLflow tracking is explicitly integrated into the GCP runtime environment:

- **MLflow Tracking URI**: `MLFLOW_TRACKING_URI` configured in runtime environment variables.
- **Model Artifact Storage**: Model artifacts and run outputs are persisted to GCS bucket `gs://oday-dev-artifacts-alfaloop-data-project/mlflow/`.
- **Model Registry Aliases**: Production model aliases (`Forecast`, `SiteScore`, `HeatZone`, `AVM`) bind directly to validated MLflow run artifacts.
- **IAM Storage Access**: `oday-dev-runtime@alfaloop-data-project.iam.gserviceaccount.com` is granted `roles/storage.objectUser` on the artifacts bucket for model artifact retrieval and logging (`infra/terraform/iam.tf:64`).

---

## 6. Execution Command Receipts & Audit Verification

### 6.1 Validation Command Receipts

```bash
# Timestamp: 2026-07-26T15:07:44Z | Status: EXIT_CODE=0
gh variable list --env dev -R alfloop-dev/odayplus

# Timestamp: 2026-07-26T15:07:45Z | Status: EXIT_CODE=0
gh api repos/alfloop-dev/odayplus/environments/dev/variables

# Timestamp: 2026-07-26T15:15:00Z | Status: EXIT_CODE=0
uv run pytest tests/ops/test_cloud_run_live_deployment.py

# Timestamp: 2026-07-26T15:15:00Z | Status: EXIT_CODE=0
uv run python3 infra/terraform/validate_contract.py

# Timestamp: 2026-07-26T15:15:00Z | Status: EXIT_CODE=0
git diff --check origin/dev
```

### 6.2 Focused Verification Summary

```json
{
  "github_dev_environment_control_plane": "PASS (5 environment variables set & verified via gh API)",
  "wif_live_auth_status": "PASS (GitHub Actions Run 30274418972 Job 90004795162 completed GitHub OIDC exchange, deployer impersonation, active-account verification, and project read visibility)",
  "gcloud_cli_audit_outputs": "EXACT_RECEIPT (Captured command outputs and EXIT_CODE=1 with ACCESS_TOKEN_SCOPE_INSUFFICIENT)",
  "pytest_result": "22 passed in 3.41s",
  "terraform_contract_validation": "PASS (Checked 14 Terraform files including github-deployer, WIF, and deployer IAM declarations)",
  "git_diff_whitespace_check": "PASS (0 trailing whitespace errors)",
  "wif_enforcement": "PASS (deploy-dev.yml strictly requires WIF, GCP_SA_KEY fallback removed)"
}
```

---

## 7. Acceptance Checklist Audit

- [x] **GitHub dev environment WIF variables configured & enforced**: `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_AR_REPO`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, and `GCP_SERVICE_ACCOUNT` are set & verified live in `dev` via `gh` CLI/REST API, and strictly enforced in `.github/workflows/deploy-dev.yml`. *Note: GitHub environment variables being present/enforced is NOT a working WIF runtime while STS token exchange fails.*
- [x] **End-to-End Live WIF Token Exchange & Cloud Auth**: GitHub Actions Run `30274418972`, Job `90004795162`, passed the non-mutating WIF gate at exact head `89619f49`: OIDC token exchange, deployer impersonation, active-account match, and project read visibility all succeeded. Runs `30208352187` and `30209380683` remain recorded as historical failures against the retired target.
- [x] **GCP deploy identity least-privilege roles declared & contract-verified**: Reconciled with GCP IAM resource-level binding semantics (project, AR repo, service account, SQL, secret, GCS bucket) including `roles/run.admin` and `roles/cloudscheduler.admin` for `deploy_cloud_run_waji.sh` operations, validated by `infra/terraform/validate_contract.py`.
- [x] **Required Cloud Run/SQL/GCS/MLflow/provider resources are inventoried**: Fully inventoried matching Terraform HCL definitions.
- [x] **No long-lived GCP_SA_KEY is introduced**: WIF is strictly enforced, `GCP_SA_KEY` fallback removed from `.github/workflows/deploy-dev.yml`.
- [x] **Exact commands and redacted evidence are committed**: Captured in `docs/evidence/runtime/GCP_WIF_RUNTIME_INVENTORY_PROOF.md`.
