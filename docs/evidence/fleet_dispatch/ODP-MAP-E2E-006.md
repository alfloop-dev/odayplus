# Fleet Execution Brief: ODP-MAP-E2E-006

- Parent: ODP-PV-LIVE-MAP-003
- Status: open
- Scope boundary: maps
- Owner lane: frontend accessibility / maps
- Reviewer lane: product validation
- Suggested branch: `task/ODP-MAP-E2E-006-tooltip-evidence`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Tooltip and evidence detail

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

- hover tooltip
- keyboard reachable tooltip
- score/state/confidence/warnings/freshness/model version fields

## Verification Evidence Required

- tooltip/evidence E2E
- accessible fallback text

## Acceptance Criteria

- tooltip includes score state confidence warnings freshness and version
- tooltip is keyboard reachable
- fallback text exposes same evidence

## Handoff Artifacts

- tooltip E2E output
- keyboard tooltip proof
- fallback text evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
