# ODay Plus Platform — Authoritative Completeness Inventory

- doc_id: ODP-COMPLETENESS-INVENTORY-001
- date: 2026-07-25
- dev HEAD at assessment: `8ec12c02`
- deployed oday-api: revision `oday-api-00005-gin`, `release_sha=8ec12c02`
- method: verified against the running system (readiness probes, live provider
  ingestion through the system's own provider classes, CI), not estimated.

## Headline

The platform's **code and architecture are complete**; the **runtime is now
largely live**. The only platform-readiness gap left is production ML model
bindings, which are **deferred on real training data** (risk-accepted), not
missing code.

`/readiness` overall = `unhealthy` **solely** because of
`PRODUCTION_MODEL_BINDINGS_UNVERIFIED`. Every other dependency is healthy.

## Layer-by-layer (verified)

| Layer | Status | Evidence |
|---|---|---|
| Durable persistence | **LIVE** | `/readiness` persistence: postgresql, durable=true, reachable=true; Cloud SQL `oday-plus-dev-postgres` RUNNABLE |
| External provider: geocode | **LIVE** | real Google Geocoding via `odp-provider-gateway/geocode`; probe http 200, schema_valid |
| External provider: POI | **LIVE** | real Google Places; 119 POIs ingested through `PoiCommercialApiProvider`, 0 quarantined |
| External provider: admin boundary | **LIVE** | official TW 鄉鎮市區界 (399 records) through `AdminBoundaryDatasetProvider`, 0 quarantined |
| Listing intake | **LIVE (assisted)** | assisted-listing-intake: URL provenance + governed manual entry (591/樂屋/好房 ToS forbid scraping); durable + geocode enrichment |
| Bulk listing partner feed | **BUILT, not contracted** | `ListingPartnerFeedProvider` implemented+tested; reconciled out of live-required pending a licensed partner ([reconciliation doc](EXTERNAL_PROVIDER_LIVE_REQUIRED_RECONCILIATION is in docs/design)) |
| Operator Console / domain APIs | **LIVE (durable, fail-closed)** | `require_live_data=true`, OperatorLiveRepository ready on postgres; data endpoints enforce RBAC (403), not fixtures |
| Production ML models (avm/sitescore/forecastops/heatzone) | **DEFERRED (risk-accepted)** | pipeline built (OSS estimators + MLflow + governance); no production alias — needs ≥90–200 real labeled rows/model. See [risk acceptance](PRODUCTION_MODEL_RISK_ACCEPTANCE_2026-07-25.md) |
| Web frontend | **updating** | rebuilt from `8ec12c02`; deploy in progress |

## Honest completeness read

- **Code / architecture:** ~95–100% — the platform is built (OSS AI runtimes,
  durable persistence, operator domains, external-data platform, assisted intake,
  governance gates). This session merged the last OSS + operator-live work (PR
  #356) and reconciled the provider gate (PR #360).
- **Runtime live-ness:** the deployed stack is on durable Postgres with three
  real external providers live and listings flowing via assisted intake. The
  remaining runtime gap is **production ML models**, which cannot be honestly
  registered until real labeled data accumulates through normal product use.
- **Production GO:** still formally **NO-GO** only on the model bindings; this is
  a data-maturity dependency, not an engineering gap.

## What changed this session

1. Merged OSS AI + operator-live + durable-persistence work to dev (PR #356;
   fixed a false-positive SAST B608 that was the sole blocker).
2. Verified durable Postgres persistence was already live on `oday-api`
   (the "mock data" was a stale July-5 `waji` static frontend, not the real stack).
3. Made three external providers genuinely live via a new `odp-provider-gateway`
   (Google Geocoding, Google Places, official TW admin boundaries) — each
   gold-verified by ingesting through the system's own provider classes.
4. Reconciled `listing.partner_feed` out of the live-required set to match the
   real product (assisted intake), keeping the bulk-feed capability for a future
   licensed partner (PR #360, CI green).
5. Deployed `oday-api` from `8ec12c02` with providers flipped live; risk-accepted
   the production model bindings pending real training data.

## To close the last gap

Accumulate real labeled data (assisted-intake listings + accrued outcomes +
provider ingestion) to each model's `minimum_rows`, then run
`product_ops/modeling/release.py` to register DEV→SHADOW→production aliases and
re-verify `/readiness`.
