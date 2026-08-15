# Release Baseline

Source baseline: `ODP-SD-12_CICD_IAC_AND_ENVIRONMENT_DESIGN`,
`ODP-QA-01_TEST_MASTER_PLAN`, `ODP-QA-07_SUBSIDY_AUDIT_EVIDENCE_MATRIX`.

The R7 deployment baseline is intentionally small but auditable:

| Layer | Artifact |
|---|---|
| Runtime image | `infra/docker/Dockerfile.api` |
| Local deploy | `infra/docker/docker-compose.yml` |
| Cloud baseline | `infra/terraform/*.tf` and `infra/terraform/env/*.tfvars.example` |
| Migration evidence | `python -m apps.cli.oday_cli migration-plan` output |
| Backfill evidence | `python -m apps.cli.oday_cli backfill-plan` output |
| Ops docs | `docs/deployment/ENVIRONMENTS.md` and `MIGRATION_BACKFILL_RUNBOOK.md` |

Release gate impact:

| Gate | R7 baseline status |
|---|---|
| Code | Focused CLI/ops tests in `tests/ops/test_migration_backfill.py`. |
| Data | Migration and backfill plan capture PIT, quality, count, and quarantine checks. |
| Security | Secrets are environment-injected; example tfvars do not carry secret values. |
| Ops | Deployment, migration, rollback, and backfill steps documented here. |
| Audit | CLI plan JSON can be attached to release evidence and audit packages. |
