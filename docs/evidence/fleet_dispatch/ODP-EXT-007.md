# Fleet Execution Brief: ODP-EXT-007

- Parent: ODP-PV-LIVE-SRC-003
- Status: open
- Scope boundary: external_data_sources
- Owner lane: data platform / source ingestion
- Reviewer lane: product validation / governance
- Suggested branch: `task/ODP-EXT-007-licensing-gate`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Licensing and allowed-use gate

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

- license metadata
- attribution
- expiry
- downstream-use flags
- export restrictions

## Verification Evidence Required

- license-blocked provider quarantine
- production mode cannot use blocked provider

## Acceptance Criteria

- license metadata controls production use
- blocked providers enter quarantine
- exports respect downstream-use flags

## Handoff Artifacts

- license registry diff
- blocked-provider test output
- export restriction evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
