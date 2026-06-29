# Product-Grade E2E Fleet Dispatch

Task: ODP-PV-008 follow-up dispatch  
Status: open execution dispatch  
Release target authority: PR #82 `headRefOid` and attached checks  
Updated: 2026-06-29

## Purpose

This dispatch turns `PRODUCT_GRADE_E2E_GAP_EXECUTION_TASKS.md` into
fleet-executable implementation lanes. It does not change the release status
of PR #82 and it is not proof that live providers, live maps, or remote staging
have already been completed.

The current release candidate has deterministic product E2E proof. The tasks
below are the work required before anyone may claim product-grade live external
data source proof, live map provider proof, or remote-staging proof.

## Dispatch Boundary

| Boundary | Current proof | Dispatch required before live claim |
|---|---|---|
| External data sources | deterministic fixtures, source-stub, connector contracts | live provider registry, credentials, adapters, scheduler, quota, freshness, licensing, product E2E |
| Maps | deterministic local MapLibre/deck/H3 E2E and canvas proof | live tile/geocoder gate, layer URL persistence, direct map picking, semantic deck pixel checks, keyboard/a11y, map resilience |
| Remote staging | deterministic deploy, health, backup, restore, rollback evidence | real staging host/url/secret configuration and remote staging drill |

## Fleet Lanes

| Fleet lane | Owner lane | Reviewer lane | Task aliases | Parent tasks |
|---|---|---|---|---|
| External provider foundation | integration / source ingestion | governance / product validation | `ODP-EXT-001`, `ODP-EXT-002`, `ODP-EXT-003` | `ODP-PV-LIVE-SRC-001` |
| External source operations | data platform / source ingestion | product validation / governance | `ODP-EXT-004`, `ODP-EXT-005`, `ODP-EXT-006`, `ODP-EXT-007`, `ODP-EXT-008` | `ODP-PV-LIVE-SRC-001`, `ODP-PV-LIVE-SRC-002`, `ODP-PV-LIVE-SRC-003` |
| Live map provider gate | maps / frontend infrastructure | frontend accessibility / product validation | `ODP-MAP-E2E-001`, `ODP-MAP-E2E-002`, `ODP-MAP-E2E-003`, `ODP-MAP-E2E-004` | `ODP-PV-LIVE-MAP-001`, `ODP-PV-LIVE-MAP-003` |
| Map accessibility and resilience | frontend accessibility / maps | product validation | `ODP-MAP-A11Y-001`, `ODP-MAP-E2E-005`, `ODP-MAP-E2E-006` | `ODP-PV-LIVE-MAP-002`, `ODP-PV-LIVE-MAP-003` |
| Remote staging rollout | platform / deployment | operations / product validation | `ODP-PV-STAGE-001`, `ODP-PV-STAGE-002` | `ODP-PV-STAGE-001`, `ODP-PV-STAGE-002` |

## External Source Dispatch

| Alias | Dispatch objective | Required implementation evidence | Required verification |
|---|---|---|---|
| `ODP-EXT-001` | Provider registry and secrets | Secret names, auth modes, environment validation, provider class metadata for listing, POI, geocode, admin boundary, competitor/manual sources; no committed secrets | Startup validation proves missing/expired credentials fail closed with clear error and correlation id |
| `ODP-EXT-002` | Live listing feed adapter | Authenticated provider client, raw landing snapshot, idempotency key, canonical transform, quarantine path | Contract tests for success, duplicate, malformed payload, unauthorized, timeout; deterministic replay fixture remains CI default |
| `ODP-EXT-003` | Live geocoder adapter | Credential handling, geocode confidence mapping, provider request id, provider observed time, retry budget | Recorded-response tests for success, low confidence, rate limit, timeout, unauthorized; UI receives confidence/freshness fields |
| `ODP-EXT-004` | Scheduled external fetch worker | Scheduler/job definition, last-success watermark, backfill command, durable source snapshot ids | Idempotent replay test and stale-source clock E2E showing `STALE` or blocked status |
| `ODP-EXT-005` | Quota/rate-limit resilience | 401/403/429/5xx/timeout handling, backoff policy, circuit breaker, alert/audit event | Simulation proves quota exhaustion degrades to stale/blocked state, not fabricated freshness |
| `ODP-EXT-006` | Freshness and data-quality gate | Per-source SLA, provider observed time, ingestion time, source snapshot id, correlation id | API/UI E2E proves freshness state and source lineage appear in product evidence |
| `ODP-EXT-007` | Licensing and allowed-use gate | License metadata, attribution, expiry, downstream-use flags, export restrictions | License-blocked provider enters quarantine and cannot be used in production mode |
| `ODP-EXT-008` | External source product E2E | Provider-mock service with auth/quota/freshness/license scenarios and persisted lineage | Product E2E runs live-provider mode when credentials or approved mock service are present; CI fixture mode remains default |

