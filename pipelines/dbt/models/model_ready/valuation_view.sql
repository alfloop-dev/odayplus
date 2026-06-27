select
    'valuation_view' as view_name,
    'v1' as view_version,
    valuation_runs.store_id::text || ':' || valuation_runs.valuation_date::text as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['asset.valuation_runs', 'operations.forecast_outputs', 'core.stores'] as source_snapshot_ids,
    case
        when valuation_runs.normalized_gm_ttm >= 0
         and valuation_runs.income_value_p50 >= 0 then 1.0
        else 0.0
    end as data_quality_score,
    0.8 as confidence,
    valuation_runs.valuation_date <= {{ var('feature_snapshot_time', 'current_timestamp') }}::date as is_training_eligible,
    valuation_runs.valuation_date <= {{ var('prediction_origin_time', 'current_timestamp') }}::date as is_scoring_eligible,
    case
        when valuation_runs.valuation_date > {{ var('feature_snapshot_time', 'current_timestamp') }}::date then 'valuation_after_snapshot'
        else ''
    end as exclusion_reason,
    valuation_runs.store_id,
    valuation_runs.valuation_date,
    valuation_runs.normalized_gm_ttm as gm_ttm,
    valuation_runs.normalized_gm_ttm,
    forecast_outputs.p10 as gm_fwd_p10,
    coalesce(forecast_outputs.p50, valuation_runs.gm_fwd_p50) as gm_fwd_p50,
    forecast_outputs.p90 as gm_fwd_p90,
    valuation_runs.asset_value_p50 as asset_book_value,
    null::numeric as remaining_asset_life,
    null::numeric as lease_remaining_months,
    null::numeric as rent_amount,
    0.0 as intervention_adjustment,
    0.8 as forecast_confidence,
    0 as comparable_count,
    jsonb_build_object(
        'income_value_p50', valuation_runs.income_value_p50,
        'market_value_p50', valuation_runs.market_value_p50,
        'fair_price_p50', valuation_runs.fair_price_p50
    ) as liquidity_features
from asset.valuation_runs
left join operations.forecast_outputs
  on forecast_outputs.store_id = valuation_runs.store_id
 and forecast_outputs.created_at <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
left join core.stores
  on stores.store_id = valuation_runs.store_id
where valuation_runs.created_at <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
