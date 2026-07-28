# ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001 — runtime evidence

Backfill authoritative ForecastOps daily history so the canonical PostgreSQL 16
`model_ready.forecast_training_view` produces complete per-store horizon windows
from real source records only.

No fixture, synthetic, seed, spine, or auto-generated row is introduced anywhere
in this task. Every transaction that reaches the target is a row that already
existed in the approved legacy PostgreSQL source, which is itself populated
exclusively by the governed Mongo-to-PostgreSQL data plane (`apps/data_platform`).

## Redaction

Every artifact in this directory is produced by
`scripts/data_plane/forecast_history_activation.py`, which emits **aggregates
only** — counts, distinct counts, min/max dates, and status/reason breakdowns.
No tenant id, store id, machine id, transaction id, order payload, or free-text
field is read into a receipt. The redaction is structural, not a post-processing
step: there is no code path in the module that selects an identifying column
into its output.

## Artifacts

| File | Mode | Meaning |
| --- | --- | --- |
| `inventory_before.json` | `inventory` | Source + target state captured before activation |
| `activation_receipt.json` | `activate` | Per-relation copy/refresh/prune counts of the activation run |
| `verify_after.json` | `verify` | Target horizon windows after activation |
| `inventory_after.json` | `inventory` | Source + target state captured after activation |

Reproduce any of them with the DSN pair and the Cloud SQL proxy attestation in
the environment:

```
python3 -m scripts.data_plane.forecast_history_activation inventory \
  --horizons 7,14,28,56,84,168 --output <path>
```

## 1. Authoritative transaction source catalog

Five authoritative relations back a ForecastOps transaction. All five are
present and readable on the source; the inventory reports each one rather than
assuming it.

| Layer | Relation | Source state at capture |
| --- | --- | --- |
| Raw landing | `fongniao_raw.raw_orders` | 392 562 rows, 392 562 distinct content hashes, 45 distinct runs, source updates 2026-05-23 → 2026-07-28 |
| Governed runs | `data_plane.ingestion_runs` | 131 runs, broken down by `source_kind` × `status` |
| Quarantine | `data_plane.quarantined_records` | Non-zero and reported by `source_kind` × `reason_code` (largest: `orders`/`MISSING_REQUIRED_FIELD`, 17 003) |
| Transaction authority | `data_plane.transaction_authority` | 373 678 snapshots, all at `authority_rank` 1, `source_kind` `orders` |
| Canonical lineage | `data_plane.canonical_lineage` | 638 740 rows total; 373 808 pointing at `core.transactions` across 45 runs |
| Canonical transactions | `core.transactions` | 373 889 rows, 338 170 forecastable, 597 forecastable stores, 547 tenants, 2026-05-22 → 2026-07-27 |

The three source counts for the same underlying records differ slightly
(`core.transactions` 373 889 in the catalog vs 373 165 in the relation census,
373 808 lineage rows, 373 678 authority snapshots). That skew is expected: the
inventory was taken while the backfill was actively writing, and each aggregate
is read in its own statement rather than under one frozen snapshot. The
after-state inventory is taken with no partition in flight.

The raw-landing row count exceeding the canonical count is expected and healthy:
raw records that fail validation land in quarantine instead of being projected,
and the quarantine table accounts for the difference explicitly.

`distinct_machines` is 0 on both sides. That is a property of the current
`orders` projection, not a coverage defect, and it is reported rather than
silently normalised away.

## 2. Before state

Captured 2026-07-28T14:27Z into `inventory_before.json`.

### Temporal coverage

| | Source | Target |
| --- | --- | --- |
| Distinct transaction dates | 46 | 37 |
| Range | 2026-05-22 → 2026-07-27 | 2026-05-22 → 2026-07-26 |
| Calendar gaps | `2026-07-01..2026-07-23` | `2026-06-23..2026-07-23` |
| `core.transactions` | 373 165 | 298 599 |
| `data_plane.canonical_lineage` | 638 740 | 300 508 |
| `data_plane.ingestion_runs` | 131 | 37 |

### Target forecast eligibility

`model_ready.forecast_training_view` held 19 141 rows over 434 stores and 547
tenants, of which only 1 303 were training-eligible, spanning
2026-06-19 → 2026-06-22 — a longest eligible streak of **4 days**.

