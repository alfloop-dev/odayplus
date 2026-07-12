# ODP-FLOW-002 — Complete Expansion HeatZone → SiteScore decision flow

**Owner:** Claude2 · **Reviewer:** Claude · **Phase:** Product Flow Implementation

## Goal

Close the expansion decision loop so the whole chain —
HeatZone ranking → Listing import/dedup → CandidateSite → SiteScore
report versions → human review decision → realization hook — is backed by
durable storage and a durable audit trail, and keeps serving the
API-backed map / list / detail views after a process restart.

## What was missing

The domain + API for the loop already existed (ODP-R2-001/002/003) and the
durable persistence backend existed (ODP-PV-009 `PersistenceBundle` /
`SqliteDocumentStore`). But three of the loop's stores were **not** sourced
from the bundle, so even in `ODP_PERSISTENCE=durable` mode they lived only in
process memory and were lost on restart:

- **HeatZone ranking** — `create_app` built `HeatZoneResultStore()` inline.
- **Listing dedup + candidate inbox** — the listings router lazily created an
  `InMemoryListingRepository` on `app.state`.
- **SiteScore decisions + realized sites** — `SiteScoreDecisionWorkflow` kept
  decisions/reports in instance dicts and the realization hook kept realized
  sites in a private dict.

SiteScore **report versions** were already durable (`DurableSiteScoreRepository`).

## Changes

### New durable twins (mirror existing in-memory surfaces exactly)
- `modules/heatzone/infrastructure/repositories.py` — `HeatZoneResultStore`
  relocated here (out of the API route) and given a public
  `find_by_idempotency_key(...)` so the route no longer reaches into private
  dicts.
- `shared/infrastructure/persistence/repositories.py` —
  `DurableHeatZoneResultStore`, `DurableListingRepository`,
  `DurableDecisionStore`, `DurableRealizedSiteStore`, all over the shared
  `SqliteDocumentStore` (new collections only; no migration needed).

### Injectable stores on the workflow
- `shared/workflow/sitescore.py` — added `DecisionStore` /
  `InMemoryDecisionStore` and `RealizedSiteStore` / `InMemoryRealizedSiteStore`
  protocols. `SiteScoreDecisionWorkflow` now persists decisions + the frozen
  source report through an injectable `store`; `CandidateSiteRealizationHook`
  persists realized sites through an injectable store. Public behaviour and
  audit events are unchanged.

### Wiring
- `shared/infrastructure/persistence/factory.py` — `PersistenceBundle` gains
  `heatzone_store`, `listing_repository`, `sitescore_decision_store`,
  `sitescore_realized_store`; memory mode → in-memory variants (byte-for-byte
  the prior behaviour), durable mode → the SQLite twins.
- `apps/api/oday_api/main.py` — sources the HeatZone store, listing repository,
  decision workflow store, and realization hook from the bundle; passes the
  durable-backed realization hook into the SiteScore router (guarded against
  double-registration).
- `apps/api/app/routes/listings.py` / `apps/api/app/routes/heatzone.py` /
  `apps/api/app/routes/sitescore.py` — accept the injected repository /
  realization hook; the HeatZone route uses the public idempotency lookup.

## Acceptance mapping

| Acceptance | Where it is satisfied |
|---|---|
| HeatZone ranking and listing dedup persist | `DurableHeatZoneResultStore` (job/latest/idempotency) + `DurableListingRepository` (dedup-key collection) |
| candidate and SiteScore versions persist | `DurableListingRepository` candidate collection + existing `DurableSiteScoreRepository` versioning |
| review decision and realization hook are audited | `DurableAuditLog` records `sitescore.decision.v1` (create/submit/approve/return); decisions + realized sites persisted via `DurableDecisionStore` / `DurableRealizedSiteStore` |
| API-backed map list detail E2E passes | `tests/e2e/e2e-expansion-product.spec.ts` (E2E-PV-005) exercises map/list/detail; `tests/integration/test_flow_002_expansion_persistence.py` proves the same loop survives a restart at the API level |

## Notes
- `memory` mode is unchanged, so existing unit/integration tests keep passing.
- Durable stores reuse `durable_documents` via new collection names only — no
  schema/migration change.
