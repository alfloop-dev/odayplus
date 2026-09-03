# Model-Ready Views Field Lineage & Classification Specification

- Date: 2026-09-03
- Task: `ODP-MEASUREMENT-SOURCE-VIEWS-NULLABLE-001`
- Phase: ODP Remediation · W2 Source Views
- References:
  - [ODP Remediation Plan](../plans/ODP_REMEDIATION_PLAN_2026-09-03.md)
  - [ODP Open Decisions](../plans/ODP_OPEN_DECISIONS_2026-09-03.md)
  - [ODP Structural Remediation Evidence](../evidence/ODP_STRUCTURAL_REMEDIATION_2026-09-01.md)
  - [Model-ready Views Baseline](MODEL_READY_VIEWS_BASELINE.md)

---

## 1. Overview and Problem Statement

Under the legacy implementation, model-ready views coalesced missing confidence scores to `1.0` (`coalesce(..., 1.0)`) and used hardcoded `1.0` constants without differentiating between:
1. **True Empirical Measurements**: Observations that may be absent (e.g., geocode confidence, POI confidence, competitor confidence, listing confidence).
2. **Deterministic Derived Rules**: Bounded calculations derived from verifiable, observable runtime predicates (e.g., PIT validity, timestamp sanity, solver state, evidence ladder).
3. **Canonical Derived Contracts**: Fixed structural invariants of authoritative systems (e.g., immutable double-entry transaction ledgers, official government deed registries, physical constants, benchmark baselines).

When measurements are missing, assigning a fake `1.0` gives unmeasured or unverified records the appearance of perfection, silently evading quality and feasibility gates downstream.

This specification formalizes:
- The removal of fake 1.0 defaults and `coalesce` on empirical measurements (`candidate_site_view` and `geo_grid_view`).
- Explicit `CASE ... WHEN` propagation of `NULL` when ANY component measurement source is absent (fixing PostgreSQL `LEAST` behavior where missing inputs were ignored).
- The complete classification and audit of every literal `1.0` and constant across all 10 DBT model-ready views and PostgreSQL contracts in `product_ops/modeling/sql/model_ready_views.sql`.
- Comprehensive column-by-column lineage and nullability semantics for parallel consumer remediation (SiteScore, DatasetSnapshot, DealRoomAVM, ForecastOps).

---

## 2. Audit and Classification of 1.0 Literals and Coalesce Expressions

Every occurrence of `1.0`, `1.00`, `coalesce(..., 1.0)`, and score constants across all 10 DBT views in `pipelines/dbt/models/model_ready/` and PostgreSQL contracts in `product_ops/modeling/sql/model_ready_views.sql` is categorized below:

### 2.1 DBT Model-Ready Views (`pipelines/dbt/models/model_ready/`)

