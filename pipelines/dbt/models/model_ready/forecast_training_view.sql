with store_days as (
    select
        store_id,
        date_trunc('day', event_time)::date as metric_date,
        max(observation_time) as latest_observation_time,
        max(ingested_at) as latest_ingested_at,
        sum(net_amount) as daily_net_revenue,
        sum(gross_amount) as daily_gross_revenue,
        count(*) as transaction_count
    from core.transactions
    where event_time < {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz
      and observation_time <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
      and ingested_at <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
    group by store_id, date_trunc('day', event_time)::date
),
features as (
    select
        store_id,
        metric_date,
        daily_net_revenue,
        daily_gross_revenue,
        transaction_count,
        lag(daily_net_revenue, 1) over (partition by store_id order by metric_date) as revenue_lag_1,
        lag(daily_net_revenue, 7) over (partition by store_id order by metric_date) as revenue_lag_7,
        avg(daily_net_revenue) over (
            partition by store_id
            order by metric_date
            rows between 7 preceding and 1 preceding
        ) as rolling_mean_7,
        avg(daily_net_revenue) over (
            partition by store_id
            order by metric_date
            rows between 28 preceding and 1 preceding
        ) as rolling_mean_28,
        latest_observation_time,
        latest_ingested_at
    from store_days
)
select
    'forecast_training_view' as view_name,
    'v1' as view_version,
    store_id::text as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['core.transactions'] as source_snapshot_ids,
    case when latest_observation_time <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz then 1.0 else 0.0 end as data_quality_score,
    1.0 as confidence,
    latest_observation_time <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as is_training_eligible,
    latest_ingested_at <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as is_scoring_eligible,
    case
        when latest_observation_time > {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz then 'pit_violation'
        else ''
    end as exclusion_reason,
    store_id,
    metric_date as date,
    daily_net_revenue,
    daily_gross_revenue,
    transaction_count,
    revenue_lag_1,
    revenue_lag_7,
    rolling_mean_7,
    rolling_mean_28
from features
