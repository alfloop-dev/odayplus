# Fleet Execution Brief: ODP-PV-STAGE-002

- Parent: ODP-PV-STAGE-002
- Status: open
- Scope boundary: remote_staging
- Owner lane: platform / deployment
- Reviewer lane: operations / product validation
- Suggested branch: `task/ODP-PV-STAGE-002-remote-staging-drill`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Remote staging drill

## Current Proof Boundary

- Current proof: deterministic deploy, health, backup, restore, rollback evidence
- Live claim requires:
- remote staging host/url/secret configuration
- remote staging drill
- version proof matching PR #82 headRefOid

## Implementation Evidence Required

- staging runbook log
- backup/restore evidence
- rollback result
- correlation id

## Verification Evidence Required

- product E2E smoke against staging
- backup/restore/rollback command against staging or approved equivalent

## Execution Commands

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

```bash
PLAYWRIGHT_BASE_URL="$ODP_STAGING_DEPLOY_URL" \
ODP_API_BASE_URL="$ODP_STAGING_API_URL" \
npx playwright test tests/e2e/product-e2e-env.spec.ts --project=chromium --timeout=90000
```

```bash
python3 scripts/e2e/verify_deployment_health_backup_rollback.py \
  --correlation-id "corr-odp-pv-stage-002"
```

```bash
python3 scripts/e2e/check_remote_staging_proof.py \
  --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" \
  --correlation-id "corr-odp-pv-stage-002-version"
```

## Blocking Dependencies

- ODP-PV-STAGE-001 has passed against the same staging target
- Staging product URL, API URL, and backing store credentials are available without committing secrets
- Backup/restore/rollback commands target staging or an approved staging-equivalent environment

## Acceptance Criteria

- staging runbook has timestamped execution log
- backup restore and rollback evidence is linked
- product E2E smoke runs against staging URL

## Handoff Artifacts

- staging runbook log
- backup/restore/rollback output
- staging E2E smoke evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
