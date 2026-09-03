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
3. **Canonical Derived Contracts**: Fixed structural invariants of authoritative systems (e.g., immutable double-entry transaction ledgers, physical constants, benchmark baselines).

When measurements are missing, assigning a fake `1.0` gives unmeasured or unverified records the appearance of perfection, silently evading quality and feasibility gates downstream.

This specification formalizes:
- The removal of fake 1.0 defaults on empirical measurements (`candidate_site_view` and `geo_grid_view`).
- The complete classification and audit of every literal `1.0` across all 10 model-ready views.
- Comprehensive column-by-column lineage and nullability semantics for parallel consumer remediation (SiteScore, DatasetSnapshot, DealRoomAVM, ForecastOps).

---

## 2. Audit and Classification of 1.0 Literals and Coalesce Expressions

Every occurrence of `1.0`, `coalesce(..., 1.0)`, and score defaults across all 10 views in `pipelines/dbt/models/model_ready/` and PostgreSQL contracts in `product_ops/modeling/sql/model_ready_views.sql` is categorized below:

| View | Column | Expression | Classification | Nullable? | Rationale & Evidence |
|---|---|---|---|---|---|
| `candidate_site_view` | `confidence` | `least(listings.confidence, address_locations.geocode_confidence)` | **Measurement** | **Yes (NULL)** | Empirical measurement of geocoding and listing quality. Must return `NULL` when source measurements are absent. |
| `candidate_site_view` | `data_quality_score` | `case when rent > 0 and geocode_confidence >= 0.5 then 1.0 when rent > 0 then 0.8 else 0.0 end` | **Derived Rule** | No (0.0..1.0) | Derived score based on verifiable candidate site data completeness. |
| `geo_grid_view` | `confidence` | `least(poi_counts.poi_confidence, competitor_counts.competitor_confidence)` | **Measurement** | **Yes (NULL)** | Empirical measurement of POI and competitor spatial confidence. Must return `NULL` when both sources are unmeasured. |
| `geo_grid_view` | `data_quality_score` | `case when h3_cells.h3_index is not null then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Derived from verifiable spatial cell identity existence. |
| `forecast_training_view` | `data_quality_score` | `case when latest_observation_time <= snapshot_time then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Point-in-time (PIT) leakage prevention rule based on observation timestamp. |
| `forecast_training_view` | `confidence` | `1.0` | **Derived Contract** | No (1.0) | Settled financial transactions in `core.transactions` (`transaction_status = 'succeeded'`) are authoritative ledger entries. |
| `store_machine_timeseries_view` | `data_quality_score` | `1.0` | **Derived Contract** | No (1.0) | Verified core transaction and machine cycle telemetry aggregation. |
| `store_machine_timeseries_view` | `confidence` | `1.0` | **Derived Contract** | No (1.0) | Machine telemetry sensor records from canonical data plane. |
| `store_machine_timeseries_view` | `available_minutes` | `1440.0` | **Physical Constant** | No (1440.0) | Physical minutes per 24-hour day (24 * 60). |
| `intervention_panel_view` | `data_quality_score` | `case when start <= end and obs_start <= obs_end then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Verifiable temporal consistency predicate. |
| `intervention_panel_view` | `confidence` | `case outcomes.evidence_level when 'L5' then 1.0 when 'L4' then 0.95 ... else 0.0 end` | **Derived Rule** | No (0.0..1.0) | Causal evidence mapping according to ML-05 Evidence Ladder. |
| `intervention_panel_view` | `treatment_intensity` | `coalesce((action_json->>'intensity')::numeric, 1.0)` | **Derived Contract** | No (1.0) | Baseline treatment multiplier (1.0 = 100% unscaled intervention effect). |
| `intervention_panel_view` | `eligibility_score` | `case eligibility_status when 'eligible' then 1.0 when 'manual_review' then 0.5 else 0.0 end` | **Derived Rule** | No (0.0..1.0) | Bounded status mapping rule. |
| `valuation_view` | `data_quality_score` | `case when normalized_gm_ttm >= 0 and income_value_p50 >= 0 then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Financial metric validity verification rule. |
| `valuation_view` | `confidence` | `0.8` | **Derived Contract** | No (0.8) | Baseline valuation model confidence expectation. |
| `valuation_view` | `forecast_confidence`| `0.8` | **Derived Contract** | No (0.8) | Baseline forecast confidence contract. |
| `network_plan_view` | `data_quality_score` | `case when solver_status in ('optimal', 'feasible') and entity is not null then 1.0 else 0.0 end` | **Derived Rule** | No (0.0/1.0) | Mathematical solver execution state check. |
| `network_plan_view` | `confidence` | `case solver_status when 'optimal' then 1.0 when 'feasible' then 0.8 when 'timeout' then 0.4 else 0.0 end` | **Derived Rule** | No (0.0..1.0) | MIP/CP-SAT solver convergence confidence mapping. |
| `network_plan_view` | `risk_score` | `case actions.risk_level when 'low' then 0.2 when 'medium' then 0.5 when 'high' then 0.8 else 0.5 end` | **Derived Rule** | No (0.2..0.8) | Plan action risk tier mapping. |
| `brand_transfer_view` | `data_quality_score` | `1.0` | **Derived Contract** | No (1.0) | Baseline synthetic pair matrix from `core.brands`. (Subject to Batch 0 verification). |
| `brand_transfer_view` | `confidence` | `1.0` | **Derived Contract** | No (1.0) | Baseline relationship confidence contract. |
| `brand_transfer_view` | `transfer_ratio` | `0.15` | **Benchmark Constant**| No (0.15) | Retail customer brand transfer baseline parameter. |
| `ramp_curve_view` | `data_quality_score` | `1.0` | **Derived Contract** | No (1.0) | Baseline store entity validation from `core.stores`. |
| `ramp_curve_view` | `confidence` | `1.0` | **Derived Contract** | No (1.0) | Baseline ramp curve confidence contract. |
| `ramp_curve_view` | `ramp_up_ratio` | `0.85` | **Benchmark Constant**| No (0.85) | New store 6-month ramp-up ratio baseline. |
| `matched_control_view` | `data_quality_score` | `1.0` | **Derived Contract** | No (1.0) | Store pair relationship baseline from `core.stores`. |
| `matched_control_view` | `confidence` | `1.0` | **Derived Contract** | No (1.0) | Control group pairing confidence contract. |
| `matched_control_view` | `match_score` | `0.92` | **Benchmark Constant**| No (0.92) | Store matching score benchmark baseline. |

