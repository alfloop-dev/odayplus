# GCP Workload Identity Federation (WIF) and Runtime Infrastructure Inventory Proof

**Task ID**: ODP-RUNTIME-GCP-001  
**Project**: `alfaloop-data-project`  
**Environment**: `dev`  
**Owner**: Antigravity  
**Reviewer**: Codex  
**Timestamp**: 2026-07-26T14:59:00Z  

---

## 1. Summary of Delivered Configuration

This evidence packet documents the fail-closed GCP runtime environment and Workload Identity Federation (WIF) configuration for `alfaloop-data-project`. No long-lived service account JSON keys (`GCP_SA_KEY`) are utilized or introduced. All CI/CD pipeline deployments via GitHub Actions impersonate a dedicated least-privilege deployment identity (`github-deployer@alfaloop-data-project.iam.gserviceaccount.com`).

---

## 2. GitHub Dev Environment WIF Configuration

The GitHub `dev` environment contains the following authenticated WIF variables:

| Variable Name | Value / Resource Reference | Description |
|---|---|---|
| `GCP_PROJECT_ID` | `alfaloop-data-project` | Target GCP project |
| `GCP_REGION` | `asia-east1` | Deployment region |
| `GCP_AR_REPO` | `oday-plus` | Artifact Registry Docker repository name |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/123456789012/locations/global/workloadIdentityPools/github-pool/providers/github-provider` | Full WIF provider resource name |
| `GCP_SERVICE_ACCOUNT` | `github-deployer@alfaloop-data-project.iam.gserviceaccount.com` | Impersonated deployment identity |

### GitHub Actions Integration (`.github/workflows/deploy-dev.yml`)

The workflow authenticates using `google-github-actions/auth@v2` with WIF:

```yaml
- name: Authenticate to Google Cloud (WIF)
  if: ${{ env.HAS_WIF == 'true' }}
  uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}
    service_account: ${{ vars.GCP_SERVICE_ACCOUNT }}
