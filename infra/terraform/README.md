# ODay Plus Production Terraform

This root module is the fail-closed GCP runtime contract for ODay Plus. It does
not deploy sample data, fixture providers, local model artifacts, SQLite, or
memory persistence.

## Managed Runtime

| Resource | Production contract |
|---|---|
| Cloud Run API | Only where `cloud_run_services_managed = true` (default; dev). Production sets it false: the Runtime Release deploy path (`deploy_cloud_run_waji.sh` via `deploy-dev.yml`) creates and owns `oday-prod-api` / `oday-prod-web` and their invoker bindings, and this stack creates no Cloud Run service or service-scoped IAM there (owner decision 2026-11-20). The load balancer NEGs and the monitoring filters name the services (`local.api_service_name` / `local.web_service_name`) in both modes. Immutable digest, exact release SHA, IAM-only invocation, Gen2, Direct VPC egress, Cloud SQL volume, `/readiness` startup probe, `/liveness` liveness probe. |
| Cloud Run Web | Immutable digest, exact release SHA, OIDC session enforcement, service-to-service identity token, private API invocation, Gen2, Direct VPC egress. |
| Cloud SQL PostgreSQL 16 | Private IP only, `REGIONAL` HA, SSD/autoresize, PITR, 30+ backups, CMEK, encrypted connections, deletion protection. |
| Secret Manager | Terraform-generated database URL, cursor key, and Web session secret plus references to the shared Web/API local identity signing key and environment-owned model credentials. External provider credentials and direct external data acquisition are disabled in consumer deployment. No secret payload is accepted as a variable or exposed as an output. |
| Cloud Storage | Separate CMEK-encrypted, versioned artifact and source-snapshot buckets plus the WORM audit-evidence module. |
| Pub/Sub | CMEK job topic, ordered subscription, bounded retry, DLQ topic/subscription, regional persistence. |
| IAM | Separate API, worker, audit-writer, and audit-retention identities with resource-level grants. |
| Production deployer IAM | `iam.tf` (prod only): custom role `prodDeployerSqlBackup` (`cloudsql.backupRuns.create/get/list`, `cloudsql.instances.get`) for the pre-migration backup `deploy-dev.yml` takes, bound to `github-deployer`, which also gets `roles/logging.viewer` and `roles/iam.serviceAccountTokenCreator` on `oday-prod-smoke-operator`. `import` blocks adopt the hand-made deployer, WIF pool/provider, `oday-prod-runtime`, `oday-prod-smoke-operator` and the `oday-prod-database-url` / `oday-prod-web-session-secret` secrets (read back 2026-11-20); the hand-made `oday-prod-sql` instance is never adopted. |
| Staging deployer IAM | `iam.tf` (staging only): the hand-granted deployer project grants written back exactly as live (2026-11-20) plus the `stagingWebLbAttach` custom role; `import` blocks adopt the existing ones (Terraform >= 1.7), and the resources name the deployer by email so a targeted apply never touches `google_service_account.github_deployer`. |
| Cloud Monitoring | `monitoring.tf`: Web `/login` uptime check, optional API `/platform/health` uptime check (Monitoring service agent OIDC), alert policies for uptime failure, API/Web 5xx ratio, API p95 latency (3.0 s ratified budget), failed Cloud Run job executions, and Cloud SQL CPU/disk/connection saturation. Notification targets come only from variables. |
| Custom-domain load balancer | `load_balancer.tf` (`web_load_balancer_enabled`): persistent global external HTTPS Application Load Balancer, reserved static IP (`prevent_destroy`), Google-managed certificate for the `web_base_url` host and `api_public_host`, 443 forwarding rule with a `MODERN`/TLS 1.2-minimum SSL policy, and a port-80 forwarding rule whose URL map only 301-redirects to HTTPS. Dev/prod route to Terraform-owned serverless NEGs; staging owns only the backend service and `staging_lifecycle.py` attaches each release's Web NEG (`web_custom_domain` verify stage) and detaches it before destroy. The `oday-plus.com.tw` zone is at an external registrar: add an A record per `web_load_balancer_hosts` entry pointing at `web_load_balancer_ip`; the certificate stays `PROVISIONING` until they resolve. |
| VPC | Custom regional subnet, Direct VPC egress, Private Service Access, default-deny egress firewall with restricted RFC1918 and Google API CIDR allow rules, no Cloud NAT, no Cloud SQL public IPv4. |

