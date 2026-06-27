from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Tenant:
    """Group/organization tenant."""
    tenant_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_name: str = ""
    status: str = "active"  # active/inactive
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class Brand:
    """Brand owned by tenant or competitor."""
    brand_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = ""
    brand_code: str = ""
    brand_name: str = ""
    brand_type: str = "owned"  # owned/franchise/competitor/external
    brand_capture_group: str = ""
    status: str = "active"  # active/inactive


@dataclass(frozen=True)
class AddressLocation:
    """Normalized address and GIS reference."""
    address_id: str = field(default_factory=lambda: str(uuid4()))
    raw_address: str = ""
    normalized_address: str = ""
    city: str = ""
    district: str = ""
    village: str = ""
    road: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    geocode_precision: str = "manual"  # rooftop/street/district/manual
    geocode_confidence: float = 0.0  # 0 to 1
    h3_res_8: str = ""
    h3_res_9: str = ""
    h3_res_10: str = ""
    manual_override_flag: bool = False


@dataclass(frozen=True)
class Store:
    """ODay Plus Store master (supports SCD2)."""
    store_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = ""
    brand_id: str = ""
    source_store_id: str = ""
    store_name: str = ""
    store_status: str = "planned"  # planned/open/suspended/closed/transferred
    ownership_type: str = "owned"  # owned/franchise/investor_operated/partner
    store_format_code: str = ""
    opened_on: date | None = None
    closed_on: date | None = None
    address_id: str = ""
    region_code: str = ""
    service_start_time: time = field(default_factory=lambda: time(0, 0))
    service_end_time: time = field(default_factory=lambda: time(23, 59))
    effective_from: datetime = field(default_factory=datetime.now)
    effective_to: datetime = field(default_factory=lambda: datetime(9999, 12, 31, 23, 59, 59))
    is_current: bool = True


@dataclass(frozen=True)
class Machine:
    """Store machine/equipment reference."""
    machine_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    source_machine_id: str = ""
    machine_serial_no: str = ""
    equipment_brand_id: str = ""
    machine_family: str = "washer"  # washer/dryer/combo/payment_terminal/other
    machine_type: str = ""
    capacity_kg: float = 0.0
    capacity_band: str = "small"  # small/medium/large/xlarge
    installed_on: date | None = None
    removed_on: date | None = None
    machine_status: str = "active"  # active/inactive/maintenance/retired
    effective_from: datetime = field(default_factory=datetime.now)
    effective_to: datetime = field(default_factory=lambda: datetime(9999, 12, 31, 23, 59, 59))


@dataclass(frozen=True)
class Transaction:
    """Store payment transaction event."""
    transaction_id: str = field(default_factory=lambda: str(uuid4()))
    source_transaction_id: str = ""
    store_id: str = ""
    machine_id: str | None = None
    member_id: str | None = None
    event_time: datetime = field(default_factory=datetime.now)
    observation_time: datetime = field(default_factory=datetime.now)
    payment_time: datetime | None = None
    gross_amount: float = 0.0
    discount_amount: float = 0.0
    net_amount: float = 0.0
    currency: str = "TWD"
    payment_method: str = "cash"
    transaction_status: str = "succeeded"  # succeeded/failed/refunded/voided/partial
    refund_of_transaction_id: str | None = None
    price_schedule_id: str | None = None
    promotion_id: str | None = None
    source_system: str = ""
    ingested_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class MachineCycle:
    """Machine execution run cycle (IoT/CDC)."""
    cycle_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    machine_id: str = ""
    transaction_id: str | None = None
    cycle_start_time: datetime = field(default_factory=datetime.now)
    cycle_end_time: datetime = field(default_factory=datetime.now)
    cycle_type: str = "wash"  # wash/dry/combo/cleaning/test
    duration_sec: int = 0
    cycle_status: str = "started"  # started/completed/failed/cancelled
    error_code: str | None = None


@dataclass(frozen=True)
class MachineStatusEvent:
    """Machine online/offline or error state changes."""
    status_event_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    machine_id: str = ""
    event_time: datetime = field(default_factory=datetime.now)
    status_type: str = "online"  # online/offline/error/available/occupied/maintenance
    severity: str = "info"  # info/warn/error/critical
    error_code: str | None = None
    resolved_time: datetime | None = None


