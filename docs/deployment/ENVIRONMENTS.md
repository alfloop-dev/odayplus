# Deployment Environments

Source baseline: `ODP-SD-12_CICD_IAC_AND_ENVIRONMENT_DESIGN`,
`ODP-OPS-02_DEPLOYMENT_AND_ENVIRONMENT_MANAGEMENT`,
`ODP-OPS-04_RUNBOOK`.

| Environment | Purpose | Data | Promotion rule |
|---|---|---|---|
| `local` | Developer compose stack and smoke checks | Synthetic/local only | No promotion. |
| `dev` | Integration baseline and migration rehearsal | Non-production snapshots | Merge to `dev` and deploy immutable image. |
| `staging` | Release candidate validation | Production-like masked data | All release gates passed or documented deviation. |
| `prod` | Production serving and governed jobs | Production | Approved release, backup checkpoint, rollback owner. |

Required environment variables:

| Variable | Required in | Purpose |
|---|---|---|
| `ODAY_ENV` | all | Runtime environment label. |
| `ODAY_DATABASE_URL` | API, worker, migration | PostgreSQL connection string from secret manager. |
| `ODAY_LOG_FORMAT` | all | Use `json` for shared structured logging. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | deployed envs | Trace export endpoint. |

Secrets must be injected by the deployment platform. They are never committed to
Terraform variable files, Docker compose, or CLI plan outputs.