| View | Column | Expression | Classification | Nullable? | Rationale & Evidence |
|---|---|---|---|---|---|
| `candidate_site_view` | `confidence` | `case when listings.confidence is not null and address_locations.geocode_confidence is not null then least(listings.confidence, address_locations.geocode_confidence) else null end` | **Measurement** | **Yes (NULL)** | Empirical measurement of geocoding and listing quality. Evaluates to `NULL` when either source measurement is absent. |
| `candidate_site_view` | `data_quality_score` | `case when listings.rent_amount > 0 and address_locations.geocode_confidence >= 0.5 then 1.0 when listings.rent_amount > 0 then 0.8 else 0.0 end` | **Derived Rule** | No (0.0..1.0) | Derived score based on verifiable candidate site data completeness. |
| `geo_grid_view` | `confidence` | `case when poi_counts.poi_confidence is not null and competitor_counts.competitor_confidence is not null then least(poi_counts.poi_confidence, competitor_counts.competitor_confidence) else null end` | **Measurement** | **Yes (NULL)** | Empirical measurement of POI and competitor spatial confidence. Evaluates to `NULL` when either source is unmeasured. |
| `geo_grid_view` | `data_quality_score` | `case when h3_cells.h3_index is not null then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Derived from verifiable spatial cell identity existence. |
| `forecast_training_view` | `data_quality_score` | `case when latest_observation_time <= ... then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Point-in-time (PIT) leakage prevention rule based on observation timestamp. |
| `forecast_training_view` | `confidence` | `null::numeric as confidence` | **Measurement** | **Yes (NULL)** | The source CTE has no confidence field; it filters succeeded transactions and the event, observation, and ingestion timestamps to the three PIT bounds, but does not infer confidence from those predicates. |
| `store_machine_timeseries_view` | `data_quality_score` | `case when transaction and cycle rows are present and transaction timestamps are within the snapshot then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | A complete score requires both source sides of the full outer join plus the transaction observation and ingestion bounds. |
| `store_machine_timeseries_view` | `confidence` | `null::numeric as confidence` | **Measurement** | **Yes (NULL)** | Neither upstream relation exposes a confidence measurement; absence remains NULL instead of being inferred from row presence. |
| `store_machine_timeseries_view` | `available_minutes` | `1440.0 as available_minutes` | **Physical Constant** | No (1440.0) | Physical minutes per 24-hour day (24 * 60). |
| `intervention_panel_view` | `data_quality_score` | `case when start <= end and obs_start <= obs_end then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Verifiable temporal consistency predicate. |
| `intervention_panel_view` | `confidence` | `case outcomes.evidence_level when 'L5' then 1.0 when 'L4' then 0.95 ... else 0.0 end` | **Derived Rule** | No (0.0..1.0) | Causal evidence mapping according to ML-05 Evidence Ladder. |
| `intervention_panel_view` | `treatment_intensity` | `coalesce((approved_action_json->>'intensity')::numeric, 1.0)` | **Derived Contract** | No (1.0) | Baseline treatment multiplier (1.0 = 100% unscaled intervention effect). |
| `intervention_panel_view` | `eligibility_score` | `case eligibility_status when 'eligible' then 1.0 when 'manual_review' then 0.5 else 0.0 end` | **Derived Rule** | No (0.0..1.0) | Bounded status mapping rule. |
| `valuation_view` | `data_quality_score` | `case when normalized_gm_ttm >= 0 and income_value_p50 >= 0 then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Financial metric validity verification rule. |
| `valuation_view` | `confidence` | `0.8 as confidence` | **Derived Contract** | No (0.8) | Baseline valuation model confidence expectation. |
| `valuation_view` | `forecast_confidence`| `0.8 as forecast_confidence` | **Derived Contract** | No (0.8) | Baseline forecast confidence contract. |
| `network_plan_view` | `data_quality_score` | `case when solver_status in ('optimal', 'feasible') and entity is not null then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Mathematical solver execution state check. |
| `network_plan_view` | `confidence` | `case solver_status when 'optimal' then 1.0 when 'feasible' then 0.8 when 'timeout' then 0.4 else 0.0 end` | **Derived Rule** | No (0.0..1.0) | MIP/CP-SAT solver convergence confidence mapping. |
| `network_plan_view` | `risk_score` | `case actions.risk_level when 'low' then 0.2 when 'medium' then 0.5 when 'high' then 0.8 else 0.5 end` | **Derived Rule** | No (0.2..0.8) | Plan action risk tier mapping. |
| `brand_transfer_view` | `data_quality_score` | `case when source_brand_id is not null and target_brand_id is not null then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | The score only certifies that both identifiers required by the pair relation are present. |
| `brand_transfer_view` | `confidence` | `null::numeric as confidence` | **Measurement** | **Yes (NULL)** | `core.brands` supplies identifiers only; no relationship-confidence measurement is available in this view. |
| `brand_transfer_view` | `transfer_ratio` | `0.15 as transfer_ratio` | **Benchmark Constant**| No (0.15) | Retail customer brand transfer baseline parameter. |
| `ramp_curve_view` | `data_quality_score` | `case when store_id is not null and effective_from <= ... then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | The score certifies a present store entity that is effective at the feature snapshot. |
| `ramp_curve_view` | `confidence` | `null::numeric as confidence` | **Measurement** | **Yes (NULL)** | `core.stores` supplies the entity and effective date only; no ramp-confidence measurement is available in this view. |
| `ramp_curve_view` | `ramp_up_ratio` | `0.85 as ramp_up_ratio` | **Benchmark Constant**| No (0.85) | New store 6-month ramp-up ratio baseline. |
| `matched_control_view` | `data_quality_score` | `case when treated_store_id is not null and control_store_id is not null then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | The score only certifies that both store identifiers required by the pair relation are present. |
| `matched_control_view` | `confidence` | `null::numeric as confidence` | **Measurement** | **Yes (NULL)** | `core.stores` supplies the pair identifiers only; match confidence is not measured by this relation. |
| `matched_control_view` | `match_score` | `0.92 as match_score` | **Benchmark Constant**| No (0.92) | Store matching score benchmark baseline. |

### 2.2 PostgreSQL Production Views (`product_ops/modeling/sql/model_ready_views.sql`)

| View | Column | Expression | Classification | Nullable? | Rationale & Evidence |
|---|---|---|---|---|---|
| `model_ready.forecast_training_view` | `data_quality_score` | `CASE WHEN lineage_complete AND source_run_complete THEN 1.0 ELSE 0.0 END::double precision` | **Derived Rule** | No (0.0/1.0) | Full data-plane canonical lineage and ingestion completion audit. |
| `model_ready.forecast_training_view` | `confidence` | `1.0::double precision AS confidence` | **Derived Contract** | No (1.0) | The production view filters `transaction_status = 'succeeded'` and `currency = 'TWD'`, then gates the score through canonical lineage and ingestion-run completion. |
| `model_ready.candidate_site_view` | `data_quality_score` | `CASE WHEN identity_lineage_complete AND prior_lineage_complete AND label_lineage_complete AND prior_covered_days = 90 AND label_covered_days = 90 THEN 1.0 ELSE 0.0 END::double precision` | **Derived Rule** | No (0.0/1.0) | Full 90-day prior/label partition completeness and point-in-time sanity. |
| `model_ready.candidate_site_view` | `confidence` | `CASE WHEN geocode_confidence IS NOT NULL THEN least(geocode_confidence, 1.0)::double precision ELSE NULL END` | **Measurement** | **Yes (NULL)** | Empirical geocode confidence from `data_plane.place_geography`. Propagates NULL when unmeasured. |
| `model_ready.heatzone_training_view` | `data_quality_score` | `CASE WHEN identity_lineage_complete AND prior_lineage_complete AND label_lineage_complete AND prior_covered_days = 90 AND label_covered_days = 28 THEN 1.0 ELSE 0.0 END::double precision` | **Derived Rule** | No (0.0/1.0) | 90-day prior and 28-day forward partition coverage audit. |
| `model_ready.heatzone_training_view` | `confidence` | `CASE WHEN average_geocode_confidence IS NOT NULL THEN least(average_geocode_confidence, 1.0)::double precision ELSE NULL END` | **Measurement** | **Yes (NULL)** | Cell average geocode confidence from immutable place geography. Propagates NULL when unmeasured. |
| `model_ready.listing_property_valuation_view` | `data_quality_score` | `1.0::double precision AS data_quality_score` | **Derived Contract** | No (1.0) | Official government open data (MOI / NTPC) transactions with verified schema sha256 and succeeded ingestion. |
| `model_ready.listing_property_valuation_view` | `confidence` | `1.0::double precision AS confidence` | **Derived Contract** | No (1.0) | Official government real estate deed registration records. |

---

## 3. Comprehensive Column Lineage by View

### 3.1 `candidate_site_view` (DBT)
- **Grain**: `candidate_site_id` x decision snapshot
- **Consumer**: SiteScore
- **Source Tables**: `expansion.candidate_sites`, `expansion.listings`, `core.address_locations`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'candidate_site_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `candidate_sites.candidate_site_id::text` | Identifier | PK from `expansion.candidate_sites` |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['expansion.candidate_sites', 'expansion.listings', 'core.address_locations']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when rent > 0 and geocode_confidence >= 0.5 then 1.0 when rent > 0 then 0.8 else 0.0 end` | Derived Rule | Evaluates to 0.0 on missing rent |
| `confidence` | NUMERIC | **Yes** | `case when listings.confidence is not null and address_locations.geocode_confidence is not null then least(listings.confidence, address_locations.geocode_confidence) else null end` | **Measurement** | **Evaluates to NULL if either source measurement is absent** |
| `is_training_eligible` | BOOLEAN | No | `listings.rent_amount > 0 and candidate_sites.created_at <= ...` | Derived Rule | False if missing rent or future created |
| `is_scoring_eligible` | BOOLEAN | No | `listings.rent_amount > 0` | Derived Rule | False if missing rent |
| `exclusion_reason` | TEXT | No | `case when listings.rent_amount <= 0 then 'missing_rent' ... else '' end` | Derived Rule | Machine-readable exclusion code |
| `candidate_site_id` | UUID | No | `candidate_sites.candidate_site_id` | Identifier | Direct pass-through |
| `listing_id` | UUID | Yes | `candidate_sites.listing_id` | Foreign Key | NULL if unlinked |
| `target_format_code` | TEXT | Yes | `candidate_sites.target_format_code` | Dimension | NULL if unassigned |
| `rent_amount` | NUMERIC | Yes | `listings.rent_amount` | Measurement | NULL if unlinked |
| `area_ping` | NUMERIC | Yes | `listings.area_ping` | Measurement | NULL if unlinked |
| `frontage_m` | NUMERIC | Yes | `listings.frontage_m` | Measurement | NULL if unlinked |
| `floor` | NUMERIC | Yes | `listings.floor` | Dimension | NULL if unlinked |
| `utility_electricity_flag` | BOOLEAN | Yes | `listings.utility_electricity_flag` | Dimension | NULL if unlinked |
| `utility_drainage_flag` | BOOLEAN | Yes | `listings.utility_drainage_flag` | Dimension | NULL if unlinked |
| `utility_gas_flag` | BOOLEAN | Yes | `listings.utility_gas_flag` | Dimension | NULL if unlinked |
| `geocode_confidence` | NUMERIC | Yes | `address_locations.geocode_confidence` | Measurement | NULL if unlinked/unresolved |
| `h3_index` | TEXT | Yes | `address_locations.h3_res_9` | Spatial Key | NULL if geocoding failed |
| `rent_per_ping` | NUMERIC | Yes | `case when listings.area_ping > 0 then listings.rent_amount / listings.area_ping else null end` | Derived | NULL if area is 0 or missing |
| `hard_rule_fail_reasons` | TEXT[] | No | `array_remove(array[...], null)` | Derived Rule | Empty array if no failures |

