# Fleet Execution Brief: ODP-MAP-E2E-003

- Parent: ODP-PV-LIVE-MAP-003
- Status: open
- Scope boundary: maps
- Owner lane: maps / frontend infrastructure
- Reviewer lane: frontend accessibility / product validation
- Suggested branch: `task/ODP-MAP-E2E-003-direct-map-picking`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Direct map picking

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

- H3 pick handler
- listing pick handler
- candidate pick handler
- shared drawer state with list selection

## Verification Evidence Required

- direct map pick E2E
- drawer identity proof
- selected state proof
- list fallback alignment

## Execution Commands

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

```bash
npx playwright test tests/e2e/e2e-map.spec.ts --project=chromium -g "direct picking" --retries=1
```

## Blocking Dependencies

- Playwright Chromium dependencies are installed for local or CI execution
- Live tile/geocoder credentials or approved mock endpoints are supplied through environment/query configuration
- Remote-staging live map proof remains separate from deterministic local map proof

## Acceptance Criteria

- map pick and list click open identical drawer state
- selected map state is visible
- list fallback remains authoritative

## Handoff Artifacts

- direct pick E2E output
- drawer identity evidence
- list fallback comparison

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