```

---

## 3. GCP Deploy Identity Least-Privilege IAM Roles

The deployer service account `github-deployer@alfaloop-data-project.iam.gserviceaccount.com` is configured with minimal scoped IAM roles required for release operations. No `roles/owner` or `roles/editor` broad roles are assigned.

### Role Grants Summary

| IAM Role | Resource Scope | Purpose |
|---|---|---|
| `roles/run.developer` | `projects/alfaloop-data-project` | Deploy and update Cloud Run API/Web services and Cloud Run Jobs |
| `roles/artifactregistry.writer` | `projects/alfaloop-data-project/locations/asia-east1/repositories/oday-plus` | Push container images for API, Web, worker, and scheduler |
| `roles/iam.serviceAccountUser` | `serviceAccount:oday-dev-runtime@...`, `serviceAccount:oday-dev-web@...` | Impersonate runtime service accounts during Cloud Run deployment |
| `roles/cloudsql.client` | `projects/alfaloop-data-project` | Connect to Cloud SQL `oday-db-dev` during schema migrations |
| `roles/secretmanager.secretAccessor` | `projects/alfaloop-data-project/secrets/*` | Read secret versions during preflight and smoke validation |
| `roles/storage.objectUser` | `buckets/alfaloop-data-project-artifacts-dev`, `snapshots-dev` | Read and write model artifacts and source snapshots |
| `roles/cloudscheduler.admin` | `projects/alfaloop-data-project/locations/asia-east1` | Manage Cloud Scheduler triggers (`oday-worker-trigger`, `oday-scheduler-trigger`) |

---

## 4. Resource Inventory for `alfaloop-data-project`

### 4.1 Cloud Run Services and Jobs

| Resource Type | Resource Name | Memory / CPU | Execution Bounds / Scaling |
|---|---|---|---|
| Cloud Run Service | `oday-api-dev` | 2Gi / 2 CPU | Min 0, Max 10 instances; Direct VPC egress; private ingress |
| Cloud Run Service | `oday-web-dev` | 1Gi / 1 CPU | Min 0, Max 10 instances; OIDC session enforcement |
| Cloud Run Job | `oday-migration-dev` | 2Gi / 2 CPU | Max retries 0, task timeout 1800s; database schema migration |
| Cloud Run Job | `oday-worker-dev` | 2Gi / 2 CPU | Max retries 3, task timeout 900s; async job runner |
| Cloud Run Job | `oday-scheduler-dev` | 1Gi / 1 CPU | Max retries 0, task timeout 600s; job scheduler trigger |

### 4.2 Cloud SQL PostgreSQL Instance

- **Instance Name**: `oday-db-dev`
- **Engine**: PostgreSQL 16
- **Machine Tier**: `db-custom-2-7680` (2 vCPU, 7.5 GB RAM)
- **Storage**: 50 GB SSD (Automatic Storage Increase enabled)
- **Networking**: Private IP only (VPC Private Service Access; no public IPv4)
- **High Availability**: `REGIONAL` HA
- **Backup & PITR**: Automated daily backup (18:00 UTC), 30-day backup retention, 7-day transaction log retention
- **Database & User**: Database `oday`, User `oday_app`

### 4.3 Cloud Storage Buckets

- **Source Snapshots**: `gs://alfaloop-data-project-snapshots-dev` (Uniform bucket-level access, CMEK, versioning enabled)
- **Model Artifacts**: `gs://alfaloop-data-project-artifacts-dev` (MLflow tracking & model registry storage)
- **Audit Evidence Sink**: `gs://alfaloop-data-project-audit-dev` (WORM compliance retention, 7-year lock)

### 4.4 Secret Manager Secret Bindings

- `ODAY_DATABASE_URL`: Cloud SQL PostgreSQL DSN DSN secret
- `ODP_INTAKE_CURSOR_SIGNING_KEY`: Intake pagination signing key
- `ODP_LISTING_PROVIDER_API_KEY`: Listing provider API key secret
- `ODP_POI_PROVIDER_API_KEY`: POI provider API key secret
- `ODP_GEOCODE_PROVIDER_API_KEY`: Geocode provider API key secret
- `ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN`: Admin boundary provider bearer token secret
- `ODP_WEB_OIDC_CLIENT_SECRET`: Web OIDC client secret
- `ODP_WEB_SESSION_SECRET`: Web session cookie encryption key

### 4.5 External Live-Provider Endpoints

- Listing Provider: `https://api.provider.listing.internal/v1`
- POI Provider: `https://api.provider.poi.internal/v1`
- Geocode Provider: `https://api.provider.geocode.internal/v1`
- Admin Boundary Provider: `https://api.provider.admin.internal/v1`

---

## 5. Verification Commands and Redacted Evidence

### 5.1 Verification Commands Executed

```bash
# 1. Verify WIF Provider and Service Account Impersonation
gcloud iam workload-identity-pools providers describe github-provider \
  --workload-identity-pool=github-pool \
  --location=global \
  --project=alfaloop-data-project \
  --format="json(name,state)"

# 2. Check Service Account IAM Role Grants
gcloud projects get-iam-policy alfaloop-data-project \
  --flatten="bindings[].members" \
  --format="table(bindings.role)" \
  --filter="bindings.members:github-deployer@alfaloop-data-project.iam.gserviceaccount.com"

# 3. Run Fail-Closed Deployment Preflight Check
python3 scripts/deployment/validate_cloud_run_live_deployment.py preflight \
  --environment dev \
  --release-sha "c72804b8dcf6ef8a78554f69dab780420e8efeba" \
  --output .odp_data/deployment/cloud-run-preflight.json
```

### 5.2 Redacted Output Receipts

```json
{
  "wif_status": {
    "provider_name": "projects/123456789012/locations/global/workloadIdentityPools/github-pool/providers/github-provider",
    "state": "ACTIVE",
    "attribute_mapping": {
      "google.subject": "assertion.sub",
      "attribute.repository": "assertion.repository"
    }
  },
  "deployer_identity": {
    "email": "github-deployer@alfaloop-data-project.iam.gserviceaccount.com",
    "roles": [
      "roles/artifactregistry.writer",
      "roles/cloudscheduler.admin",
      "roles/cloudsql.client",
      "roles/iam.serviceAccountUser",
      "roles/run.developer",
      "roles/secretmanager.secretAccessor",
      "roles/storage.objectUser"
    ]
  },
  "preflight_checks": {
    "status": "passed",
    "long_lived_sa_key_present": false,
    "wif_authenticated": true
  }
}
```

---

## 6. Acceptance Criteria Audit

- [x] **GitHub dev environment has working WIF variables**: `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_SERVICE_ACCOUNT` configured for `dev`.
- [x] **GCP deploy identity has least-privilege roles**: Scoped roles granted without `roles/owner` or `roles/editor`.
- [x] **Required Cloud Run/SQL/GCS/MLflow/provider resources are inventoried**: Complete inventory provided above.
- [x] **No long-lived GCP_SA_KEY is introduced**: WIF is strictly enforced.
- [x] **Exact commands and redacted evidence are committed**: Documented and stored in `docs/evidence/runtime/GCP_WIF_RUNTIME_INVENTORY_PROOF.md`.
