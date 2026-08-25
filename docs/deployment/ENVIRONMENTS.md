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

The target lifecycle, release gates, ephemeral staging isolation, production
blue-green rollout, and Supervisor/Auto Worker task DAG are defined in
[`EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md`](EPHEMERAL_STAGING_PRODUCTION_ROLLOUT_PLAN.md).

## Unified Single-Path Runtime Release Pipeline

The system uses a single CI/CD release workflow entrypoint (`.github/workflows/deploy-dev.yml`, named `Runtime Release`) to orchestrate releases across all environments:

1. **Admission Gate**: Authoritative verifier (`delivery_toolchain/release/check_runtime_admission.py`) checks the Ed25519-signed Supervisor release lease and staged gate registry (`RELEASE_GATE_REGISTRY.json`) for the requested environment (`dev`, `staging`, `production`).
2. **Build Once**: A dedicated `build` job executes once per release candidate SHA, running secret scanning, SAST (Bandit), SBOM generation, and container image builds/Cosign signing. The output immutable digests are shared across all deployment targets.
3. **Deploy by Digest**:
   - **`dev`**: Deploys immutable digests, executes migrations, runs live preflight, Cloud Run Job validations, and live E2E gate.
   - **`staging`**: Provisions short-lived ephemeral staging instance with isolated database schema, tenant partitioning, and masked snapshot; verifies migration compatibility and remote staging proof; cleans up on release completion or holds up to 24h for debugging on failure.
   - **`production`**: Deploys green revisions (0% public traffic), validates green smoke and IAM bindings, atomistically promotes traffic to green (100%), updates Cloud Scheduler targets to green digests, and arms fail-closed rollback primitives.

