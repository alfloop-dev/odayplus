# Model-ready Views Baseline

## Purpose

This baseline implements the first dbt-facing contract between canonical data and
Learning Hub dataset snapshots. It follows `ODP-DATA-06` and `ODP-DATA-07`:
model-ready views expose versioned, point-in-time-safe features instead of raw
or canonical tables directly.

## Implemented Views

| View | Grain | Consumer | Baseline source tables |
|---|---|---|---|
| `geo_grid_view` | H3 cell x snapshot | HeatZone | `geo.h3_cells`, `geo.pois`, `geo.competitor_stores`, `expansion.listings` |
| `candidate_site_view` | candidate site x decision time | SiteScore | `expansion.candidate_sites`, `expansion.listings`, `core.address_locations` |
| `store_machine_timeseries_view` | store/machine x date | ForecastOps, monitoring | `core.transactions`, `core.machine_cycles` |
| `forecast_training_view` | store x date x snapshot | ForecastOps training | `core.transactions` |
| `intervention_panel_view` | store x date x intervention | InterventionOps, PriceOps | `operations.interventions`, `operations.intervention_outcomes` |
| `valuation_view` | store x valuation date | DealRoomAVM | `asset.valuation_runs`, `operations.forecast_outputs`, `core.stores` |
| `network_plan_view` | planning entity x quarter | NetPlan | `network.network_plans`, `network.network_plan_actions` |

The broader `ODP-DATA-06` catalog also includes `brand_transfer_view`,
`ramp_curve_view`, and `matched_control_view`. Those remain documented
follow-on views because the current canonical baseline does not yet include
every source table needed for a useful physical model.

## Common Contract

Every baseline view emits these common fields:

| Field | Meaning |
|---|---|
| `view_name` | Stable model-ready view name. |
| `view_version` | Schema/semantic version for downstream model contracts. |
| `entity_id` | Primary entity key at the view grain. |
| `feature_snapshot_time` | Latest time at which features are allowed to be visible. |
| `prediction_origin_time` | Prediction or decision origin time. |
| `source_snapshot_ids` | Source lineage identifiers or canonical source table placeholders. |
| `data_quality_score` | Baseline view-level quality score. |
| `confidence` | Feature confidence after source penalties. |
| `is_training_eligible` | Whether the row may enter training. |
| `is_scoring_eligible` | Whether the row may be used for scoring. |
| `exclusion_reason` | Machine-readable reason for exclusion. |

## Point-in-time Rules

The dbt SQL applies the first PIT boundary for event-like canonical tables:

```sql
event_time < prediction_origin_time
and observation_time <= feature_snapshot_time
and ingested_at <= feature_snapshot_time
```

The Python dataset snapshot helper in
`modules/learninghub/domain/dataset_snapshot.py` enforces the in-process
contract before a training dataset is registered:

- `feature_snapshot_time` must not be after `prediction_origin_time`.
- Feature `event_time` must not be after `prediction_origin_time`.
- Feature `observation_time` and `available_from` must not be after
  `feature_snapshot_time`.
- `available_to`, when present, must remain after `feature_snapshot_time`.
- `label_maturity_time` must not be after `feature_snapshot_time`.

## Dataset Snapshot Artifact

`build_dataset_snapshot()` returns a `DatasetSnapshot` with:

| Field | Source |
|---|---|
| `dataset_snapshot_id` | Supplied ID or deterministic hash of view/entity/time/source lineage. |
| `view_versions` | Distinct `view_name -> view_version` mapping. |
| `entity_count` | Unique model entity count. |
| `time_range` | Minimum feature snapshot time through maximum prediction origin time. |
| `source_snapshot_ids` | Sorted source lineage set from the input records. |
| `training_record_count` | Count of rows eligible for training. |
| `scoring_record_count` | Count of rows eligible for scoring. |

## Acceptance Evidence

Focused tests live in `tests/data/test_pit_snapshot.py` and cover:

- Snapshot indexing of view versions, source snapshots, and entity counts.
- Rejection of feature snapshots after prediction origin time.
- Rejection of labels that have not reached maturity.
- Rejection of features whose availability starts after the feature snapshot.