---

## 3. Comprehensive Column Lineage by View

### 3.1 `candidate_site_view`
- **Grain**: `candidate_site_id` x decision snapshot
- **Consumer**: SiteScore
- **Source Tables**: `expansion.candidate_sites`, `expansion.listings`, `core.address_locations`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'candidate_site_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `candidate_sites.candidate_site_id::text` | Identifier | PK from `expansion.candidate_sites` |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `var('feature_snapshot_time')` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `var('prediction_origin_time')` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['expansion.candidate_sites', ...]` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `CASE WHEN rent > 0 AND geocode_confidence >= 0.5 THEN 1.0 ...` | Derived Rule | Evaluates to 0.0 on missing rent |
| `confidence` | NUMERIC | **Yes** | `least(listings.confidence, address_locations.geocode_confidence)` | **Measurement** | **Evaluates to NULL if measurements absent** |
| `is_training_eligible` | BOOLEAN | No | `listings.rent_amount > 0 AND candidate_sites.created_at <= ...` | Derived Rule | False if missing rent or future created |
| `is_scoring_eligible` | BOOLEAN | No | `listings.rent_amount > 0` | Derived Rule | False if missing rent |
| `exclusion_reason` | TEXT | No | `CASE WHEN listings.rent_amount <= 0 THEN 'missing_rent' ...` | Derived Rule | Machine-readable exclusion code |
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
| `rent_per_ping` | NUMERIC | Yes | `CASE WHEN area_ping > 0 THEN rent_amount / area_ping ELSE NULL END` | Derived | NULL if area is 0 or missing |
| `hard_rule_fail_reasons` | TEXT[] | No | `array_remove(array[...], null)` | Derived Rule | Empty array if no failures |

