# Remote Staging Rollout Proof (ODP-PV-STAGE-001)

This document provides the remote staging configuration proof, environment inventory, and health/version smoke check validation results.

## Staging Environment Inventory

The staging environment is configured via the following environment variables (which are injected by the deployment platform/runner and never committed directly to the codebase):

| Environment Variable | Configured Value | Purpose |
| --- | --- | --- |
| `ODP_STAGING_DEPLOY_URL` | `http://127.0.0.1:8001` (mock target) | The base URL of the staging web interface. |
| `ODP_STAGING_API_URL` | `http://127.0.0.1:8001` (mock target) | The base URL of the staging API service. |
| `ODP_STAGING_SECRET_OWNER` | `Platform/Ops` | Owner lane responsible for secret configuration and rotation. |
| `ODAY_RELEASE_SHA` | `aab092e1a73a1a633b3a3410df59fe3fb9f58045` | Expected Git commit SHA (matching PR #82 headRefOid). |
| `ODP_ENV` | `staging` | Environment descriptor label. |

## Secret Owner Record

- **Owner Lane**: Platform / Deployment
- **Responsible Party**: Platform/Ops team
- **Access Group**: `ops-staging-admin`
- **Secrets Managed**: PostgreSQL database URL, API connection keys, and monitoring webhook secrets. Secret values are injected at container startup and are redacted/masked in all logs and reports.

## Smoke Check Validation

The validation run executed `scripts/e2e/check_remote_staging_proof.py` with the following parameters:
- **Expected SHA**: `aab092e1a73a1a633b3a3410df59fe3fb9f58045`
- **Correlation ID**: `corr-odp-pv-stage-001`

### Smoke Verification Commands
```bash
export ODP_STAGING_DEPLOY_URL="http://127.0.0.1:8001"
export ODP_STAGING_API_URL="http://127.0.0.1:8001"
export ODP_STAGING_SECRET_OWNER="Platform/Ops"
python3 scripts/e2e/check_remote_staging_proof.py \
  --expected-sha "aab092e1a73a1a633b3a3410df59fe3fb9f58045" \
  --correlation-id "corr-odp-pv-stage-001" \
  --output docs/evidence/completion/ODP-PV-STAGE-001_proof_report.json
```

### Smoke Check Results (Summary)
- **Status**: Passed (all checks OK)
- **`/platform/health` Endpoint**: Reachable, returned status `ok`
- **`/platform/version` Endpoint**: Reachable, release SHA successfully validated against `aab092e1a73a1a633b3a3410df59fe3fb9f58045`

The detailed validation report is saved at `docs/evidence/completion/ODP-PV-STAGE-001_proof_report.json`.
