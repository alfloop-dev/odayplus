# ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001 — runtime evidence

Backfill authoritative ForecastOps daily history so the canonical PostgreSQL 16
`model_ready.forecast_training_view` produces complete per-store horizon windows
from real source records only.

No fixture, synthetic, seed, spine, or auto-generated row is introduced anywhere
in this task. Every transaction that reaches the target is a row that already
existed in the approved legacy PostgreSQL source, which is itself populated
exclusively by the governed Mongo-to-PostgreSQL data plane (`apps/data_platform`).

**This claim is tested — see §10.** Target rows with no primary key in the
source: zero, across all six copied `core` relations
(`canonical_row_drift_audit.json`). The same audit found the converse failing:
16 953 target rows had drifted from the source they were copied from, including
a transaction the source records as `refunded` at 0.00 that the target still
reported as `succeeded` at 230.00 and the view still counted as revenue. That is
Defect F, and it is fixed.

## Redaction

Receipts carry **aggregates only** — counts, distinct counts, min/max dates, and
status/reason breakdowns. No tenant id, store id, machine id, transaction id,
order payload or free-text field is published. Run ids and partition keys are
published deliberately: they identify ingestion work, not a customer, and the
evidence is unreadable without them.

**This claim is tested, and testing it found a violation.** The section used to
say the redaction was structural — that every artifact came from
`scripts/data_plane/forecast_history_activation.py`, which has no code path
selecting an identifying column. Both halves were wrong. Most artifacts here are
written by probes under `runbook/`, not by that module, and one of those probes
was publishing raw ids: `eligibility_model_fidelity.json` carried 10 tenant ids
and 10 store ids verbatim in its two illustrative sample fields. The structural
argument was true of the module it described and simply did not cover the files
it was claiming to cover.

`runbook/evidence-redaction-audit.py` now checks the promise instead of
restating it. It extracts every identifier-shaped token from every committed
file and classifies each one against the database — membership in
`core.stores` / `core.tenants` / `core.machines` / `core.transactions` /
`data_plane.ingestion_runs`, rather than resemblance, because store ids and the
run ids the policy publishes are both UUIDs and no pattern can tell them apart.
Hits are reported as salted fingerprints, never as values, so the audit cannot
leak what it is auditing. The `run_id` class is the control: it is allowed, it
is known to be present, and it must come back non-zero, since a clean report
from an audit that cannot find an identifier at all would prove nothing.

Current state, `evidence_redaction_audit.json`: 49 files scanned, 14
identifier-shaped tokens, **all 14 classified as `run_id`** — 0 leaked, 0
unclassified, control meaningful. The probe now fingerprints its samples at
source, and the already-committed receipt was rewritten in place by
`runbook/redact-fidelity-sample.py`, which records the transformation inside the
file rather than applying it silently.

Two limits stated rather than glossed. The raw values **remain in this branch's
git history**, in the commits that first added that receipt; removing them means
rewriting history on a pushed branch, which is a human's call and is flagged
here rather than done. And the audit covers UUID-shaped identifiers:
`core.transactions.member_id` is a varchar and unpopulated in this data, and
monetary amounts are not an identifier set — neither is testable this way and
neither is claimed.

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
| `per_store_streak_headroom.json` | — | Per-store transacting-day runs measured over the landed span, proving per-store eligibility tracks the global span rather than trailing it (see §7). Carries its own predicate and window. |
| `slice_deadline_headroom.json` | — | Why `activeDeadlineSeconds` was raised 14400→28800 on the remaining slices, and the measurement that forced it. |
| `lineage_projection_throughput.json` | — | What actually governs a partition's wall-clock: lineage projection is ~90 % of it, at a rate that has fallen to 209 rows/min. Turns slice sizing from a tripwire into a projection, and shows the raised deadline has only ~18 % throughput margin (see §7). |
| `runbook/lineage-throughput-probe.py` | — | The read-only probe that produced it. Two aggregate `SELECT`s; excludes resumed stub partitions and says how many it dropped. |
| `horizon_critical_path.json` | — | Which remaining slice actually decides criterion 3, measured against the view's own eligibility rule: the gap-fill family moves h28 from 0 to 2, the backwards family to 419 (see §7). Captured while `-s4` was still projected; `horizon_critical_path_after_s4.json` re-measures it at 406 with `-s4` real. |
| `runbook/horizon-critical-path-probe.py` | — | The read-only probe that produced it. Mirrors `forecast_training_view` on the source plane; projections are bracketed and labelled as projections. |
| `horizon_critical_path_after_s4.json` | — | The same probe re-run once `-s4` was real rather than projected. The backwards family now reads h28 = 406 against the gap-fill family's 2, and `landed_measured` carries the first real 28-day window (see §7). |
| `donor_projection_backtest.json` | — | The donor rule behind those projections, scored against a blind holdout — the four dates `-s4` landed after the projection was cached. Per-date recall/precision, the continuity score island length actually depends on, and the attestation assumption (see §7). |
| `runbook/donor-projection-backtest.py` | — | The backtest that produced it. Imports the donor logic from the probe under test rather than restating it; read-only, and it scores no in-flight slice. |
| `backwards_landing_validation.json` | — | The backwards projection scored against backwards dates as they land, closing the distance limit the `-s4` backtest leaves open. First committed at zero scored dates — the method and its guards fixed before any backwards date could be scored; now all 6 of `-b1`'s scoreable dates plus `-b2`'s first (2026-05-11), 0 upper-bound breaches (see §7). |
| `runbook/backwards-landing-validation.py` | — | The probe that produces it. **Re-run after each backwards slice completes**; it strengthens monotonically as `-b2`..`-b4` land. |
| `backwards_window_store_density.json` | — | Whether the backwards windows actually hold the stores the critical path donates to them: no gap day across the 24-day span, and 421 stores trading every day of it against an independently projected 419 (see §7). Carries its own grain and control range. |
| `backwards_window_store_density_probe.pod.yaml` | — | The exact read-only Pod that produced it. One aggregation, counts only; place ids never leave the pod. |
| `runbook/deadline-guard-v1.sh` | — | Fourth keeper. Extends `activeDeadlineSeconds` on the Active slice before it can be killed, because a deadline kill is what created Defect D and there is no safe kill for this workload. |
| `backwards_window_store_mappability.json` | — | Whether the backwards stores survive `require_place` mapping against the current-state `core.stores` dimension: attrition is date-independent and 420 of the 421 always-trading backwards stores are mappable (see §7). |
| `backwards_window_mappability_probe.pod.yaml` | — | The exact read-only Pod that produced it. Both sides exchange salted 16-hex digests; no place id in pod logs. |
| `eligibility_model_fidelity.json` | — | The critical-path projection's eligibility rule and `forecast_training_view` run against **one** PG16 snapshot and set-compared: the model admits nothing the view rejects, so h28 = 419 is a floor (see §8). |
| `runbook/eligibility-model-fidelity-probe.py` | — | The read-only probe that produced it. Temp relations only, transaction rolled back. |
| `lineage_activation_loss.json` | — | Defect E: activation deleted 1 841 `canonical_lineage` rows it could not re-insert, leaving 1 693 transactions unattested on the target while the source held them (see §8). Carries the live measurement and a deterministic before/after reproduction. |
| `runbook/lineage-activation-loss-probe.py` | — | The probe that produced it. Reproduces the loss over a scratch schema created and rolled back inside one transaction. |
| `lineage_convergence_rehearsal.json` | — | The Defect E fix rehearsed on the **live** pair: the real `activate` relation chain run twice inside a rolled-back transaction, fix backed out vs in place, so the fix is proven where it will actually run rather than only on a scratch schema (see §8). Carries per-relation counts and the chain's wall clock. |
| `runbook/lineage-convergence-rehearsal.py` | — | The rehearsal that produced it. Same advisory lock and statement timeout as `run_activation`; both arms roll back and neither commits. |
| `settle_state.json` | — | Written by the finisher **immediately before** `activate`: the incomplete partitions and abandoned-lineage ownership that were true at that moment. The two conditions the gate no longer blocks on, stated instead of hidden (see §7, v6). |
| `training_contract_readiness.json` | — | Criterion 5 answered by running the registry's own loader and `prepare_model_rows` against the live target rather than restating their rules: 3 of 4 data gates pass and horizon expansion fails, because the target's eligible span is 13 days against a 28-day shortest horizon. Also shows the target is frozen at an old activation — `SOURCE_RUN_NOT_COMPLETE` from 2026-07-02 on (see §9). |
| `runbook/training-contract-readiness-probe.py` | — | The probe that produced it. Imports the code under test. Covers the whole read-only prefix of `train()`, including `_temporal_validation` — reported under `model_quality_probe`, outside the verdict, because its thresholds are the registry task's (see §9). |
| `runbook/training-contract-probe-runner.sh` | — | Runs that probe detached, like the keepers. A worker turn is shorter than the probe, and a probe killed with its worker leaves a PostgreSQL backend still executing (see §9). |
| `runbook/training-contract-quality-stage-test.py` | — | Fixture tests for that probe's `model_quality_probe` stage: it returns a verdict, leaks no segment value, contains its own errors, and never moves `data_gates_passed`. Needs no database — the stage's first live run is unattended, so it is tested before then. |
| `canonical_row_drift_audit.json` | — | Defect F: the live source and target compared row for row across every copied `core` relation, testing this file's opening claim. No injected row anywhere (0 of 6 relations), and 16 953 drifted ones — including a `refunded` transaction the target still counted as a 230.00 sale (see §10). Carries its own method and redaction statement. |
| `runbook/canonical-row-drift-audit.py` | — | The read-only audit that produced it. Server-side digests first so identical groups cost nothing; only disagreeing groups are pulled and diffed. Compares `geom` as EWKB so a rendering difference cannot be reported as drift. |
| `canonical_row_drift_rehearsal.json` | — | The Defect F fix rehearsed on the **live** pair: the real `activate` chain run twice inside a rolled-back transaction, core relations frozen vs refreshing. Drift 1 847 → 0, exactly 16 953 rows refreshed and 0 pruned, and the chain runs no slower (see §10). |
| `runbook/canonical-row-drift-rehearsal.py` | — | The rehearsal that produced it. Same advisory lock and statement timeout as `run_activation`; both arms roll back and neither commits. |
| `evidence_redaction_audit.json` | — | Every identifier-shaped token in this directory, classified against the database. Found and then cleared a real violation; see **Redaction** above. |
| `runbook/evidence-redaction-audit.py` | — | The audit that produced it. Classifies by table membership rather than by pattern, reports salted fingerprints only, and uses the allowed `run_id` class as its control. |
| `runbook/redact-fidelity-sample.py` | — | One-shot, kept committed so the rewrite of `eligibility_model_fidelity.json`'s sample fields is reproducible rather than an unexplained diff. Idempotent. |

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

