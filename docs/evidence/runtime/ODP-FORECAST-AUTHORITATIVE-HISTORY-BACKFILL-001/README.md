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
| `lineage_repair_plan.json` | `plan` | Read-only scope of the §6 lineage repair, from `repair_unattested_lineage.py` |
| `unattested_lineage_sweep.json` | — | Sweep of **every** non-terminal run in the source, classified, proving the §6 repair scope is exactly one run. Carries its own `sweep_sql`. |
| `attestation_coverage_after_s3.json` | — | Per-day attestation measured **after** `-s3` landed 2026-07-06..07-11, turning §6's 44-day cap from a projection into a measurement. Carries its own SQL. |
| `upstream_source_depth_probe.json` | — | Read-only measurement of how far back the **authoritative upstream** actually goes, proving the landed 2026-05-23 floor is a window-clamp artifact (see §7). Carries its own predicate and redaction statement. |
| `source_depth_probe.pod.yaml` | — | The exact read-only Pod that produced it. Aggregates only; no write path. |
| `orders_history_backfill_jobs.applied.json` | — | The `-b1`/`-b2`/`-b3` backwards-extension Job manifests that close §7 without any destructive action. Secrets appear only as `secretKeyRef` names, never values. |

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

#### The 44-day cap, measured rather than projected

The paragraph above was written before any gap partition had landed, so its
44 was derived from the source's date span. `attestation_coverage_after_s3.json`
re-derives it from ingested data, after job `-s3` backfilled
2026-07-06..2026-07-11 for real. It evaluates the view's own predicate —
`bool_and(status = 'SUCCEEDED' AND finished_at IS NOT NULL)` over
`canonical_lineage` rows for `core.transactions` — per day, and separates
blocking runs into *in flight* (partition has no `SUCCEEDED` run yet, so it will
settle on its own) and *permanent*, using the same rule as the sweep.

| Scenario | Longest contiguous attested span |
| --- | --- |
| After the in-flight run settles, Defect D unrepaired | **44 days** (2026-05-22 .. 2026-07-04) |
| After the in-flight run settles, Defect D repaired | **51 days** (2026-05-22 .. 2026-07-11) |

The measured 44 matches the projected 44 exactly. The repaired figure is 51 and
not 66 only because `-s4`/`-s5` had not yet landed 2026-07-12..2026-07-22 at
capture time; those partitions join the 51 to the already-attested
2026-07-23..2026-07-27 tail.

Two further results fall out of the same capture, both worth stating because
they are the cheap ways this diagnosis could have been wrong:

- The permanent holes are exactly `2026-07-05` and `2026-07-06` — no other date
  on the acceptance surface is blocked by a run that cannot settle. Defect D is
  the sole permanent obstacle, not one of several.
- Every landed day has `lineage_complete = true`. The backfill mechanism itself
  is sound: `-s3`'s six partitions attested cleanly, which is what distinguishes
  "the ingestion path is broken" from "one interrupted run left a scar".

So the repair is not merely mandatory in principle; with the rest of the
pipeline now demonstrably working, it is the only remaining blocker between this
task and acceptance criterion 3.

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

### Why the ingestion code is not the thing to change either — in this task

The follow-up above is the correct durable fix, and the narrower half of it
(`_lineage` in `apps/data_platform/store.py` upserting `run_id` when the
incumbent run is non-terminal, instead of `DO NOTHING`) would make a re-ingest
heal this partition with no delete at all. It is still not the move here, for a
reason that is structural rather than aesthetic.

The backfill does not run this checkout. Every `orders-history` Job in §5 is
pinned by digest —
`asia-east1-docker.pkg.dev/.../data-platform@sha256:f60383b6…` in all three
applied manifests — so the code that writes `canonical_lineage` is whatever was
built at that release SHA. Changing `store.py` on this task branch would change
nothing about what `-s6` does; it would first have to merge, rebuild, republish,
and re-pin, and only then could the partition be re-ingested. That is a release
cycle standing between this task and its own acceptance bar, on a shared
ingestion path used by every pipeline, to repair 4 752 rows in one of them.