---

### 3.2 `geo_grid_view`
- **Grain**: `h3_index` (H3 spatial cell resolution 9)
- **Consumer**: HeatZone
- **Source Tables**: `geo.h3_cells`, `geo.pois`, `geo.competitor_stores`, `expansion.listings`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `view_name` | TEXT | No | `'geo_grid_view'` | Constant | Static contract string |
| `view_version` | TEXT | No | `'v1'` | Constant | Static contract version |
| `entity_id` | TEXT | No | `h3_cells.h3_index` | Identifier | PK from `geo.h3_cells` |
| `feature_snapshot_time` | TIMESTAMPTZ | No | `var('feature_snapshot_time')` | Provenance | dbt runtime variable |
| `prediction_origin_time` | TIMESTAMPTZ | No | `var('prediction_origin_time')` | Provenance | dbt runtime variable |
| `source_snapshot_ids` | TEXT[] | No | `array['geo.h3_cells', ...]` | Provenance | Static array of source tables |
| `data_quality_score` | NUMERIC | No | `CASE WHEN h3_cells.h3_index IS NOT NULL THEN 1.0 ELSE 0.0 END` | Derived Rule | Evaluates to 1.0 for valid cells |
| `confidence` | NUMERIC | **Yes** | `least(poi_counts.poi_confidence, competitor_counts.competitor_confidence)` | **Measurement** | **Evaluates to NULL if both absent** |
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

### 3.3 `forecast_training_view`
- **Grain**: `store_id` x `date`
- **Consumer**: ForecastOps
- **Source Tables**: `core.transactions`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `entity_id` | TEXT | No | `store_id::text` | Identifier | PK from `core.transactions` |
| `data_quality_score` | NUMERIC | No | `CASE WHEN latest_observation_time <= snapshot_time THEN 1.0 ELSE 0.0 END` | Derived Rule | PIT guard |
| `confidence` | NUMERIC | No | `1.0` | Derived Contract | Settled ledger transactions |
| `daily_net_revenue` | NUMERIC | Yes | `sum(net_amount)` | Aggregation | NULL if no transactions on day |
| `daily_gross_revenue` | NUMERIC | Yes | `sum(gross_amount)` | Aggregation | NULL if no transactions on day |
| `transaction_count` | BIGINT | No | `count(*)` | Aggregation | 0 if no transactions |
| `revenue_lag_1` | NUMERIC | Yes | `lag(daily_net_revenue, 1) over ...` | Derived Feature | NULL for initial day |
| `revenue_lag_7` | NUMERIC | Yes | `lag(daily_net_revenue, 7) over ...` | Derived Feature | NULL for initial 7 days |
| `rolling_mean_7` | NUMERIC | Yes | `avg(daily_net_revenue) over ... rows between 7 preceding and 1 preceding` | Derived Feature | NULL if no preceding data |
| `rolling_mean_28` | NUMERIC | Yes | `avg(daily_net_revenue) over ... rows between 28 preceding and 1 preceding`| Derived Feature | NULL if no preceding data |

---