> **Superseded by §6, then retired entirely in §7 (v6).** Condition 2 below
> assumed re-running a partition always re-points an abandoned run's lineage.
> That holds only when the abandoned run committed nothing; see Defect D for the
> measured counter-example. Both conditions turned out to be permanent
> deadlocks and are now *reported* in `settle_state.json` rather than gated on —
> the live gate is "driver done and no orders-history Job `Active`".

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
trails the global span. The next subsection measures how far.

### How far per-store streaks trail the global span

Measured, not assumed — receipt in `per_store_streak_headroom.json`. Over the
currently landed span below the split, `2026-05-23..2026-07-04` (43 days),
**410 of 583 stores hold an unbroken transacting-day run across the entire
window**. Mean best run is 36.0 days and the maximum is 43.

That maximum is the tell: 43 is the window width, so the run length is capped by
how much history has been ingested, not by store behaviour. `stores with a run
≥ 56 d` reads `0` for the same reason and is **not** a finding — no run can
exceed a 43-day window. For the 410 stores at the ceiling, per-store streak
*equals* the global span, so every day added below the floor lengthens their run
by exactly one. The plan's global arithmetic transfers to them one-for-one, and
`-b3` puts them at 33 eligible dates, or 6 complete h28 windows each.

The 36.0 mean across all 583 stores is the long tail of intermittent stores.
Those were never going to reach h28 under any amount of backfill, and the
criterion does not depend on them.

One residual stays open: a store transacting daily through June and July might
have been intermittent, or not yet trading, in early May, in which case
extending the floor would not extend *its* run. Two independent facts bound it.
`upstream_source_depth_probe.json` already measured upstream daily counts below
the landed floor — `05-14` 6 600, `05-16` 11 011, `05-18` 7 037, `05-20` 6 307,
`05-22` 7 150 — the same order of magnitude as landed July partitions (`07-11`
6 752, `07-10` 8 056, `07-09` 7 787), so the source is at comparable daily
density rather than thinning out; and the collection runs back to `2024-06-26`,
so early May sits deep inside its history rather than at its edge.

Closing that residual exactly would need a per-store upstream aggregate, which
is declined on two grounds: the probe's redaction deliberately selects no store
field, and the `private-pool` node sits at ~96 % memory while a slice runs, so
an added Pod risks sitting `Pending` and delaying `-s4`. `-b4` is the margin
that absorbs the residual, which is why it is queued ahead of `-s5` rather than
dropped. The honest way to close it is empirical: re-run the same measurement
once `-b3` lands and check the unbroken-run count holds near 410 against a
61-day window.

### The Defect D split does not break the prior-day count

Worth stating explicitly, because it is easy to read the split as more damaging
than it is. `transaction_daily` and `mature_daily` apply **no filter** on
`lineage_complete` or `source_run_complete` — those are computed as *flags*, not
predicates, and only gate the target row. So `prior_day_count_28` counts a day
that was merely *ingested*, whether or not it was ever attested.

`2026-07-05` and `2026-07-06` therefore still contribute to the 28-day prior
window of later dates. What they break is their own eligibility as **targets**,
and that is what severs the eligible-date streak at the split. This is precisely
why the fix is to lengthen the span *below* the split rather than to bridge it,
and it is the mechanism behind the `N - 28` arithmetic used above.

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

### v6 — clause (a) was the same deadlock, and it is reachable

v5 kept clause (a) on the reasoning above. That reasoning does not survive
contact with how driver v4 actually behaves. The driver does **not** abort on a
failed slice: it records the failure, moves to the next job, and always logs
`BACKFILL-DRIVER-DONE`. So if any slice dies mid-partition — exactly what
Defect C did to `-s1`, and exactly what the raised `activeDeadlineSeconds`
defends against — that partition keeps a non-terminal run with no `SUCCEEDED`
sibling **forever**, the driver finishes, and the finisher spins on
`unfinished_partitions=1` until the host is rebooted. The result is *no evidence
at all* about the 40+ days that landed correctly, which is precisely the outcome
v5's own argument condemns.

Clause (a) is also **subsumed** by the test that replaced clause (b): a
partition whose run is non-terminal while no Job is `Active` is not in flight.
It is abandoned — settled, just settled badly — and its day is already excluded
fail-closed as `SOURCE_RUN_NOT_COMPLETE`.

`runbook/forecast-finisher-v6.sh` therefore reduces the gate to exactly one
condition (driver done **and** zero `Active` orders-history Jobs, both failing
closed on an unreachable cluster or database) and gives clause (a) the same
treatment v5 gave clause (b).

Demoting a gate is only defensible if the cost it guarded becomes **visible**
rather than silent, and a log line in `/tmp` is neither committed nor evidence.
So v6 stops discarding both measurements and writes
[`settle_state.json`](settle_state.json) into the evidence directory next to the
activation receipt: which partitions were incomplete at activation time, which
runs still own lineage, and the partition keys that costs. An exact-head
reviewer can then see what was true when the receipt was taken instead of having
to trust that nothing was.

One caveat belongs in the evidence rather than only in the script, and
`settle_state.json` carries it: `transaction_daily` and `mature_daily` filter on
neither `lineage_complete` nor `source_run_complete`, so a *partially* ingested
day still contributes a short daily row to later dates' `prior_day_count_28`. An
abandoned partition therefore costs more than its own eligibility. That is why
the receipt names the partition keys rather than only counting them — **if a
reported incomplete partition falls inside the span carrying the h28 windows,
the correct response is to re-run that slice and re-activate, not to accept the
receipt.**

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