The delete is the smaller change precisely because it is not a change: it runs
against the data, under a tested tool, with a verified restore, and leaves the
platform's behaviour for every other pipeline exactly as the release SHA defines
it. The code fix belongs in the follow-up, where it can be reviewed as the
platform change it actually is.

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

Step 3 is job `-s6` (`2026-07-06..2026-07-07`, already created and suspended,
§5 topology). It re-reads the whole day because step 2 removes the checkpoint:
`Pipeline.run_partition` resumes from `data_plane.checkpoints` and the job
entrypoint exposes no way to disable that, so the checkpoint deletion is what
makes the re-ingest complete rather than another 52-second no-op.

### The repair is a reviewable tool, not an ad-hoc statement

Steps 1–2 are implemented as `scripts/data_plane/repair_unattested_lineage.py`
so the delete is bounded by tested guards instead of operator care. It refuses,
fail-closed, to touch:

| Guard | Refuses when | Why |
| --- | --- | --- |
| terminal status | run is `SUCCEEDED`/`FAILED` | that lineage is exactly what the view is meant to trust |
| reconciliation values | `finished_at`, or any of the three checksums, is non-null | a reconciliation is an attestation even without a terminal status |
| reported progress | `processed_count` or `valid_loaded` > 0 | same |
| modelled scope | run owns lineage for any table other than `core.transactions` | one partition re-ingest would not rebuild it, so the delete would be one-way |
| known window | run has no `partition_key` | there would be nothing to re-ingest |

`plan` is the default and read-only; `apply` additionally requires
`--confirm-run-id` to repeat the run id and a `--backup` path that is written
**and re-read** before a single row is deleted; the lineage delete and the
checkpoint delete share one transaction, and the delete is rolled back if its
row count disagrees with the plan. `restore` re-inserts the backup.

The module deliberately offers no way to re-point lineage at another run and no
way to mark a run terminal. Both would make the view report an attestation that
never happened — the same failure as relaxing the view, just written elsewhere.

`lineage_repair_plan.json` is the read-only plan for `069b0984`, taken against
the live source: 4 752 lineage rows and 1 checkpoint to delete, and **4 752
transactions left with no lineage at all** until `-s6` re-attests them. That
number is reported rather than smoothed over, because it means the delete widens
the outage until the re-ingest lands.

### The repair scope is one run, and that was verified rather than assumed

Defect D was found while investigating a single partition, so "only `069b0984`
is affected" started as an artefact of where the investigation happened to look.
`unattested_lineage_sweep.json` re-derives it independently: it sweeps **every**
non-terminal run in the source database — not just the orders backfill — and
classifies each one, so a second instance cannot hide behind the known one.

Twelve non-terminal runs exist. Each is resolved by two questions:

| Verdict | Runs | Basis |
| --- | --- | --- |
| `defect_d_permanent` | `069b0984` (07-06, 4 752 rows) | owns `core.transactions` lineage **and** its partition already has a SUCCEEDED successor, so nothing will ever transition it |
| `in_flight` | `7c71ab3d` (07-09) | owns `core.transactions` lineage but its partition has no SUCCEEDED run yet — it is the `-s3` slice still executing, and settles on its own |
| `not_applicable` | ten runs | own no lineage at all (self-healed, e.g. `3d0937f1`), or own lineage for another canonical table |

The `not_applicable` group includes one run worth naming, because it looks
alarming and is not: `87423a49` (partition `2026-07-26__2026-07-27`) is a
permanently `RUNNING` run holding **20 270** lineage rows. It is out of scope on
the gate's own terms — its lineage is for `data_plane.forecast_inputs` from
`ai_revenue_stats`, and `forecast_training_view` joins lineage only
`WHERE canonical_table = 'core.transactions'`. It is pre-existing damage from
the 07-27 nightly pipeline, unrelated to this backfill, and this task does not
repair it. It is recorded here so a reviewer does not have to rediscover it, and
because it is the same defect class in a different pipeline.

