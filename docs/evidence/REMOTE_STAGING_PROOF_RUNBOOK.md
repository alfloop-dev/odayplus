# Remote Staging Proof Runbook

Generated: 2026-06-29  
Scope: `ODP-PV-STAGE-001`, `ODP-PV-STAGE-002`  
Release authority: PR #82 `headRefOid` and attached checks.

## Purpose

This runbook is the execution handoff for the remote staging proof. It is not a
claim that staging is complete. It defines the configuration, commands, and
evidence required before any operator or fleet can close the staging proof
tasks.

## Current State

The GitHub `staging` environment exists, but repository variables and secrets
are not configured in the current external state. The workflow
`.github/workflows/deploy-staging.yml` is still a placeholder that echoes a
TODO instead of deploying a remote host.

Therefore:

- `ODP-PV-STAGE-001` cannot be closed until staging URL/host/secret ownership is configured.
- `ODP-PV-STAGE-002` cannot be closed until a real staging smoke and drill report is captured.
- Document-only PRs must not close either task.

## Required GitHub Environment Configuration

Configure these under the GitHub `staging` environment or repository settings.
Do not commit secret values.

| Name | Type | Owner | Purpose |
|---|---|---|---|
| `ODP_STAGING_DEPLOY_URL` | variable | Platform/Ops | Public staging web URL displayed by GitHub deployments. |
| `ODP_STAGING_API_URL` | variable | Platform/Ops | Base API URL used by smoke checks. |
| `ODP_STAGING_HOST` | variable | Platform/Ops | Remote host or orchestrator target for deployment. |
| `ODP_STAGING_SECRET_OWNER` | variable | Platform/Ops | Human/team accountable for secret rotation and access review. |
| `ODP_STAGING_DEPLOY_USER` | secret | Platform/Ops | Remote deploy user, if SSH/host deployment is used. |
| `ODP_STAGING_SSH_PRIVATE_KEY` | secret | Platform/Ops | Remote deployment key, if SSH/host deployment is used. |
| `ODP_STAGING_DATABASE_URL` | secret | Platform/Ops | Staging database connection string. |

The deployed API container must receive:

```text
ODAY_RELEASE_SHA=<PR #82 headRefOid or promoted main commit SHA>
```

The API exposes this at:

```text
GET /platform/version
```

The response field `release_sha` must match the release authority SHA.

## ODP-PV-STAGE-001 Execution

1. Verify PR #82 authority.

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

2. Export staging configuration without printing secrets.

```bash
export ODP_STAGING_DEPLOY_URL="https://<staging-web-host>"
export ODP_STAGING_API_URL="https://<staging-api-host>"
export ODP_STAGING_SECRET_OWNER="<team-or-person>"
```

3. Run remote staging config and smoke proof.

```bash
python3 scripts/e2e/check_remote_staging_proof.py \
  --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" \
  --correlation-id "corr-odp-pv-stage-001"
```

Expected output:

- `env:ODP_STAGING_DEPLOY_URL` is configured.
- `env:ODP_STAGING_API_URL` is configured.
- `env:ODP_STAGING_SECRET_OWNER` is configured.
- `/platform/health` returns `status=ok`.
- `/platform/version.release_sha` equals PR #82 `headRefOid`.

The report is written to:

```text
.odp_data/remote-staging-proof/remote-staging-proof-report.json
```

Attach that report path or its redacted contents to the task evidence.

## ODP-PV-STAGE-002 Execution

Run the staging drill only after ODP-PV-STAGE-001 passes.

1. Run product smoke against staging.

```bash
PLAYWRIGHT_BASE_URL="$ODP_STAGING_DEPLOY_URL" \
ODP_API_BASE_URL="$ODP_STAGING_API_URL" \
npx playwright test tests/e2e/product-e2e-env.spec.ts --project=chromium --timeout=90000
```

2. Run backup/restore/rollback against staging or the approved equivalent
orchestrator command. The command must record a correlation id and must not
reuse local-only evidence.

```bash
python3 scripts/e2e/verify_deployment_health_backup_rollback.py \
  --correlation-id "corr-odp-pv-stage-002"
```

If the staging deployment uses a managed database or orchestrator rather than
the local compose topology, attach the equivalent platform-native output with:

- backup artifact id and timestamp;
- restore target and timestamp;
- rollback command/result;
- correlation id;
- health/version smoke after restore/rollback.

3. Re-run the version proof.

```bash
python3 scripts/e2e/check_remote_staging_proof.py \
  --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" \
  --correlation-id "corr-odp-pv-stage-002-version"
```

## Closeout Criteria

`ODP-PV-STAGE-001` is complete only when:

- staging host/url/secret owner are configured;
- `/platform/health` is reachable;
- `/platform/version.release_sha` matches PR #82 `headRefOid`;
- the redacted proof report is attached.

`ODP-PV-STAGE-002` is complete only when:

- staging product smoke runs against the staging URL;
- backup/restore/rollback evidence is captured for staging or an approved
  equivalent;
- the drill output includes a correlation id;
- post-drill health/version proof still matches PR #82 `headRefOid`.
