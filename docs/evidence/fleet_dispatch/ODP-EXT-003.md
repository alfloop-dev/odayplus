# Fleet Execution Brief: ODP-EXT-003

- Parent: ODP-PV-LIVE-SRC-001
- Status: open
- Scope boundary: external_data_sources
- Owner lane: integration / source ingestion
- Reviewer lane: governance / product validation
- Suggested branch: `task/ODP-EXT-003-live-geocoder`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Live geocoder adapter

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

- credential handling
- geocode confidence mapping
- provider request id
- provider observed time
- retry budget

## Verification Evidence Required

- recorded response success test
- low confidence test
- rate-limit retry test
- timeout test
- unauthorized test

## Acceptance Criteria

- geocoder outputs confidence and lineage
- rate limits use retry budget
- unauthorized mode fails closed

## Handoff Artifacts

- recorded-response fixtures
- geocode contract test output
- confidence mapping evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