---

### 3.2 `geo_grid_view` (DBT)
- **Grain**: `h3_index` (H3 spatial cell resolution 9)
- **Consumer**: HeatZone
- **Source Tables**: `geo.h3_cells`, `geo.pois`, `geo.competitor_stores`, `expansion.listings`, `core.address_locations`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'geo_grid_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `h3_cells.h3_index` | Identifier | PK from `geo.h3_cells` |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['geo.h3_cells', 'geo.pois', 'geo.competitor_stores', 'expansion.listings']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when h3_cells.h3_index is not null then 1.0 else 0.0 end` | Derived Rule | Evaluates to 1.0 for valid cells |
| `confidence` | NUMERIC | **Yes** | `case when poi_counts.poi_confidence is not null and competitor_counts.competitor_confidence is not null then least(poi_counts.poi_confidence, competitor_counts.competitor_confidence) else null end` | **Measurement** | **Evaluates to NULL if either source is unmeasured** |
| `is_training_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true for grid |
| `is_scoring_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true for grid |
| `exclusion_reason` | TEXT | No | `''` | Derived Rule | Empty string |
| `h3_index` | TEXT | No | `h3_cells.h3_index` | Spatial Key | Direct pass-through |
| `h3_resolution` | INTEGER | No | `h3_cells.h3_resolution` | Dimension | Direct pass-through |
| `admin_city` | TEXT | Yes | `h3_cells.admin_city` | Dimension | NULL if unmapped |
| `admin_district` | TEXT | Yes | `h3_cells.admin_district` | Dimension | NULL if unmapped |
| `poi_school_count` | BIGINT | No | `coalesce(poi_counts.poi_school_count, 0)` | Aggregation | 0 when no school POIs in cell |
| `poi_residential_count` | BIGINT | No | `coalesce(poi_counts.poi_residential_count, 0)` | Aggregation | 0 when no residential POIs in cell |
| `poi_market_count` | BIGINT | No | `coalesce(poi_counts.poi_market_count, 0)` | Aggregation | 0 when no market POIs in cell |
| `competitor_count_500m` | BIGINT | No | `coalesce(competitor_counts.competitor_count_500m, 0)` | Aggregation | 0 when no competitors in cell |
| `competitor_capacity_proxy_500m` | NUMERIC | No | `coalesce(competitor_counts.competitor_capacity_proxy_500m, 0)` | Aggregation | 0 when no competitors in cell |
| `rent_p50_per_ping` | NUMERIC | No | `coalesce(listing_counts.rent_p50_per_ping, 0)` | Aggregation | 0 when no active listings in cell |
| `listing_count_active` | BIGINT | No | `coalesce(listing_counts.listing_count_active, 0)` | Aggregation | 0 when no active listings in cell |

