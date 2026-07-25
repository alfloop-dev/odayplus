# Production ML Model Risk Acceptance

- doc_id: ODP-MODEL-RISK-ACCEPT-001
- date: 2026-07-25
- decision: **GO with risk accepted** (deploy live; production model bindings deferred)
- accepted_by: Human/Ops (bjoe734)

## Scope

The four platform-readiness production models are **not** registered with a
production MLflow alias at deploy time:

| service | model | training spec min rows |
|---|---|---|
| forecastops | forecast_revenue_interval | 90 |
| avm | dealroom_avm | 120 |
| sitescore | sitescore_propensity | 200 |
| heatzone | heatzone_priority | 200 |

At readiness time `/readiness` reports
`PRODUCTION_MODEL_BINDINGS_UNVERIFIED` for these services.

## Why this is accepted, not a defect

The training + registration pipeline is fully built and governed
(`scripts/models/release.py`, OSS estimators — CatBoost/LightGBM/StatsForecast —
MLflow registry, DEV→SHADOW→production promotion with a required rollback
candidate). It refuses to train below `minimum_rows` of **real labeled** data
rather than inventing a model.

The fresh deployment has no accumulated labeled training data yet: listings enter
through assisted manual intake (per-URL, governed) and outcomes accrue over time
as the product is used. Model training data is intrinsic to real business
operation and has no external substitute (unlike geocode/POI/admin-boundary,
which are now live via real Google/official sources).

Registering "production" models on absent or synthetic data would be false
evidence. We therefore defer production model bindings until real data reaches
the per-model minimums, and accept the interim state.

## What IS live at this deploy

- Durable Postgres persistence (`ODP_PERSISTENCE=postgresql`, reachable).
- External providers live: `geocode.primary_api`, `poi.commercial_api`,
  `admin_boundary.official_dataset` (real upstreams via `odp-provider-gateway`).
- Listings via assisted-listing-intake (live, manual entry).

## Exit criteria (to close this risk)

For each model, once `labeled_row_count >= minimum_rows` in the model-ready
views, run `scripts/models/release.py` to register a DEV candidate, promote
through SHADOW to a production alias with a rollback candidate, and re-verify
`/readiness` shows the model bindings ready.
