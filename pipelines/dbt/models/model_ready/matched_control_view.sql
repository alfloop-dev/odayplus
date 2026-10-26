with store_pairs as (
    select
        s1.store_id as treated_store_id,
        s2.store_id as control_store_id
    from core.stores s1
    cross join core.stores s2
    where s1.store_id != s2.store_id
)
select
    'matched_control_view' as view_name,
    'v1' as view_version,
    (treated_store_id || '_' || control_store_id) as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['core.stores'] as source_snapshot_ids,
    case
        when treated_store_id is not null and control_store_id is not null then 1.0
        else 0.0
    end as data_quality_score,
    null::numeric as confidence,
    true as is_training_eligible,
    true as is_scoring_eligible,
    '' as exclusion_reason,
    treated_store_id,
    control_store_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz::date as match_date,
    0.92 as match_score
from store_pairs
