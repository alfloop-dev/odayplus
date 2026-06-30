# Product-Grade E2E Fleet Kickoff Runbook

- PR authority: #82 headRefOid and attached checks
- Queue status: ready_for_fleet_pickup
- Queue role: historical_initial_dispatch
- Current remaining queue: `docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json`
- Updated: 2026-06-29

## Operator Preflight

- Confirm PR #82 `headRefOid` and attached checks before starting work.
- Do not claim live-provider, live-map, or remote-staging proof until the relevant task evidence is attached.
- Keep deterministic fixture/source-stub tests as CI defaults.
- Use each task's suggested branch and brief file as the handoff contract.
- Execute any task-specific `execution_commands` before requesting review.

## Fleet Pickup Sequence

### External provider foundation

- Owner lane: integration / source ingestion
- Reviewer lane: governance / product validation

| Task | Suggested Branch | Brief | Dispatch Command |
|---|---|---|---|
| ODP-EXT-001 | `task/ODP-EXT-001-provider-registry-secrets` | `docs/evidence/fleet_dispatch/ODP-EXT-001.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-001` |
| ODP-EXT-002 | `task/ODP-EXT-002-live-listing-feed` | `docs/evidence/fleet_dispatch/ODP-EXT-002.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-002` |
| ODP-EXT-003 | `task/ODP-EXT-003-live-geocoder` | `docs/evidence/fleet_dispatch/ODP-EXT-003.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-003` |

### External source operations

- Owner lane: data platform / source ingestion
- Reviewer lane: product validation / governance

| Task | Suggested Branch | Brief | Dispatch Command |
|---|---|---|---|
| ODP-EXT-004 | `task/ODP-EXT-004-scheduled-fetch` | `docs/evidence/fleet_dispatch/ODP-EXT-004.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-004` |
| ODP-EXT-005 | `task/ODP-EXT-005-quota-rate-limit` | `docs/evidence/fleet_dispatch/ODP-EXT-005.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-005` |
| ODP-EXT-006 | `task/ODP-EXT-006-freshness-quality-gate` | `docs/evidence/fleet_dispatch/ODP-EXT-006.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-006` |
| ODP-EXT-007 | `task/ODP-EXT-007-licensing-gate` | `docs/evidence/fleet_dispatch/ODP-EXT-007.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-007` |
| ODP-EXT-008 | `task/ODP-EXT-008-external-source-product-e2e` | `docs/evidence/fleet_dispatch/ODP-EXT-008.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-EXT-008` |

### Live map provider gate

- Owner lane: maps / frontend infrastructure
- Reviewer lane: frontend accessibility / product validation

| Task | Suggested Branch | Brief | Dispatch Command |
|---|---|---|---|
| ODP-MAP-E2E-001 | `task/ODP-MAP-E2E-001-live-tile-geocoder` | `docs/evidence/fleet_dispatch/ODP-MAP-E2E-001.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-MAP-E2E-001` |
| ODP-MAP-E2E-002 | `task/ODP-MAP-E2E-002-layer-url-persistence` | `docs/evidence/fleet_dispatch/ODP-MAP-E2E-002.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-MAP-E2E-002` |
| ODP-MAP-E2E-003 | `task/ODP-MAP-E2E-003-direct-map-picking` | `docs/evidence/fleet_dispatch/ODP-MAP-E2E-003.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-MAP-E2E-003` |
| ODP-MAP-E2E-004 | `task/ODP-MAP-E2E-004-deck-pixel-proof` | `docs/evidence/fleet_dispatch/ODP-MAP-E2E-004.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-MAP-E2E-004` |

### Map accessibility and resilience

- Owner lane: frontend accessibility / maps
- Reviewer lane: product validation

| Task | Suggested Branch | Brief | Dispatch Command |
|---|---|---|---|
| ODP-MAP-A11Y-001 | `task/ODP-MAP-A11Y-001-keyboard-map` | `docs/evidence/fleet_dispatch/ODP-MAP-A11Y-001.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-MAP-A11Y-001` |
| ODP-MAP-E2E-005 | `task/ODP-MAP-E2E-005-map-resilience` | `docs/evidence/fleet_dispatch/ODP-MAP-E2E-005.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-MAP-E2E-005` |
| ODP-MAP-E2E-006 | `task/ODP-MAP-E2E-006-tooltip-evidence` | `docs/evidence/fleet_dispatch/ODP-MAP-E2E-006.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-MAP-E2E-006` |

### Remote staging rollout

- Owner lane: platform / deployment
- Reviewer lane: operations / product validation

| Task | Suggested Branch | Brief | Dispatch Command |
|---|---|---|---|
| ODP-PV-STAGE-001 | `task/ODP-PV-STAGE-001-remote-staging-config` | `docs/evidence/fleet_dispatch/ODP-PV-STAGE-001.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-PV-STAGE-001` |
| ODP-PV-STAGE-002 | `task/ODP-PV-STAGE-002-remote-staging-drill` | `docs/evidence/fleet_dispatch/ODP-PV-STAGE-002.md` | `python3 scripts/e2e/check_product_grade_fleet_dispatch.py --task ODP-PV-STAGE-002` |

## Completion Handback

For every task, the implementation fleet must attach:

- implementation evidence
- verification evidence
- acceptance criteria proof
- handoff artifacts

A document-only PR must not close any `ready_for_fleet` queue entry.