---

### 3.3 `forecast_training_view` (DBT)
- **Grain**: `store_id` x `date`
- **Consumer**: ForecastOps
- **Source Tables**: `core.transactions`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'forecast_training_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `store_id::text` | Identifier | Direct pass-through |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['core.transactions']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when latest_observation_time <= ... then 1.0 else 0.0 end` | Derived Rule | PIT guard |
| `confidence` | NUMERIC | **Yes** | `null::numeric` | Measurement | No upstream confidence field exists; the view preserves absence as NULL even though the source is filtered to succeeded, PIT-safe rows. |
| `is_training_eligible` | BOOLEAN | No | `latest_observation_time <= ...` | Derived Rule | False on PIT violation |
| `is_scoring_eligible` | BOOLEAN | No | `latest_ingested_at <= ...` | Derived Rule | False if ingested after snapshot |
| `exclusion_reason` | TEXT | No | `case when latest_observation_time > ... then 'pit_violation' else '' end` | Derived Rule | Machine-readable exclusion code |
| `store_id` | UUID | No | `store_id` | Identifier | Direct pass-through |
| `date` | DATE | No | `metric_date` | Dimension | Transaction aggregation date |
| `daily_net_revenue` | NUMERIC | Yes | `sum(net_amount)` | Aggregation | NULL if no transactions on day |
| `daily_gross_revenue` | NUMERIC | Yes | `sum(gross_amount)` | Aggregation | NULL if no transactions on day |
| `transaction_count` | BIGINT | No | `count(*)` | Aggregation | 0 if no transactions |
| `revenue_lag_1` | NUMERIC | Yes | `lag(daily_net_revenue, 1) over ...` | Derived Feature | NULL for initial day |
| `revenue_lag_7` | NUMERIC | Yes | `lag(daily_net_revenue, 7) over ...` | Derived Feature | NULL for initial 7 days |
| `rolling_mean_7` | NUMERIC | Yes | `avg(daily_net_revenue) over ... rows between 7 preceding and 1 preceding` | Derived Feature | NULL if no preceding data |
| `rolling_mean_28` | NUMERIC | Yes | `avg(daily_net_revenue) over ... rows between 28 preceding and 1 preceding`| Derived Feature | NULL if no preceding data |

---

