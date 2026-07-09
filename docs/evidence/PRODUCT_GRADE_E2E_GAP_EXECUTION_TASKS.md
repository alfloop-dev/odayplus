# Product-Grade E2E Gap Execution Tasks

Task: ODP-PV-008 follow-up backlog  
Status: open execution backlog  
Release target authority: PR #82 `headRefOid` and attached checks  
Updated: 2026-06-29

## Purpose

This document turns product-grade gaps into fleet-executable tasks. External
source operational gates and map product-grade E2E/a11y proof have now been
implemented in follow-up fleet PRs. The current PR #82 release candidate still
must not be described as complete remote staging proof until the staging tasks
below are implemented and verified.

## Current Proven Boundary

| Area | Proven now | Not yet proven |
|---|---|---|
| External data sources | Deterministic source-stub, external fixtures, connector contract tests, live-provider adapter tests, scheduled fetch worker tests, quota/rate-limit resilience, freshness/data-quality gates, licensing gates, and product E2E mock proof | Provider-specific production credential rotation and provider-specific production licensing approval |
| Maps | Deterministic local MapLibre/deck/H3 E2E, live tile/geocoder boundary gate, canvas and semantic deck pixel proof, map/list/drawer sync, layer URL persistence, direct map picking, resilience states, tooltip/evidence detail, and full keyboard accessibility | Remote-staging proof against actual live tile/geocoder endpoints |
| Remote staging | Deterministic deployment, health, backup, restore, rollback evidence; `/platform/version` endpoint and remote staging proof checker are available | Real staging host/url/secret configuration, staging health/version proof matching PR #82 `headRefOid`, product smoke against staging URL, and a live staging drill |

Compatibility invariants for release coverage tests: the remaining boundary
still names provider credential/OAuth, scheduled external fetch,
quota/rate-limit, production licensing, live tile rollout, live geocoder rollout,
full keyboard accessibility, direct map picking, and remote staging
host/url/secret. Some of these have deterministic or local follow-up proof
above, but they remain listed so the release packet does not blur deterministic
proof with provider-specific production or remote-staging proof.

## Execution Tasks

### ODP-PV-LIVE-SRC-001 Live Provider Connector Wiring

Status: implemented for adapter/mock-live release proof; provider-specific
production credential rotation remains an operations follow-up.

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

Status: implemented for deterministic scheduled-worker and freshness-gate proof.

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

Status: implemented for deterministic quota/rate-limit and allowed-use proof.

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

Status: implemented as a live boundary/config gate; remote-staging proof against
actual tile/geocoder endpoints remains under `ODP-PV-STAGE-001/002`.

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

Status: implemented by `ODP-MAP-A11Y-001`.

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

Status: implemented by `ODP-MAP-E2E-002`, `ODP-MAP-E2E-003`,
`ODP-MAP-E2E-004`, and `ODP-MAP-E2E-006`.

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
- Execution runbook: `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md`.
- Required implementation evidence:
  - remote staging host/url/secret configuration.
  - Documented environment variables and secret owner.
  - Health endpoint and version endpoint showing PR #82 `headRefOid`.
- Required tests:
  - Smoke check against the remote staging URL.
  - Evidence artifact proving the remote build version matches the candidate.
- Required command:
  ```bash
  python3 scripts/e2e/check_remote_staging_proof.py \
    --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" \
    --correlation-id "corr-odp-pv-stage-001"
  ```
- Acceptance:
  - The release cannot claim live remote staging rollout until this task passes.

### ODP-PV-STAGE-002 Remote Staging Drill

- Owner lane: platform / operations
- Scope: rerun deployment, health, backup, restore, rollback, and product E2E
  evidence against the remote staging target.
- Execution runbook: `docs/evidence/REMOTE_STAGING_PROOF_RUNBOOK.md`.
- Required implementation evidence:
  - Staging runbook execution log.
  - Backup/restore evidence from the staging backing store.
  - Rollback drill result and correlation id.
