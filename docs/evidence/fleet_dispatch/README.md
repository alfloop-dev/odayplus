# Product-Grade E2E Fleet Dispatch Report

- PR: #82
- Authority: GitHub PR #82 headRefOid and attached checks
- Status: open_execution_dispatch
- Updated: 2026-06-29

## Dispatch Lanes

| Lane | Owner Lane | Reviewer Lane | Task Count | Aliases |
|---|---|---|---:|---|
| External provider foundation | integration / source ingestion | governance / product validation | 3 | ODP-EXT-001, ODP-EXT-002, ODP-EXT-003 |
| External source operations | data platform / source ingestion | product validation / governance | 5 | ODP-EXT-004, ODP-EXT-005, ODP-EXT-006, ODP-EXT-007, ODP-EXT-008 |
| Live map provider gate | maps / frontend infrastructure | frontend accessibility / product validation | 4 | ODP-MAP-E2E-001, ODP-MAP-E2E-002, ODP-MAP-E2E-003, ODP-MAP-E2E-004 |
| Map accessibility and resilience | frontend accessibility / maps | product validation | 3 | ODP-MAP-A11Y-001, ODP-MAP-E2E-005, ODP-MAP-E2E-006 |
| Remote staging rollout | platform / deployment | operations / product validation | 2 | ODP-PV-STAGE-001, ODP-PV-STAGE-002 |

## Scope Boundaries

### external_data_sources

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

### maps

- Current proof: deterministic local MapLibre/deck/H3 E2E and canvas proof
- Live claim requires:
- live tile and geocoder boundary gate
- layer toggle URL persistence
- direct map picking
- semantic deck pixel checks
- keyboard accessibility
- map resilience states
- tooltip and evidence detail

### remote_staging

- Current proof: deterministic deploy, health, backup, restore, rollback evidence
- Live claim requires:
- remote staging host/url/secret configuration
- remote staging drill
- version proof matching PR #82 headRefOid

## Task Brief Commands

Run `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task <task-id>` for one fleet task.

| Task | Suggested Branch | Acceptance Count | Handoff Artifact Count |
|---|---|---:|---:|
| ODP-EXT-001 | `task/ODP-EXT-001-provider-registry-secrets` | 3 | 3 |
| ODP-EXT-002 | `task/ODP-EXT-002-live-listing-feed` | 3 | 3 |
| ODP-EXT-003 | `task/ODP-EXT-003-live-geocoder` | 3 | 3 |
| ODP-EXT-004 | `task/ODP-EXT-004-scheduled-fetch` | 3 | 3 |
| ODP-EXT-005 | `task/ODP-EXT-005-quota-rate-limit` | 3 | 3 |
| ODP-EXT-006 | `task/ODP-EXT-006-freshness-quality-gate` | 3 | 3 |
| ODP-EXT-007 | `task/ODP-EXT-007-licensing-gate` | 3 | 3 |
| ODP-EXT-008 | `task/ODP-EXT-008-external-source-product-e2e` | 3 | 3 |
| ODP-MAP-E2E-001 | `task/ODP-MAP-E2E-001-live-tile-geocoder` | 3 | 3 |
| ODP-MAP-E2E-002 | `task/ODP-MAP-E2E-002-layer-url-persistence` | 3 | 3 |
| ODP-MAP-E2E-003 | `task/ODP-MAP-E2E-003-direct-map-picking` | 3 | 3 |
| ODP-MAP-E2E-004 | `task/ODP-MAP-E2E-004-deck-pixel-proof` | 3 | 3 |
| ODP-MAP-A11Y-001 | `task/ODP-MAP-A11Y-001-keyboard-map` | 3 | 3 |
| ODP-MAP-E2E-005 | `task/ODP-MAP-E2E-005-map-resilience` | 3 | 3 |
| ODP-MAP-E2E-006 | `task/ODP-MAP-E2E-006-tooltip-evidence` | 3 | 3 |
| ODP-PV-STAGE-001 | `task/ODP-PV-STAGE-001-remote-staging-config` | 3 | 3 |
| ODP-PV-STAGE-002 | `task/ODP-PV-STAGE-002-remote-staging-drill` | 3 | 3 |