### 3.4 `store_machine_timeseries_view` (DBT)
- **Grain**: `store_id` : `machine_id` : `date`
- **Consumer**: ForecastOps, Store Monitoring
- **Source Tables**: `core.transactions`, `core.machine_cycles`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'store_machine_timeseries_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `coalesce(t.store_id, c.store_id)::text || ':' || coalesce(t.machine_id, c.machine_id, 'store')::text || ':' || coalesce(t.metric_date, c.metric_date)::text` | Identifier | Composite key |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['core.transactions', 'core.machine_cycles']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when both source rows and transaction PIT timestamps are present then 1.0 else 0.0 end` | Derived Rule | Missing either source side yields 0.0. |
| `confidence` | NUMERIC | **Yes** | `null::numeric` | Measurement | No source confidence field exists, so missing measurement remains NULL. |
| `is_training_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `is_scoring_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `exclusion_reason` | TEXT | No | `''` | Derived Rule | Empty string |
| `store_id` | UUID | No | `coalesce(t.store_id, c.store_id)` | Identifier | Store identifier |
| `machine_id` | UUID | Yes | `coalesce(t.machine_id, c.machine_id)` | Identifier | Machine identifier |
| `date` | DATE | No | `coalesce(t.metric_date, c.metric_date)` | Dimension | Aggregation calendar date |
| `gross_revenue` | NUMERIC | No | `coalesce(t.gross_revenue, 0)` | Aggregation | 0 if no transactions |
| `net_revenue` | NUMERIC | No | `coalesce(t.net_revenue, 0)` | Aggregation | 0 if no transactions |
| `transaction_count` | BIGINT | No | `coalesce(t.transaction_count, 0)` | Aggregation | 0 if no transactions |
| `cycle_count` | BIGINT | No | `coalesce(c.cycle_count, 0)` | Aggregation | 0 if no cycles |
| `occupied_minutes` | NUMERIC | No | `coalesce(c.occupied_seconds, 0) / 60.0` | Derived | 0 if no cycles |
| `available_minutes` | NUMERIC | No | `1440.0` | Physical Constant | 24 * 60 minutes/day |
| `downtime_minutes` | NUMERIC | No | `greatest(0.0, 1440.0 - (coalesce(c.occupied_seconds, 0) / 60.0))` | Derived | 1440.0 if idle all day |
| `utilization_rate` | NUMERIC | No | `coalesce(c.occupied_seconds, 0) / 86400.0` | Derived | 0.0 if idle all day |
| `avg_cycle_duration_sec`| NUMERIC | No | `coalesce(c.avg_cycle_duration_sec, 0)` | Aggregation | 0 if no cycles |
| `refund_count` | BIGINT | No | `coalesce(t.refund_count, 0)` | Aggregation | 0 if no refunds |

---

### 3.5 `intervention_panel_view` (DBT)
- **Grain**: `store_id` : `intervention_id` : `date`
- **Consumer**: InterventionOps, PriceOps
- **Source Tables**: `operations.interventions`, `operations.intervention_outcomes`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'intervention_panel_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `interventions.store_id::text || ':' || interventions.intervention_id::text || ':' || interventions.start_time::date::text` | Identifier | Composite key |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['operations.interventions', 'operations.intervention_outcomes']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when start <= end and obs_start <= obs_end then 1.0 else 0.0 end` | Derived Rule | 0.0 on timestamp mismatch |
| `confidence` | NUMERIC | No | `case outcomes.evidence_level when 'L5' then 1.0 ... else 0.0 end` | Derived Rule | ML-05 Evidence Ladder |
| `is_training_eligible` | BOOLEAN | No | `outcomes.evidence_level is not null and ...` | Derived Rule | False if unrated or unmatured |
| `is_scoring_eligible` | BOOLEAN | No | `interventions.start_time <= ...` | Derived Rule | False if future treatment |
| `exclusion_reason` | TEXT | No | `case when ... then 'evidence_unrated' ... else '' end` | Derived Rule | Exclusion code |
| `intervention_id` | UUID | No | `interventions.intervention_id` | Identifier | Direct pass-through |
| `store_id` | UUID | No | `interventions.store_id` | Identifier | Direct pass-through |
| `date` | DATE | No | `interventions.start_time::date` | Dimension | Start date |
| `intervention_type` | TEXT | No | `interventions.intervention_type` | Dimension | Type code |
| `treatment_flag` | BOOLEAN | No | `true` | Dimension | Active treatment marker |
| `treatment_intensity`| NUMERIC | No | `coalesce((approved_action_json ->> 'intensity')::numeric, 1.0)` | Derived Contract | Default 1.0 (unscaled) |
| `eligibility_score` | NUMERIC | No | `case interventions.eligibility_status when 'eligible' then 1.0 when 'manual_review' then 0.5 else 0.0 end` | Derived Rule | Status mapping |
| `propensity_score` | NUMERIC | Yes | `null::numeric` | Parameter | NULL until model estimation |
| `overlap_interventions` | TEXT[] | No | `array[]::text[]` | Structure | Overlap list |
| `pre_period_flag` | BOOLEAN | No | `prediction_origin_time < start_time` | Dimension | Relative timing |
| `treatment_period_flag` | BOOLEAN | No | `prediction_origin_time >= start_time and < end_time` | Dimension | Relative timing |
| `observation_period_flag`| BOOLEAN | No | `prediction_origin_time >= obs_start and <= obs_end` | Dimension | Relative timing |
| `outcome_revenue` | NUMERIC | Yes | `outcomes.incremental_revenue` | Measurement | NULL if outcome not yet mature |
| `outcome_gm` | NUMERIC | Yes | `outcomes.incremental_gross_margin` | Measurement | NULL if outcome not yet mature |
| `evidence_level` | TEXT | Yes | `outcomes.evidence_level` | Category | NULL if outcome unrated |

---

