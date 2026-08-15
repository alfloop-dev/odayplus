with brand_pairs as (
    select
        b1.brand_id as source_brand_id,
        b2.brand_id as target_brand_id,
        b1.created_at as latest_observation_time
    from core.brands b1
    cross join core.brands b2
    where b1.brand_id != b2.brand_id
)
select
    'brand_transfer_view' as view_name,
    'v1' as view_version,
    (source_brand_id || '_' || target_brand_id) as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['core.brands'] as source_snapshot_ids,
    1.0 as data_quality_score,
    1.0 as confidence,
    true as is_training_eligible,
    true as is_scoring_eligible,
    '' as exclusion_reason,
    source_brand_id,
    target_brand_id,
    'urban' as location_type,
    'ODAY_G2' as store_format_code,
    '0_6m' as store_age_bucket,
    0.15 as transfer_ratio
from brand_pairs
