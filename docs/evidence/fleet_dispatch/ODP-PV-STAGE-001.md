# Fleet Execution Brief: ODP-PV-STAGE-001

- Parent: ODP-PV-STAGE-001
- Status: open
- Scope boundary: remote_staging
- Owner lane: platform / deployment
- Reviewer lane: operations / product validation
- Suggested branch: `task/ODP-PV-STAGE-001-remote-staging-config`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Remote staging configuration

## Current Proof Boundary

- Current proof: deterministic deploy, health, backup, restore, rollback evidence
- Live claim requires:
- remote staging host/url/secret configuration
- remote staging drill
- version proof matching PR #82 headRefOid

## Implementation Evidence Required

- remote staging host/url/secret configuration
- documented environment variables
- secret owner
- health endpoint
- version endpoint

## Verification Evidence Required

- staging smoke check
- version matches PR #82 headRefOid

## Execution Commands

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

```bash
export ODP_STAGING_DEPLOY_URL="https://<staging-web-host>"
export ODP_STAGING_API_URL="https://<staging-api-host>"
export ODP_STAGING_SECRET_OWNER="<team-or-person>"
python3 scripts/e2e/check_remote_staging_proof.py \
  --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" \
  --correlation-id "corr-odp-pv-stage-001"
```

## Blocking Dependencies

- GitHub staging environment variables/secrets are configured outside the repository
- The deployed API receives ODAY_RELEASE_SHA equal to PR #82 headRefOid or the promoted SHA
- .github/workflows/deploy-staging.yml is replaced or supplemented by a real staging deployment path

## Acceptance Criteria

- staging host and secrets are documented
- health endpoint is reachable
- version endpoint matches PR #82 headRefOid

## Handoff Artifacts

- staging env inventory
- health/version smoke output
- secret owner record

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