@dataclass(frozen=True)
class WorkOrder:
    """Maintenance and repair work orders."""
    work_order_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    machine_id: str | None = None
    issue_type: str = "failure"  # failure/cleaning/inspection/complaint
    issue_subtype: str = ""
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: datetime | None = None
    status: str = "open"  # open/in_progress/resolved/cancelled
    severity: str = "medium"  # low/medium/high/critical
    cost_amount: float = 0.0
    root_cause: str | None = None


@dataclass(frozen=True)
class CustomerServiceCase:
    """Customer support issues."""
    case_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    machine_id: str | None = None
    member_id: str | None = None
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: datetime | None = None
    channel: str = "LINE"  # LINE/web/phone/system
    topic_code: str = ""
    sentiment_score: float = 0.0
    resolution_status: str = "unresolved"  # resolved/unresolved/escalated
    ttr_minutes: float = 0.0


@dataclass(frozen=True)
class GeoCell:
    """H3 Geographical spatial unit."""
    geo_cell_id: str = field(default_factory=lambda: str(uuid4()))
    h3_index: str = ""
    h3_resolution: int = 8
    parent_h3_index: str | None = None
    centroid_latitude: float = 0.0
    centroid_longitude: float = 0.0
    admin_city: str = ""
    admin_district: str = ""
    service_area_id: str | None = None


@dataclass(frozen=True)
class Poi:
    """Point of interest data."""
    poi_id: str = field(default_factory=lambda: str(uuid4()))
    source_poi_id: str = ""
    poi_name: str = ""
    poi_category: str = ""
    poi_subcategory: str = ""
    address_id: str = ""
    geo_cell_id: str = ""
    status: str = "active"  # active/closed/unknown
    confidence: float = 1.0
    snapshot_id: str = ""


@dataclass(frozen=True)
class CompetitorStore:
    """Competitor laundry location."""
    competitor_store_id: str = field(default_factory=lambda: str(uuid4()))
    brand_name: str = ""
    store_name: str = ""
    address_id: str = ""
    geo_cell_id: str = ""
    estimated_capacity: float = 0.0
    distance_to_nearest_oday_m: float = 0.0
    status: str = "active"  # active/closed/unknown
    confidence: float = 1.0
    last_verified_at: datetime | None = None


@dataclass(frozen=True)
class Listing:
    """Real estate listing (merges source properties)."""
    listing_id: str = field(default_factory=lambda: str(uuid4()))
    source_listing_id: str = ""
    source_id: str = ""
    listing_status: str = "active"  # active/inactive/leased/manual_review/stale
    address_id: str = ""
    rent_amount: float = 0.0
    currency: str = "TWD"
    area_ping: float = 0.0
    floor: str = ""
    frontage_m: float = 0.0
    depth_m: float = 0.0
    corner_flag: bool = False
    parking_flag: bool = False
    utility_electricity_flag: bool = False
    utility_drainage_flag: bool = False
    utility_gas_flag: bool = False
    available_from: date | None = None
    snapshot_id: str = ""
    confidence: float = 1.0


@dataclass(frozen=True)
class CandidateSite:
    """Shortlisted candidate location."""
    candidate_site_id: str = field(default_factory=lambda: str(uuid4()))
    listing_id: str | None = None
    address_id: str = ""
    target_format_code: str = ""
    site_status: str = "new"  # new/screened/scored/visited/rejected/approved/opened
    created_by: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class ModelVersion:
    """Machine learning model release metadata."""
    model_version_id: str = ""
    model_name: str = ""
    model_family: str = "heatzone"  # heatzone/sitescore/forecast/price/adlift/avm/netplan
    registry_uri: str = ""
    training_dataset_snapshot_id: str = ""
    feature_view_version: str = ""
    status: str = "development"  # development/staging/shadow/canary/production/retired
    released_at: datetime | None = None


