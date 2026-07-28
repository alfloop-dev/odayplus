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
| `orders_history_gap_jobs.applied.json` | — | The exact `-s3`/`-s4`/`-s5` Job manifests applied to close the source gap (see §5). Secrets appear only as `secretKeyRef` names, never values. |

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

`-s1` did not finish; see **Defect C** below for why, and for the `-s3`/`-s4`/`-s5`
slices that supersede `-s2` and actually close the gap. Each daily partition
opens its own `data_plane.ingestion_runs` row with its own checksums, so the
backfilled days carry exactly the same run lineage as any other day.

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

## 5. Defect C — the orders-history workload cannot finish its own default window

`-s1` was killed by Kubernetes at 2026-07-28T16:26:37Z with
`reason: DeadlineExceeded`, exactly four hours after it started. It had not
failed on data: it had completed 13 of its 16 daily partitions
(`2026-06-23` .. `2026-07-05` all `SUCCEEDED`) and was mid-way through
`2026-07-06` when the deadline fired.

This is a defect in the rendered workload, not in this run.
`infra/k8s/data-platform/workloads.yaml.tpl` pairs

- `activeDeadlineSeconds: 14400` (4 h) on the orders-history Job, with
- a default window of `end - 62 days` (`render.py`, `orders_history_start`),

while an observed daily partition costs roughly 10–25 minutes. A 62-day window
therefore needs 10–25 hours and can *never* complete inside its own deadline;
any window wider than about nine partitions is structurally unable to finish.
`-s2` (15 days) would have died the same way, so it is left suspended.

The gap is instead closed by three slices derived from `-s2`'s own
last-applied manifest — same release SHA, same digest-pinned image, same
`activeDeadlineSeconds`, same service account and sidecar; only the name, the
`execution-order` / `hard-limit` annotations, and the two window env vars
differ:

| Job | Window | Partitions |
| --- | --- | --- |
| `…-93cb9f94-s3` | 2026-07-06 .. 2026-07-12 | 6 |
| `…-93cb9f94-s4` | 2026-07-12 .. 2026-07-18 | 6 |
| `…-93cb9f94-s5` | 2026-07-18 .. 2026-07-23 | 5 |

They run **sequentially, staying suspended until it is their turn**, which is a
correctness requirement rather than a politeness one. The `private-pool` node
pool holds exactly one node and does not scale up, so a second unsuspended Job
only parks its pod in `Pending` — and `activeDeadlineSeconds` counts wall-clock
from the Job's `startTime`, not from when its pod is actually scheduled. A
queued-but-unsuspended slice would spend its entire budget waiting for a node
and then be killed having done nothing. Kubernetes resets `.status.startTime`
when a suspended Job is resumed, so each slice gets a full, untouched four hours
at the moment it actually holds the node.

Corrected coverage arithmetic, measured on the source rather than assumed: the
contiguous run is `2026-05-23 .. 2026-07-05` (44 days) plus
`2026-07-23 .. 2026-07-27`, leaving the true gap at `2026-07-06 .. 2026-07-22`
(17 partitions). Closing it yields one contiguous span
`2026-05-23 .. 2026-07-27` = 66 days → 39 eligible days → 12 h28 windows per
store, which clears the 28-day bar.

### Settling condition

> **Superseded by §6.** Condition 2 below assumed re-running a partition always
> re-points an abandoned run's lineage. That holds only when the abandoned run
> committed nothing; see Defect D for the measured counter-example and the
> repair it requires.

A Job killed by `activeDeadlineSeconds` leaves its in-flight
`data_plane.ingestion_runs` row at `RUNNING` **permanently** — nothing
transitions it, and re-running the partition opens a *new* run rather than
resurrecting the old one. So "the source is settled" cannot mean "zero
non-terminal runs"; that condition never becomes true again. The two conditions
that actually matter before activation are:

1. every partition holding a non-terminal run also holds a `SUCCEEDED` run, and
2. no non-terminal run still **owns** `canonical_lineage` rows.

Condition 2 is the one with teeth, and it ties directly back to Defect B:
`source_run_complete` is a `bool_and` over a transaction's runs, so an abandoned
run that still owns lineage poisons its day's eligibility. Re-running the
partition re-projects that lineage onto the run that finished, dropping the
abandoned run to zero owned rows — which is precisely how the earlier
`2026-06-23` casualty healed (run `3d0937f1` now owns 0 lineage rows on the
source, down from the 1 839 it held). Until that re-projection lands, activating
would only copy the poison forward.

## 6. Defect D — a resumed partition leaves unreconciled lineage that no re-run can repair

