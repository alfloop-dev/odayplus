# Fleet Execution Brief: ODP-EXT-005

- Parent: ODP-PV-LIVE-SRC-003
- Status: open
- Scope boundary: external_data_sources
- Owner lane: data platform / source ingestion
- Reviewer lane: product validation / governance
- Suggested branch: `task/ODP-EXT-005-quota-rate-limit`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Quota and rate-limit resilience

## Current Proof Boundary

- Current proof: deterministic fixtures, source-stub, connector contracts
- Live claim requires:
- provider registry
- credential or OAuth wiring
- live adapters
- scheduled fetch
- quota and rate-limit handling
- freshness and data-quality gate
- licensing and allowed-use gate
- product E2E in live-provider mode

## Implementation Evidence Required

- 401/403/429/5xx/timeout handling
- backoff policy
- circuit breaker
- alert/audit event

## Verification Evidence Required

- quota exhaustion simulation
- rate-limit simulation
- clear stale/blocked degraded state

## Acceptance Criteria

- provider failures do not fabricate freshness
- quota exhaustion emits alert/audit evidence
- degraded mode is explicit

## Handoff Artifacts

- failure simulation output
- circuit breaker evidence
- alert/audit event sample

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