## Map Dispatch

| Alias | Dispatch objective | Required implementation evidence | Required verification |
|---|---|---|---|
| `ODP-MAP-E2E-001` | Live tile/geocoder boundary gate | `MAP_TILE_URL` or equivalent, geocoder configuration, source attribution, terms display, list fallback | Staging tile/geocoder smoke or explicit conditional proof; outage E2E leaves list/ranking/detail usable |
| `ODP-MAP-E2E-002` | Layer toggle URL persistence | H3, listing, candidate, confidence, freshness, existing-store, competitor, and risk layer state in URL or persisted state | Toggle, reload, and share-state E2E verifies visible layer state is restored |
| `ODP-MAP-E2E-003` | Direct map picking | H3/listing/candidate pick handlers open the same detail drawer state as list selection | Playwright direct map pick proves drawer identity, selected state, and audit/list fallback alignment |
| `ODP-MAP-E2E-004` | Deck pixel content proof | Semantic pixel/content checks for H3 polygon, listing point, candidate point, selected highlight, confidence/stale pattern | E2E fails if layer toggles no longer change rendered deck state, not merely if canvas is blank |
| `ODP-MAP-A11Y-001` | Keyboard map/list/drawer accessibility | Keyboard layer controls, list fallback selection, drawer open/close, focus return, focus-visible styling | Tab/Enter/Escape flow and axe scan for HeatZone map route |
| `ODP-MAP-E2E-005` | Map state resilience | Loading, empty, error with correlation id, partial failed layer, no-geometry fallback | E2E proves map failure does not block ranking/list/detail workflow |
| `ODP-MAP-E2E-006` | Tooltip and evidence detail | Hover and keyboard-reachable tooltip for score, state, confidence, warnings, freshness, model/version fields | Tooltip/evidence E2E validates values, source metadata, and accessible fallback text |

## Remote Staging Dispatch

| Alias | Dispatch objective | Required implementation evidence | Required verification |
|---|---|---|---|
| `ODP-PV-STAGE-001` | Remote staging configuration | Remote staging host/url/secret configuration, documented env vars, secret owner, health and version endpoint | Smoke check proves staging version matches PR #82 `headRefOid` |
| `ODP-PV-STAGE-002` | Remote staging drill | Staging runbook log, backup/restore evidence, rollback result, correlation id | Product E2E smoke plus backup/restore/rollback command against staging or approved staging-equivalent resources |

## Handoff Rules

- Dispatch tasks remain open until runtime or E2E evidence exists for the
  specific live scope. A document-only PR must not close any task above.
- Workers must keep Deterministic fixture/source-stub tests as CI defaults.
- Workers must separate live-provider proof, live-map proof, and remote-staging
  proof from deterministic PR #82 proof in every release note.
- Workers must not commit provider secrets, hardcode `dev@<hash>` evidence, or
  claim `main` represents the release candidate without checking PR #82
  `headRefOid`.
- Human/Ops can approve deterministic E2E independently, but live external
  source, live map, and remote staging claims remain conditional until this
  dispatch has matching implementation evidence.