### 3.6 `valuation_view` (DBT)
- **Grain**: `store_id` : `valuation_date`
- **Consumer**: DealRoomAVM
- **Source Tables**: `asset.valuation_runs`, `operations.forecast_outputs`, `core.stores`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'valuation_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `valuation_runs.store_id::text || ':' || valuation_runs.valuation_date::text` | Identifier | Composite key |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['asset.valuation_runs', 'operations.forecast_outputs', 'core.stores']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when normalized_gm_ttm >= 0 and income_value_p50 >= 0 then 1.0 else 0.0 end` | Derived Rule | 0.0 on negative financial inputs |
| `confidence` | NUMERIC | No | `0.8` | Derived Contract | Baseline model confidence |
| `is_training_eligible` | BOOLEAN | No | `valuation_runs.valuation_date <= ...::date` | Derived Rule | False if future valuation |
| `is_scoring_eligible` | BOOLEAN | No | `valuation_runs.valuation_date <= ...::date` | Derived Rule | False if future valuation |
| `exclusion_reason` | TEXT | No | `case when valuation_date > ... then 'valuation_after_snapshot' else '' end` | Derived Rule | Exclusion code |
| `store_id` | UUID | No | `valuation_runs.store_id` | Identifier | Direct pass-through |
| `valuation_date` | DATE | No | `valuation_runs.valuation_date` | Dimension | Valuation date |
| `gm_ttm` | NUMERIC | Yes | `valuation_runs.normalized_gm_ttm` | Measurement | NULL if uncalculated |
| `normalized_gm_ttm` | NUMERIC | Yes | `valuation_runs.normalized_gm_ttm` | Measurement | NULL if uncalculated |
| `gm_fwd_p10` | NUMERIC | Yes | `forecast_outputs.p10` | Derived | Forward forecast p10 |
| `gm_fwd_p50` | NUMERIC | Yes | `coalesce(forecast_outputs.p50, valuation_runs.gm_fwd_p50)` | Derived | Forward forecast p50 |
| `gm_fwd_p90` | NUMERIC | Yes | `forecast_outputs.p90` | Derived | Forward forecast p90 |
| `asset_book_value` | NUMERIC | Yes | `valuation_runs.asset_value_p50` | Measurement | NULL if uncalculated |
| `remaining_asset_life`| NUMERIC | Yes | `null::numeric` | Measurement | NULL (Batch 1 depreciation) |
| `lease_remaining_months`| NUMERIC | Yes | `null::numeric` | Measurement | NULL |
| `rent_amount` | NUMERIC | Yes | `null::numeric` | Measurement | NULL |
| `intervention_adjustment`| NUMERIC | No | `0.0` | Constant | 0.0 baseline |
| `forecast_confidence`| NUMERIC | No | `0.8` | Derived Contract | Baseline forecast confidence |
| `comparable_count` | INTEGER | No | `0` | Dimension | 0 baseline |
| `liquidity_features` | JSONB | No | `jsonb_build_object('income_value_p50', ...)` | Structure | Formatted JSONB structure |

---

### 3.7 `network_plan_view` (DBT)
- **Grain**: `planning_entity_id` : `planning_quarter`
- **Consumer**: NetPlan
- **Source Tables**: `network.network_plans`, `network.network_plan_actions`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'network_plan_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `coalesce(actions.store_id::text, actions.candidate_site_id::text) || ':' || actions.quarter` | Identifier | Composite key |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['network.network_plans', 'network.network_plan_actions']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when solver_status in ('optimal', 'feasible') and ... is not null then 1.0 else 0.0 end` | Derived Rule | 0.0 on solver failure |
| `confidence` | NUMERIC | No | `case solver_status when 'optimal' then 1.0 when 'feasible' then 0.8 when 'timeout' then 0.4 else 0.0 end` | Derived Rule | Solver confidence map |
| `is_training_eligible` | BOOLEAN | No | `plans.planning_period_start <= ...::date` | Derived Rule | False if future planning |
| `is_scoring_eligible` | BOOLEAN | No | `plans.solver_status in ('optimal', 'feasible')` | Derived Rule | False on infeasible |
| `exclusion_reason` | TEXT | No | `case when solver_status not in ... then 'solver_not_usable' ... else '' end` | Derived Rule | Exclusion code |
| `entity_type` | TEXT | No | `case when actions.candidate_site_id is not null then 'candidate_site' else 'existing_store' end` | Dimension | Type classification |
| `planning_entity_id` | TEXT | No | `coalesce(actions.store_id::text, actions.candidate_site_id::text)` | Identifier | Entity identifier |
| `planning_quarter` | TEXT | No | `actions.quarter` | Dimension | Quarter (e.g. 2026_Q2) |
| `action_candidates` | TEXT[] | No | `array[actions.action_type]` | Dimension | Array of actions |
| `expected_gm_p10` | NUMERIC | Yes | `null::numeric` | Parameter | NULL |
| `expected_gm_p50` | NUMERIC | Yes | `actions.expected_gm_delta` | Measurement | Output delta from solver |
| `expected_gm_p90` | NUMERIC | Yes | `null::numeric` | Parameter | NULL |
| `capital_required` | NUMERIC | Yes | `actions.capital_required` | Measurement | Capital requirement |
| `lease_constraint` | JSONB | Yes | `plans.constraint_summary_json -> 'lease'` | Structure | Lease constraint JSON |
| `construction_lead_time`| INTEGER | Yes | `null::integer` | Parameter | NULL |
| `staffing_constraint` | JSONB | Yes | `plans.constraint_summary_json -> 'staffing'` | Structure | Staffing constraint JSON |
| `cannibalization_matrix_row` | JSONB | No | `jsonb_build_object('network_plan_action_id', actions.network_plan_action_id)` | Structure | Matrix reference |
| `valuation_p50` | NUMERIC | Yes | `null::numeric` | Parameter | NULL |
| `risk_score` | NUMERIC | No | `case actions.risk_level when 'low' then 0.2 when 'medium' then 0.5 when 'high' then 0.8 else 0.5 end` | Derived Rule | Risk mapping |
| `hard_constraint_flags`| JSONB | Yes | `plans.constraint_summary_json` | Structure | Constraint disclosures |