The permanence test above is the same one the finisher gate uses, so the
evidence and the automation cannot disagree about what is settled. That gate is
also scoped to `source_kind = 'orders'`, which is what keeps `87423a49` from
deadlocking the finisher forever on a defect outside this task.

### The delete is reversible, and that was checked rather than asserted

The approval this repair is parked on is easier to give if the downside is
bounded, so the rollback path was verified before asking for it — not after.

The `apply` backup already exists at `/tmp/odp-lineage-backup-069b0984.json`
(2.4 MB, taken 16:41Z). It is full-fidelity rather than a summary:

| Backup section | Contents |
| --- | --- |
| `lineage` | 4 752 rows carrying **all nine** `data_plane.canonical_lineage` columns, including `source_snapshot_id`, `content_sha256` and `projected_at` |
| `checkpoints` | the 1 partition checkpoint row |
| `run` | the `069b0984` `ingestion_runs` record itself |

Because `source_snapshot_id` and `content_sha256` are captured verbatim, a
`restore` reproduces the exact primary keys that were deleted — the restored
rows are the original rows, not equivalent ones. `restore` is covered by
`test_restore_rejects_an_empty_backup`, so the one way a restore could silently
no-op (an empty or truncated backup) is refused rather than reported as success.

This does **not** make the delete safe to run unreviewed — it makes the blast
radius recoverable if the re-ingest behaves unexpectedly. The irreversible part
was never the rows; it is that the source database is shared, so the window
between the delete and `-s6` completing is visible to anything else reading it.

**Status: parked, and no longer on the critical path.** Deleting governed lineage
from the shared source database is a destructive, human-gated action. It has not
been executed and, as of section 7, it does not need to be: the 28-day window is
reachable without it. The plan below stays on file because the two lost days are
still genuinely lost, but nothing in this task now waits on it.

Re-planned at 17:19Z against the live source and the scope is unchanged — still
4 752 lineage rows and 1 checkpoint, still exactly one run. The plan is stable,
so approval does not need to be re-scoped if it ever arrives.

## 7. The 44-day cap was never a ceiling — the source floor is self-inflicted

Sections 5 and 6 concluded that criterion 3 was unreachable: the longest
contiguous attested span is 44 days (`2026-05-22..2026-07-04`), an h28 window
needs 56, Defect D permanently costs `2026-07-05` and `2026-07-06`, and the only
repair is human-gated. Every one of those measurements is correct. The
**conclusion drawn from them was wrong**, because all of them were taken against
*landed* data and none of them asked what the authoritative source actually
holds.

It holds about two years.

| | Landed (`fongniao_raw.raw_orders`) | Upstream (`fongniao_prod.orders`) |
| --- | --- | --- |
| Documents | 478 265 | **2 170 979** |
| Earliest | `2026-05-23T00:00:02.915Z` | **`2024-06-26T13:33:27.431Z`** |

Measured read-only in-cluster by `source_depth_probe.pod.yaml`, receipt in
`upstream_source_depth_probe.json`.

### Why the floor is an artifact and not a boundary

Three independent facts, any one of which is suggestive and which together are
conclusive:

- `min(source_updated_at)` is `2026-05-23T00:00:02.915Z` — **2.9 seconds** after
  the `2026-05-23T00:00:00Z` lower bound of the first orders-history window. A
  real data boundary does not land 2.9 s past a requested bound; a clamp does.
- `min(observed_at)` is `2026-07-24T18:37Z`. Every raw row in the table was
  landed by the 07-24 job or later. There is no older ingestion to have found
  older data.
- `render.py` renders `ODP_ORDERS_HISTORY_START` as `end - 62 days`, and the
  first job ran with `end = 2026-07-24`. `2026-07-24 - 62d = 2026-05-23`,
  exactly. The floor is the default window.

