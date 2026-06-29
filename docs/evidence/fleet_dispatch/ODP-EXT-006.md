# Fleet Execution Brief: ODP-EXT-006

- Parent: ODP-PV-LIVE-SRC-002
- Status: open
- Scope boundary: external_data_sources
- Owner lane: data platform / source ingestion
- Reviewer lane: product validation / governance
- Suggested branch: `task/ODP-EXT-006-freshness-quality-gate`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Freshness and data-quality gate

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

- per-source freshness SLA
- provider observed time
- ingestion time
- source snapshot id
- correlation id

## Verification Evidence Required

- API freshness state E2E
- UI freshness state E2E
- source lineage appears in evidence

## Acceptance Criteria

- freshness SLA is source-specific
- API and UI expose the same freshness status
- lineage contains correlation id

## Handoff Artifacts

- freshness policy diff
- API/UI E2E output
- lineage evidence sample

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