### What actually governs a partition's wall-clock, and why the deadline is now guarded

Slice sizing was defended, up to this point, by a tripwire: measure each
partition, and if one exceeds 80 minutes, raise that slice's
`activeDeadlineSeconds`. That was the honest thing to write while the cause was
unknown — per-partition duration is demonstrably **not** a function of partition
size, since `-s1` projected 13 693 rows in 19.4 min while `-s4` took 52.8 min for
12 214. But a tripwire fires after the fact and only helps if somebody is awake,
and `-b1`..`-b4` and `-s5` run unattended for many hours.

`runbook/lineage-throughput-probe.py` finds the governing quantity, because every
`canonical_lineage` row carries `run_id` and `projected_at` and so each run leaves
a per-minute trace of its own work. Receipt:
`lineage_projection_throughput.json`, 18 full-length partitions.

- **Lineage projection is 89.6 % of a partition's wall-clock on average.** A
  partition is not an ingest that happens to write lineage; it is a lineage
  projection with an ingest at the front. `core.transactions` rows land in a
  single bulk commit inside the partition's first minute — the remaining ~50
  minutes of `-s4`'s first partition were projection.
- **Its throughput is not stable and has been falling**: ~620–820 rows/min
  across early `-s1`, 209–765 across `-s3`/`-s4`, with the slowest full-length
  partition at **209.2 rows/min**. Duration is therefore predictable after all —
  `rows ÷ rate` — but only against a rate that drifts.

Two numbers had to be kept out of that. A partition **resumed** after a kill has
only a few hundred records left and reports thousands of rows/min; those are
measurements of a different thing, and including them lifts the `-s3`/`-s4` mean
from ~414 to ~931 rows/min. That is precisely the sort of figure that would
justify a deadline which then kills a slice, so the probe excludes any partition
whose projection span is under 5 minutes and reports how many it dropped.

Planning against the **slowest** recent partition rather than the mean — the
question is not how fast it usually goes but whether the slow case still
finishes — the heaviest day seen (13 693 rows) costs 65.5 min, so six partitions
cost **392.7 min against the raised 480 min budget**. It fits. The sharper
number is the other one: a slice exactly fills the budget at **171.2 rows/min**,
and the current worst case is 209.2. **The margin on the raised deadline is
about 18 % of throughput, not a comfortable multiple.**

That margin is too thin to leave unattended, so the tripwire is now a fourth
keeper, `runbook/deadline-guard-v1.sh`. Every 5 minutes it finds the single
Active slice and, if it is within 45 minutes of its deadline, extends it by 2 h
up to a 16 h cap. Its one and only mutation is raising
`activeDeadlineSeconds`; it never suspends, resumes or deletes a Job, and it
matches only this task's `93cb9f94-` slices, never the unrelated
orders-history Jobs sharing the namespace. All four of its paths — discovery,
liveness, the cap branch and the patch itself — were exercised against the live
`-s4` before it was armed, the patch verified as a no-op that left the same pod
at `RESTARTS 0` with its age unbroken.

One design point is worth stating because the obvious alternative is wrong. The
tempting rule is "extend only if the job is provably progressing, otherwise let
it die". Here there is **no such thing as a safe deadline kill**: a hung slice
has already committed lineage rows, and the kill is exactly what strands them —
that is Defect D, and it is permanent. An over-extended hung slice merely costs
node time and is undone by suspending it by hand. So the guard extends even when
liveness cannot be proven, bounded by the cap, and makes the doubt loud instead:
it measures `max(projected_at)` on every decision and logs `liveness=OK` /
`STALE` / `UNKNOWN(db-unreachable)`. `projected_at` is the only external liveness
signal that exists for a running partition — a `RUNNING` row in
`ingestion_runs` reports `valid_loaded = 0` until it finishes, and
`ingested_at` is a single early burst.

### Which slice actually decides criterion 3

Driver v4's queue is `-s4, -b1, -b2, -b3, -b4, -s5`, and each slice costs about
five hours. That ordering was inherited rather than chosen: `-s4`/`-s5` finish
the ORIGINAL gap-fill plan, `-b1`..`-b4` are the backwards extension added once
the `2026-05-23` floor turned out to be a window-clamp artifact. Which family
actually moves acceptance criterion 3 had never been measured, so
`runbook/horizon-critical-path-probe.py` asks the view's own question — how many
stores hold `h` consecutive eligible dates — against the landed grid and against
per-slice counterfactuals. Receipt: `horizon_critical_path.json`.

Landed today, over `2026-05-22..2026-07-27` (58 ingested days, 55 attested):
**h7 = 438 stores, h14 = 411, h28 = 0**, longest per-store eligible run 23 days.
Criteria for the 7- and 14-day windows are therefore already met by real data;
only h28 is outstanding, exactly as expected.

The projection is the useful part, and it is blunt:

| scenario | h7 | h14 | **h28** |
| --- | --- | --- | --- |
| landed (measured) | 438 | 411 | **0** |
| `+ -s4, -s5` (gap-fill) | 470 | 446 | **2** |
| `+ -b1, -b2, -b3` (backwards) | 462 | 438 | **419** |
| `+ -b4` | 475 | 440 | **431** |
| everything | 497 | 473 | **432** |

**The gap-fill family cannot deliver criterion 3 and the backwards family can.**
Completing `-s4` and `-s5` moves h28 from 0 to 2; `-b1`..`-b3` moves it to 419.
The reason is the Defect D split: `prior_day_count_28` is counted over
`mature_daily` with no attestation filter, so `2026-07-05`/`07-06` do not erase
their neighbours from anyone's prior window — they only lose their own
eligibility, cutting each store's eligible-date island in two. The upper island
tops out at `2026-07-07..07-27`, twenty-one days, so no amount of gap-filling
reaches twenty-eight. Only the island below the split can grow, and it grows
downwards. This does not make `-s4`/`-s5` waste: the `2026-07-06..07-22` hole is
a real hole in the authoritative history and criteria 1 and 4 are about
coverage. It does mean **`-b3` is the acceptance gate**, and everything after it
in the queue is margin.

The queue was NOT reordered even so. `-s4` is mid-partition, and suspending a
Job deletes its pod — which is precisely the mechanism that created Defect D, a
permanent and unrepairable hole, to save at most five hours on a run of roughly
thirty. The remaining queue is already critical-path-first (`-b1`, `-b2`, `-b3`
before `-b4` and `-s5`), so there is nothing to gain by touching it.

Two honesty notes about the projection. Counterfactual days add no revenue and
change no predicate; they mark a date present and attested for the stores
projected to be trading, so this projects island LENGTH only. And the donor rule
is where the probe's first run was wrong: it donated the store set of the
nearest landed date, which for every backwards date is `2026-05-22` — a day
holding 3 stores and 3 transactions, timezone-edge stragglers pulled in by the
`2026-05-23` window's lower bound rather than a trading day. Every backwards
scenario then extended exactly three stores and reported `h28 = 3`, a fact about
the probe and not about the plan. Donors are now restricted to dense landed days
and the projection is bracketed: optimistic (traded on the nearest dense day)
against strict (traded on all seven nearest dense days). The two agree at
**419 and 419** for `-b1`..`-b3`, so the conclusion does not rest on the
assumption. It is also consistent with `per_store_streak_headroom.json`, which
counted 410 stores trading unbroken across the landed 43-day window and left as
a falsifiable follow-up whether ~410 would hold against a 61-day window.

### The donated store set, checked against the source

Both brackets still shared one assumption: that the backwards dates are trading
days at all, carrying stores in the numbers the landed era does. Donation is a
statement about the *source*, and every measurement supporting it had been taken
on the *target*. `upstream_source_depth_probe.json` does not settle it either —
it measured order counts, on five sampled days, none below `2026-05-14`. Eleven
thousand orders can come from two hundred stores or six hundred, and because
eligibility needs 28 **consecutive** prior days, a single thin day anywhere in
the span would break every store's run and take `-b3`'s yield toward zero. That
day would have been found only after roughly eight hours of ingestion.

