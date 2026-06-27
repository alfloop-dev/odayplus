export interface Tenant {
  tenant_id: string;
  tenant_name: string;
  status: 'active' | 'inactive';
  created_at: string; // ISO 8601 DateTime
}

export interface Brand {
  brand_id: string;
  tenant_id: string;
  brand_code: string;
  brand_name: string;
  brand_type: 'owned' | 'franchise' | 'competitor' | 'external';
  brand_capture_group: string;
  status: 'active' | 'inactive';
}

export interface AddressLocation {
  address_id: string;
  raw_address: string;
  normalized_address: string;
  city: string;
  district: string;
  village: string;
  road: string;
  latitude: number;
  longitude: number;
  geocode_precision: 'rooftop' | 'street' | 'district' | 'manual';
  geocode_confidence: number;
  h3_res_8: string;
  h3_res_9: string;
  h3_res_10: string;
  manual_override_flag: boolean;
}

export interface Store {
  store_id: string;
  tenant_id: string;
  brand_id: string;
  source_store_id: string;
  store_name: string;
  store_status: 'planned' | 'open' | 'suspended' | 'closed' | 'transferred';
  ownership_type: 'owned' | 'franchise' | 'investor_operated' | 'partner';
  store_format_code: string;
  opened_on: string | null; // ISO 8601 Date
  closed_on: string | null; // ISO 8601 Date
  address_id: string;
  region_code: string;
  service_start_time: string; // HH:MM:SS
  service_end_time: string; // HH:MM:SS
  effective_from: string; // ISO 8601 DateTime
  effective_to: string; // ISO 8601 DateTime
  is_current: boolean;
}

export interface Machine {
  machine_id: string;
  store_id: string;
  source_machine_id: string;
  machine_serial_no: string;
  equipment_brand_id: string;
  machine_family: 'washer' | 'dryer' | 'combo' | 'payment_terminal' | 'other';
  machine_type: string;
  capacity_kg: number;
  capacity_band: 'small' | 'medium' | 'large' | 'xlarge';
  installed_on: string | null; // ISO 8601 Date
  removed_on: string | null; // ISO 8601 Date
  machine_status: 'active' | 'inactive' | 'maintenance' | 'retired';
  effective_from: string;
  effective_to: string;
}

export interface Transaction {
  transaction_id: string;
  source_transaction_id: string;
  store_id: string;
  machine_id: string | null;
  member_id: string | null;
  event_time: string; // ISO 8601 DateTime
  observation_time: string; // ISO 8601 DateTime
  payment_time: string | null; // ISO 8601 DateTime
  gross_amount: number;
  discount_amount: number;
  net_amount: number;
  currency: string;
  payment_method: string;
  transaction_status: 'succeeded' | 'failed' | 'refunded' | 'voided' | 'partial';
  refund_of_transaction_id: string | null;
  price_schedule_id: string | null;
  promotion_id: string | null;
  source_system: string;
  ingested_at: string;
}

export interface MachineCycle {
  cycle_id: string;
  store_id: string;
  machine_id: string;
  transaction_id: string | null;
  cycle_start_time: string;
  cycle_end_time: string;
  cycle_type: 'wash' | 'dry' | 'combo' | 'cleaning' | 'test';
  duration_sec: number;
  cycle_status: 'started' | 'completed' | 'failed' | 'cancelled';
  error_code: string | null;
}

export interface MachineStatusEvent {
  status_event_id: string;
  store_id: string;
  machine_id: string;
  event_time: string;
  status_type: 'online' | 'offline' | 'error' | 'available' | 'occupied' | 'maintenance';
  severity: 'info' | 'warn' | 'error' | 'critical';
  error_code: string | null;
  resolved_time: string | null;
}

export interface WorkOrder {
  work_order_id: string;
  store_id: string;
  machine_id: string | null;
  issue_type: 'failure' | 'cleaning' | 'inspection' | 'complaint';
  issue_subtype: string;
  opened_at: string;
  closed_at: string | null;
  status: 'open' | 'in_progress' | 'resolved' | 'cancelled';
  severity: 'low' | 'medium' | 'high' | 'critical';
  cost_amount: number;
  root_cause: string | null;
}

