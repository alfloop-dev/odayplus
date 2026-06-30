# Fleet Execution Brief: ODP-EXT-008

- Parent: ODP-PV-LIVE-SRC-001
- Status: open
- Scope boundary: external_data_sources
- Owner lane: data platform / source ingestion
- Reviewer lane: product validation / governance
- Suggested branch: `task/ODP-EXT-008-external-source-product-e2e`
- Release authority: PR #82 headRefOid and attached checks

## Objective

External source product E2E

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

- provider-mock service
- auth scenario
- quota scenario
- freshness scenario
- license scenario
- persisted lineage

## Verification Evidence Required

- live-provider mode product E2E
- CI fixture mode remains default

## Execution Commands

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py -q
```

## Blocking Dependencies

- Provider secrets and live credentials are supplied by environment or approved mock service, never committed
- Deterministic fixture/source-stub mode remains the CI default when live credentials are absent
- Release evidence distinguishes provider-specific production proof from deterministic or mock-live proof

## Acceptance Criteria

- provider mock covers auth quota freshness and license scenarios
- live-provider mode has product E2E proof
- default CI still uses deterministic fixtures

## Handoff Artifacts

- provider mock service evidence
- product E2E output
- CI fixture mode proof

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
