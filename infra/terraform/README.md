# Terraform

Terraform modules and environment composition for the first GCP deployment
baseline.

## Managed Baseline

| Resource | Role |
|---|---|
| Cloud Run | API runtime container. |
| Cloud SQL PostgreSQL 16 | Transactional canonical store; PostGIS extension is created by migration. |
| Cloud Storage | Snapshots, evidence packages, model artifacts, and release artifacts. |
| Pub/Sub | Async job topic and dead-letter topic. |
| Service Account | Runtime identity for deployable services. |

## Usage

```bash
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform plan -var-file=env/dev.tfvars.example
```

The `.tfvars.example` files are templates only. Real project ids, image digests,
IAM bindings, and secrets are environment-owned release inputs.
