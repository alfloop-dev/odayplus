# Fleet Execution Brief: ODP-EXT-001

- Parent: ODP-PV-LIVE-SRC-001
- Status: open
- Scope boundary: external_data_sources
- Owner lane: integration / source ingestion
- Reviewer lane: governance / product validation
- Suggested branch: `task/ODP-EXT-001-provider-registry-secrets`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Provider registry and secrets for listing, POI, geocode, admin boundary, and competitor/manual sources

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

- secret names
- auth modes
- startup validation
- provider class metadata
- no committed secrets

## Verification Evidence Required

- missing credentials fail closed
- expired or unauthorized credentials include correlation id

## Acceptance Criteria

- provider classes are registered
- startup validation fails closed without secrets
- release evidence contains no secret values

## Handoff Artifacts

- provider registry diff
- secret inventory with names only
- startup validation test output

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
