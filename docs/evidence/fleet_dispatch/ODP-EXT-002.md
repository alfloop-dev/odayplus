# Fleet Execution Brief: ODP-EXT-002

- Parent: ODP-PV-LIVE-SRC-001
- Status: open
- Scope boundary: external_data_sources
- Owner lane: integration / source ingestion
- Reviewer lane: governance / product validation
- Suggested branch: `task/ODP-EXT-002-live-listing-feed`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Live listing feed adapter

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

- authenticated provider client
- raw landing snapshot
- idempotency key
- canonical transform
- quarantine path

## Verification Evidence Required

- success contract test
- duplicate contract test
- malformed payload contract test
- unauthorized contract test
- timeout contract test
- fixture-compatible replay

## Acceptance Criteria

- listing adapter persists raw and canonical snapshots
- bad records enter quarantine
- fixture replay remains CI default

## Handoff Artifacts

- adapter contract test output
- source snapshot sample
- quarantine event evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
