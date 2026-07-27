# Live PIT geography backfill evidence

Task: `ODP-PRODUCTION-GEOGRAPHY-BACKFILL-001`
Executor: `Claude3`
Execution date: `2026-07-27`
Reviewer: `Codex6`

## Execution environment (redacted)

- Target: PostgreSQL 16 Cloud SQL instance `alfaloop-data-project:asia-east1:oday-dev-sql`
  via the approved `cloud-sql-python-connector` transport
  (`ODP_GEO_CLOUD_SQL_CONNECTOR=true`, short-lived OAuth access token; DSN and
  token redacted).
- Providers: `ODP_EXTERNAL_PROVIDER_MODE=live` with the governed provider
  gateway; `ODP_PRODUCTION_PROVIDER_IDS=poi.commercial_api,geocode.primary_api,admin_boundary.official_dataset`.
  API keys/tokens redacted. Fixture/mock modes are refused by the CLI.
- Command (foreground, per supervisor instruction — no background/detached
  execution):

  ```bash
  ODP_GEO_BACKFILL_RATE_LIMIT_PER_SECOND=10 \
  python -m apps.data_platform.geography_backfill run
  ```

## Terminal run report (exit 0)

Deterministic run id `00172489-4c56-5c07-a89b-c11f070ec9e9`, partition
`2026-07-27`:

```json
{"admin_snapshot_id": "adminboundary-20260727-8ae543f5e03e",
 "eligible_stores": 2442, "inserted": 467, "partition_complete": true,
 "partition_key": "2026-07-27", "poi_snapshot_id": "poi-20260727-f7c9c3154087",
 "processed": 2405,
 "quarantined": {"ADDRESS_UNNORMALIZABLE": 1, "ADMIN_BOUNDARY_MISMATCH": 298,
                 "COORDINATES_OUT_OF_MARKET": 35, "GEOCODE_ADMIN_MISMATCH": 9,
                 "GEOCODE_UNRESOLVED": 153},
 "quarantined_total": 496, "reconciled": true,
 "run_id": "00172489-4c56-5c07-a89b-c11f070ec9e9",
 "skipped_no_address": 37, "unchanged": 1442}
```

Identity check: `processed (2405) = unchanged (1442) + inserted (467) +
quarantined (496)` and `eligible (2442) = processed (2405) +
skipped_no_address (37)`. At terminal execution time, before the
exact-head accounting correction described below, the `ingestion_runs` row
recorded the last attempt's deltas:
`SUCCEEDED / processed 2405 / valid_loaded 467 / quarantined 496 /
reconciled=true / partition_complete=true`. This historical observation is
retained rather than rewritten as if it came from the corrected build.

## Idempotent replay, proven live

Four executions of the same partition shared the deterministic run id and
reproduced identical cumulative counters at every checkpoint they reached:

| Execution (2026-07-27 UTC) | Terminated by | Last checkpoint |
|---|---|---|
| ~19:17Z | worker session kill | `200/2442 (q=34)` |
| ~19:25Z | worker session kill | `1100/2442 (q=222)` |
| ~19:33Z | live geocode HTTP 400 (see fix below) | `1800/2442 (q=374)` |
| final   | terminal exit 0 | `2442/2442 (q=496)` |

The final pass replayed checkpoints `q=34@200`, `q=194@1000`, `q=222@1100`,
`q=374@1800` byte-identical to the earlier transcripts before inserting the
remaining stores. Rows already projected became `unchanged` (1442); no
canonical row was overwritten.

## Mid-run fix: per-address provider rejection (commit `80749815`)

The ~19:33Z execution aborted at ~1850/2442: one store's western-order raw
address (`"1f, No. 14號柳川東路二段西區台中市台灣 403"`) normalizes to the
empty string, and the live gateway rejects an empty address with HTTP 400
`address required`, which killed the whole batch. Per the data-plane contract
(malformed records quarantine; only infrastructure failures stop the batch),
`PlaceGeographyBackfill` now:

- quarantines `ADDRESS_UNNORMALIZABLE` before any provider call when the
  normalized address is empty (no live call is made with an empty address);
- quarantines `GEOCODE_REJECTED` when the live provider returns an HTTP 400
  tied to one address;
- still aborts the run without advancing the checkpoint on auth, rate-limit,
  timeout, and 5xx failures (verified by parametrized tests).

Verification: `pytest tests/integration/test_place_geography_backfill.py`
(9 passed, PostgreSQL 16), `ruff check` / `ruff format --check` clean.

## Exact-head replay corrections (commit `042b4f97`)

Independent review found two replay gaps after the live execution:

1. `ingestion_runs.valid_loaded` and `quarantined_count` reflected the latest
   attempt's deltas, while the deterministic run row represents the complete
   partition. Exact head now persists durable full-partition totals
   (`canonical_after=1909`, `quarantined_after=496`,
   `processed_count=2405`) and retains per-attempt deltas in `final_cursor`.
2. A same-partition replay could refetch an admin/POI dataset whose provider
   retained the snapshot id while changing volatile fetch metadata. Exact
   head reuses the immutable dataset snapshot already attached to the
   deterministic run. If a provider reissues a snapshot id with different
   stable content outside that reuse path, the run fails closed and never
   mutates the stored snapshot.

These corrections do not claim a second production backfill. They make the
next replay deterministic and correct the durable run-row accounting when it
executes. On the exact task head, the PostgreSQL 16 integration suite proves
partial-run recovery, cumulative run-row totals, same-partition dataset
snapshot reuse, immutable-content conflict rejection, quarantine behavior,
and idempotent canonical writes:

```text
uv run pytest tests/integration/test_place_geography_backfill.py -q
............                                                     [100%]
12 passed

uv run ruff check apps/data_platform/geography_backfill.py \
  tests/integration/test_place_geography_backfill.py
All checks passed!

uv run ruff format --check apps/data_platform/geography_backfill.py \
  tests/integration/test_place_geography_backfill.py
2 files already formatted
```

## PG16 end-state verification (queried after the terminal run)

| Check | Result |
|---|---|
| `data_plane.place_geography` rows | 1909 |
| Rows with non-empty `h3_res_8`, `h3_res_9`, `h3_res_10` | 1909 (100%) |
| Distinct `h3_res_9` cells | 1443 |
| `geocode_confidence` min / avg / max | 0.4000 / 0.9309 / 0.9800 |
| Rows with confidence < 0.7 (flagged `low_geocode_confidence`) | 192 |
| Rows with NULL `observed_at` or `valid_from` | 0 |
| `observed_at` range | 2026-07-27T19:17:31Z → 2026-07-27T19:45:15Z (real call times) |
| Tenant mismatches vs `core.stores` | 0 |
| Distinct tenants with geography | 810 |
| `canonical_lineage` rows for `place_geography` | 1909 (1:1, none missing) |
| Geography rows without a provider snapshot | 0 |
| `geography_provider_snapshots` rows (immutable, verbatim payloads) | 5337 |
| Dataset snapshots | `adminboundary-20260727-8ae543f5e03e`, `poi-20260727-f7c9c3154087` |

Quarantine evidence for the run accumulates one row per distinct live
observation (content-addressed snapshot ids): 935 unresolved rows covering
496 distinct stores — the same 496 stores the terminal report counts. Earlier
executions' differing observations remain recorded, not overwritten.

### Admin-boundary vintage finding

`admin_boundary.official_dataset` serves a pre-2014 vintage snapshot
(`桃園縣`, no `桃園市`). Geocoded stores in Taoyuan therefore fail closed as
`ADMIN_BOUNDARY_MISMATCH` (the bulk of the 298): a genuine cross-provider
conflict between the live geocoder's current administrative naming and the
official dataset snapshot. Recorded in quarantine with both snapshots'
lineage; not resolved by overwriting either provider's observation.

## HeatZone 28-day label eligibility (actual counts, fail-closed)

`python -m apps.data_platform.geography_backfill inventory --model heatzone`
against the same DSN:

```json
{"contract_trainable": true, "contract_version": "heatzone-training-view-v2",
 "eligible_row_count": 0, "labeled_row_count": 0, "ready": false,
 "relation": "model_ready.heatzone_training_view", "relation_exists": true,
 "train_row_count": 0, "validation_row_count": 0, "test_row_count": 0,
 "tenant_count": 0, "temporal_min": null, "temporal_max": null}
```

Actual train/validation/test = **0/0/0 against the contract minimum of 200
rows** (`scripts/models/contracts.py`, `minimum_rows=200`). The geography
layer this task owns is complete (478 distinct stores with both geography and
transactions), but three independent upstream gates in
`model_ready.heatzone_training_view` each evaluate to zero on the real data:

1. **No qualifying authoritative order days.** All 35 `SUCCEEDED` orders
   ingestion runs fail the view's gate
   (`reconciled AND partition_complete AND quarantined_count = 0 AND
   processed_count = valid_loaded`) — every one of the 35 fails all four
   conditions.
2. **`data_plane.transaction_authority` is empty** (no `orders` authority
   rows), so no transaction can enter the label base despite 270,635
   succeeded TWD rows in `core.transactions`.
3. **`core.stores.opened_on` is NULL for all 2442 stores** and
   `canonical_lineage` has no `core.stores` rows, so `store_source`
   (which requires real `opened_on`) is empty. Inferring `opened_on` is
   forbidden by this task's acceptance; no value was fabricated.

Consequently HeatZone remains **fail-closed with zero eligible labels** —
consistent with `ODP-PRODUCTION-MODEL-REGISTRY-001`'s standing instruction
that HeatZone must not train unless real data meets contract minimums. The
three gaps above belong to the orders/store ingestion lanes
(`ODP-PRODUCTION-MODEL-DATA-BACKFILL-001` surface), not to the geography
layer; they are enumerated here so the fleet can seed follow-up work.

## Acceptance mapping

1. *Approved live provider calls with immutable snapshot and tenant lineage* —
   5337 verbatim provider snapshots + 2 dataset snapshots; every canonical row
   has 1:1 lineage and 0 tenant mismatches.
2. *Idempotent replay; conflicts quarantined, never overwritten* — four-run
   replay table above; conflict/quarantine rows accumulate as distinct
   observations; 0 canonical overwrites.
3. *Real observed/valid timestamps, geocode confidence, H3 resolution 9* —
   100% H3 8/9/10 coverage, 0 NULL timestamps, confidence recorded per row.
4. *HeatZone inventory reports actual train/validation/test counts against
   minimum 200* — 0/0/0 vs 200, `ready=false`, blockers enumerated with exact
   counts.
5. *No fixture/mock/synthetic coordinate or inferred `opened_on`* — CLI
   refuses non-live modes; all coordinates are provider-attributable via
   snapshots; `opened_on` left NULL everywhere.
6. *Independent exact-head review and PG16 integration tests* — 12/12 PG16
   integration tests pass locally; exact-head review requested from `Codex6`.