---

### 3.8 `brand_transfer_view` (DBT)
- **Grain**: `source_brand_id` : `target_brand_id` : `location_type` : `store_age_bucket`
- **Consumer**: SiteScore
- **Source Tables**: `core.brands` (Subject to Batch 0 verification)

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'brand_transfer_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `source_brand_id || '_' || target_brand_id` | Identifier | Composite brand pair key |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['core.brands']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when both brand identifiers are not null then 1.0 else 0.0 end` | Derived Rule | The identifier-presence predicate is the only quality evidence in this relation. |
| `confidence` | NUMERIC | **Yes** | `null::numeric` | Measurement | No relationship-confidence source is present. |
| `is_training_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `is_scoring_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `exclusion_reason` | TEXT | No | `''` | Derived Rule | Empty string |
| `source_brand_id` | UUID | No | `b1.brand_id` | Identifier | Direct pass-through |
| `target_brand_id` | UUID | No | `b2.brand_id` | Identifier | Direct pass-through |
| `location_type` | TEXT | No | `'urban'` | Dimension | Urban tier |
| `store_format_code` | TEXT | No | `'ODAY_G2'` | Dimension | Format code |
| `store_age_bucket` | TEXT | No | `'0_6m'` | Dimension | Age tier |
| `transfer_ratio` | NUMERIC | No | `0.15` | Benchmark Constant | Standard retail benchmark parameter |

---

### 3.9 `ramp_curve_view` (DBT)
- **Grain**: `store_id` : `calendar_date`
- **Consumer**: SiteScore, ForecastOps
- **Source Tables**: `core.stores`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'ramp_curve_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `store_id::text` | Identifier | Direct pass-through |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['core.stores']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when store_id is not null and effective_from <= ... then 1.0 else 0.0 end` | Derived Rule | The source-effective predicate is the only quality evidence in this relation. |
| `confidence` | NUMERIC | **Yes** | `null::numeric` | Measurement | No ramp-confidence source is present. |
| `is_training_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `is_scoring_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `exclusion_reason` | TEXT | No | `''` | Derived Rule | Empty string |
| `store_id` | UUID | No | `store_id` | Identifier | Direct pass-through |
| `store_cohort` | TEXT | No | `'2026_Q1'` | Dimension | Cohort identifier |
| `store_age_months` | INTEGER | No | `6` | Dimension | Store age in months |
| `calendar_date` | DATE | No | `{{ var('feature_snapshot_time') }}::timestamptz::date` | Dimension | Snapshot calendar date |
| `ramp_up_ratio` | NUMERIC | No | `0.85` | Benchmark Constant | 6-month ramp-up ratio baseline |

---

### 3.10 `matched_control_view` (DBT)
- **Grain**: `treated_store_id` : `control_store_id` : `match_date`
- **Consumer**: AdLift
- **Source Tables**: `core.stores`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'matched_control_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `treated_store_id || '_' || control_store_id` | Identifier | Composite store pair key |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `{{ var('feature_snapshot_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `{{ var('prediction_origin_time') }}::timestamptz` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['core.stores']` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `case when both treated and control store identifiers are not null then 1.0 else 0.0 end` | Derived Rule | The identifier-presence predicate is the only quality evidence in this relation. |
| `confidence` | NUMERIC | **Yes** | `null::numeric` | Measurement | No match-confidence source is present. |
| `is_training_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `is_scoring_eligible` | BOOLEAN | No | `true` | Derived Rule | Constant true |
| `exclusion_reason` | TEXT | No | `''` | Derived Rule | Empty string |
| `treated_store_id` | UUID | No | `s1.store_id` | Identifier | Treated store ID |
| `control_store_id` | UUID | No | `s2.store_id` | Identifier | Control store ID |
| `match_date` | DATE | No | `{{ var('feature_snapshot_time') }}::timestamptz::date` | Dimension | Snapshot match date |
| `match_score` | NUMERIC | No | `0.92` | Benchmark Constant | Synthetic matching benchmark baseline |

---

### 3.11 PostgreSQL Production Views (`product_ops/modeling/sql/model_ready_views.sql`)