Nothing older than 2026-05-23 has ever been requested. `_window_query` filters
Mongo on `$or(updatedAt, createdAt)` with no lower clamp of its own, and
`_orders_history_window` validates only that the window is ≤ 62 days and sits on
UTC day boundaries — there is no floor anywhere in the path. The history was
always available; nobody asked for it.

The days immediately below the floor are dense and continuous, not a sparse
tail: 6 307–11 011 orders/day across `2026-05-14..2026-05-22`, 179 503 orders in
`2026-05-01..2026-05-23`, and 115 k–252 k per month back through at least
2025-11.

### Why this reaches criterion 3 without the section 6 delete

A date `D` is training-eligible only if `D` is attested **and** rows exist for
`D-28..D-1` (`prior_day_count_28 = 28`). So `N` contiguous attested days yield
`N - 28` eligible dates, which is where the 56-day requirement comes from.

Sections 5 and 6 treated the Defect D split as something to be *bridged*, which
requires the delete. It can instead be **left alone**: the attested span below
the split does not have to reach across `07-05`/`07-06`, it only has to get
longer at the other end, which is pure governed ingestion of real records.

| | Attested span below the split | Eligible dates | h28 |
| --- | --- | --- | --- |
| Today | `2026-05-23..2026-07-04` (~44 d) | ~15 | no |
| After `-b1` | `2026-05-17..2026-07-04` (49 d) | 21 | no |
| After `-b2` | `2026-05-11..2026-07-04` (55 d) | 27 | no |
| **After `-b3`** | **`2026-05-05..2026-07-04` (61 d)** | **33** | **yes** |
| After `-b4` | `2026-04-29..2026-07-04` (67 d) | 39 | yes, with margin |

`-b1` = `2026-05-17..2026-05-23`, `-b2` = `2026-05-11..2026-05-17`,
`-b3` = `2026-05-05..2026-05-11`, `-b4` = `2026-04-29..2026-05-05` — **six**
daily partitions each, not eight, and each derived verbatim from the `-s3`
manifest exactly as `-s3`/`-s4`/`-s5` were derived from `-s2`. Manifests in
`orders_history_backfill_jobs.applied.json`; the applied windows are readable
back off the cluster from each Job's `ODP_ORDERS_HISTORY_START`/`_END`.

Slice width is a **correctness** decision, not a throughput one. `-s3`'s six
partitions measured 1/10/10/25/37/35 minutes — 118 total, 37 worst case. Six
worst-case partitions is 222 min against the 240 min `activeDeadlineSeconds`;
eight would be 296 and would reproduce Defect C. A deadline kill is not a
harmless retry, it is exactly how Defect D's permanently unattested lineage was
created, so each slice must fit its own deadline with margin.

That sizing survives on 18 minutes of headroom, which is not a margin, it is a
coin flip — and `-s4`'s first partition landed at 32 min against `-s3`'s 37 min
ceiling, so the ceiling is not falling as the slices move into heavier dates.
So the deadline itself was raised, on the six task-scoped slices still to run
(`-s4`, `-b1`..`-b4`, `-s5`), from `14400` to `28800`; receipt in
`slice_deadline_headroom.json`. `activeDeadlineSeconds` is one of the few
mutable Job spec fields, and the patch was accepted on the **running** `-s4`
with no pod disruption — same pod, `RESTARTS 0`, age unbroken across the patch.
Raising a kill timer cannot alter, delete, or admit a single record: it changes
no window, no image digest, and no ingestion semantics, only how long a slice
may take to finish work it was already going to do, and it is reversible by
patching the value back. `28800` is still a bound, not an absence of one — six
partitions at the 37 min ceiling is 222 min against 480, so a genuinely hung run
is still caught.

