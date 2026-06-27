with transaction_days as (
    select
        store_id,
        machine_id,
        date_trunc('day', event_time)::date as metric_date,
        sum(gross_amount) as gross_revenue,
        sum(net_amount) as net_revenue,
        count(*) as transaction_count,
        count(*) filter (where transaction_status = 'refunded') as refund_count,
        max(observation_time) as latest_observation_time,
        max(ingested_at) as latest_ingested_at
    from core.transactions
    where event_time < {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz
      and observation_time <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
    group by store_id, machine_id, date_trunc('day', event_time)::date
),
cycle_days as (
    select
        store_id,
        machine_id,
        date_trunc('day', cycle_start_time)::date as metric_date,
        count(*) as cycle_count,
        sum(duration_sec) as occupied_seconds,
        avg(duration_sec) as avg_cycle_duration_sec
    from core.machine_cycles
    where cycle_start_time < {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz
    group by store_id, machine_id, date_trunc('day', cycle_start_time)::date
)
select
    'store_machine_timeseries_view' as view_name,
    'v1' as view_version,
    coalesce(t.store_id, c.store_id)::text || ':' || coalesce(t.machine_id, c.machine_id, 'store')::text || ':' || coalesce(t.metric_date, c.metric_date)::text as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['core.transactions', 'core.machine_cycles'] as source_snapshot_ids,
    1.0 as data_quality_score,
    1.0 as confidence,
    true as is_training_eligible,
    true as is_scoring_eligible,
    '' as exclusion_reason,
    coalesce(t.store_id, c.store_id) as store_id,
    coalesce(t.machine_id, c.machine_id) as machine_id,
    coalesce(t.metric_date, c.metric_date) as date,
    coalesce(t.gross_revenue, 0) as gross_revenue,
    coalesce(t.net_revenue, 0) as net_revenue,
    coalesce(t.transaction_count, 0) as transaction_count,
    coalesce(c.cycle_count, 0) as cycle_count,
    coalesce(c.occupied_seconds, 0) / 60.0 as occupied_minutes,
    1440.0 as available_minutes,
    greatest(0.0, 1440.0 - (coalesce(c.occupied_seconds, 0) / 60.0)) as downtime_minutes,
    coalesce(c.occupied_seconds, 0) / 86400.0 as utilization_rate,
    coalesce(c.avg_cycle_duration_sec, 0) as avg_cycle_duration_sec,
    coalesce(t.refund_count, 0) as refund_count
from transaction_days t
full outer join cycle_days c
  on t.store_id = c.store_id
 and coalesce(t.machine_id::text, '') = coalesce(c.machine_id::text, '')
 and t.metric_date = c.metric_date
