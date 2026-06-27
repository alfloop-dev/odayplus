select
    'network_plan_view' as view_name,
    'v1' as view_version,
    coalesce(actions.store_id::text, actions.candidate_site_id::text) || ':' || actions.quarter as entity_id,
    {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz as feature_snapshot_time,
    {{ var('prediction_origin_time', 'current_timestamp') }}::timestamptz as prediction_origin_time,
    array['network.network_plans', 'network.network_plan_actions'] as source_snapshot_ids,
    case
        when plans.solver_status in ('optimal', 'feasible')
         and coalesce(actions.store_id, actions.candidate_site_id) is not null then 1.0
        else 0.0
    end as data_quality_score,
    case plans.solver_status
        when 'optimal' then 1.0
        when 'feasible' then 0.8
        when 'timeout' then 0.4
        else 0.0
    end as confidence,
    plans.planning_period_start <= {{ var('feature_snapshot_time', 'current_timestamp') }}::date as is_training_eligible,
    plans.solver_status in ('optimal', 'feasible') as is_scoring_eligible,
    case
        when plans.solver_status not in ('optimal', 'feasible') then 'solver_not_usable'
        when coalesce(actions.store_id, actions.candidate_site_id) is null then 'missing_planning_entity'
        else ''
    end as exclusion_reason,
    case when actions.candidate_site_id is not null then 'candidate_site' else 'existing_store' end as entity_type,
    coalesce(actions.store_id::text, actions.candidate_site_id::text) as planning_entity_id,
    actions.quarter as planning_quarter,
    array[actions.action_type] as action_candidates,
    null::numeric as expected_gm_p10,
    actions.expected_gm_delta as expected_gm_p50,
    null::numeric as expected_gm_p90,
    actions.capital_required,
    plans.constraint_summary_json -> 'lease' as lease_constraint,
    null::integer as construction_lead_time,
    plans.constraint_summary_json -> 'staffing' as staffing_constraint,
    jsonb_build_object('network_plan_action_id', actions.network_plan_action_id) as cannibalization_matrix_row,
    null::numeric as valuation_p50,
    case actions.risk_level
        when 'low' then 0.2
        when 'medium' then 0.5
        when 'high' then 0.8
        else 0.5
    end as risk_score,
    plans.constraint_summary_json as hard_constraint_flags
from network.network_plan_actions actions
join network.network_plans plans
  on plans.network_plan_id = actions.network_plan_id
where actions.created_at <= {{ var('feature_snapshot_time', 'current_timestamp') }}::timestamptz
