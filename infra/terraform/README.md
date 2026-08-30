# ODay Plus Production Terraform

This root module is the fail-closed GCP runtime contract for ODay Plus. It does
not deploy sample data, fixture providers, local model artifacts, SQLite, or
memory persistence.

## Managed Runtime

| Resource | Production contract |
|---|---|
| Cloud Run API | Immutable digest, exact release SHA, IAM-only invocation, Gen2, Direct VPC egress, Cloud SQL volume, `/readiness` startup probe, `/healthz` liveness probe. |
| Cloud Run Web | Immutable digest, exact release SHA, OIDC session enforcement, service-to-service identity token, private API invocation, Gen2, Direct VPC egress. |
| Cloud SQL PostgreSQL 16 | Private IP only, `REGIONAL` HA, SSD/autoresize, PITR, 30+ backups, CMEK, encrypted connections, deletion protection. |
| Secret Manager | Terraform-generated database URL, cursor key, and Web session secret plus references to environment-owned model credentials. External provider credentials and direct external data acquisition are disabled in consumer deployment. No secret payload is accepted as a variable or exposed as an output. |
| Cloud Storage | Separate CMEK-encrypted, versioned artifact and source-snapshot buckets plus the WORM audit-evidence module. |
| Pub/Sub | CMEK job topic, ordered subscription, bounded retry, DLQ topic/subscription, regional persistence. |
| IAM | Separate API, worker, audit-writer, and audit-retention identities with resource-level grants. |
| VPC | Custom regional subnet, Direct VPC egress, Private Service Access, default-deny egress firewall with restricted RFC1918 and Google API CIDR allow rules, no Cloud NAT, no Cloud SQL public IPv4. |

## Production Fail-Closed Gates

A production plan fails when any of the following is true:

- the API or Web image is not pinned as `...@sha256:<64 hex>`;
- `release_sha` is not an exact 40-character Git SHA;
- API or Web Cloud Run has fewer than two minimum instances, or the API has a public invoker;
- OIDC issuer/JWKS are not HTTPS or no audience is configured;
- external provider mode is not fixture or any external provider credentials, endpoints, auth status, or provider IDs are projected;
- MLflow is not a remote HTTPS registry;
- approved AVM/forecast model metadata is incomplete or the artifact digest is invalid;
- any production input contains mock, fixture, synthetic, seed, memory, SQLite,
  local, stub, replay, sandbox, development, latest, placeholder, example, or TODO markers;
- Cloud SQL production capacity, PITR, backup retention, private networking,
  regional HA, CMEK, or deletion protection is weakened by this module.

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
3. OIDC issuer, audience list, JWKS URI, Web client registration/secret, public
   HTTPS Web origin, API invoker members, and Web invoker members. **Required
   only when `auth_mode = "oidc"`**; the default `"local"` mode deploys with
   password-only authentication and no OIDC dependency.
4. External provider live endpoints, credentials, and Cloud NAT egress IPs are
   disabled in consumer-only platform snapshot deployment. Direct external
   provider acquisition is off.
5. Model registry credentials (if MLflow does not use workload identity) and OIDC
   client secret.
6. Remote MLflow URI, production aliases/artifacts, AVM liquidity approval
   metadata, digest, dataset snapshot, and OSS forecast engine/model.
7. Optional MLflow credential secret IDs when the registry does not use
   workload identity.
8. A migration/reconciliation release job and canonical live datasets that make
   `/readiness` return HTTP 200 before Cloud Run startup expires.
9. DNS, managed HTTPS, and Cloud Armor policy when a custom Web origin is used.
   The module deploys the Web BFF while leaving the API at
   internal/load-balancer ingress.

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
5. Verify `/healthz`, `/readiness`, exact `release_sha`, OIDC rejection/acceptance,
   model lineage, Pub/Sub/DLQ, backup/PITR, and restore drill
   evidence before routing user traffic.

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