@dataclass(frozen=True)
class PredictionRun:
    """Model scoring executing run."""
    prediction_run_id: str = field(default_factory=lambda: str(uuid4()))
    model_version_id: str = ""
    feature_snapshot_time: datetime = field(default_factory=datetime.now)
    prediction_origin_time: datetime = field(default_factory=datetime.now)
    prediction_horizon: str = ""
    input_snapshot_id: str = ""
    output_uri: str = ""
    run_status: str = "queued"  # queued/running/succeeded/failed/partial


@dataclass(frozen=True)
class Prediction:
    """Specific prediction output (forecast/value/probabilities)."""
    prediction_id: str = field(default_factory=lambda: str(uuid4()))
    prediction_run_id: str = ""
    entity_type: str = ""  # geo_cell/candidate_site/store/intervention/valuation
    entity_id: str = ""
    target_name: str = ""
    p10_value: float = 0.0
    p50_value: float = 0.0
    p90_value: float = 0.0
    unit: str = ""
    explanation_json: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass(frozen=True)
class Decision:
    """Recommended system action or human selection."""
    decision_id: str = field(default_factory=lambda: str(uuid4()))
    decision_type: str = "site_go_wait_reject"  # site_go_wait_reject/alert_action/price/ad/valuation/netplan/model_release
    entity_type: str = ""
    entity_id: str = ""
    recommendation: str = ""
    decision_status: str = "proposed"  # proposed/approved/rejected/overridden/executed/cancelled/expired
    policy_version_id: str = ""
    prediction_run_id: str | None = None
    created_by: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class Approval:
    """Workflow approval signatures for decisions."""
    approval_id: str = field(default_factory=lambda: str(uuid4()))
    decision_id: str = ""
    approver_id: str = ""
    approval_status: str = "pending"  # pending/approved/rejected/returned/escalated
    approved_at: datetime | None = None
    comment: str | None = None


@dataclass(frozen=True)
class HeatZoneScore:
    """HeatZone Radar geographic evaluation score."""
    heatzone_score_id: str = field(default_factory=lambda: str(uuid4()))
    geo_cell_id: str = ""
    score_run_id: str = ""
    heat_score: float = 0.0
    priority_rank: int = 0
    unmet_demand_score: float = 0.0
    format_fit_score: float = 0.0
    cannibalization_risk_score: float = 0.0
    rent_feasibility_score: float = 0.0
    heatzone_state: str = "untouched"  # untouched/partially_absorbed/saturated/under_realized/still_expandable
    confidence: float = 1.0


@dataclass(frozen=True)
class SiteScoreRun:
    """Detailed SiteScore execution summary."""
    sitescore_run_id: str = field(default_factory=lambda: str(uuid4()))
    candidate_site_id: str = ""
    target_format_code: str = ""
    prediction_run_id: str = ""
    m1_p10: float = 0.0
    m1_p50: float = 0.0
    m1_p90: float = 0.0
    m3_p10: float = 0.0
    m3_p50: float = 0.0
    m3_p90: float = 0.0
    m6_p10: float = 0.0
    m6_p50: float = 0.0
    m6_p90: float = 0.0
    m12_p10: float = 0.0
    m12_p50: float = 0.0
    m12_p90: float = 0.0
    payback_p50_months: float = 0.0
    decision_recommendation: str = "go"  # go/wait/reject/investigate
    report_uri: str = ""


@dataclass(frozen=True)
class ForecastOutput:
    """Revenue or utilization model predictions."""
    forecast_output_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    prediction_run_id: str = ""
    horizon_days: int = 28
    target_metric: str = "revenue"
    p10: float = 0.0
    p50: float = 0.0
    p90: float = 0.0
    trajectory_class: str = "plateau"  # ramping/growing/plateau/declining
    turning_point_probability: float = 0.0
    sitescore_gap_ratio: float = 0.0


@dataclass(frozen=True)
class Alert:
    """Store operational anomalies (four-light model)."""
    alert_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    alert_level: str = "green"  # green/yellow/orange/red
    alert_reason_code: str = ""
    evidence_json: dict[str, Any] = field(default_factory=dict)
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: datetime | None = None
    status: str = "open"  # open/acknowledged/in_progress/resolved/dismissed


