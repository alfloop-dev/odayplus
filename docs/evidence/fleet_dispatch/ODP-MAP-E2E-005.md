# Fleet Execution Brief: ODP-MAP-E2E-005

- Parent: ODP-PV-LIVE-MAP-002
- Status: open
- Scope boundary: maps
- Owner lane: frontend accessibility / maps
- Reviewer lane: product validation
- Suggested branch: `task/ODP-MAP-E2E-005-map-resilience`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Map state resilience

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

- loading state
- empty state
- error with correlation id
- partial failed layer
- no-geometry fallback

## Verification Evidence Required

- map failure E2E
- ranking/list/detail workflow remains usable

## Acceptance Criteria

- map loading empty and error states are explicit
- correlation id is visible on errors
- list/detail workflow survives map failure

## Handoff Artifacts

- state fixture screenshots
- failure E2E output
- correlation id evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
