# Product-Grade E2E Gap Execution Tasks

Task: ODP-PV-008 follow-up backlog  
Status: open execution backlog  
Release target authority: PR #82 `headRefOid` and attached checks  
Updated: 2026-06-29

## Purpose

This document turns the remaining product-grade gaps into fleet-executable
tasks. The current PR #82 release candidate has deterministic product E2E proof,
but it must not be described as complete live-provider, live-map, or remote
staging proof until the tasks below are implemented and verified.

## Current Proven Boundary

| Area | Proven now | Not yet proven |
|---|---|---|
| External data sources | Deterministic source-stub, external fixtures, connector contract tests, product environment smoke | Live provider ingestion, provider credential/OAuth wiring, scheduled external fetch, quota/rate-limit handling, provider-specific freshness, production licensing |
| Maps | Deterministic local MapLibre/deck/H3 E2E, canvas nonblank proof, map/list/drawer sync | Live tile rollout, live geocoder rollout, full keyboard accessibility, layer toggles, direct map picking, deck.gl semantic pixel coverage |
| Remote staging | Deterministic deployment, health, backup, restore, rollback evidence | Real staging host/url/secret configuration and a live staging drill |

## Execution Tasks

### ODP-PV-LIVE-SRC-001 Live Provider Connector Wiring

- Owner lane: integration / source ingestion
- Scope: replace fixture-only source proof with at least one configured live
  provider path for listing, POI, and competitor/store source classes.
- Required implementation evidence:
  - Provider credential/OAuth or API-key loading through environment/secret
    configuration, with no secret values committed.
  - Source connector client(s) with retry-safe request handling.
  - Quarantine path for malformed, partial, or unauthorized provider records.
  - Audit/source lineage fields: provider id, request id, provider observed
    time, ingestion time, source snapshot id, and correlation id.
- Required tests:
  - Contract tests for auth failure, provider timeout, malformed payload,
    duplicate record, and successful ingestion.
  - Product E2E smoke proving live-provider mode can run when credentials are
    present, while deterministic fixture mode remains the CI default.
- Acceptance:
  - CI must still pass without live credentials.
  - Live-provider mode must fail closed with a clear error when credentials are
    missing, expired, or unauthorized.
  - The release packet must distinguish live-provider proof from fixture proof.

### ODP-PV-LIVE-SRC-002 Scheduled Fetch, Freshness, And Backfill

- Owner lane: data platform / source ingestion
- Scope: prove external source freshness is maintained by scheduled ingestion,
  not only by static snapshots.
- Required implementation evidence:
  - Scheduler/job definition for listing, POI, and competitor/store fetch.
  - Idempotent ingest batch id and replay/backfill command.
  - Freshness SLA check surfaced in product E2E evidence.
  - Alert/quarantine event when freshness SLA is breached.
- Required tests:
  - Unit/contract tests for idempotent replay and stale-source detection.
  - E2E test that advances a stale fixture/source clock and verifies UI/data
    quality state shows `STALE` or blocked status.
- Acceptance:
  - A failed scheduled provider fetch must not silently reuse old data as fresh.
  - Audit evidence must show the batch id and freshness state used by the UI.

### ODP-PV-LIVE-SRC-003 Quota, Rate-Limit, And Licensing Guardrails

- Owner lane: governance / integration
- Scope: make provider operational limits explicit before production promotion.
- Required implementation evidence:
  - Provider quota/rate-limit configuration.
  - Backoff and retry budget policy.
  - License/terms registry entry for each live provider.
  - Export restrictions for provider-derived sensitive fields.
- Required tests:
  - Rate-limit response simulation.
  - Quota-exhausted degraded mode.
  - License-blocked provider cannot be used in production mode.
- Acceptance:
  - Product release notes must list provider classes and licensing state.
  - The app must degrade to clear stale/blocked state rather than silently
    fabricating source freshness.

### ODP-PV-LIVE-MAP-001 Live Tile And Geocoder Rollout