#### `model_ready.listing_property_valuation_view`
- **Grain**: Official sale transaction outcome (`source_id:authority_partition:source_record_id:source_variant_id`)
- **Consumer**: DealRoom Real Estate Research Models
- **Source Tables**: `external_data.real_estate_transactions`, `external_data.real_estate_ingestion_runs`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'listing_property_valuation_view'::text` | Constant | Static contract string |
| `view_version` | TEXT | No | `'listing-property-valuation-view-v1'::text` | Constant | Static contract version |
| `entity_id` | TEXT | No | `concat('tw-official-sale:', source_id, ':', ...)` | Identifier | Composite transaction key |
| `source_id` | TEXT | No | `outcome.source_id` | Identifier | Authority source ID |
| `authority_partition` | TEXT | No | `outcome.authority_partition` | Dimension | Partition key |
| `source_variant_id` | TEXT | No | `outcome.source_variant_id` | Identifier | Source variant ID |
| `municipality` | TEXT | Yes | `outcome.municipality` | Dimension | Administrative city |
| `district` | TEXT | Yes | `outcome.district` | Dimension | Administrative district |
| `market_segment` | TEXT | Yes | `concat_ws(':', municipality, district, transaction_target)` | Dimension | Segment composite |
| `transaction_target` | TEXT | Yes | `outcome.transaction_target` | Dimension | Target property type |
| `realized_transaction_at`| TIMESTAMPTZ | Yes | `ranked.transaction_date::timestamp AT TIME ZONE 'Asia/Taipei'` | Dimension | Local transaction timestamp |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `ingestion.fetched_at` | Provenance | Ingestion fetch timestamp |
| `prediction_origin_time` | TIMESTAMPTZ | No | `ingestion.fetched_at + interval '1 microsecond'` | Provenance | Offset timestamp |
| `label_maturity_time` | TIMESTAMPTZ | No | `ingestion.fetched_at` | Provenance | Label maturity timestamp |
| `realized_transaction_price` | DOUBLE PRECISION | No | `total_price_twd::double precision` | Measurement | Actual transaction price |
| `land_area_sqm` | DOUBLE PRECISION | No | `coalesce(land_area_sqm, 0)::double precision` | Measurement | 0 if unrecorded |
| `building_area_sqm` | DOUBLE PRECISION | No | `coalesce(building_area_sqm, 0)::double precision` | Measurement | 0 if unrecorded |
| `room_count` | DOUBLE PRECISION | No | `coalesce(room_count, 0)::double precision` | Dimension | 0 if unrecorded |
| `hall_count` | DOUBLE PRECISION | No | `coalesce(hall_count, 0)::double precision` | Dimension | 0 if unrecorded |
| `bathroom_count` | DOUBLE PRECISION | No | `coalesce(bathroom_count, 0)::double precision` | Dimension | 0 if unrecorded |
| `building_type` | TEXT | No | `coalesce(building_type, 'UNKNOWN')::text` | Dimension | 'UNKNOWN' fallback |
| `main_use` | TEXT | No | `coalesce(main_use, 'UNKNOWN')::text` | Dimension | 'UNKNOWN' fallback |
| `main_material` | TEXT | No | `coalesce(main_material, 'UNKNOWN')::text` | Dimension | 'UNKNOWN' fallback |
| `building_age_years` | DOUBLE PRECISION | No | `case when ... then 0 else extract(year from ...) - completion_year end::double precision` | Derived | 0 if unrecorded/future |
| `completion_year_known`| BOOLEAN | No | `ranked.completion_year is not null and ...` | Derived Rule | True if completion year valid |
| `parking_area_sqm` | DOUBLE PRECISION | No | `coalesce(parking_area_sqm, 0)::double precision` | Dimension | 0 if unrecorded |
| `has_elevator` | BOOLEAN | No | `coalesce(ranked.has_elevator, FALSE)` | Dimension | FALSE fallback |
| `elevator_known` | BOOLEAN | No | `ranked.has_elevator is not null` | Derived Rule | True if elevator recorded |
| `source_snapshot_ids` | TEXT[] | No | `ARRAY[source_snapshot_id]::text[]` | Provenance | Source snapshot array |
| `data_quality_score` | DOUBLE PRECISION | No | `1.0::double precision` | **Derived Contract** | Official government deed registration |
| `confidence` | DOUBLE PRECISION | No | `1.0::double precision` | **Derived Contract** | Official government deed registration |
| `is_training_eligible` | BOOLEAN | No | `ingestion_status = 'SUCCEEDED' and total_price_twd > 0 and ...` | Derived Rule | Multi-predicate gate |
| `is_scoring_eligible` | BOOLEAN | No | `FALSE` | Derived Rule | Constant FALSE for outcome relation |
| `exclusion_reason` | TEXT | Yes | `CASE WHEN ingestion_status <> 'SUCCEEDED' THEN 'SOURCE_RUN_NOT_COMPLETE' ... ELSE NULL END` | Derived Rule | Machine-readable exclusion code |

---

## 4. Downstream Consumer Integration Guidance

### 4.1 SiteScore Consumer (`modules/sitescore/domain/scoring.py`)
1. **Handling `confidence is None`**:
   - When `candidate_site_view.confidence` is `None`, SiteScore must fail-closed via `ODP-FR-SITE-004` feasibility rules.
   - Do not apply `or 1.0` in `to_sitescore_model_row`.
   - Reason for abstention must explicitly state `MISSING_SOURCE_CONFIDENCE`.

### 4.2 DatasetSnapshot / LearningHub Admission (`modules/learninghub/domain/dataset_snapshot.py`)
1. **Row Mapper**:
   - The row mapper in `dataset_snapshot.py` must preserve `None` for `confidence` and `data_quality_score` when missing from source views.
   - Training dataset admission must block records with `confidence is None` or log explicit exclusion codes rather than silently promoting unmeasured rows.
