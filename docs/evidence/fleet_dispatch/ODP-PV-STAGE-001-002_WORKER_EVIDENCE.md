# ODP-PV-STAGE-001/002 Worker Evidence

Generated: 2026-06-29T17:00:26Z  
Worker lane: Remote staging rollout  
Authority worktree: `/home/lupin/odayplus-dev`  
Release authority: PR #82 `headRefOid`

## Scope

This evidence covers the remote staging rollout lane only:

- `ODP-PV-STAGE-001` remote staging configuration proof.
- `ODP-PV-STAGE-002` remote staging drill proof.

It does not modify external data source implementation, map implementation, or generated fleet briefs.

## Preflight

Passed in `/home/lupin/odayplus-dev`:

```bash
pwd
test -f docs/evidence/fleet_dispatch/ODP-PV-STAGE-001.md
test -f docs/evidence/fleet_dispatch/ODP-PV-STAGE-002.md
test -f scripts/e2e/check_remote_staging_proof.py
```

Output:

```text
/home/lupin/odayplus-dev
```

## Task Brief Inputs Reviewed

Read:

- `docs/evidence/fleet_dispatch/ODP-PV-STAGE-001.md`
- `docs/evidence/fleet_dispatch/ODP-PV-STAGE-002.md`
- `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md`

Required execution boundary:

- `ODP-PV-STAGE-001` requires staging deploy URL, staging API URL, staging secret owner, reachable `/platform/health`, and `/platform/version.release_sha` matching PR #82 `headRefOid`.
- `ODP-PV-STAGE-002` may run only after `ODP-PV-STAGE-001` passes against the same staging target.
- Document-only evidence must not close either task.

## GitHub Environment Inventory

Repository:

```text
alfloop-dev/odayplus
https://github.com/alfloop-dev/odayplus
```

PR #82 authority check:

```text
original handback headRefOid: 1494e51f7c90a35abbbc1b9feec6bb2dbb8d5633
state: OPEN
draft: true
mergeable: MERGEABLE
checks: ci/product-e2e-gate/e2e-operational-evidence/build-and-publish/deploy all SUCCESS at query time
```

Authority refresh procedure:

```text
command: gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
rule: use the returned headRefOid and attached checks as the current release authority
note: evidence-only merges intentionally move PR #82 headRefOid; historical sample SHAs are not current authority
```

GitHub environments:

```text
dev: exists
staging: exists
```

Repository-level variables:

```text
none returned by gh variable list --repo alfloop-dev/odayplus
```

Repository-level secrets:

```text
none returned by gh secret list --repo alfloop-dev/odayplus
```

Staging environment variables:

```text
total_count: 0
names: []
```

Staging environment secrets:

```text
total_count: 0
names: []
```

Staging deploy workflow status at original handback time:

- `.github/workflows/deploy-staging.yml` exists.
- At original handback time, the workflow contained a TODO placeholder and did
  not deploy a real staging host.
- It references `${{ vars.ODP_STAGING_DEPLOY_URL }}`, but that variable is not configured.

Current workflow guard:

- `.github/workflows/deploy-staging.yml` is now a fail-closed
  Deploy/Verify Staging workflow.
- It resolves `ODAY_RELEASE_SHA`, runs
  `scripts/e2e/check_remote_staging_proof.py`, and uploads a redacted proof
  report artifact.
- It still requires external staging variables/secrets and a real staging
  target; it does not itself create the missing host/API URL/database.

Secret values were not printed or accessed.

## Fail-Closed Remote Staging Checker

Command run:

```bash
EXPECTED_SHA="$(gh pr view 82 --json headRefOid --jq .headRefOid)"
python3 scripts/e2e/check_remote_staging_proof.py \
  --expected-sha "$EXPECTED_SHA" \
  --correlation-id "corr-odp-pv-stage-001" \
  --output .odp_data/remote-staging-proof/odp-pv-stage-001-missing-env-report.json
```

Result:

```text
checker_exit=1
```

Checker output:

```text
Remote staging proof checks failed:
- env:ODP_STAGING_DEPLOY_URL: missing
- env:ODP_STAGING_API_URL: missing
- env:ODP_STAGING_SECRET_OWNER: missing
report=.odp_data/remote-staging-proof/odp-pv-stage-001-missing-env-report.json
```

Saved report:

```text
.odp_data/remote-staging-proof/odp-pv-stage-001-missing-env-report.json
```

Report summary:

```text
ok: false
expected_sha: sample PR #82 headRefOid captured before PR #128 merged
correlation_id: corr-odp-pv-stage-001
missing:
- env:ODP_STAGING_DEPLOY_URL
- env:ODP_STAGING_API_URL
- env:ODP_STAGING_SECRET_OWNER
secret_values_redacted: true
```

## ODP-PV-STAGE-001 Status

Not complete.

Evidence proves the checker fails closed when staging configuration is absent. It does not prove remote staging readiness.

Missing external state:

- `ODP_STAGING_DEPLOY_URL`
- `ODP_STAGING_API_URL`
- `ODP_STAGING_SECRET_OWNER`
- Real staging host/deployment target.
- Deployed API configured with `ODAY_RELEASE_SHA` equal to the current PR #82 `headRefOid` or the promoted SHA.
- Reachable remote `/platform/health`.
- Reachable remote `/platform/version` matching the release authority SHA.
- Real staging deployment target and backing services, or an approved
  platform-native deployment path that feeds the fail-closed verifier.

## ODP-PV-STAGE-002 Status

Not run and not complete.

Reason:

- `ODP-PV-STAGE-002` depends on `ODP-PV-STAGE-001` passing against the same staging target.
- Because staging URL/API URL/secret owner are missing, running staging Playwright smoke would only test an unset target and would not produce valid product-grade staging evidence.

Missing external state:

- Product smoke against `ODP_STAGING_DEPLOY_URL`.
- API smoke against `ODP_STAGING_API_URL`.
- Backup/restore/rollback drill against staging or approved staging-equivalent infrastructure.
- Post-drill health/version proof with correlation id.

## Commands Run

```bash
pwd && test -f docs/evidence/fleet_dispatch/ODP-PV-STAGE-001.md && test -f docs/evidence/fleet_dispatch/ODP-PV-STAGE-002.md && test -f scripts/e2e/check_remote_staging_proof.py
```

```bash
sed -n '1,240p' docs/evidence/fleet_dispatch/ODP-PV-STAGE-001.md
sed -n '1,260p' docs/evidence/fleet_dispatch/ODP-PV-STAGE-002.md
sed -n '1,260p' docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md
```

```bash
gh repo view --json nameWithOwner,url
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
gh api repos/alfloop-dev/odayplus/environments --jq '.environments[] | {name: .name, protection_rules: (.protection_rules|length), deployment_branch_policy: .deployment_branch_policy}'
gh variable list --repo alfloop-dev/odayplus
gh secret list --repo alfloop-dev/odayplus
gh api repos/alfloop-dev/odayplus/environments/staging/variables --jq '{total_count, names: [.variables[].name]}'
gh api repos/alfloop-dev/odayplus/environments/staging/secrets --jq '{total_count, names: [.secrets[].name]}'
sed -n '1,220p' .github/workflows/deploy-staging.yml
```

```bash
EXPECTED_SHA="$(gh pr view 82 --json headRefOid --jq .headRefOid)"
python3 scripts/e2e/check_remote_staging_proof.py \
  --expected-sha "$EXPECTED_SHA" \
  --correlation-id "corr-odp-pv-stage-001" \
  --output .odp_data/remote-staging-proof/odp-pv-stage-001-missing-env-report.json
```

## Completion Rule

Do not close `ODP-PV-STAGE-001` or `ODP-PV-STAGE-002` from this evidence alone. The current result is an external-state blocker report, not a completed remote staging rollout.