@dataclass(frozen=True)
class Intervention:
    """Prescriptive operational adjustments."""
    intervention_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    intervention_type: str = "price"  # price/ad/promotion/crm/maintenance/cleaning/other
    trigger_alert_id: str | None = None
    eligibility_status: str = "eligible"  # eligible/ineligible/manual_review
    action_set_json: dict[str, Any] = field(default_factory=dict)
    approved_action_json: dict[str, Any] = field(default_factory=dict)
    start_time: datetime = field(default_factory=datetime.now)
    end_time: datetime = field(default_factory=datetime.now)
    observation_start_time: datetime = field(default_factory=datetime.now)
    observation_end_time: datetime = field(default_factory=datetime.now)
    status: str = "proposed"  # proposed/approved/executing/observing/evaluated/stopped/rolled_back


@dataclass(frozen=True)
class InterventionOutcome:
    """Outcome causal impact evaluation."""
    outcome_id: str = field(default_factory=lambda: str(uuid4()))
    intervention_id: str = ""
    outcome_time: datetime = field(default_factory=datetime.now)
    incremental_revenue: float = 0.0
    incremental_gross_margin: float = 0.0
    method: str = "synthetic"  # before_after/did/synthetic/uplift/manual
    evidence_level: str = "medium"  # low/medium/high/causal_candidate
    side_effect_json: dict[str, Any] = field(default_factory=dict)
    label_maturity_time: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class ValuationRun:
    """Store transaction business valuation."""
    valuation_run_id: str = field(default_factory=lambda: str(uuid4()))
    store_id: str = ""
    valuation_date: date = field(default_factory=date.today)
    normalized_gm_ttm: float = 0.0
    gm_fwd_p50: float = 0.0
    income_value_p10: float = 0.0
    income_value_p50: float = 0.0
    income_value_p90: float = 0.0
    asset_value_p50: float = 0.0
    market_value_p50: float = 0.0
    fair_price_p50: float = 0.0
    reserve_price: float = 0.0
    asking_price: float = 0.0
    report_uri: str = ""


@dataclass(frozen=True)
class NetworkPlan:
    """Overall multi-quarter store network plan."""
    network_plan_id: str = field(default_factory=lambda: str(uuid4()))
    planning_period_start: date = field(default_factory=date.today)
    planning_period_end: date = field(default_factory=date.today)
    scenario_name: str = "base"  # base/downside/upside/custom
    objective_value: float = 0.0
    solver_status: str = "optimal"  # optimal/feasible/infeasible/timeout/error
    constraint_summary_json: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class NetworkPlanAction:
    """Strategic action assigned in plan."""
    network_plan_action_id: str = field(default_factory=lambda: str(uuid4()))
    network_plan_id: str = ""
    store_id: str | None = None
    candidate_site_id: str | None = None
    action_type: str = "keep"  # open/keep/improve/move/exit
    quarter: str = ""
    expected_gm_delta: float = 0.0
    capital_required: float = 0.0
    risk_level: str = "low"  # low/medium/high


@dataclass(frozen=True)
class AuditEvent:
    """Security and operational audit trail."""
    audit_event_id: str = field(default_factory=lambda: str(uuid4()))
    actor_id: str = ""
    actor_type: str = "service"  # user/service/system
    action: str = ""  # create/update/delete/approve/reject/export/run_model
    entity_type: str = ""
    entity_id: str = ""
    occurred_at: datetime = field(default_factory=datetime.now)
    before_hash: str | None = None
    after_hash: str | None = None
    ip_address: str = ""
    correlation_id: str = ""


@dataclass(frozen=True)
class DataSnapshot:
    """Point-in-time snapshot references."""
    snapshot_id: str = field(default_factory=lambda: str(uuid4()))
    snapshot_type: str = "raw"  # raw/canonical/model_ready/training
    source_id: str = ""
    snapshot_time: datetime = field(default_factory=datetime.now)
    storage_uri: str = ""
    schema_version: str = ""
    row_count: int = 0
    quality_score: float = 1.0
    created_by_run_id: str = ""
