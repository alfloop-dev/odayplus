# ODP-GITHUB-GCP-ENV-BOOTSTRAP-001 — Staging & Production GitHub and GCP Environment Protection

- **Task ID**: `ODP-GITHUB-GCP-ENV-BOOTSTRAP-001`
- **Title**: 建立 staging/production GitHub 與 GCP 環境保護
- **Owner**: Claude2 (Claimed by Antigravity3 under helper lease)
- **Reviewer**: Antigravity2
- **Phase**: Wave 3 - Environment Bootstrap
- **Date**: 2026-08-25
- **Source Plan**: [`docs/deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](../../deployment/EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md)

---

## 1. Overview & Objectives

This task establishes and verifies GitHub Environments, environment protection rules, Workload Identity Federation (WIF), IAM policies, and secret reference governance across `dev`, `staging`, and `production` deployment targets for ODay Plus, fulfilling the requirements of Wave 3 Environment Bootstrap.

Key guarantees enforced:
1. **GitHub Environment Protection**: `staging` and `production` environments are explicitly protected on GitHub with mandatory required reviewers.
2. **Keyless WIF & IAM Governance**: Deployment strictly uses Workload Identity Federation (`projects/767864276141/.../providers/odayplus`) and impersonated service accounts (`github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com`). Long-lived keys (`GCP_SA_KEY`) remain strictly prohibited.
3. **Secret Reference Boundary**: GitHub holds *only* resource references/selectors (e.g. Secret Manager secret names), never raw secret values or plaintext connection strings.
4. **Fail-Closed on Missing Human Authority**: Where dedicated production GCP resources require human operator authority (GCP Project creation, production database credentials, OAuth client secret, on-call SLO thresholds), the configuration explicitly fails closed and documents the missing authority checklist rather than inserting fake or placeholder values.
5. **Redacted Audit Trail**: Complete readback receipts of GitHub environments, variables, WIF policies, and authority prerequisites are recorded in this evidence directory.

---

## 2. GitHub Environments & Reviewer Protection

Three canonical deployment environments exist in repository `alfloop-dev/odayplus`:

| Environment | Purpose | Protection Rule | Required Reviewers |
|---|---|---|---|
| `dev` | Continuous integration & automated integration tests | None (Automated CI/CD deploy on dev merge) | N/A |
| `staging` | Ephemeral release rehearsal (migration, E2E, rollback) | `required_reviewers` | `Alien-alfaloop` (ID: 122770408), `ajoe734` (ID: 169176954) |
| `production` | Blue-green live serving (0% green smoke → 100% atomic switch) | `required_reviewers` | `Alien-alfaloop` (ID: 122770408), `ajoe734` (ID: 169176954) |

### Reviewer Readback Verification
Both `staging` and `production` environments are configured with protection rules via the GitHub Deployments API:
- Protection Rule Type: `required_reviewers`
- Prevent Self Review: `false`
- Reviewers:
  - `Alien-alfaloop` (`User`, ID: `122770408`)
  - `ajoe734` (`User`, ID: `169176954`)

Readback details: [`github-environments-audit.json`](github-environments-audit.json).

---

## 3. Workload Identity Federation (WIF) & IAM Configuration

All automated deployment and build jobs authenticate to GCP via keyless Workload Identity Federation.

### 3.1 GCP Workload Identity Pool & Provider
- **Project ID**: `odayplus-runtime-20260825` (Project Number: `767864276141`)
- **Location**: `global`
- **Workload Identity Pool**: `github-actions` (`projects/767864276141/locations/global/workloadIdentityPools/github-actions`)
- **Workload Identity Provider**: `odayplus` (`projects/767864276141/locations/global/workloadIdentityPools/github-actions/providers/odayplus`)
- **OIDC Issuer**: `https://token.actions.githubusercontent.com`
- **Attribute Condition**: `assertion.repository == 'alfloop-dev/odayplus'`
- **Attribute Mappings**:
  - `google.subject`: `assertion.sub`
  - `attribute.actor`: `assertion.actor`
  - `attribute.aud`: `assertion.aud`
  - `attribute.repository`: `assertion.repository`

### 3.2 Deployment Service Account
- **Service Account**: `github-deployer@odayplus-runtime-20260825.iam.gserviceaccount.com`
- **IAM Policy**:
  - `roles/iam.workloadIdentityUser` bound to `principalSet://iam.googleapis.com/projects/767864276141/locations/global/workloadIdentityPools/github-actions/attribute.repository/alfloop-dev/odayplus`
- **Artifact Registry**: `oday-plus-dev` in `asia-east1` (`asia-east1-docker.pkg.dev/odayplus-runtime-20260825/oday-plus-dev`)

Readback details: [`gcp-wif-iam-audit.json`](gcp-wif-iam-audit.json).

---

## 4. Environment Variables & Secret References

### 4.1 GitHub Environment Variables Summary
- **`dev` Environment**: 38 variables configured covering Cloud Run services/jobs, Cloud SQL connection, Secret Manager secret references, Cloud Scheduler triggers, and MLflow/Auth endpoints.
- **`staging` Environment**: 7 base variables configured (`GCP_PROJECT_ID`, `GCP_REGION`, `GCP_AR_REPO`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `ODP_FORECAST_ENGINE`, `ODP_FORECAST_MODEL`). Ephemeral resources (database, tenant partition, bucket prefix, jobs) are provisioned per-release dynamically via `staging_lifecycle.py` and Terraform module `infra/terraform/modules/ephemeral_staging`.
- **`production` Environment**: 0 variables currently populated. Intentionally held in a fail-closed state pending human authority provisioning for dedicated production GCP resources.

### 4.2 Secret Reference Governance
All secret bindings in the runtime release pipeline (`deploy-dev.yml` and `deploy_cloud_run_waji.sh`) reference Secret Manager secret names rather than raw values:
- `ODAY_DATABASE_URL_SECRET`: `oday-plus-dev-api-database-url-pg16`
- `ODP_AUTH_PRINCIPAL_MAP_SECRET`: `oday-plus-dev-auth-principal-map`
- `ODP_WEB_SESSION_SECRET_SECRET`: `oday-plus-dev-web-session-secret`
- `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`: `oday-plus-dev-web-oidc-client-secret`

Readback details: [`github-variables-audit.json`](github-variables-audit.json).

---

## 5. Human Authority Prerequisites for Production

In accordance with Rollout Plan §16, Auto-Workers must not invent fictitious values or placeholders for production infrastructure. The following decisions and external resources require Human/Ops operator provisioning before live production deployment:

1. **`PROD-GCP-01` (GCP Project & Resource Naming)**: Formal selection of dedicated production GCP Project ID (or formal signoff on project partitioning) and resource naming.
2. **`PROD-GCP-02` (WIF & IAM Service Accounts)**: Production Workload Identity Pool provider attribute mapping for repository `alfloop-dev/odayplus`, and least-privilege production deployer/runtime service accounts.
3. **`PROD-GCP-03` (Cloud SQL & Secret Manager)**: Production Cloud SQL PostgreSQL 16 instance with private IP, and production Secret Manager secrets (`ODAY_DATABASE_URL_SECRET`, `ODP_AUTH_PRINCIPAL_MAP_SECRET`, `ODP_WEB_SESSION_SECRET_SECRET`, `ODP_WEB_OIDC_CLIENT_SECRET_SECRET`).
4. **`PROD-GCP-04` (OAuth Client & Custom Domains)**: Google Auth Platform Web application OAuth 2.0 Client ID & Secret with production redirect URIs, and custom domain URLs (`ODP_PROD_DEPLOY_URL`, `ODP_PROD_API_URL`).
5. **`PROD-OPS-05` (Operations & Governance)**: Production watch window length, SLO latency/error rate rollback thresholds, and designated on-call operator.

Readback details: [`production-authority-prerequisites.json`](production-authority-prerequisites.json).

---

## 6. Audit Receipts Index

| Artifact | Format | Description |
|---|---|---|
| [`github-environments-audit.json`](github-environments-audit.json) | JSON | Live readback of GitHub environments (`dev`, `staging`, `production`) and protection rules |
| [`github-variables-audit.json`](github-variables-audit.json) | JSON | Live readback of environment variables across `dev`, `staging`, and `production` |
| [`gcp-wif-iam-audit.json`](gcp-wif-iam-audit.json) | JSON | Live readback of GCP WIF pool, provider, deployer SA IAM policy, and Secret Manager references |
| [`production-authority-prerequisites.json`](production-authority-prerequisites.json) | JSON | Structured checklist of required human authority parameters for production rollout |

---

## 7. Verification Summary

- **Operations & Deployment Contract Tests**:
  ```bash
  uv run --python 3.12 pytest tests/ops/
  ```
  **Result**: 614 passed, 14 subtests passed in 106.69s.
- **GitHub API Environments & Protection Rules Verification**:
  - `gh api repos/alfloop-dev/odayplus/environments/staging` → Confirmed `protection_rules` contains `required_reviewers` with `Alien-alfaloop` and `ajoe734`.
  - `gh api repos/alfloop-dev/odayplus/environments/production` → Confirmed `protection_rules` contains `required_reviewers` with `Alien-alfaloop` and `ajoe734`.
- **GCP WIF & IAM Policy Verification**:
  - `gcloud iam workload-identity-pools providers describe odayplus ...` → Confirmed mapped to `alfloop-dev/odayplus`.
  - `gcloud iam service-accounts get-iam-policy github-deployer@...` → Confirmed `roles/iam.workloadIdentityUser` bound.