export interface CustomerServiceCase {
  case_id: string;
  store_id: string;
  machine_id: string | null;
  member_id: string | null;
  opened_at: string;
  closed_at: string | null;
  channel: 'LINE' | 'web' | 'phone' | 'system';
  topic_code: string;
  sentiment_score: number;
  resolution_status: 'resolved' | 'unresolved' | 'escalated';
  ttr_minutes: number;
}

export interface GeoCell {
  geo_cell_id: string;
  h3_index: string;
  h3_resolution: number;
  parent_h3_index: string | null;
  centroid_latitude: number;
  centroid_longitude: number;
  admin_city: string;
  admin_district: string;
  service_area_id: string | null;
}

export interface Poi {
  poi_id: string;
  source_poi_id: string;
  poi_name: string;
  poi_category: string;
  poi_subcategory: string;
  address_id: string;
  geo_cell_id: string;
  status: 'active' | 'closed' | 'unknown';
  confidence: number;
  snapshot_id: string;
}

export interface CompetitorStore {
  competitor_store_id: string;
  brand_name: string;
  store_name: string;
  address_id: string;
  geo_cell_id: string;
  estimated_capacity: number;
  distance_to_nearest_oday_m: number;
  status: 'active' | 'closed' | 'unknown';
  confidence: number;
  last_verified_at: string | null;
}

export interface Listing {
  listing_id: string;
  source_listing_id: string;
  source_id: string;
  listing_status: 'active' | 'inactive' | 'leased' | 'manual_review' | 'stale';
  address_id: string;
  rent_amount: number;
  currency: string;
  area_ping: number;
  floor: string;
  frontage_m: number;
  depth_m: number;
  corner_flag: boolean;
  parking_flag: boolean;
  utility_electricity_flag: boolean;
  utility_drainage_flag: boolean;
  utility_gas_flag: boolean;
  available_from: string | null;
  snapshot_id: string;
  confidence: number;
}

export interface CandidateSite {
  candidate_site_id: string;
  listing_id: string | null;
  address_id: string;
  target_format_code: string;
  site_status: 'new' | 'screened' | 'scored' | 'visited' | 'rejected' | 'approved' | 'opened';
  created_by: string;
  created_at: string;
}

export interface ModelVersion {
  model_version_id: string;
  model_name: string;
  model_family: 'heatzone' | 'sitescore' | 'forecast' | 'price' | 'adlift' | 'avm' | 'netplan';
  registry_uri: string;
  training_dataset_snapshot_id: string;
  feature_view_version: string;
  status: 'development' | 'staging' | 'shadow' | 'canary' | 'production' | 'retired';
  released_at: string | null;
}

export interface PredictionRun {
  prediction_run_id: string;
  model_version_id: string;
  feature_snapshot_time: string;
  prediction_origin_time: string;
  prediction_horizon: string;
  input_snapshot_id: string;
  output_uri: string;
  run_status: 'queued' | 'running' | 'succeeded' | 'failed' | 'partial';
}

export interface Prediction {
  prediction_id: string;
  prediction_run_id: string;
  entity_type: 'geo_cell' | 'candidate_site' | 'store' | 'intervention' | 'valuation';
  entity_id: string;
  target_name: string;
  p10_value: number;
  p50_value: number;
  p90_value: number;
  unit: string;
  explanation_json: Record<string, any>;
  confidence: number;
}

export interface Decision {
  decision_id: string;
  decision_type: 'site_go_wait_reject' | 'alert_action' | 'price' | 'ad' | 'valuation' | 'netplan' | 'model_release';
  entity_type: string;
  entity_id: string;
  recommendation: string;
  decision_status: 'proposed' | 'approved' | 'rejected' | 'overridden' | 'executed' | 'cancelled' | 'expired';
  policy_version_id: string;
  prediction_run_id: string | null;
  created_by: string;
  created_at: string;
}

export interface Approval {
  approval_id: string;
  decision_id: string;
  approver_id: string;
  approval_status: 'pending' | 'approved' | 'rejected' | 'returned' | 'escalated';
  approved_at: string | null;
  comment: string | null;
}

