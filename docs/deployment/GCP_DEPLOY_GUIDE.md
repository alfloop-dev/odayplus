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
| `ODP_AUTH_MODE` | Environment | Authoritative authentication mode. Password-first `local` is the default and needs no Google OAuth client; `oidc` additionally requires the OIDC variables below. | *(unset)* / `oidc` |
| `ODP_WEB_BASE_URL` | Environment | Canonical HTTPS web origin backing cookies, CSRF, and redirects. Required in **both** auth modes; the Web runtime fails closed without it in production. | `https://oday-web-dev.example.run.app` |

#### Authentication mode resolution

`ODP_AUTH_MODE` is the single input that decides whether OIDC is deployed. The
release script, the fail-closed preflight, the Web runtime, and the API's own
auth boundary all read it through the same resolver
(`product_ops/deployment/auth_mode.sh` and its Python half,
`shared/auth/mode.py`), so a revision can never be built with the OIDC client
secret bound but the issuer missing, or the reverse. Resolution order, first
match wins:

1. `ODP_AUTH_MODE` — `local` or `oidc`.
2. `ODP_AUTH_OIDC_ENABLED` — legacy boolean alias, kept so environments that
   only ever set the flag keep deploying unchanged.
3. `ODP_WEB_OIDC_ISSUER` — a configured issuer keeps a pre-contract environment
   on OIDC until it opts into an explicit mode. The API process never receives
   that variable, so its boundary reads `ODP_AUTH_OIDC_ISSUER` as the
   equivalent pre-contract signal.
4. Otherwise `local`.

The API is sent the *resolved* `ODP_AUTH_MODE`, not the raw operator inputs, and
the legacy alias is deliberately not forwarded to it: one authoritative value
cannot arrive split. In `local` mode the boundary discards the OIDC issuer,
audiences, and JWKS URI outright and refuses OIDC-issued tokens with
`issuer_mismatch`, so an environment that switches to password-first stops
trusting OIDC identities even while its previous OIDC variables are still set.
An invalid or self-contradicting mode disables the OIDC provider rather than
guessing at one.

Setting `ODP_AUTH_MODE` and `ODP_AUTH_OIDC_ENABLED` to disagreeing values is a
split configuration and fails the preflight rather than deploying either half.
In `oidc` mode `ODP_WEB_OIDC_ISSUER`, `ODP_WEB_OIDC_CLIENT_ID`, and
`ODP_WEB_OIDC_CLIENT_SECRET_SECRET` must all be present.

Both halves of the resolver read their inputs the same way, because "one
resolver" is otherwise only true on the happy path:

* **Normalisation.** `ODP_AUTH_MODE` and `ODP_AUTH_OIDC_ENABLED` are trimmed and
  lower-cased before they are compared, so `LOCAL`, ` local `, and `local` are
  one input, and `TRUE` is the same flag as `true`.
* **Placeholder values.** A variable whose value is empty or is only a
  placeholder token (`changeme`, `dummy`, `example`, `fixture`, `mock`,
  `placeholder`, `seed`, `todo`, …) counts as unconfigured everywhere. A
  placeholder `ODP_WEB_OIDC_ISSUER` therefore does not switch a pre-contract
  environment to OIDC, and it does not satisfy `oidc` mode either.

`ODP_AUTH_ISSUER`, `ODP_AUTH_AUDIENCES`, and `ODP_AUTH_JWKS_URI` stay required
in **both** modes: they also verify the Cloud Run service-identity token that
the deployment smoke stage mints, so they are not OIDC-only inputs. These are
the **migration aliases**; true runtime env separation is now provided by:

- `ODP_AUTH_SERVICE_ISSUER`, `ODP_AUTH_SERVICE_JWKS_URI`,
  `ODP_AUTH_SERVICE_AUDIENCES` — always injected, carry the Cloud Run
  service-identity provider (typically `https://accounts.google.com`).
- `ODP_AUTH_OIDC_ISSUER`, `ODP_AUTH_OIDC_JWKS_URI`,
  `ODP_AUTH_OIDC_AUDIENCES` — injected only when `auth_mode = "oidc"`;
  empty strings in `local` mode.

The API boundary's `config_from_env` prioritises the separated vars and falls
back to the legacy globals when they are absent, so both pre-contract
(legacy-only) and post-contract (separated) deployments work. The separated
vars prevent the reopen #5 defect where an OIDC token matched the service
path because both shared `ODP_AUTH_ISSUER`.

#### Service identities must be declared in `ODP_AUTH_PRINCIPAL_MAP`

