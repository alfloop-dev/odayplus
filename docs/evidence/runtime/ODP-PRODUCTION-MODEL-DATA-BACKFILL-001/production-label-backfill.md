# Production model label backfill evidence

Task: `ODP-PRODUCTION-MODEL-DATA-BACKFILL-001`
Executor: `Codex6`
Execution date: `2026-07-27`
Reviewer: `Codex2`

## Activated data

The approved legacy PostgreSQL store was read through Cloud SQL Auth Proxy with
the active GCP operator identity. The canonical PostgreSQL 16 activation target
was migrated to the repository schema and received only persisted production
rows. No fixture, seed, synthetic, prediction, or mock row was used.

The copy was dependency ordered and primary-key conflict safe:

| Relation | Source rows processed |
|---|---:|
| `core.tenants` | 1,354 |
| `core.brands` | 1,354 |
| `core.address_locations` | 2,405 |
| `core.stores` | 2,442 |
| `core.machines` | 7,562 |
| `core.transactions` | 298,599 |
| attributable transaction `data_plane.ingestion_runs` | 36 |
| transaction `data_plane.canonical_lineage` | 298,599 |

The target inserts used `ON CONFLICT DO NOTHING`. Re-running the same bounded
copy therefore cannot duplicate a canonical entity or lineage tuple. Foreign
keys preserve store/tenant ownership, and lineage retains its original
`tenant_id`; no cross-tenant remapping was performed.

The versioned `scripts/models/sql/model_ready_views.sql` artifact was installed
under a transaction advisory lock. After composing the official-outcome
contract from `dev`, the installed SQL SHA-256 is
`0bbcad43ed07f81ecd1a6f6a4e7e387efb3abb540b4d98c8d101a0bdd292dca8`.

Both approved GCS locations were inventoried and contained zero objects before
this activation:

- source snapshots: `gs://oday-dev-source-snapshots-alfaloop-data-project`
- model artifacts: `gs://alfaloop-data-project-oday-plus-model-artifacts`

No unproven GCS label object was imported.

The fixed official-source registry was then used to download and atomically
upsert both approved Taiwan sale-outcome sources:

| Official source | Dataset | Parsed observations | Current projections | Outcome range |
|---|---|---:|---:|---|
| Ministry of the Interior | `25119` | 8,456 | 8,456 | 2016-01-04 through 2026-07-08 |
| New Taipei City Land Administration | `acce802d-58cc-4dff-9e7a-9ecc517f78be` | 50,665 | 50,662 | 2013-03-24 through 2026-12-06 |

Both receipts are `SUCCEEDED`, use
`government-open-data-license-v1`, retain content/schema SHA-256 and immutable
source observations, and had zero stale rows. Deterministic run IDs plus
source/snapshot advisory locks make exact replay idempotent.

## Redacted model inventory

Inventory exposes aggregates only: no tenant identifier, source payload,
credential, or label value.

| Model | Label | Train | Validation | Test | Minimum total | Tenant count | Snapshot count | Temporal cutoff | Result |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| ForecastOps | `daily_net_revenue` | 783 | 260 | 260 | 90 | 413 | 220,597 | 2026-06-19 through 2026-06-22 | usable |
| Listing-property AVM | official `realized_transaction_price` | 35,471 | 11,823 | 11,823 | 120 | global authority | 2 | 2013-03-23 through 2026-07-07 UTC | research usable |
| DealRoom AVM | DealRoom realized transaction outcome | 0 | 0 | 0 | 120 | 0 | 0 | none | blocked |
| SiteScore | `realized_90d_net_revenue` | 0 | 0 | 0 | 200 | 0 | 0 | none | blocked |
| HeatZone | `realized_28d_cell_net_revenue` | 0 | 0 | 0 | 200 | 0 | 0 | none | blocked |

ForecastOps has 1,303 eligible labeled rows and exceeds every deterministic
60/20/20 split minimum (54/18/18). Its realized labels come from successful
TWD `core.transactions`; every eligible row is backed by completed ingestion
runs and immutable canonical source-snapshot lineage.

## Irreducible source gates

The official listing-property contract has 59,117 eligible labeled rows. It is
intentionally separate from the DealRoom runtime AVM: its government
municipality/area/building feature schema cannot be silently substituted for
the DealRoom store/financial feature schema, and production promotion remains
blocked until a compatible runtime consumer and live inference smoke exist.

The remaining zero counts are business-data gaps, not permission to infer
labels.

- **DealRoom AVM — source owner: DealRoom Operations / Asset Data Platform.**
  Required fields are an attributable completed deal, realized transaction
  price/time, canonical tenant/store/property identity, and the financial and
  lease features bound by `model_ready.valuation_view`. Earliest activation
  gate: persist mature completed-deal outcomes and commit a reviewed identity
  link. Official listing-property outcomes are available but contract
  incompatible; `asset.valuation_runs` and predictions remain forbidden labels.
- **SiteScore — source owner: Expansion Operations and POS Data Platform.**
  Required fields are an attributable `store_id`, actual `opened_on`,
  `store_format_code`, point-in-time address/H3 assignment, and complete
  successful TWD POS outcomes for all 90 label days. The approved store has
  2,442 stores but zero `opened_on` values and zero qualifying geography
  history. Earliest activation gate: Operations publishes opening observations
  and Data Platform ingests PIT geography with completed daily partitions.
- **HeatZone — source owner: Geo/Data Platform and POS Data Platform.**
  Required fields are immutable H3 assignment with observed/valid timestamps,
  geocode confidence, completed authoritative order partitions, and attributable
  28-day realized cell revenue. The approved store has zero geography history.
  Earliest activation gate: the selected official/admin-boundary and POI
  providers persist canonical PIT snapshots, followed by 90 prior and 28 label
  days of complete POS partitions.

## Verification

- PostgreSQL servers: approved legacy PostgreSQL 15.18 and target PostgreSQL 16.
- Target pre-activation inventory: only the `public` schema; zero user tables.
- Model-ready installer preflight: 270,635 successful TWD transactions,
  2,442 stores, zero SiteScore anchors, zero HeatZone cells.
- Post-activation model inventory: ForecastOps 1,303 eligible labels with
  783/260/260 chronological splits; official listing-property AVM 59,117 with
  35,471/11,823/11,823 splits; DealRoom AVM/SiteScore/HeatZone remain zero.
- Inventory implementation tests:
  `pytest -q tests/integration/test_model_training_release.py
  tests/integration/test_model_ready_geo_views.py
  tests/models/test_model_ready_geo_contracts.py`.

The independent reviewer should re-run the redacted inventory against the
target and confirm these aggregate counts and gates before approval.
