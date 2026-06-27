select
    'intervention_panel_view' as view_name,
    'v1' as view_version,
    interventions.store_id::text || ':' || interventions.intervention_id::text || ':' || interventions.start_time::date::text as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['operations.interventions', 'operations.intervention_outcomes'] as source_snapshot_ids,
    case
        when interventions.start_time <= interventions.end_time
         and interventions.observation_start_time <= interventions.observation_end_time then 1.0
        else 0.0
    end as data_quality_score,
    case
        when outcomes.evidence_level = 'high' then 1.0
        when outcomes.evidence_level = 'causal_candidate' then 0.9
        when outcomes.evidence_level = 'medium' then 0.75
        when outcomes.evidence_level = 'low' then 0.5
        else 0.7
    end as confidence,
    coalesce(outcomes.label_maturity_time, interventions.observation_end_time)
      <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as is_training_eligible,
    interventions.start_time <= {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as is_scoring_eligible,
    case
        when interventions.start_time > {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz then 'future_treatment'
        when coalesce(outcomes.label_maturity_time, interventions.observation_end_time) > {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz then 'label_not_mature'
        else ''
    end as exclusion_reason,
    interventions.intervention_id,
    interventions.store_id,
    interventions.start_time::date as date,
    interventions.intervention_type,
    true as treatment_flag,
    coalesce((interventions.approved_action_json ->> 'intensity')::numeric, 1.0) as treatment_intensity,
    case interventions.eligibility_status
        when 'eligible' then 1.0
        when 'manual_review' then 0.5
        else 0.0
    end as eligibility_score,
    null::numeric as propensity_score,
    array[]::text[] as overlap_interventions,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz < interventions.start_time as pre_period_flag,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz >= interventions.start_time
      and {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz < interventions.end_time as treatment_period_flag,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz >= interventions.observation_start_time
      and {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz <= interventions.observation_end_time as observation_period_flag,
    outcomes.incremental_revenue as outcome_revenue,
    outcomes.incremental_gross_margin as outcome_gm,
    outcomes.evidence_level
from operations.interventions
left join operations.intervention_outcomes outcomes
  on outcomes.intervention_id = interventions.intervention_id
where interventions.created_at <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
