# Fleet Execution Brief: ODP-MAP-E2E-001

- Parent: ODP-PV-LIVE-MAP-001
- Status: open
- Scope boundary: maps
- Owner lane: maps / frontend infrastructure
- Reviewer lane: frontend accessibility / product validation
- Suggested branch: `task/ODP-MAP-E2E-001-live-tile-geocoder`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Live tile/geocoder boundary gate

## Current Proof Boundary

- Current proof: deterministic local MapLibre/deck/H3 E2E and canvas proof
- Live claim requires:
- live tile and geocoder boundary gate
- layer toggle URL persistence
- direct map picking
- semantic deck pixel checks
- keyboard accessibility
- map resilience states
- tooltip and evidence detail

## Implementation Evidence Required

- MAP_TILE_URL or equivalent
- geocoder configuration
- source attribution
- terms display
- list fallback

## Verification Evidence Required

- staging tile/geocoder smoke
- tile outage E2E
- geocoder outage E2E
- list/ranking/detail still usable

## Execution Commands

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

```bash
npx playwright test tests/e2e/e2e-map-live-boundary.spec.ts --project=chromium --retries=1
```

## Blocking Dependencies

- Playwright Chromium dependencies are installed for local or CI execution
- Live tile/geocoder credentials or approved mock endpoints are supplied through environment/query configuration
- Remote-staging live map proof remains separate from deterministic local map proof

## Acceptance Criteria

- runtime displays attribution and terms
- tile/geocoder outage does not block list workflow
- staging smoke proves configured endpoints

## Handoff Artifacts

- map endpoint config diff
- outage E2E output
- staging smoke evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