`backwards_window_store_density.json` measures it directly, at the view's own
grain — `state = TRADE_SUCCESS`, `currency = TWD`, grouped by `createdAt` UTC
day, store taken as `place`, which is the upstream pre-image of
`transaction_daily`'s store-day. Unmapped places quarantine, so an upstream
distinct-place count is an upper bound on landed stores; a landed control
fortnight (`2026-06-10..06-23`) measured the identical way cancels that bias,
since the claim is relative density.

- **No gap day exists.** All 24 days of `2026-04-29..2026-05-22` carry 497–527
  transacting stores, median 513. No empty day, no sparse day, nothing that can
  break the consecutive-prior-day requirement.
- **Early May sits at 0.934 of the control** (median 513 against 549 stores/day)
  — a smooth secular growth trend, not a cliff. The donated set is therefore
  mildly generous, so realised `h28` should land a little under 419; criterion 3
  needs only that complete 28-day windows exist at all.
- **421 stores trade on every one of the 24 days**, against the 419 the
  critical-path probe reached from landed PG streaks plus donation. Two
  independent methods converging on ~420 means criterion 3 no longer rests on a
  single projection.

This retires the assumption, not the acceptance evidence: it measures the
upstream pre-image, cannot predict per-store mapping attrition, and does not
substitute for the post-activation `verify`. The queue stands unchanged — no
rescope, no reorder, no extra slice is indicated.

### Mapping attrition, the gap that check left open

Finding D4 above names its own limit: an upstream distinct-place count is a
pre-image, and it *cannot predict per-store mapping attrition*. That limit is
load-bearing rather than cosmetic. `store.py::_PostgresLookup.require_place`
resolves every transaction's `place` against `core.stores` joined to
`core.brands`, requiring `brand_code` to start with `fongniao_`; a missing row
raises `MissingMappingError` and the transaction is **quarantined, never
projected**. `core.stores` is a *current-state* dimension, so a store that
traded densely in early May but has since left the upstream `places` collection
has no row today and would contribute zero landed store-days. And
`mapping.py::project_transaction` needs no other identity lookup — no device, no
machine, no member — so place mappability is the **only** per-store attrition
channel between a dense upstream day and a landed store-day. A date-dependent
cliff here would have invalidated both brackets at once, and would have surfaced
only after `-b3` had already spent its eight hours.

`backwards_window_store_mappability.json` closes it. The Atlas side must run
in-cluster (IP allowlist) while `core.stores` is reachable only locally through
the proxy, so rather than add a `cloud-sql-proxy` sidecar to a pod on a node
already at ~96 % memory — which risks leaving it `Pending` and delaying the
running slice — the two sides exchange a fixed, documented, non-secret digest,
`sha256("odp-mappability-v1|" + place_id)[:16]`. No plaintext identifier reaches
the pod log, so the density probe's redaction discipline survives intact and the
intersection is still exact.

- **Attrition is date-independent.** Backwards places map at 0.9715 (545/561),
  the landed control fortnight at 0.9711 (571/588) — a differential of
  +0.04 pp, with the backwards window marginally *better*. There is no early-May
  cliff of departed stores.
- **The cohort that decides criterion 3 is essentially untouched.** Of the 421
  stores trading every one of the 24 backwards days, **420 are mappable** and
  exactly one is not; the control fortnight has the identical shape, 485 of 486.
  Attrition costs the projection about one store, not a tranche.
- **The method is exact where it can be checked.** On the landed control
  fortnight it predicts 571 distinct stores and 485 trading all 14 days;
  `core.transactions` holds 571 and 485, with **zero** predicted-but-absent and
  **zero** landed-but-unpredicted. Every predicted full-window store is a landed
  full-window store, so `mappable ⇒ lands` with no observed slippage. That makes
  the backwards number a validated prediction rather than an estimate.
- **It independently reproduces the density receipt** — 561 backwards places,
  588 control places, 421 full-24 stores, recomputed from scratch. The two
  receipts agree at the digest level, not merely at the headline.

Three independent routes now agree on the same figure: landed-streak donation
419, upstream density 421, mappability-adjusted **420**. What this still does not
bound is row-level quarantine for other reasons (`INVALID_AMOUNT`,
`STATUS_MAPPING_UNAPPROVED`, contract violations) — those cut a store's order
count without removing it from a store-day, so they cannot break a consecutive
run, and they remain a matter for the post-activation `verify`.

### The donor rule itself, scored against a blind holdout

The three routes above all corroborate the *population* the projection runs
over. None of them tests the **rule** that turns a landed store set into a
projected one, and that rule is what produced 419 in the first place: a store
gets a not-yet-ingested date if it traded on the nearest dense landed day
(optimistic) or on every one of the nearest seven (strict). No donor projection
had ever been compared against an outcome, for the plain reason that until now
no projected date had subsequently landed.

`-s4` supplied one, and it is blind in the strict sense rather than the
convenient one. The critical-path probe's grid was cached at **19:54Z** while
`-s4` was still mid-flight: it held 2026-07-12 and 2026-07-13, the two
partitions the slice had finished by then, and nothing from 2026-07-14 onwards.
Those four dates landed afterwards under the same Job. The projection for them
was therefore made with none of their data, and
`donor_projection_backtest.json` scores it against what arrived. The donor logic
is **imported** from `horizon-critical-path-probe.py` rather than restated, so
what is scored is the code under test.

- **Both rules under-state a single day, and neither invents much.** Over the
  four dates, optimistic recall **0.937** at precision **0.975**; strict recall
  **0.851** at precision **0.994**. The rule's error is overwhelmingly one of
  omission — the stores it names really did trade.
- **The bracket contains the truth on the quantity that matters.** Per-date
  recall is the wrong score for h28, because the rule is all-or-none per store
  while a real store can trade three days of four and break its island. Scored
  on the population trading **every** holdout date: **519 actual**, against
  **529** optimistic and **464** strict. The optimistic arm runs ~2 % high, the
  strict arm ~11 % low, and the measured value sits between them. The bracket
  brackets — demonstrated rather than asserted, which is what it was introduced
  to do after the 2026-05-22 straggler collapsed the first version of the probe.
- **All four holdout dates landed fully attested**, so the counterfactual's
  second assumption — that a projected date is attested as well as present —
  also held. Nothing in the rule's optimism hides there.
- **Re-measured with `-s4` real rather than projected, the attribution is
  unchanged.** `-b1..-b3` moves h28 to **406** (it read 419 when those dates
  were projected); `-b4` adds 11 more; the gap-fill family still reaches **2**.
  The 3 % drop is in the direction the backtest predicts for the optimistic arm,
  and 406 versus 2 is not a margin any plausible rule error closes. `-b3`
  remains the gate.
- **The first real 28-day window now exists.** `landed_measured` reports
  h7 = 459, h14 = 411, **h28 = 1**, longest per-store eligible run 28 days —
  up from h7 = 438 / h14 = 411 / h28 = 0 / 23 days before `-s4`. One store is
  not criterion 3, but the quantity is no longer structurally zero.

Two limits, stated rather than buried. The holdout dates sit one to five days
from their donors and extend an island *forwards*; `-b1..-b4` sit up to two
months from theirs, so this bounds the rule's error in the favourable regime.
It could have falsified the rule outright and did not, but a clean result here
is a lower bound on the error, not a guarantee at two months' distance — which
is exactly why the density and mappability probes measure the backwards
population against the source instead of projecting it. Read the three together.
Second, the re-measured `landed_measured` block above was captured at 21:46Z,
eight minutes after the driver resumed `-b1`, so its ingested span opens at
2026-05-16 and includes two partial in-flight dates. They are excluded from the
backtest's holdout by construction, and they sit far below any eligible date, so
they do not move the horizon counts; they are noted because the span figure in
that receipt would otherwise look like a slice had completed.

### Closing the distance limit, as the backwards dates land

The `-s4` backtest bounds the donor rule in the favourable regime and says so.
The only thing that closes the remaining question is scoring the projection
against backwards dates themselves, in the regime it was a projection of, and
`-b1` started producing those at 21:38Z. `runbook/backwards-landing-validation.py`
is built to be re-run after each backwards slice; it strengthens monotonically as
`-b2`, `-b3` and `-b4` land. Receipt: `backwards_landing_validation.json`.