- Required tests:
  - Product E2E smoke against staging URL.
  - Backup/restore/rollback command against staging resources or an approved
    staging-equivalent drill.
- Required commands:
  ```bash
  PLAYWRIGHT_BASE_URL="$ODP_STAGING_DEPLOY_URL" \
  ODP_API_BASE_URL="$ODP_STAGING_API_URL" \
  npx playwright test tests/e2e/product-e2e-env.spec.ts --project=chromium --timeout=90000

  python3 scripts/e2e/check_remote_staging_proof.py \
    --expected-sha "$(gh pr view 82 --json headRefOid --jq .headRefOid)" \
    --correlation-id "corr-odp-pv-stage-002-version"
  ```
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

## Fleet Task Aliases

The umbrella ODP-PV tasks above may be split into these narrower fleet tasks
when parallel execution is useful.

### External Source Aliases

| Alias | Parent | Scope | Required proof |
|---|---|---|---|
| `ODP-EXT-001` | `ODP-PV-LIVE-SRC-001` | Provider registry and secrets for listing, POI, geocode, admin boundary, and competitor/manual sources | Secret names, auth modes, startup validation, no committed secrets |
| `ODP-EXT-002` | `ODP-PV-LIVE-SRC-001` | Live listing feed adapter | Contract validation, raw landing, idempotency, fixture-compatible replay |
| `ODP-EXT-003` | `ODP-PV-LIVE-SRC-001` | Live geocoder adapter | Credential handling, rate-limit retry, confidence mapping, deterministic recorded-response tests |
| `ODP-EXT-004` | `ODP-PV-LIVE-SRC-002` | Scheduled external fetch worker | Last-success watermark, backfill window, idempotency key, durable source snapshot ids |
| `ODP-EXT-005` | `ODP-PV-LIVE-SRC-003` | Quota/rate-limit resilience | 401/403/429/5xx/timeout simulation, retry budget, circuit breaker, alert/audit event |
| `ODP-EXT-006` | `ODP-PV-LIVE-SRC-002` | Freshness and data-quality gate | Per-source freshness SLA, stale blocking/warning behavior, UI/API freshness status |
| `ODP-EXT-007` | `ODP-PV-LIVE-SRC-003` | Licensing and allowed-use gate | License metadata, expiry, attribution, downstream-use flags, `license_blocked` quarantine |
| `ODP-EXT-008` | `ODP-PV-LIVE-SRC-001` | External source product E2E | Provider-mock service with auth/quota/freshness/license scenarios and persisted lineage |

### Map Aliases

| Alias | Parent | Scope | Required proof |
|---|---|---|---|
| `ODP-MAP-E2E-001` | `ODP-PV-LIVE-MAP-001` | Live tile/geocoder boundary gate | Configured staging tile/geocoder smoke or explicit conditional proof |
| `ODP-MAP-E2E-002` | `ODP-PV-LIVE-MAP-003` | Layer toggle URL persistence | Toggle H3/listing/candidate/confidence/freshness layers, reload restore, visible layer state |
| `ODP-MAP-E2E-003` | `ODP-PV-LIVE-MAP-003` | Direct map picking | H3, listing, and candidate map pick opens the same drawer state as list selection |
| `ODP-MAP-E2E-004` | `ODP-PV-LIVE-MAP-003` | Deck pixel content proof | H3 polygon, listing point, candidate point, and selected highlight pixel/content checks |
| `ODP-MAP-A11Y-001` | `ODP-PV-LIVE-MAP-002` | Keyboard map/list/drawer accessibility | Tab/Enter/Escape/focus-return flow and axe/focus-visible checks |
| `ODP-MAP-E2E-005` | `ODP-PV-LIVE-MAP-002` | Map state resilience | Loading, empty, error with correlation id, partial failed layer, and no-geometry fallback |
| `ODP-MAP-E2E-006` | `ODP-PV-LIVE-MAP-003` | Tooltip and evidence detail | Hover plus keyboard reachable tooltip proof for score, state, confidence, warnings, and version fields |