export interface HeatZoneScore {
  heatzone_score_id: string;
  geo_cell_id: string;
  score_run_id: string;
  heat_score: number;
  priority_rank: number;
  unmet_demand_score: number;
  format_fit_score: number;
  cannibalization_risk_score: number;
  rent_feasibility_score: number;
  heatzone_state: 'untouched' | 'partially_absorbed' | 'saturated' | 'under_realized' | 'still_expandable';
  confidence: number;
}

export interface SiteScoreRun {
  sitescore_run_id: string;
  candidate_site_id: string;
  target_format_code: string;
  prediction_run_id: string;
  m1_p10: number;
  m1_p50: number;
  m1_p90: number;
  m3_p10: number;
  m3_p50: number;
  m3_p90: number;
  m6_p10: number;
  m6_p50: number;
  m6_p90: number;
  m12_p10: number;
  m12_p50: number;
  m12_p90: number;
  payback_p50_months: number;
  decision_recommendation: 'go' | 'wait' | 'reject' | 'investigate';
  report_uri: string;
}

export interface ForecastOutput {
  forecast_output_id: string;
  store_id: string;
  prediction_run_id: string;
  horizon_days: number;
  target_metric: string;
  p10: number;
  p50: number;
  p90: number;
  trajectory_class: 'ramping' | 'growing' | 'plateau' | 'declining';
  turning_point_probability: number;
  sitescore_gap_ratio: number;
}

export interface Alert {
  alert_id: string;
  store_id: string;
  alert_level: 'green' | 'yellow' | 'orange' | 'red';
  alert_reason_code: string;
  evidence_json: Record<string, any>;
  opened_at: string;
  closed_at: string | null;
  status: 'open' | 'acknowledged' | 'in_progress' | 'resolved' | 'dismissed';
}

export interface Intervention {
  intervention_id: string;
  store_id: string;
  intervention_type: string;
  trigger_alert_id: string | null;
  eligibility_status: string;
  action_set_json: Record<string, any>;
  approved_action_json: Record<string, any>;
  start_time: string;
  end_time: string;
  observation_start_time: string;
  observation_end_time: string;
  status: 'proposed' | 'approved' | 'executing' | 'observing' | 'evaluated' | 'stopped' | 'rolled_back';
}

export interface InterventionOutcome {
  outcome_id: string;
  intervention_id: string;
  outcome_time: string;
  incremental_revenue: number;
  incremental_gross_margin: number;
  method: string;
  evidence_level: 'low' | 'medium' | 'high' | 'causal_candidate';
  side_effect_json: Record<string, any>;
  label_maturity_time: string;
}

export interface ValuationRun {
  valuation_run_id: string;
  store_id: string;
  valuation_date: string;
  normalized_gm_ttm: number;
  gm_fwd_p50: number;
  income_value_p10: number;
  income_value_p50: number;
  income_value_p90: number;
  asset_value_p50: number;
  market_value_p50: number;
  fair_price_p50: number;
  reserve_price: number;
  asking_price: number;
  report_uri: string;
}

export interface NetworkPlan {
  network_plan_id: string;
  planning_period_start: string;
  planning_period_end: string;
  scenario_name: string;
  objective_value: number;
  solver_status: string;
  constraint_summary_json: Record<string, any>;
  created_at: string;
}

export interface NetworkPlanAction {
  network_plan_action_id: string;
  network_plan_id: string;
  store_id: string | null;
  candidate_site_id: string | null;
  action_type: 'open' | 'keep' | 'improve' | 'move' | 'exit';
  quarter: string;
  expected_gm_delta: number;
  capital_required: number;
  risk_level: string;
}

export interface AuditEvent {
  audit_event_id: string;
  actor_id: string;
  actor_type: 'user' | 'service' | 'system';
  action: string;
  entity_type: string;
  entity_id: string;
  occurred_at: string;
  before_hash: string | null;
  after_hash: string | null;
  ip_address: string;
  correlation_id: string;
}

export interface DataSnapshot {
  snapshot_id: string;
  snapshot_type: 'raw' | 'canonical' | 'model_ready' | 'training';
  source_id: string;
  snapshot_time: string;
  storage_uri: string;
  schema_version: string;
  row_count: number;
  quality_score: number;
  created_by_run_id: string;
}