It scores two claims of different standing. That landed stores never exceed the
upstream per-day place count is a **falsifiable invariant** — an unresolvable
`place` is quarantined by `store.py::require_place` and lands nothing, so a
breach would mean the density measurement never described the population that
lands, and 419 / 421 / 420 would all have to be recomputed. That landed stores
sit near `0.9715 × upstream` is a calibration check, where a miss is
informative rather than fatal.

Its completeness rule is the substance of it, and it is two rules rather than
one, because the obvious single rule is wrong in both directions.

- **A date needs a SUCCEEDED run over its own whole-day partition.** 2026-05-16
  and 2026-05-22 pass the view's attestation predicate and hold 1 and 3 stores,
  because no partition ever covered them — they are timezone-edge stragglers
  clipped in by a neighbouring window's bound. Scoring one against a ~520-store
  prediction reports a 500-store shortfall that means nothing. This is the
  2026-05-22 straggler that already broke the first critical-path probe,
  arriving a second time by a different route.
- **A date also needs every run owning its transactions to be SUCCEEDED**, which
  is not implied by the first. Partition windows are cut on the source's update
  cursor while the grain here is `event_time`, so a few transactions always spill
  across the UTC day boundary: when `2026-05-17__2026-05-18` reached SUCCEEDED,
  two of 2026-05-17's transactions were owned by the still-RUNNING
  `2026-05-18__2026-05-19` run, and `bool_and` held the date back. Expect each
  slice to yield its dates one behind the partition frontier. 2026-07-06 is the
  same test failing for the other reason — a SUCCEEDED partition whose lineage
  is unreconciled — which is why both rules are kept separate and both reported.
- **A date finally needs a SUCCEEDED run over the FOLLOWING day's partition**,
  and this third rule was added at 00:25Z because the first two do not actually
  guarantee what the paragraph above claims they do. `bool_and` over owning runs
  holds a date back only once the following run has *claimed* its spill rows, so
  between that run starting and writing them there is a window in which a date
  passes both earlier tests vacuously, on an under-count. 2026-05-21 walked into
  it: its own partition succeeded at 00:19Z, `2026-05-22__2026-05-23` was three
  minutes old and owned none of 05-21's transactions yet, and the date scored
  512 stores against a 517 bound — a ratio of 0.9903, sitting mid-range
  among every other scored date and so invisible as an error. It is the same
  mid-flight trap as the 518→520 hand-reading above, arriving by a third route,
  and it is worth stating why the trap is so hard to see: a partial read can only
  ever *under*-count, and an under-count always appears to respect an upper
  bound. The new rule closes it structurally rather than by timing — it can only
  withhold dates, never admit them, and it makes "one behind the partition
  frontier" a property of the code instead of a description of its luck.

The guard is not ceremony. Read by hand while `-b1`'s first partition was still
running, 2026-05-17 showed 518 stores over 8 733 transactions — comfortably
inside the predicted band, and it looked like a confirmation. Twenty-five
minutes later it read 520 over 11 246 and was still climbing. A partial
partition can only under-count, so it will always appear to respect an upper
bound; reading one mid-flight manufactures a pass out of an unfinished write.
**The receipt was therefore first committed scoring zero dates and listing four
exclusions.** That was the correct state at capture time, not a null result: it
is the record that the method was fixed, and its guards demonstrated, *before*
any backwards date could be scored — so the numbers it eventually reports cannot
have been fitted to them.

**`-b1` is complete — six partitions, all SUCCEEDED, 3h19m — and all six of its
scoreable dates hold the invariant** (receipt re-captured 00:59Z; **0
breaches**). Each became scoreable exactly one behind the partition frontier,
which is now enforced rather than observed:

| date | landed stores | upstream bound | landed/upstream | predicted | error |
| --- | --- | --- | --- | --- | --- |
| 2026-05-17 | 520 | 527 | 0.9867 | 512 | +8 |
| 2026-05-18 | 511 | 517 | 0.9884 | 502 | +9 |
| 2026-05-19 | 505 | 511 | 0.9883 | 496 | +9 |
| 2026-05-20 | 511 | 515 | 0.9922 | 500 | +11 |
| 2026-05-21 | 512 | 517 | 0.9903 | 502 | +10 |
| 2026-05-22 | 515 | 523 | 0.9847 | 508 | +7 |

2026-05-22 is the more interesting of the two new rows. It is the straggler that
broke the first critical-path probe and forced the first completeness rule —
the date that read **3 stores** because no partition had ever covered it, only a
neighbouring window's bound clipping a few transactions in. `-b1`'s last
partition `2026-05-22__2026-05-23` covered it properly, and it now reads **515
stores over 6 178 transactions**, at a ratio of 0.9847 that sits inside the band
every other backwards date occupies. So the straggler was an artifact of
partition coverage exactly as claimed, and not a real collapse in trading — the
claim §7 made about it is now measured rather than inferred.

2026-05-21 closes the loop on the rule added an hour earlier. Withheld at 00:22Z
because its following partition was mid-flight, it scored at 00:59Z once that
partition succeeded — at **512 stores over 5 690 transactions, byte-identical to
the premature reading**. So this particular date would have been fine. That is
the honest result and it does not weaken the rule: the guard can only ever
withhold, the reading it protects against was directly observed on 2026-05-17
(8 733 transactions climbing to 11 246 twenty-five minutes later), and a rule
that is only invoked when it changes the answer is a rule you cannot trust the
rest of the time. Twice now — 05-19 and 05-21 — the stricter gate has withheld a
date whose number turned out to be right; both times the value was that the
number is now *guaranteed*, not merely correct.

One correction the third rule forces on the 23:28Z capture above. Under the rule
as it now stands, that capture scored 2026-05-19 a step early: only
`2026-05-17__2026-05-18` through `2026-05-19__2026-05-20` had succeeded, so
05-19's own following partition was still the frontier. Re-measured at 00:25Z,
after two further partitions closed, 05-19 reads 505 stores over 5 391
transactions — identical to what it read then. So the earlier receipt's numbers
were right; what was wrong was that nothing in the code guaranteed they would
be. That is the whole reason the rule is worth adding: the failure mode is not a
visibly wrong number, it is a right-looking number with no guarantee behind it.

So the falsifiable claim survives its first real tests in the backwards regime:
nothing landed that the density measurement did not already contain, which is the
condition every one of 419 / 421 / 420 rests on. Note also that the landed store
counts themselves — 520 / 511 / 505 — are the same magnitude the upstream density
probe reported for early May, which is the population `-b3`'s 406 was computed
over.

The calibration reads the other way, and in the direction that costs nothing.
The `0.9715` mapping rate under-states landed stores on all seven dates, by 7 to
11 (mean +9.3), at a strikingly stable `landed/upstream` of 0.9847–0.9922 (mean
0.9890).
That is the same sign the `-s4` backtest found on its holdout (both arms
under-stated, optimistic recall .937), now reproduced **two months** from its
donors rather than days from them — the regime the backtest explicitly could not
reach. A projection that under-states cannot manufacture criterion 3; if this
sign holds across `-b2` and `-b3`, the measured h28 should land at or above the
projected 406, not below it.

The first `-b2` date agrees. `2026-05-11` became scoreable once `-b2`'s second
partition succeeded, and it is the deepest date measured so far — six days below
anything `-b1` could reach. It lands 507 stores against a 511 upstream bound:
within the bound, ratio 0.9922 at the very top of the band the `-b1` dates
established, and still under-stating the point prediction by 11. So the first
evidence from outside `-b1` shows the relationship holding rather than decaying
with distance, which is the specific way it could have failed.

The honest limits. Seven dates are seven dates, and six of them are from `-b1`,
the slice nearest the landed era; `-b3` sits three weeks further back and is where
criterion 3 is actually decided. Three of `-b2`'s dates stay excluded for now
(2026-05-12, 05-13 and 05-16, plus 05-10 below the slice), each waiting on the
partition that follows it — 05-16's own partition is `-b2`'s last. And the
invariant tested here is
per-date presence, not the per-store *continuity* that h28 needs: a date can land
its full store count while individual stores still break their islands. That
continuity claim is the one the `-s4` backtest scored and this probe does not.
Re-run after `-b2` and `-b3`.

