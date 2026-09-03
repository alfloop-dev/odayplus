select
    'ramp_curve_view' as view_name,
    'v1' as view_version,
    store_id::text as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['core.stores'] as source_snapshot_ids,
    case
        when store_id is not null
         and effective_from <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
            then 1.0
        else 0.0
    end as data_quality_score,
    null::numeric as confidence,
    true as is_training_eligible,
    true as is_scoring_eligible,
    '' as exclusion_reason,
    store_id,
    '2026_Q1' as store_cohort,
    6 as store_age_months,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz::date as calendar_date,
    0.85 as ramp_up_ratio
from core.stores
where effective_from <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