| Horizon | Complete windows | Stores with a complete window |
| --- | --- | --- |
| h7 | 0 | 0 |
| h14 | 0 | 0 |
| h28 | 0 | 0 |
| h56 | 0 | 0 |
| h84 | 0 | 0 |
| h168 | 0 | 0 |

Exclusion reasons:

| Reason | Rows |
| --- | --- |
| `ELIGIBLE` | 1 303 |
| `INSUFFICIENT_28_DAY_HISTORY` | 17 385 |
| `SOURCE_RUN_NOT_COMPLETE` | 453 |

Two distinct defects are visible in that table, and they need different fixes.

## 3. Defect A — the source itself was missing 23 days

`2026-07-01..2026-07-23` was absent from the **source**, so no copy operation
could have produced it. This is a real data gap, and the only governed way to
close it is to re-run the approved ingestion pipeline over those dates.

It is closed by the existing GKE workloads on release
`93cb9f94ca05818dda5eda1c08d7ab5351d4adc0`, which re-ingest from the upstream
system of record one daily partition at a time, writing through the same
raw → validate → quarantine → project → lineage path as scheduled production
ingestion:

- `oday-data-platform-orders-history-93cb9f94-s1` — 2026-06-23 .. 2026-07-09
- `oday-data-platform-orders-history-93cb9f94-s2` — 2026-07-09 .. 2026-07-24

No job was created or edited for this task; `spec.suspend` is the only field
touched, and only to release `-s2` after `-s1` reaches `Complete`. Each daily
partition opens its own `data_plane.ingestion_runs` row with its own checksums,
so the backfilled days carry exactly the same run lineage as any other day.

The 28-day acceptance bar needs more continuous history than it first appears:
eligibility itself requires `prior_day_count_28 = 28`, so an h28 window needs 56
consecutive covered days. Closing the gap yields continuous coverage across
2026-05-22 → 2026-07-27, which clears that bar; either job alone does not.

## 4. Defect B — the activation copy could never converge (`SOURCE_RUN_NOT_COMPLETE`)

453 rows were excluded for a reason that no amount of backfilling would fix, and
this is the defect this task's code change repairs.

The PG15 → PG16 activation copies every relation with `ON CONFLICT DO NOTHING`.
That is correct for immutable records and makes replay idempotent, but it has a
structural consequence: **a row that already exists in the target can never be
revisited**. Two failure modes follow.

1. A row copied while the source was mid-flight is frozen at that intermediate
   state forever. Target `data_plane.ingestion_runs` held run `3d0937f1`
   (partition `2026-06-23__2026-06-24`) at status `RUNNING`, captured from a copy
   taken while that run was still executing. The source had long since finished.
2. A row the source has superseded survives in the target as a stale pointer.
   That abandoned run still owned 1 839 `core.transactions` lineage rows which
   the source had re-projected under the run that actually completed.

`forecast_training_view` computes `source_run_complete` as a `bool_and` over all
of a transaction's runs, so a single stale pointer to a non-terminal run holds an
entire day out of eligibility permanently.

The fix (commit `10c0b78e`) adds two source-driven convergence steps to
`copy_relation`, both of which write only values the source holds at that moment:

- **`refresh_key`** — re-reads the remaining columns of an already-present target
  row from the source. Declared for `data_plane.ingestion_runs` only, whose
  lifecycle legitimately advances in place toward a terminal status. Core
  relations stay strictly insert-only.
- **`prune_superseded_by`** with a fail-closed **`prune_keep_key`** — deletes a
  target lineage row that no longer exists in the source selection, but *only*
  when the source still holds another lineage row for the same canonical record.
  Declared for `data_plane.canonical_lineage` only. The keep-key guard is what
  makes the prune safe: it can re-point lineage at the run that superseded it,
  and it can never strip the last lineage a record has. `Relation.__post_init__`
  rejects a prune declared without its keep-key, so the guard cannot be omitted
  by mistake.

Regression coverage lives in `tests/unit/test_forecast_history_activation.py`.

## 5. After state

Populated once both GKE partitions reach `Complete` and activation runs. See
`activation_receipt.json`, `verify_after.json`, and `inventory_after.json`.

Activation is deliberately **not** run while any `orders` partition is still
`RUNNING`: the `refresh_key` step would faithfully mirror that non-terminal
status into the target, reproducing Defect B from the other direction.