Use `/liveness` for Cloud Run probes and external process checks. Google lists
some paths ending in `z` as [reserved URL paths](https://docs.cloud.google.com/run/docs/known-issues#reserved-url-paths),
which can return a frontend 404 before reaching the container. The API retains
`/healthz` for existing local callers; `/health` remains the detailed health
report and `/readiness` continues to enforce the runtime readiness contract.

## Production Fail-Closed Gates

A production plan fails when any of the following is true:

- the API or Web image is not pinned as `...@sha256:<64 hex>`;
- `release_sha` is not an exact 40-character Git SHA;
- `cloud_sql_instance_name` is empty or the derived `oday-prod-sql` (the hand-made instance this stack must not import or manage);
- where this stack owns Cloud Run (`cloud_run_services_managed = true`), API or Web Cloud Run has fewer than two minimum instances, or the API has a public invoker. Where the deploy path owns them, `deploy_cloud_run_waji.sh` refuses production unless `ODP_CLOUD_RUN_MIN_INSTANCES` is at least 2;
- OIDC issuer/JWKS are not HTTPS or no audience is configured;
- external provider mode is not fixture or any external provider credentials, endpoints, auth status, or provider IDs are projected;
- `release_profile` (required, `full` or `core-ops`) is `full` and MLflow is not a remote HTTPS registry, or the approved AVM/forecast model metadata is incomplete or the artifact digest is invalid;
- `release_profile = "core-ops"` (no model served) and `ODP_FORECAST_ENGINE` / `ODP_FORECAST_MODEL` are missing from `model_runtime_config`. Model values are never filled with placeholders (ODP-CORE-OPS-RELEASE-PROFILE-001 HD-06 / F2);
- any production input contains mock, fixture, synthetic, seed, memory, SQLite,
  local, stub, replay, sandbox, development, latest, placeholder, example, or TODO markers;
- Cloud SQL production capacity, PITR, backup retention, private networking,
  regional HA, CMEK, or deletion protection is weakened by this module.
- no alert notification target (`monitoring_notification_emails` or
  `monitoring_notification_channel_ids`), no `monitoring_api_uptime_host`, or no
  `monitoring_cloud_sql_max_connections` is supplied.
- `web_load_balancer_enabled` is false, `api_public_host` is empty, or
  `monitoring_api_uptime_host` differs from `api_public_host` (the API ingress is
  internal-and-load-balancer only, so the load balancer is its only external path).

The authoritative blocker is
`terraform_data.production_contract.lifecycle.precondition`. Terraform `check`
blocks repeat the conditions for readable diagnostics, but no resource or API
enablement can begin until the lifecycle preconditions pass.

`ODAY_ENV=prod`, `ODP_DEPLOY_ENV=prod`, `ODP_PERSISTENCE=postgresql`,
`ODP_REQUIRE_LIVE_DATA=true`, `ODP_EXTERNAL_PROVIDER_MODE=fixture`,
`ODP_PRODUCT_MODE=live`, and `ODP_OBJECT_STORE=gcs` are Terraform-owned and
cannot be overridden through `runtime_additional_env`.

Dev defaults to PostgreSQL with platform snapshot consumer startup.
Staging and production read platform snapshots; external provider live mode
and egress NAT are disabled across all environments.

## Required External Values

Values, approvals, and secret payloads are owned outside Terraform:

1. GCP project, billing, organization policies, and a pre-created GCS state
   bucket with versioning, retention, CMEK, and tightly scoped Terraform-runner
   access.
2. Immutable API and Web image digests built from the same exact source commit.
3. Public HTTPS Web origin (`web_base_url`), API invoker members, and Web
   invoker members. Required in every `auth_mode`.
4. Token verification inputs. `service_auth_issuer`, `service_auth_jwks_uri`,
   and `service_auth_audiences` verify the Cloud Run service-identity and
   deployment smoke tokens and apply in every `auth_mode`; the first two default
   to Google's issuer and JWKS endpoint. `oidc_issuer`, `oidc_audiences`,
   `oidc_jwks_uri`, and the Web client registration/secret are consumed **only
   when `auth_mode = "oidc"`**. The default `"local"` mode deploys with
   password-only authentication and no Google OAuth client or OIDC secret; when
   `service_auth_audiences` is empty it falls back to `oidc_audiences` so
   pre-contract deployments keep their existing accepted audience list.
   `identity_token_signing_key_ref` is also required in production; its one
   pinned Secret Manager version is mounted into both API and Web as
   `ODP_IDENTITY_TOKEN_SIGNING_KEY`, and the API resolver uses `local-default`
   for that plain key.
5. External provider live endpoints, credentials, and Cloud NAT egress IPs are
   disabled in consumer-only platform snapshot deployment. Direct external
   provider acquisition is off.
6. Model registry credentials (if MLflow does not use workload identity), and
   the Web OIDC client secret when `auth_mode = "oidc"`.
7. Remote MLflow URI, production aliases/artifacts, AVM liquidity approval
   metadata, digest, dataset snapshot, and OSS forecast engine/model.
8. Optional MLflow credential secret IDs when the registry does not use
   workload identity.
9. A migration/reconciliation release job and canonical live datasets that make
   `/readiness` return HTTP 200 before Cloud Run startup expires.
10. DNS, managed HTTPS, and Cloud Armor policy when a custom Web origin is used.
    The module deploys the Web BFF while leaving the API at
    internal/load-balancer ingress.
11. Alert notification targets (on-call email addresses or existing
    notification channel ids), an API host that Cloud Monitoring uptime checkers
    can reach (the API ingress is internal/load-balancer only, so the run.app
    host is not reachable by itself), and the Cloud SQL instance's effective
    `max_connections`. See
    `docs/evidence/runtime/ODP-PROD-MONITORING-ALERTING-001/README.md`.

The application WORM/object-store client must use Cloud Run workload identity
or metadata-server ADC. Do not create or inject service-account JSON keys or
short-lived OAuth access tokens through Terraform.

## Secret Handling

The database password and cursor signing key are generated by the `random`
provider and written to regional Secret Manager versions. They are marked
sensitive by their providers, never appear in `.tfvars`, and are not outputs.
As with every Terraform-generated credential, the encrypted Terraform state
contains sensitive material. The GCS backend must therefore use CMEK,
versioning, retention, access logging, and a dedicated Terraform-runner role.

Model secret **IDs** may be supplied in `.tfvars`; external provider secrets are disabled and rejected. Their payloads are read by Cloud Run from Secret Manager at runtime and never enter Terraform configuration or outputs.

## Bootstrap and Apply

Create a backend configuration outside the repository:

```hcl
bucket = "REPLACE_WITH_GOVERNED_STATE_BUCKET"
prefix = "oday-plus/prod"
```

Then:

```bash
terraform -chdir=infra/terraform init \
  -backend-config=/secure/path/prod.backend.hcl

terraform -chdir=infra/terraform fmt -check -recursive
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform plan \
  -var-file=/secure/path/prod.tfvars \
  -out=/secure/path/prod.tfplan
```

The first environment bootstrap is intentionally two-phase because API startup
uses the real `/readiness` gate:

1. Apply through
   `google_secret_manager_secret_version.database_url` to create the private
   database, runtime user, and DSN secret.
2. Run the committed PostgreSQL migrations and live-data reconciliation from an
   approved migration identity with Cloud SQL Client and access only to the
   database URL secret.
3. Confirm model secrets have enabled versions, MLflow production aliases
   are approved, and the canonical datasets exist.
4. Run a fresh full plan, review it, and apply the saved plan.
5. Verify `/liveness`, `/readiness`, exact `release_sha`, OIDC rejection/acceptance,
   model lineage, Pub/Sub/DLQ, backup/PITR, and restore drill
   evidence before routing user traffic.

Production with `cloud_run_services_managed = false` needs no bootstrap target:
the deploy path runs migrations and creates the services after the full apply.
The staging foundation remains a targeted subset (see
`docs/evidence/runtime/ODP-STAGING-FOUNDATION-IAC-REMEDIATION-001`).

Never use `-target` for routine updates. The one bootstrap target above exists
only to break the initial database-migration/readiness dependency.

## Rotation and Recovery

- Rotate model credentials by adding a new enabled Secret Manager
  version; deploy a new Cloud Run revision and verify readiness before disabling
  the old version.
- Database and cursor keys are replaced only through a reviewed state move or
  taint/replacement procedure. Database rotation also requires updating the SQL
  user and replaying the generated DSN secret atomically.
- Cloud SQL restore must be exercised quarterly into an isolated instance.
  Reconcile SQL snapshot metadata against GCS object generations before opening
  traffic.
- Pub/Sub DLQ replay requires an authorized worker/operator identity and a
  recorded replay receipt. Never republish anonymously from a developer shell.
- Production KMS keys and the locked audit retention policy are deliberately
  protected from normal destroy operations.

## Local Validation

Run:

```bash
python3 infra/terraform/validate_contract.py
python3 -m unittest discover -s infra/terraform/tests -p 'test_*.py'
```

`validate_contract.py` is a dependency-free structural contract check. It is
not a replacement for `terraform fmt -check`, `terraform validate`, and a real
GCP production plan.