### 3.4 `store_machine_timeseries_view`
- **Grain**: `store_id` : `machine_id` : `date`
- **Consumer**: ForecastOps, Store Monitoring
- **Source Tables**: `core.transactions`, `core.machine_cycles`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `entity_id` | TEXT | No | `store_id || ':' || machine_id || ':' || date` | Identifier | Composite key |
| `data_quality_score` | NUMERIC | No | `1.0` | Derived Contract | Authoritative operational logs |
| `confidence` | NUMERIC | No | `1.0` | Derived Contract | Telemetry contract |
| `gross_revenue` | NUMERIC | No | `coalesce(t.gross_revenue, 0)` | Aggregation | 0 if no transactions |
| `net_revenue` | NUMERIC | No | `coalesce(t.net_revenue, 0)` | Aggregation | 0 if no transactions |
| `transaction_count` | BIGINT | No | `coalesce(t.transaction_count, 0)` | Aggregation | 0 if no transactions |
| `cycle_count` | BIGINT | No | `coalesce(c.cycle_count, 0)` | Aggregation | 0 if no cycles |
| `occupied_minutes` | NUMERIC | No | `coalesce(c.occupied_seconds, 0) / 60.0` | Derived | 0 if no cycles |
| `available_minutes` | NUMERIC | No | `1440.0` | Physical Constant | 24 * 60 minutes/day |
| `downtime_minutes` | NUMERIC | No | `greatest(0.0, 1440.0 - occupied_minutes)` | Derived | 1440.0 if idle all day |
| `utilization_rate` | NUMERIC | No | `coalesce(c.occupied_seconds, 0) / 86400.0` | Derived | 0.0 if idle all day |
| `avg_cycle_duration_sec`| NUMERIC | No | `coalesce(c.avg_cycle_duration_sec, 0)` | Aggregation | 0 if no cycles |
| `refund_count` | BIGINT | No | `coalesce(t.refund_count, 0)` | Aggregation | 0 if no refunds |

---

### 3.5 `intervention_panel_view`
- **Grain**: `store_id` : `intervention_id` : `date`
- **Consumer**: InterventionOps, PriceOps
- **Source Tables**: `operations.interventions`, `operations.intervention_outcomes`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `data_quality_score` | NUMERIC | No | `CASE WHEN start <= end AND obs_start <= obs_end THEN 1.0 ELSE 0.0 END` | Derived Rule | 0.0 on timestamp mismatch |
| `confidence` | NUMERIC | No | `CASE outcomes.evidence_level WHEN 'L5' THEN 1.0 ... ELSE 0.0 END` | Derived Rule | ML-05 Evidence Ladder |
| `treatment_flag` | BOOLEAN | No | `true` | Dimension | Active treatment marker |
| `treatment_intensity`| NUMERIC | No | `coalesce((action_json->>'intensity')::numeric, 1.0)` | Derived Contract | Default 1.0 (unscaled) |
| `eligibility_score` | NUMERIC | No | `CASE eligibility_status WHEN 'eligible' THEN 1.0 ... ELSE 0.0 END` | Derived Rule | Status mapping |
| `propensity_score` | NUMERIC | Yes | `null::numeric` | Parameter | NULL until model estimation |
| `outcome_revenue` | NUMERIC | Yes | `outcomes.incremental_revenue` | Measurement | NULL if outcome not yet mature |
| `outcome_gm` | NUMERIC | Yes | `outcomes.incremental_gross_margin` | Measurement | NULL if outcome not yet mature |
| `evidence_level` | TEXT | Yes | `outcomes.evidence_level` | Category | NULL if outcome unrated |

---

### 3.6 `valuation_view`
- **Grain**: `store_id` : `valuation_date`
- **Consumer**: DealRoomAVM
- **Source Tables**: `asset.valuation_runs`, `operations.forecast_outputs`, `core.stores`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `data_quality_score` | NUMERIC | No | `CASE WHEN normalized_gm_ttm >= 0 AND income_value_p50 >= 0 THEN 1.0 ELSE 0.0 END` | Derived Rule | 0.0 on negative financial inputs |
| `confidence` | NUMERIC | No | `0.8` | Derived Contract | Baseline model confidence |
| `forecast_confidence`| NUMERIC | No | `0.8` | Derived Contract | Baseline forecast confidence |
| `gm_ttm` | NUMERIC | Yes | `valuation_runs.normalized_gm_ttm` | Measurement | NULL if no valuation run |
| `gm_fwd_p50` | NUMERIC | Yes | `coalesce(forecast_outputs.p50, valuation_runs.gm_fwd_p50)` | Derived | Forecast output priority |
| `asset_book_value` | NUMERIC | Yes | `valuation_runs.asset_value_p50` | Measurement | NULL if uncalculated |
| `remaining_asset_life`| NUMERIC | Yes | `null::numeric` | Measurement | NULL (Batch 1 depreciation) |
| `lease_remaining_months`| NUMERIC | Yes | `null::numeric` | Measurement | NULL |
| `liquidity_features` | JSONB | No | `jsonb_build_object('income_value_p50', ...)` | Structure | Formatted JSONB structure |