`-s1`, `-s2`, `-s3` and the parked `-s6` were deliberately left at `14400`:
`-s1` is the preserved Defect C exhibit and `-s3` the measured baseline the
sizing argument rests on, and rewriting them would erase the evidence.

The raised deadline does **not** buy wider slices. Six partitions stays. The
width was chosen so a slice fits its own budget and it still does — now with
headroom rather than without — and spending that headroom on eight partitions
would rebuild exactly the Defect C shape this is meant to retire.

`-b4` is not redundant margin. The arithmetic above is a **global** span, while
`prior_day_count_28` is evaluated per store: a store that transacts on no day in
the window produces no row and breaks its own streak, so per-store eligibility
trails the global span. Section 7.1 measures how far.

This is the same governed ingestion path, the same digest-pinned release image,
and the same immutable-snapshot-plus-run-lineage contract as every other slice.
No fixture, no synthetic row, no relaxation of the view predicate, no write of
any kind against the source database, and no human-gated destructive action.

### What Defect D still costs

The two days `2026-07-05` and `2026-07-06` remain permanently unattested and are
still correctly excluded by the view as `SOURCE_RUN_NOT_COMPLETE`. The task no
longer *depends* on repairing them, but the defect is real, the repair plan in
section 6 stays on file, and the cost is now bounded and stated: the training
window ends at `2026-07-04` rather than running through the 07-23..07-27 tail.

### The finisher gate had to change, and this is why that is not a weakening

The finisher's settling gate (`runbook/forecast-finisher-v5.sh`) previously had
two clauses: **(a)** every partition with a non-terminal run also has a
`SUCCEEDED` run, and **(b)** no non-terminal run still *owns*
`canonical_lineage` rows. Clause (b) was written while the plan was still to
repair Defect D, where "a dead run still owns lineage" correctly meant "the
repair has not landed yet, do not activate".

With the repair off the critical path, clause (b) became a **permanent
deadlock**: run `069b0984` is dead, keeps its 4 752 lineage rows forever, and
nothing will ever transition it. The finisher would have waited for a condition
that cannot occur.

Clause (b) was never a safety property. What makes activation unsafe is a
partition still **mid-flight**, because `refresh_key` would mirror a genuinely
in-progress `RUNNING` status into the target and reproduce Defect B from the
other direction. A permanently abandoned run is not mid-flight — it is settled,
just settled badly. Its days are *already* excluded by the view as
`SOURCE_RUN_NOT_COMPLETE`, and blocking activation does not make them eligible;
it only prevents ever producing evidence about the days that are.

So v5 replaces clause (b) with a direct and **stronger** test of what clause (b)
was standing in for: no orders Job is `Active` in the cluster, measured against
Kubernetes rather than inferred from SQL. Clause (a) is kept unchanged, because
"a killed partition was never re-run" is real incompleteness and must still
block. Clause (b)'s measurement is not discarded — it is **demoted to a logged
fact**, so the finisher reports how much lineage is still owned by abandoned
runs and the cost is stated in the evidence rather than hidden behind a gate
that never opens.

### Operational note

The upstream is MongoDB Atlas and its IP allowlist admits only the
**private-pool** node pool's Cloud NAT egress. An identical probe pinned to
`default-pool` — which had spare capacity — failed with
`ServerSelectionTimeoutError` against all three replica-set members. Anything
that needs to reach the source must share the single private-pool node with the
running backfill slice, which is why the probe requests 10 m CPU / 64 Mi: while
a slice is active that node sits at ~96 % of allocatable memory, and a probe
that could not fit would sit `Pending` and delay the very slice it was written
not to disturb.

## 8. After state

Populated once `-s3`/`-s4`/`-s5` reach `Complete`, the source meets the settling
condition above, and activation runs. See
`activation_receipt.json`, `verify_after.json`, and `inventory_after.json`.

Activation is deliberately **not** run while any `orders` partition is still
`RUNNING`: the `refresh_key` step would faithfully mirror that non-terminal
status into the target, reproducing Defect B from the other direction.