A token that verifies against the service issuer proves *who* the caller is.
It never proves *what* the caller may do. Per the auth contract §4.4 and
ADR-0003, the roles and scope of a service identity come from
`ODP_AUTH_PRINCIPAL_MAP` (bound from `ODP_AUTH_PRINCIPAL_MAP_SECRET`) alone —
the boundary ignores any `roles`, `tenant_id`, or scope claims carried in the
token itself.

The practical consequence for operators: **a service account that is not a key
in `ODP_AUTH_PRINCIPAL_MAP` cannot authenticate at all.** The boundary fails
closed with `unknown_service` (HTTP 401) rather than admitting the caller with
whatever privileges its token asserts. When adding a new service-to-service
caller, or when the deployment smoke stage starts returning 401, add the
service account's `sub` (or its verified `email`) to the principal-map secret
and redeploy.

For a GCP service account the `email` key is normally the one you want. The
smoke token is minted with `gcloud auth print-identity-token
--impersonate-service-account=... --include-email`, and Google puts an opaque
**numeric** unique id in `sub` while the service-account address travels in
`email` with `email_verified: true`. In `oidc` mode the service and OIDC
issuers are both `https://accounts.google.com`, so the boundary first probes
the principal map by `sub`; when that misses it runs the *full* service
verification (signature, issuer, audience, `iat`/`nbf`/`exp`) and only then
looks the caller up by its verified `email`. An unverified `email`, or one
that is not a principal-map key, is never an identity fact: the token falls
through to the OIDC identity-store lookup and is rejected as
`federated_identity_not_linked`.

This gate closes a real bypass. Under the deployed shape both
`ODP_AUTH_ISSUER` and `ODP_AUTH_SERVICE_ISSUER` are
`https://accounts.google.com`, and in `local` mode the OIDC path is off, so any
token signed by a trusted key fell through to the service path. Without the
gate that path read `roles` straight off the token, so an unlinked token
claiming `roles: ["platform_admin"]` and an attacker-chosen `tenant_id` was
authenticated as a platform admin.

### 3.2 Secret Reference Governance (Zero Plaintext Secrets in GitHub)

Secrets are never stored as plaintext strings in GitHub repository settings or workflow files. GitHub Environment Variables hold only the Secret Manager secret name/reference (`<secret-name>:latest`):

| Variable Name | Secret Manager Secret Name | Secret Purpose |
|---|---|---|
| `ODAY_DATABASE_URL_SECRET` | `oday-plus-dev-api-database-url-pg16` | PostgreSQL connection string (`postgresql://...`) |
| `ODP_AUTH_PRINCIPAL_MAP_SECRET` | `oday-plus-dev-auth-principal-map` | Subject & SA email to RBAC role mappings JSON |
| `ODP_WEB_SESSION_SECRET_SECRET` | `oday-plus-dev-web-session-secret` | Web application session signing key |
| `ODP_IDENTITY_TOKEN_SIGNING_KEY_SECRET` | `oday-plus-dev-identity-token-signing-key` | Shared Web/API local access-token signing key; bind the same pinned version to both services |
| `ODP_WEB_OIDC_CLIENT_SECRET_SECRET` | `oday-plus-dev-web-oidc-client-secret` | Google OAuth Web client secret (**required only in `oidc` mode**; never bound to Cloud Run in `local` mode) |

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
   - For `staging`: 透過 `product_ops/deployment/staging_lifecycle.py create` 建立短生命週期 release-scoped 隔離資源（DB、Bucket、Tenant、Service Accounts、Cloud Run Services/Jobs、Paused 排程），並將 Terraform output handoff 作為唯一 runtime authority；`staging_lifecycle.py verify` 執行 9 階段完整演練（Migration compatibility, snapshot materialization, authenticated smoke, worker idempotency, scheduler one-shot, backup/restore drill, real rollback target switch/health/restore, controlled-VPC public-egress deny probe, external providers disabled），五個 Cloud Run release resources 均以 `ALL_TRAFFIC` 經受控 VPC，並以 live readback 證明 public canary 被拒絕。成功 closeout 後由同一 workflow 的獨立 `environment: staging` closeout job 先讀取並驗證 production watch receipt（同 candidate/manifest/release），再以 staging WIF 精確 cleanup；失敗時僅在 create state、ownership inventory 與 lifecycle marker 可讀時執行 `staging_lifecycle.py hold`，依 TTL（24h）保留供除錯。
   - For `production`: Deploys green candidate revisions (0% public traffic), executes green smoke checks, executes blue→green 100% traffic switch via `product_ops/deployment/bluegreen_release.py`, captures production traffic state live with readback, and updates scheduler trigger digests.

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
