# Fleet Execution Brief: ODP-EXT-004

- Parent: ODP-PV-LIVE-SRC-002
- Status: open
- Scope boundary: external_data_sources
- Owner lane: data platform / source ingestion
- Reviewer lane: product validation / governance
- Suggested branch: `task/ODP-EXT-004-scheduled-fetch`
- Release authority: PR #82 headRefOid and attached checks

## Objective

Scheduled external fetch worker

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

- scheduler/job definition
- last-success watermark
- backfill command
- durable source snapshot ids

## Verification Evidence Required

- idempotent replay test
- stale-source clock E2E
- STALE or BLOCKED UI/data status

## Execution Commands

```bash
gh pr view 82 --json headRefOid,isDraft,state,mergeable,statusCheckRollup,url
```

```bash
python3 scripts/external_data_backfill.py --provider-id listing.partner_feed --start 2026-06-28T10:00:00Z --end 2026-06-28T12:00:00Z --interval-hours 1
```

```bash
uv run pytest tests/e2e/test_external_source_product_e2e.py -k "live_provider_mode_product_e2e" -q
```

## Blocking Dependencies

- Provider secrets and live credentials are supplied by environment or approved mock service, never committed
- Deterministic fixture/source-stub mode remains the CI default when live credentials are absent
- Release evidence distinguishes provider-specific production proof from deterministic or mock-live proof

## Acceptance Criteria

- scheduled fetch creates durable batch ids
- backfill is idempotent
- stale sources are visible as STALE or BLOCKED

## Handoff Artifacts

- scheduler definition
- backfill command output
- freshness E2E evidence

## Completion Rules

- document-only PRs must not close these tasks
- deterministic fixture/source-stub tests must remain CI defaults
- live-provider proof must be reported separately from deterministic PR #82 proof
- live-map proof must be reported separately from deterministic PR #82 proof
- remote-staging proof must be reported separately from deterministic PR #82 proof
- provider secrets must never be committed
- release evidence must use PR #82 headRefOid, not a hardcoded dev hash