The settling condition in §5 assumed that re-running a partition re-points the
abandoned run's lineage. **Measured against the source, that is only true when
the abandoned run committed nothing.** It does not hold in general, and it did
not hold here.

Compare the two casualties on `data_plane.ingestion_runs`:

| Run | Partition | Status | `processed_count` | Lineage owned |
| --- | --- | --- | --- | --- |
| `3d0937f1` | `2026-06-23__2026-06-24` | `RUNNING` (abandoned) | 0 | 0 |
| `85294064` | `2026-06-23__2026-06-24` | `SUCCEEDED` | 6 472 | all |
| `069b0984` | `2026-07-06__2026-07-07` | `RUNNING` (abandoned) | 0 | **4 752** |
| `5efc0a7d` | `2026-07-06__2026-07-07` | `SUCCEEDED` | 2 721 | 2 721 |

`3d0937f1` was killed before it committed a single batch, so the re-run rebuilt
the day from nothing and the partition healed on its own. `069b0984` was killed
by the §5 deadline *after* committing 4 752 batches, and the resuming run
`5efc0a7d` picked up from its checkpoint — finishing the partition in 52 seconds
because it only had 2 721 records left to read.

Those 4 752 lineage rows can never be re-pointed by re-running the partition.
`canonical_lineage` is written with
`ON CONFLICT (source_snapshot_id, canonical_table, canonical_id) DO NOTHING`, and
`source_snapshot_id` is `snapshot_id_for_content(kind, source_id, content_sha256)`
— derived from content, so an unchanged record re-read from the source produces
the *same* key and the insert is silently discarded, preserving the original
`run_id`. There is no `DELETE` against `canonical_lineage` anywhere in
`apps/data_platform/`. The attribution is immovable by any supported operation.

Measured blast radius, by `event_time` date:

| Date | Poisoned `core.transactions` |
| --- | --- |
| 2026-07-05 | 4 |
| 2026-07-06 | 4 749 |

Four transactions are enough to disqualify 2026-07-05 outright, because
`source_run_complete` is a `bool_and`. That severs the contiguous span at
2026-07-05/07-06 and caps continuous coverage at 44 days — an h28 window needs
56, so the 28-day acceptance bar becomes unreachable no matter how many of the
remaining partitions land. Healing this partition is mandatory, not cosmetic.

### Why the view is not the thing to change

It is tempting to redefine `source_run_complete` in terms of the *partition*
rather than the individual run — the resume design does deliberately build one
partition out of several runs, so the current `bool_and` is arguably modelling
the wrong unit.

That would be wrong here. Reconciliation runs at the end of a run, over the
checksums that run accumulated: `5efc0a7d` reconciled its own 2 721 records and
nothing else. **No run ever reconciled the 4 752.** Relaxing the view would
admit records whose source/raw/canonical checksums were never compared to
anything, which is precisely the "mark immature data eligible" outcome this
task's acceptance forbids. The strictness is load-bearing; the data is what is
wrong.

This is a genuine platform defect worth its own follow-up: on a resumed
partition, the records committed by the earlier run are never reconciled by
anyone. Either the resuming run should reconcile the whole partition, or it
should re-attribute the prior run's lineage to itself.

### The repair

Make partition `2026-07-06__2026-07-07` genuinely ingested and reconciled by a
single run, rather than laundering the existing rows:

1. Back up the abandoned run's lineage, the run row, and the partition
   checkpoint (`/tmp/odp-lineage-backup-069b0984.json`, 4 752 rows).
2. Delete the 4 752 `canonical_lineage` rows owned by `069b0984` and the
   partition's `checkpoints` row.
3. Re-run the partition, which — with no checkpoint to resume from — re-reads
   the full day from the authoritative upstream, re-projects every record, and
   reconciles all ~7 473 in one run.

Nothing is fabricated: every re-created row is re-derived from the system of
record. `core.transactions` itself is not deleted, only re-projected through the
same idempotent upsert. The step is reversible in both directions — the backup
restores the prior state exactly, and a failed re-run leaves the affected
transactions merely lineage-less (already ineligible, no worse) and re-runnable.

## 7. After state

Populated once `-s3`/`-s4`/`-s5` reach `Complete`, the source meets the settling
condition above, and activation runs. See
`activation_receipt.json`, `verify_after.json`, and `inventory_after.json`.

Activation is deliberately **not** run while any `orders` partition is still
`RUNNING`: the `refresh_key` step would faithfully mirror that non-terminal
status into the target, reproducing Defect B from the other direction.