- Owner lane: maps / frontend infrastructure
- Scope: prove map rendering against a configured live tile endpoint and live
  geocoder path, while keeping deterministic local map tests.
- Required implementation evidence:
  - `MAP_TILE_URL` or equivalent live tile rollout configuration.
  - Geocoder configuration with failure/degraded mode.
  - Runtime source attribution and terms display.
  - Fallback to list/table when map service fails.
- Required tests:
  - E2E against a staging tile/geocoder endpoint.
  - Failure-mode E2E for tile outage and geocoder outage.
  - Visual/pixel smoke proving live basemap and H3 overlay both render.
- Acceptance:
  - Map failure must not block list/ranking/detail workflows.
  - Release evidence must distinguish deterministic local map proof from live
    map provider proof.

### ODP-PV-LIVE-MAP-002 Full Keyboard And Accessibility Map Coverage

- Owner lane: frontend accessibility
- Scope: make map workflows usable without pointer-only interaction.
- Required implementation evidence:
  - Keyboard layer control navigation.
  - Keyboard access to HeatZone/listing selection and drawer open/close.
  - Focus management between map/list/drawer.
  - Screen-reader summary/list fallback for map layers.
- Required tests:
  - Playwright keyboard-only flow for layer selection, list fallback, drawer
    open/close, and return focus.
  - Axe scan for the HeatZone map route.
- Acceptance:
  - User can complete HeatZone ranking to detail drawer without mouse input.
  - Color/status is not the only map risk signal.

### ODP-PV-LIVE-MAP-003 Layer Toggle, Picking, And Deck Pixel Semantics

- Owner lane: maps / frontend E2E
- Scope: strengthen map proof beyond canvas nonblank checks.
- Required implementation evidence:
  - Layer toggle state is URL-shareable or persisted according to product spec.
  - Direct map picking opens the same detail state as the list.
  - Deck.gl overlay has semantic pixel checks for H3/marker visibility.
- Required tests:
  - Toggle HeatZone, listing, existing-store, competitor-store, and risk layers.
  - Direct map click/pick opens HeatZone or candidate detail.
  - Pixel checks confirm selected layer color/pattern changes, not only that the
    canvas is nonblank.
- Acceptance:
  - The map E2E must fail if layer toggles stop changing rendered layer state.
  - The list alternative must remain authoritative for accessibility and audit.

### ODP-PV-STAGE-001 Remote Staging Configuration

- Owner lane: platform / deployment
- Scope: configure a real remote staging target for the release candidate.
- Required implementation evidence:
  - remote staging host/url/secret configuration.
  - Documented environment variables and secret owner.
  - Health endpoint and version endpoint showing PR #82 `headRefOid`.
- Required tests:
  - Smoke check against the remote staging URL.
  - Evidence artifact proving the remote build version matches the candidate.
- Acceptance:
  - The release cannot claim live remote staging rollout until this task passes.

### ODP-PV-STAGE-002 Remote Staging Drill

- Owner lane: platform / operations
- Scope: rerun deployment, health, backup, restore, rollback, and product E2E
  evidence against the remote staging target.
- Required implementation evidence:
  - Staging runbook execution log.
  - Backup/restore evidence from the staging backing store.
  - Rollback drill result and correlation id.
- Required tests:
  - Product E2E smoke against staging URL.
  - Backup/restore/rollback command against staging resources or an approved
    staging-equivalent drill.
- Acceptance:
  - Staging drill evidence must be linked from the go/no-go package.
  - Human/Ops may approve deterministic E2E separately, but live staging
    rollout remains conditional until this task is complete.

## Dispatch Rules

- These tasks are follow-up execution work, not proof that the current PR #82
  draft release is incomplete for deterministic E2E.
- Do not close a task with only a document update. Each task requires runtime or
  E2E evidence matching its scope.
- Do not hardcode `dev@<hash>` in evidence. Use PR #82 `headRefOid` and attached
  checks as the release candidate authority.
- Deterministic fixture/source-stub tests must remain as CI defaults even after
  live-provider paths are added.