One performance note worth keeping. `canonical_lineage`'s unique index leads
with `source_snapshot_id`, because it exists to serve the ingestion upsert's
`ON CONFLICT`. A probe that filters on `canonical_id` alone cannot use it, so
the first version's per-row correlated subquery degraded to one scan of a
multi-million-row table per transaction and had to be killed after ten minutes.
Joining the lineage side against the transaction set instead lets the planner
make a single hash-join pass: 26 s over the same window.

## 8. Defect E — activation destroyed lineage, and the view declined to notice

Everything in §7 that put `-b3` on the critical path was computed by
`horizon-critical-path-probe.py`, which **re-implements** the view's eligibility
rule on the source plane. The number acceptance is judged on comes from
`model_ready.forecast_training_view`, and the two had never been compared.
`eligibility_model_fidelity.json` compares them properly — both run against one
PG16 snapshot, so no state drift can explain a disagreement, and the eligible
`(tenant, store, date)` **sets** are compared rather than the counts, which
could agree by coincidence.

Result: identical grain (24 441 store-days each side) and
**`only_in_model` = 0** — the model admits nothing the view rejects. So
`h28 = 419` is a **floor**, not an over-count, and the `-b3` attribution stands.
The model is conservative by 378 store-days (h7: view 432 vs model 428).

Chasing *why* it is conservative found a defect neither plane had reported.

**The loss.** On the target, 1 690 of 2026-06-23's 5 606 qualifying transactions
carry no `canonical_lineage` row at all; the source carries lineage for every
one of them. Not staleness: the target's newest lineage row is `projected_at`
`2026-07-28T14:34:34Z`, the missing rows were projected at `12:28`, and the run
that owns them (`85294064`, partition `2026-06-23__06-24`, `SUCCEEDED`) is
itself present in the target. Counted globally, the source holds 377 283 rows
projected at or before the target's own cutoff and the target holds 375 442 —
**1 841 rows destroyed by the copy**, across exactly two dates.

**Mechanism.** `canonical_lineage`'s primary key is
`(source_snapshot_id, canonical_table, canonical_id)`, and `source_snapshot_id`
is derived from record **content**. Slice `-s1` re-projected 2026-06-23 under a
new run, which changes `run_id` and nothing else — the key is identical. So:

1. `INSERT ... ON CONFLICT DO NOTHING` discards the re-pointed row, because the
   target already holds that key under the old run.
2. `prune_sql` then deletes the target's old row: no staged row matches its
   `(run_id, canonical_id)`, and the fail-closed keeper check passes because a
   staged row *does* exist for its `canonical_id`.

The guard added in §4 was meant to make stripping a record's last lineage
impossible, and it does look for a keeper — but it interrogates **staging**,
i.e. what the source holds, not what the target will hold after an insert that
may have been discarded. Both steps behaved as written; the composition loses
the row.

**Why nothing reported it.** `transaction_daily.lineage_complete` is
`bool_and(cardinality(source_snapshot_ids) > 0)`. A transaction with no lineage
at all makes that expression NULL, and `bool_and` **ignores** NULLs — so a day
where 1 690 of 5 606 transactions have no lineage whatsoever still reports
`lineage_complete`, `data_quality_score = 1.0` and `is_training_eligible`. Two
defects cancelling: activation destroys the attestation, the view declines to
notice. That is also the whole reason the projection model looked pessimistic —
it coalesces the same test to `FALSE`, so it was the only thing in the system
telling the truth about 2026-06-23.

**Fix.** `canonical_lineage` now declares
`refresh_key = ("source_snapshot_id", "canonical_table", "canonical_id")` — the
primary key. The re-pointed `run_id` is written in place before the prune runs,
so the prune finds nothing superseded. This reuses the same convergence step
`ingestion_runs` already used; no new machinery. `prune_sql` is unchanged, and
the reproduction in `lineage_activation_loss.json` drives the **real** builders
over a scratch schema created and rolled back inside one transaction:
prune-only leaves the transaction with **0** lineage rows, refresh-then-prune
leaves **1**, re-pointed at the run that completed.
`test_every_pruning_relation_can_also_refresh_in_place` now makes the pairing
structural rather than a fact about today's two relations.

**Blast radius, and why it is not on the backwards critical path.** A row is
lost only on the activation that *follows* its re-pointing, and the next
activation restores it — the blocking row is gone by then. `-b1`..`-b4` ingest
dates the target has never held, so they cannot trigger it at all; the exposure
is dates re-ingested *after* they were already activated, which is exactly what
`-s1` did to 2026-06-23. The final activation therefore both repairs the
existing 1 693 transactions and no longer creates new casualties.

### The fix rehearsed where it will actually run

