# Fleet Execution Brief: ODP-MAP-A11Y-001

- Parent: ODP-PV-LIVE-MAP-002
- Status: open
- Scope boundary: maps
- Owner lane: frontend accessibility / maps
- Reviewer lane: product validation
- Suggested branch: `task/ODP-MAP-A11Y-001-keyboard-map`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Keyboard map/list/drawer accessibility

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

- keyboard layer controls
- list fallback selection
- drawer open/close
- focus return
- focus-visible styling

## Verification Evidence Required

- Tab/Enter/Escape E2E
- axe scan for HeatZone map route

## Acceptance Criteria

- HeatZone selection can be completed without pointer input
- focus returns after drawer close
- axe scan passes for map route

## Handoff Artifacts

- keyboard-only E2E output
- axe report
- focus-order evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