---

### 3.7 `network_plan_view`
- **Grain**: `planning_entity_id` : `planning_quarter`
- **Consumer**: NetPlan
- **Source Tables**: `network.network_plans`, `network.network_plan_actions`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `data_quality_score` | NUMERIC | No | `CASE WHEN solver_status IN ('optimal', 'feasible') AND entity IS NOT NULL THEN 1.0 ELSE 0.0 END` | Derived Rule | 0.0 on solver failure |
| `confidence` | NUMERIC | No | `CASE solver_status WHEN 'optimal' THEN 1.0 WHEN 'feasible' THEN 0.8 WHEN 'timeout' THEN 0.4 ELSE 0.0 END` | Derived Rule | Solver confidence map |
| `risk_score` | NUMERIC | No | `CASE actions.risk_level WHEN 'low' THEN 0.2 WHEN 'medium' THEN 0.5 WHEN 'high' THEN 0.8 ELSE 0.5 END` | Derived Rule | Risk mapping |
| `action_candidates` | TEXT[] | No | `array[actions.action_type]` | Dimension | Array of actions |
| `expected_gm_p50` | NUMERIC | Yes | `actions.expected_gm_delta` | Measurement | Output delta from solver |
| `capital_required` | NUMERIC | Yes | `actions.capital_required` | Measurement | Capital requirement |
| `hard_constraint_flags`| JSONB | Yes | `plans.constraint_summary_json` | Structure | Constraint disclosures |

---

### 3.8 `brand_transfer_view`
- **Grain**: `source_brand_id` : `target_brand_id` : `location_type` : `store_age_bucket`
- **Consumer**: SiteScore
- **Source Tables**: `core.brands` (Subject to Batch 0 verification)

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `data_quality_score` | NUMERIC | No | `1.0` | Derived Contract | Base brand pairs |
| `confidence` | NUMERIC | No | `1.0` | Derived Contract | Mock contract |
| `transfer_ratio` | NUMERIC | No | `0.15` | Benchmark Constant | Standard retail benchmark parameter |

---

### 3.9 `ramp_curve_view`
- **Grain**: `store_id` : `calendar_date`
- **Consumer**: SiteScore, ForecastOps
- **Source Tables**: `core.stores`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `data_quality_score` | NUMERIC | No | `1.0` | Derived Contract | Store entity baseline |
| `confidence` | NUMERIC | No | `1.0` | Derived Contract | Baseline contract |
| `ramp_up_ratio` | NUMERIC | No | `0.85` | Benchmark Constant | 6-month ramp-up ratio baseline |

---

### 3.10 `matched_control_view`
- **Grain**: `treated_store_id` : `control_store_id` : `match_date`
- **Consumer**: AdLift
- **Source Tables**: `core.stores`

| Column | Type | Nullable | Source Expression | Classification | Missing Source Handling |
|---|---|---|---|---|---|
| `data_quality_score` | NUMERIC | No | `1.0` | Derived Contract | Store pairing baseline |
| `confidence` | NUMERIC | No | `1.0` | Derived Contract | Baseline pairing contract |
| `match_score` | NUMERIC | No | `0.92` | Benchmark Constant | Synthetic matching benchmark baseline |

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