The reproduction above is deterministic, but it holds one row on a scratch
schema. `prune_sql`'s fail-closed keeper check reads correct on the page and is
wrong only because it interrogates *staging* rather than the post-insert
*target* — a defect a second reading of the SQL had already failed to catch
once. So the fix was measured instead of re-read.
`lineage_convergence_rehearsal.json` drives the **real** `copy_relation` over
the **real** `ACTIVATION_RELATIONS` chain, in dependency order, against the live
PG15 source and PG16 target, under the same advisory lock and statement timeout
as `run_activation`. Both arms roll back; the only variable is whether
`canonical_lineage` carries `refresh_key`. (`ingestion_runs` keeps its
`refresh_key` in both arms — that is Defect B's fix and is not under test.)

This matters at exactly one moment. §8's loss probe recorded that a destroyed
row is restored by the *next* activation, which is reassuring for a system that
activates continuously and worthless here: the finisher activates **once**, at
the end, immediately after a backfill in which every resumed partition
re-pointed its lineage. Whatever the unfixed arm destroys is what the acceptance
receipt would have been missing, permanently.

- **The unfixed arm still destroys.** Pre-fix code run against today's target
  prunes a lineage row it cannot re-insert and strips a transaction of its last
  attestation — `baseline_keys_deleted = 1`, and nothing would have restored it.
- **The fixed arm destroys nothing.** `target_rows_pruned = 0`,
  `baseline_keys_deleted = 0`, and the row the other arm deleted is instead
  `refreshed` in place, carrying the new `run_id` across a content-derived key
  the target already held.
- **Activation repairs the existing damage under either arm.** The 1 842
  transactions the target currently holds without any lineage drop to **0**
  after the fixed arm and to **1** after the unfixed one: rows *absent* from the
  target are re-inserted by the plain `ON CONFLICT DO NOTHING` insert regardless,
  so the arms differ only on rows the target still holds under a superseded run.
  That is the precise shape of the defect, confirmed on live data.
- **The chain fits the finisher's budget with two orders of magnitude to
  spare** — 94 s fixed, 113 s unfixed, against a first escalating budget of
  3600 s. Nothing had measured a full copy chain against that number before.

The exposure measured *today* is one row rather than a tranche, because `-s1`'s
casualties are already gone from the target and re-insert cleanly. That is a
statement about the current target, not about the acceptance run: `-b1`..`-b4`
each land dates under fresh runs, and any partition that resumes re-points its
lineage under a new `run_id` — every one of those is a row the unfixed code
would have deleted at exactly the moment there is no next activation to restore
it. The rehearsal was run twice, eight minutes apart, and R1–R3 came out
identical both times.

The view's NULL-swallowing `bool_and` is **left as it is** and reported here
instead. Tightening it is a change to a canonical model contract that other
tasks read, it would make the reported horizon counts *fall* rather than rise,
and with Defect E fixed there is no longer a known population of
lineage-less transactions for it to mask. Naming it is what this evidence owes
the reviewer; changing it is not this task's call.

## 9. Criterion 5, measured instead of assumed — and what it exposes

Four of this task's five acceptance criteria had receipts. The fifth —
"ODP-PRODUCTION-MODEL-REGISTRY-001 can resume training" — had none, and none of
the coverage probes answers it, because they all ask how many days landed rather
than whether the thing that consumes the view can start. Those differ by every
gate the consumer applies that the coverage probes never model.
`runbook/training-contract-readiness-probe.py` closes that, on the rule the
donor backtest set: **import the code under test, never restate it.** It builds
the real `PostgresModelReadySource` against the live target, calls the real
`inventory()`/`load()` with the real `MODEL_SPECS["forecastops"]`, and hands the
result to the real `prepare_model_rows`, so the gates are evaluated in the order
`train()` applies them. Receipt: `training_contract_readiness.json`.

### Where the probe stops, and one reason that turned out not to be real

The first revision stopped before `_temporal_validation` — the step that fits a
regression and scores it against `max_normalized_mae`/`min_p80_coverage` — for
three stated reasons. Two of them hold. It genuinely is a model-quality gate,
its thresholds belong to the registry task, and a history backfill cannot be
said to pass or fail a regression's accuracy. The third, "it needs LightGBM",
was an obstacle that does not exist here: `lightgbm` 4.7.0 is installed and
`lightgbm_regressor` is the spec's own `algorithm`. A reason that is not real is
worth removing from evidence whether or not it changes the conclusion.

Removing it changes the conclusion. `train()` runs
`load` → `prepare_model_rows` → `_temporal_validation` → `register_dataset_snapshot`,
and **everything that writes is on the far side of `_temporal_validation`** —
the dataset snapshot, the feature pipeline, the artifacts, the registry entry.
So the entire read-only prefix of the training path is reachable, and the rows
it needs are ones this probe has already spent forty minutes loading; the
marginal cost is one in-memory fit, measured at 1.7 s on a 390-row fixture. A
criterion-5 receipt that stops one step short of the last reachable step, for a
reason that was not binding, is answering less than it can. Worth noting too
that `train()` raises this failure as `ModelReadyDataError` — the same class it
raises for missing rows — so the registry's own taxonomy does not file it purely
under modelling.

The probe therefore **runs it and reports it outside the verdict**, under
`model_quality_probe`, with `gates_this_task: false` and the owning task named.
It cannot move `data_gates_passed` or `blocking_gate`; a `FAIL` there leaves the
data verdict intact, which is asserted directly in the change's tests. This is
the same discipline as the finisher's demoted measurements, applied in the other
direction: *reporting* a measurement inside a verdict it does not belong to
would be one mistake, and *declining to take* a cheap reachable measurement is
the other. `not_evaluated` now names what is actually not evaluated — everything
from `register_dataset_snapshot` onward, all of which writes.

Fidelity and redaction, both load-bearing. The real
`BoundedModelTrainingRelease._temporal_validation` is invoked rather than
restated, so the kind-based branch and the temporal split follow `release.py` if
either changes; it touches only `self.regression_trainer`, so a namespace
carrying the class's own default supplies the whole dependency, where building a
real instance would demand a service, an artifact store and a registry — all of
which write. And `_segment_validation` puts the segment value, **a store id**,
into both its per-segment records and its failure strings. This directory
publishes counts only, so segment outcomes are aggregated to a count and a
metric distribution, global rule failures (which name no store) are kept
verbatim, and the test asserts no segment value reaches the receipt.

One limit stated plainly: the stage's first run against live data is the
finisher's post-activation one. It cannot run before then, because the data
gates block at horizon expansion and there are no prepared rows to fit — so the
committed pre-activation receipt shows it as `not reached`, which is the honest
reading rather than a gap.

**Result: 3 of 4 data gates pass, and the run is blocked at horizon expansion.**

| gate | verdict |
| --- | --- |
| `eligible_rows_exist` | PASS — 5 210 eligible rows over 459 stores |
| `inventory_ready` | PASS — contract `forecast-training-view-v2`, trainable |
| `loader_returns_rows` | PASS — 5 210 eligible labeled rows loaded |
| `horizon_expansion_and_row_gates` | **FAIL** — "daily forecast rows do not contain a complete canonical horizon window" |

The failure is the expected one and it lands exactly where §7 predicted it
would. `FORECASTOPS_HORIZON_WEEKS = (4, 8, 12, 24)`, so the shortest window
`expand_forecast_horizon_rows` will build is **28 days** — h7 and h14, the
horizons this task has been reporting all along, are not training inputs at all.
The target's eligible span is **13 days**, `2026-06-19..2026-07-01`, and 13 < 28,
so **every one of the four declared horizons expands to zero rows**. That the
critical path was independently placed at `-b3` *because* `-b3` is the slice that
moves h28 is now a measured agreement rather than a coincidence.

**But the number that matters most here is the eligible span's two endpoints,
and neither is what a reader of §7 would expect.**

- The floor, `2026-06-19`, is exactly 28 days after `2026-05-22` — the target's
  own `min(date)`. So the target still begins at the *pre-`-b1`* floor: none of
  `-b1`'s six days, and none of `-s3`/`-s4`'s, have reached it.
- The ceiling, `2026-07-01`, is not a maturity boundary. Measured directly from
  the view's `exclusion_reason`, 2026-06-28 through 07-01 each carry ~434 eligible
  stores, and then **2026-07-02 holds 334 rows of which zero are eligible, every
  one `SOURCE_RUN_NOT_COMPLETE`**.

Both endpoints say the same thing: **the PG16 target is frozen at an old
activation.** That is not a contradiction of §7's `h28 = 1` — it is the
difference between two databases. `horizon-critical-path-probe.py` reads
`ODP_LEGACY_DATABASE_URL`, the PG15 **source**, and re-implements the view's
eligibility there; this probe reads `ODAY_DATABASE_URL`, the PG16 **target**,
through the real view. So every h7/h14/h28 figure in §7 is a source-side
projection of what activation *will* produce, and the gap between the two is
precisely the activation that has not run yet. Worth stating plainly because it
is easy to misread a source-side projection as a description of the target.

That also makes the Defect B fix load-bearing rather than incidental. The
frozen `SOURCE_RUN_NOT_COMPLETE` rows from 07-02 onward are the exact shape
`ON CONFLICT DO NOTHING` could never revisit; `refresh_key` (`10c0b78e`) is what
lets the next activation reconsider them at all. So criterion 5 needs two
distinct things, and only one of them is more ingestion: `-b3` for the 28-day
span, and an activation carrying the Defect B fix to move the target off
2026-07-01.

Re-run this probe after activation. Until then it is a genuine pre-activation
baseline — captured, like the landing validation, before it could report a pass.

Two lessons from running it, both in the *reporting* path rather than anything
under test, and both fixed in `c8c9a09b`. The loader returns `date` as an ISO
**string**; the span calculation subtracted the cells directly and died with
`TypeError: unsupported operand type(s) for -: 'str' and 'str'` — after a
44-minute live run, at the exact moment the receipt was about to record the
horizon-expansion failure, so the entire measurement was lost to a one-line type
assumption in a helper that computes nothing anyone gates on. It now coerces via
`_as_date`, and more importantly the whole descriptive helper is called through
`_safe_expansion_shape`, which captures any unexpected error as a
`describe_error` field. A measurement this expensive should degrade to a missing
paragraph, never to a missing receipt. Third lesson, operational: run it
**detached** (`runbook/training-contract-probe-runner.sh`). Launched as an
ordinary child of a worker it dies with the worker and PostgreSQL does not
notice — the first attempt left a backend still spilling to disk twenty minutes
after its client was gone, competing for I/O with its own replacement. Terminate
such a backend explicitly with `pg_terminate_backend`; it will not clear itself.

## 10. Defect F — the fact table was frozen too, and the view was counting a refund

The first paragraph of this file has claimed since the task opened:

> No fixture, synthetic, seed, spine, or auto-generated row is introduced
> anywhere in this task. Every transaction that reaches the target is a row that
> already existed in the approved legacy PostgreSQL source.

That is two claims and nothing had checked either. It is the same shape as the
redaction promise above — a structural argument about code standing in for a
measurement of what landed — and that one turned out to be false. The structural
argument here is real as far as it goes: `forecast_history_activation.py` copies
and never generates, and `install_views.py` separately rejects
`generate_series(`, `random(`, `setseed(` and `create table as` from the
model-ready SQL. Neither statement says what is in the database.

`runbook/canonical-row-drift-audit.py` asks both halves directly, comparing the
live PG15 source against the live PG16 target across every `core` relation the
activation copies. An aggregate pass computes a count, a digest of the sorted
primary keys and a digest of the sorted rows server-side, so identical groups
cost nothing to prove; only the disagreeing groups are pulled row by row and
diffed. `geom` is compared as EWKB rather than as text, because a rendering
difference between two servers would otherwise be indistinguishable from drift.

### The first half holds, by measurement this time

Target rows whose primary key the source does not hold: **zero**, in all six
relations. Nothing on the target was invented. Criterion 3's prohibition is now
answered by a receipt (`canonical_row_drift_audit.json`) rather than by the
paragraph asserting it.

### The second half did not

**16 953 target rows no longer match the source**, and one of them has teeth.

`apps/data_platform/store.py` writes canonical transactions with
`ON CONFLICT (transaction_id) DO UPDATE`, rewriting `store_id`, `event_time`,
`observation_time`, `payment_time`, the three amounts, `currency`,
`transaction_status` and `ingested_at` whenever it re-projects a changed
upstream record. Seven of those are exactly what `forecast_training_view` reads.
`Relation("core", "transactions")` carried no `refresh_key`, so `copy_relation`
reached it with `ON CONFLICT DO NOTHING` and an already-present target row could
never be revisited. That is **Defect B's mechanism, moved off the ingestion
ledger and onto the fact table** — and unlike Defect B it was invisible, because
a stale row looks exactly like a correct one until you ask the source.

The exhibit, on `2026-07-26`: one transaction stands at `refunded` / **0.00** in
the source and `succeeded` / **230.00** on the target. The view admits rows on
`transaction_status = 'succeeded'`, so **the target counts a refund as a sale**
and puts phantom revenue into a training label. One row is not a large error;
being unable to correct it is the finding. A further 1 846 transaction rows
carry stale `observation_time` / `ingested_at`, which are not cosmetic either —
they feed `source_available_at`, hence `label_maturity_time`, and the
`observation_time >= event_time AND ingested_at >= observation_time` half of
`source_run_complete`.

Every drift, in every relation, runs **source-newer**. Not one runs the other
way, which is what a copy that freezes rather than corrupts should look like.

The dimensions drift too: `tenants`, `brands`, `stores` and `machines` in
`updated_at` only — a column no model-ready view reads — and
`address_locations` in `geom` on 1 493 rows, which is real data (both servers
run PostGIS 3.6.0 and the comparison is binary). Only the transaction drift
reaches the forecast contract, but leaving five relations knowingly frozen after
writing them down would be worse than fixing them.

### The fix, and why the docstring had to change with it

Every `core` relation now refreshes on its primary key. Two properties keep that
narrow rather than broad: `refresh_sql` already guards with `IS DISTINCT FROM`,
so a converged row costs nothing and only genuine drift is rewritten; and no
`core` relation declares a prune, so a refresh can **correct** a record and can
never **remove** one.

`refresh_key`'s docstring used to forbid precisely this — *"never to rewrite the
record a row describes"* — and a unit test named
`test_only_lifecycle_relations_converge_and_only_lineage_prunes` pinned it, both
written by this task during the Defect E fix. The restriction reads as
conservative and is the opposite: it is what froze a refund out of the target.
It assumed the target owns something worth protecting, and it does not — every
`core` relation here is a copy of the approved source, which is itself written
only by the governed data plane. The rule is now the mirror rule, and the
deletion-side invariant the test was really protecting is asserted directly
instead: only `canonical_lineage` prunes.

### Rehearsed where it will actually run

Defect E was a case where the deductive reading of this same function looked
safe and was wrong — `prune_sql`'s fail-closed keeper check is real, it just
interrogates staging instead of the post-insert target — and 1 841 lineage rows
were gone before anyone measured. The finisher runs `activate` exactly **once**,
so there is no second activation to correct a mistake. So
`runbook/canonical-row-drift-rehearsal.py` follows
`lineage-convergence-rehearsal.py`: two arms over the real `copy_relation` and
the real `ACTIVATION_RELATIONS` chain against the live pair, same order, same
statement timeout, same advisory lock as `run_activation`, each inside a
transaction that is **rolled back**. One variable — `refresh_key` on the `core`
relations. `ingestion_runs` and `canonical_lineage` keep theirs in both arms;
those are Defects B and E and are not under test.

`canonical_row_drift_rehearsal.json`:

| | frozen core (pre-fix) | refreshing core (shipped) |
| --- | --- | --- |
| drift left behind | **1 847** | **0** |
| `core` rows refreshed | 0 | **16 953** |
| `core` rows pruned | 0 | **0** |
| chain wall clock | 285.3 s | 274.8 s |

Three things worth reading off that table. The refreshing arm rewrote **16 953
rows — exactly the count the audit measured as drifted**, relation by relation
(1 354 / 1 354 / 2 405 / 2 442 / 7 551 / 1 847). A refresh key that failed to
identify a target row one-for-one would have rewritten more; that it rewrote the
drifted set and nothing else is the empirical form of the uniqueness argument.
Pruned stays **0**. And the fix **costs nothing** — the refreshing chain ran
10 s faster than the frozen one, which is noise on a 4½-minute chain, because
`IS DISTINCT FROM` means the other 550 000 rows are compared and skipped.

One number in that receipt should not be over-read. The arms report
`transactions_admitted_by_the_view_filter` as 509 270 and 510 312, and the
1 042-row gap is **not** the fix: the arms ran five minutes apart against a
source that `-b2` was still writing to, and `rows_absent_from_source_probe`
(267 and 264) records the same live growth. The reversal's effect is exactly the
**−1** the audit isolated, measured against one snapshot of both sides.

### What this changes about the acceptance receipt

Without this fix the acceptance activation would have inserted the backfill
correctly and left every already-present row at whatever the first copy saw —
including the refund. The evidence would have looked complete, because nothing
in the before/after pair compares the target's *content* to the source's; they
compare coverage.

Both measurements above are also **pre-activation**, which is the same gap v7
closed for criterion 5: the only evidence about content parity would have been
taken against the target that the acceptance activation then replaced. So the
live finisher is now **v8**, which adds one stage after `inventory` — this same
audit, unmodified, against the freshly activated target, written as
`canonical_row_drift_audit_after_activation.json`. It is read-only and takes
about four minutes, so it runs *before* the 45-minute criterion-5 probe rather
than after; it fails soft, because a measurement is not a gate. Everything else
is v7 verbatim, including the gate and the driver handshake.

Reading it: injected rows must stay 0, and drift should now be 0 too. A small
non-zero drift is **not** a Defect F regression — the source keeps ingesting, so
a row written between the copy and the audit reads as drift. Compare against the
pre-activation 16 953 and against the refresh counts in
`activation_receipt.json` before concluding anything.

This is the fourth defect in this task whose signature is a conflict clause that
cannot revisit a row — B on the ingestion ledger, D on lineage attribution, E on
lineage deletion, F on the fact table — so the pattern is worth stating once,
precisely. `ON CONFLICT DO NOTHING` decides that **the first write wins
forever**, and whether that is safe depends entirely on what the conflict key
is:

* **Keyed on a content-derived id** — `source_snapshot_id`, or
  `(source_snapshot_id, canonical_table, canonical_id)`. Every `DO NOTHING` in
  `apps/data_platform/store.py` and `geography_backfill.py` is of this kind, and
  it is the correct shape: a *changed* record hashes to a new key, so it lands
  as a new row and nothing is lost. The edge it does have is Defect D — an
  *unchanged* record regenerates the *same* key, so it can never be
  re-attributed to a different run, which is why a partition killed mid-flight
  leaves lineage no re-run can repair.
* **Keyed on a stable business identity** — `run_id`, `transaction_id`,
  `store_id`. Here a changed record keeps its key, so `DO NOTHING` discards the
  correction outright and the stale row is permanent. That is Defects B and F,
  and it is why every relation in `ACTIVATION_RELATIONS` now carries a
  `refresh_key`.

The activation copies on the second kind of key while the source updates in
place on the same key. Three of its relations looked append-only and were not.

## 11. After state

Populated once `-s3`/`-s4`/`-s5` reach `Complete`, the source meets the settling
condition above, and activation runs. See
`activation_receipt.json`, `verify_after.json`, and `inventory_after.json`.

Activation is deliberately **not** run while any `orders` partition is still
`RUNNING`: the `refresh_key` step would faithfully mirror that non-terminal
status into the target, reproducing Defect B from the other direction.
