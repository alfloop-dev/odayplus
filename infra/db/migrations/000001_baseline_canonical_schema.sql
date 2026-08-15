-- Baseline Canonical Schema for ODay Plus
-- Citing ODP-DATA-04 (Canonical Data Model) and ODP-SD-05 (Database & Storage Design)
-- Database Target: PostgreSQL + PostGIS

-- Enable UUID and spatial extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis";

-- ---------------------------------------------------------
-- Schemas Initialization
-- ---------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS workflow;
CREATE SCHEMA IF NOT EXISTS expansion;
CREATE SCHEMA IF NOT EXISTS operations;
CREATE SCHEMA IF NOT EXISTS pricing;
CREATE SCHEMA IF NOT EXISTS marketing;
CREATE SCHEMA IF NOT EXISTS asset;
CREATE SCHEMA IF NOT EXISTS network;
CREATE SCHEMA IF NOT EXISTS learning;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS geo;

-- ---------------------------------------------------------
-- Schema: core
-- ---------------------------------------------------------

-- 1. core.tenants
CREATE TABLE core.tenants (
    tenant_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. core.brands
CREATE TABLE core.brands (
    brand_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES core.tenants(tenant_id),
    brand_code VARCHAR(100) NOT NULL UNIQUE,
    brand_name VARCHAR(255) NOT NULL,
    brand_type VARCHAR(50) NOT NULL DEFAULT 'owned', -- owned/franchise/competitor/external
    brand_capture_group VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 3. core.address_locations
CREATE TABLE core.address_locations (
    address_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    raw_address TEXT NOT NULL,
    normalized_address TEXT,
    city VARCHAR(100),
    district VARCHAR(100),
    village VARCHAR(100),
    road VARCHAR(255),
    latitude NUMERIC(10, 7),
    longitude NUMERIC(10, 7),
    geom GEOMETRY(Point, 4326),
    geocode_precision VARCHAR(50) DEFAULT 'manual', -- rooftop/street/district/manual
    geocode_confidence NUMERIC(3, 2), -- 0 to 1
    h3_res_8 VARCHAR(15),
    h3_res_9 VARCHAR(15),
    h3_res_10 VARCHAR(15),
    manual_override_flag BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_address_locations_geom ON core.address_locations USING GIST(geom);
CREATE INDEX idx_address_locations_h3_res_9 ON core.address_locations(h3_res_9);

-- 4. core.stores
CREATE TABLE core.stores (
    store_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    brand_id UUID NOT NULL REFERENCES core.brands(brand_id),
    source_store_id VARCHAR(255),
    store_name VARCHAR(255) NOT NULL,
    store_status VARCHAR(50) NOT NULL DEFAULT 'planned', -- planned/open/suspended/closed/transferred
    ownership_type VARCHAR(50) NOT NULL DEFAULT 'owned', -- owned/franchise/investor_operated/partner
    store_format_code VARCHAR(100),
    opened_on DATE,
    closed_on DATE,
    address_id UUID REFERENCES core.address_locations(address_id),
    region_code VARCHAR(100),
    service_start_time TIME NOT NULL DEFAULT '00:00:00',
    service_end_time TIME NOT NULL DEFAULT '23:59:59',
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    effective_to TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_stores_brand_status ON core.stores(brand_id, store_status);
CREATE INDEX idx_stores_effective_range ON core.stores(effective_from, effective_to);

-- 5. core.machines
CREATE TABLE core.machines (
    machine_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    source_machine_id VARCHAR(255),
    machine_serial_no VARCHAR(255),
    equipment_brand_id VARCHAR(100),
    machine_family VARCHAR(50) NOT NULL DEFAULT 'washer', -- washer/dryer/combo/payment_terminal/other
    machine_type VARCHAR(100),
    capacity_kg NUMERIC(5, 2),
    capacity_band VARCHAR(50) DEFAULT 'medium', -- small/medium/large/xlarge
    installed_on DATE,
    removed_on DATE,
    machine_status VARCHAR(50) NOT NULL DEFAULT 'active', -- active/inactive/maintenance/retired
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    effective_to TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_machines_store ON core.machines(store_id);

-- 6. core.transactions
CREATE TABLE core.transactions (
    transaction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_transaction_id VARCHAR(255),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID REFERENCES core.machines(machine_id),
    member_id VARCHAR(255),
    event_time TIMESTAMP WITH TIME ZONE NOT NULL,
    observation_time TIMESTAMP WITH TIME ZONE NOT NULL,
    payment_time TIMESTAMP WITH TIME ZONE,
    gross_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    net_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    currency VARCHAR(10) NOT NULL DEFAULT 'TWD',
    payment_method VARCHAR(50) NOT NULL DEFAULT 'cash',
    transaction_status VARCHAR(50) NOT NULL DEFAULT 'succeeded', -- succeeded/failed/refunded/voided/partial
    refund_of_transaction_id UUID REFERENCES core.transactions(transaction_id),
    price_schedule_id VARCHAR(255),
    promotion_id VARCHAR(255),
    source_system VARCHAR(100) NOT NULL,
    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_transactions_store_time ON core.transactions(store_id, event_time);
CREATE INDEX idx_transactions_machine ON core.transactions(machine_id);

-- 7. core.machine_cycles
CREATE TABLE core.machine_cycles (
    cycle_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    transaction_id UUID REFERENCES core.transactions(transaction_id),
    cycle_start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    cycle_end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    cycle_type VARCHAR(50) NOT NULL DEFAULT 'wash', -- wash/dry/combo/cleaning/test
    duration_sec INTEGER NOT NULL DEFAULT 0,
    cycle_status VARCHAR(50) NOT NULL DEFAULT 'started', -- started/completed/failed/cancelled
    error_code VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_machine_cycles_machine_time ON core.machine_cycles(machine_id, cycle_start_time);

-- 8. core.machine_status_events
CREATE TABLE core.machine_status_events (
    status_event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    event_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status_type VARCHAR(100) NOT NULL DEFAULT 'online', -- online/offline/error/available/occupied/maintenance
    severity VARCHAR(50) NOT NULL DEFAULT 'info', -- info/warn/error/critical
    error_code VARCHAR(100),
    resolved_time TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_machine_status_machine_time ON core.machine_status_events(machine_id, event_time);

-- 9. core.work_orders
CREATE TABLE core.work_orders (
    work_order_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID REFERENCES core.machines(machine_id),
    issue_type VARCHAR(100) NOT NULL DEFAULT 'failure', -- failure/cleaning/inspection/complaint
    issue_subtype VARCHAR(100),
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL,
    closed_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(50) NOT NULL DEFAULT 'open', -- open/in_progress/resolved/cancelled
    severity VARCHAR(50) NOT NULL DEFAULT 'medium', -- low/medium/high/critical
    cost_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    root_cause TEXT
);
CREATE INDEX idx_work_orders_store ON core.work_orders(store_id);

-- 10. core.customer_service_cases
CREATE TABLE core.customer_service_cases (
    case_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID REFERENCES core.machines(machine_id),
    member_id VARCHAR(255),
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL,
    closed_at TIMESTAMP WITH TIME ZONE,
    channel VARCHAR(50) NOT NULL DEFAULT 'LINE', -- LINE/web/phone/system
    topic_code VARCHAR(100),
    sentiment_score NUMERIC(3, 2) DEFAULT 0.00,
    resolution_status VARCHAR(50) DEFAULT 'unresolved', -- resolved/unresolved/escalated
    ttr_minutes NUMERIC(8, 2) DEFAULT 0.00
);
CREATE INDEX idx_customer_cases_store ON core.customer_service_cases(store_id);

-- ---------------------------------------------------------
-- Schema: geo
-- ---------------------------------------------------------

-- 11. geo.h3_cells
CREATE TABLE geo.h3_cells (
    geo_cell_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    h3_index VARCHAR(15) NOT NULL UNIQUE,
    h3_resolution INTEGER NOT NULL DEFAULT 8,
    parent_h3_index VARCHAR(15),
    centroid_latitude NUMERIC(10, 7) NOT NULL,
    centroid_longitude NUMERIC(10, 7) NOT NULL,
    geom GEOMETRY(Polygon, 4326),
    admin_city VARCHAR(100),
    admin_district VARCHAR(100),
    service_area_id VARCHAR(100)
);
CREATE INDEX idx_h3_cells_geom ON geo.h3_cells USING GIST(geom);

-- 12. geo.pois
CREATE TABLE geo.pois (
    poi_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_poi_id VARCHAR(255) NOT NULL,
    poi_name VARCHAR(255) NOT NULL,
    poi_category VARCHAR(100) NOT NULL,
    poi_subcategory VARCHAR(100),
    address_id UUID REFERENCES core.address_locations(address_id),
    geo_cell_id UUID REFERENCES geo.h3_cells(geo_cell_id),
    status VARCHAR(50) NOT NULL DEFAULT 'active', -- active/closed/unknown
    confidence NUMERIC(3, 2) DEFAULT 1.00,
    snapshot_id VARCHAR(100) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_pois_geo_cell ON geo.pois(geo_cell_id);

-- 13. geo.competitor_stores
CREATE TABLE geo.competitor_stores (
    competitor_store_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    brand_name VARCHAR(255) NOT NULL,
    store_name VARCHAR(255) NOT NULL,
    address_id UUID REFERENCES core.address_locations(address_id),
    geo_cell_id UUID REFERENCES geo.h3_cells(geo_cell_id),
    estimated_capacity NUMERIC(5, 2) DEFAULT 0.00,
    distance_to_nearest_oday_m NUMERIC(10, 2),
    status VARCHAR(50) NOT NULL DEFAULT 'active', -- active/closed/unknown
    confidence NUMERIC(3, 2) DEFAULT 1.00,
    last_verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_competitor_stores_geo_cell ON geo.competitor_stores(geo_cell_id);

-- ---------------------------------------------------------
-- Schema: expansion
-- ---------------------------------------------------------

-- 14. expansion.listings
CREATE TABLE expansion.listings (
    listing_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_listing_id VARCHAR(255) NOT NULL,
    source_id VARCHAR(100) NOT NULL,
    listing_status VARCHAR(50) NOT NULL DEFAULT 'active', -- active/inactive/leased/manual_review/stale
    address_id UUID REFERENCES core.address_locations(address_id),
    rent_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    currency VARCHAR(10) NOT NULL DEFAULT 'TWD',
    area_ping NUMERIC(8, 2) NOT NULL DEFAULT 0.00,
    floor VARCHAR(50),
    frontage_m NUMERIC(5, 2),
    depth_m NUMERIC(5, 2),
    corner_flag BOOLEAN DEFAULT FALSE,
    parking_flag BOOLEAN DEFAULT FALSE,
    utility_electricity_flag BOOLEAN DEFAULT FALSE,
    utility_drainage_flag BOOLEAN DEFAULT FALSE,
    utility_gas_flag BOOLEAN DEFAULT FALSE,
    available_from DATE,
    snapshot_id VARCHAR(100) NOT NULL,
    confidence NUMERIC(3, 2) DEFAULT 1.00,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_listings_address ON expansion.listings(address_id);
CREATE INDEX idx_listings_status ON expansion.listings(listing_status);

-- 15. expansion.candidate_sites
CREATE TABLE expansion.candidate_sites (
    candidate_site_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    listing_id UUID REFERENCES expansion.listings(listing_id),
    address_id UUID REFERENCES core.address_locations(address_id),
    target_format_code VARCHAR(100) NOT NULL,
    site_status VARCHAR(50) NOT NULL DEFAULT 'new', -- new/screened/scored/visited/rejected/approved/opened
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_candidate_sites_address ON expansion.candidate_sites(address_id);

-- ---------------------------------------------------------
-- Schema: learning
-- ---------------------------------------------------------

-- 16. learning.model_versions
CREATE TABLE learning.model_versions (
    model_version_id VARCHAR(100) PRIMARY KEY,
    model_name VARCHAR(255) NOT NULL,
    model_family VARCHAR(50) NOT NULL, -- heatzone/sitescore/forecast/price/adlift/avm/netplan
    registry_uri VARCHAR(512) NOT NULL,
    training_dataset_snapshot_id VARCHAR(100) NOT NULL,
    feature_view_version VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'development', -- development/staging/shadow/canary/production/retired
    released_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 17. learning.prediction_runs
CREATE TABLE learning.prediction_runs (
    prediction_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_version_id VARCHAR(100) REFERENCES learning.model_versions(model_version_id),
    feature_snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL,
    prediction_origin_time TIMESTAMP WITH TIME ZONE NOT NULL,
    prediction_horizon VARCHAR(50) NOT NULL,
    input_snapshot_id VARCHAR(100) NOT NULL,
    output_uri VARCHAR(512),
    run_status VARCHAR(50) NOT NULL DEFAULT 'queued', -- queued/running/succeeded/failed/partial
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 18. learning.predictions
CREATE TABLE learning.predictions (
    prediction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prediction_run_id UUID NOT NULL REFERENCES learning.prediction_runs(prediction_run_id),
    entity_type VARCHAR(100) NOT NULL, -- geo_cell/candidate_site/store/intervention/valuation
    entity_id VARCHAR(255) NOT NULL,
    target_name VARCHAR(100) NOT NULL,
    p10_value NUMERIC(16, 4) NOT NULL,
    p50_value NUMERIC(16, 4) NOT NULL,
    p90_value NUMERIC(16, 4) NOT NULL,
    unit VARCHAR(50),
    explanation_json JSONB,
    confidence NUMERIC(3, 2) DEFAULT 1.00,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_predictions_run_entity ON learning.predictions(prediction_run_id, entity_type, entity_id);

-- ---------------------------------------------------------
-- Schema: workflow
-- ---------------------------------------------------------

-- 19. workflow.decisions
CREATE TABLE workflow.decisions (
    decision_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision_type VARCHAR(100) NOT NULL DEFAULT 'site_go_wait_reject', -- site_go_wait_reject/alert_action/price/ad/valuation/netplan/model_release
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    recommendation TEXT,
    decision_status VARCHAR(50) NOT NULL DEFAULT 'proposed', -- proposed/approved/rejected/overridden/executed/cancelled/expired
    policy_version_id VARCHAR(100) NOT NULL,
    prediction_run_id UUID REFERENCES learning.prediction_runs(prediction_run_id),
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_decisions_entity ON workflow.decisions(entity_type, entity_id);

-- 20. workflow.approvals
CREATE TABLE workflow.approvals (
    approval_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision_id UUID NOT NULL REFERENCES workflow.decisions(decision_id),
    approver_id VARCHAR(255) NOT NULL,
    approval_status VARCHAR(50) NOT NULL DEFAULT 'pending', -- pending/approved/rejected/returned/escalated
    approved_at TIMESTAMP WITH TIME ZONE,
    comment TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_approvals_decision ON workflow.approvals(decision_id);

-- ---------------------------------------------------------
-- Schema: expansion / module-specific
-- ---------------------------------------------------------

-- 21. expansion.heatzone_scores
CREATE TABLE expansion.heatzone_scores (
    heatzone_score_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    geo_cell_id UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id),
    score_run_id UUID NOT NULL,
    heat_score NUMERIC(6, 2) NOT NULL,
    priority_rank INTEGER NOT NULL,
    unmet_demand_score NUMERIC(6, 2) NOT NULL,
    format_fit_score NUMERIC(6, 2) NOT NULL,
    cannibalization_risk_score NUMERIC(6, 2) NOT NULL,
    rent_feasibility_score NUMERIC(6, 2) NOT NULL,
    heatzone_state VARCHAR(50) NOT NULL DEFAULT 'untouched', -- untouched/partially_absorbed/saturated/under_realized/still_expandable
    confidence NUMERIC(3, 2) DEFAULT 1.00,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_heatzone_scores_run ON expansion.heatzone_scores(score_run_id);
CREATE INDEX idx_heatzone_scores_cell ON expansion.heatzone_scores(geo_cell_id);

-- 22. expansion.site_score_runs
CREATE TABLE expansion.site_score_runs (
    sitescore_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    candidate_site_id UUID NOT NULL REFERENCES expansion.candidate_sites(candidate_site_id),
    target_format_code VARCHAR(100) NOT NULL,
    prediction_run_id UUID NOT NULL REFERENCES learning.prediction_runs(prediction_run_id),
    m1_p10 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m1_p50 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m1_p90 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m3_p10 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m3_p50 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m3_p90 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m6_p10 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m6_p50 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m6_p90 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m12_p10 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m12_p50 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    m12_p90 NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    payback_p50_months NUMERIC(4, 1),
    decision_recommendation VARCHAR(50) NOT NULL DEFAULT 'go', -- go/wait/reject/investigate
    report_uri VARCHAR(512),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------
-- Schema: operations / module-specific
-- ---------------------------------------------------------

-- 23. operations.forecast_outputs
CREATE TABLE operations.forecast_outputs (
    forecast_output_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    prediction_run_id UUID NOT NULL REFERENCES learning.prediction_runs(prediction_run_id),
    horizon_days INTEGER NOT NULL DEFAULT 28,
    target_metric VARCHAR(100) NOT NULL DEFAULT 'revenue',
    p10 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    p50 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    p90 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    trajectory_class VARCHAR(50) NOT NULL DEFAULT 'plateau', -- ramping/growing/plateau/declining
    turning_point_probability NUMERIC(3, 2) DEFAULT 0.00,
    sitescore_gap_ratio NUMERIC(5, 2) DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_forecast_outputs_store ON operations.forecast_outputs(store_id);

-- 24. operations.alerts
CREATE TABLE operations.alerts (
    alert_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    alert_level VARCHAR(50) NOT NULL DEFAULT 'green', -- green/yellow/orange/red
    alert_reason_code VARCHAR(100) NOT NULL,
    evidence_json JSONB NOT NULL,
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(50) NOT NULL DEFAULT 'open', -- open/acknowledged/in_progress/resolved/dismissed
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_alerts_store ON operations.alerts(store_id);

-- 25. operations.interventions
CREATE TABLE operations.interventions (
    intervention_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    intervention_type VARCHAR(100) NOT NULL DEFAULT 'price', -- price/ad/promotion/crm/maintenance/cleaning/other
    trigger_alert_id UUID REFERENCES operations.alerts(alert_id),
    eligibility_status VARCHAR(100) NOT NULL DEFAULT 'eligible', -- eligible/ineligible/manual_review
    action_set_json JSONB NOT NULL,
    approved_action_json JSONB NOT NULL,
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    observation_start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    observation_end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'proposed', -- proposed/approved/executing/observing/evaluated/stopped/rolled_back
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_interventions_store ON operations.interventions(store_id);

-- 26. operations.intervention_outcomes
CREATE TABLE operations.intervention_outcomes (
    outcome_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    intervention_id UUID NOT NULL REFERENCES operations.interventions(intervention_id),
    outcome_time TIMESTAMP WITH TIME ZONE NOT NULL,
    incremental_revenue NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    incremental_gross_margin NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    method VARCHAR(100) NOT NULL DEFAULT 'synthetic', -- before_after/did/synthetic/uplift/manual
    evidence_level VARCHAR(50) NOT NULL DEFAULT 'medium', -- low/medium/high/causal_candidate
    side_effect_json JSONB,
    label_maturity_time TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------
-- Schema: asset / module-specific
-- ---------------------------------------------------------

-- 27. asset.valuation_runs
CREATE TABLE asset.valuation_runs (
    valuation_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    valuation_date DATE NOT NULL,
    normalized_gm_ttm NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    gm_fwd_p50 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    income_value_p10 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    income_value_p50 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    income_value_p90 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    asset_value_p50 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    market_value_p50 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    fair_price_p50 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    reserve_price NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    asking_price NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    report_uri VARCHAR(512),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------
-- Schema: network / module-specific
-- ---------------------------------------------------------

-- 28. network.network_plans
CREATE TABLE network.network_plans (
    network_plan_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    planning_period_start DATE NOT NULL,
    planning_period_end DATE NOT NULL,
    scenario_name VARCHAR(100) NOT NULL DEFAULT 'base', -- base/downside/upside/custom
    objective_value NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    solver_status VARCHAR(50) NOT NULL DEFAULT 'optimal', -- optimal/feasible/infeasible/timeout/error
    constraint_summary_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 29. network.network_plan_actions
CREATE TABLE network.network_plan_actions (
    network_plan_action_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    network_plan_id UUID NOT NULL REFERENCES network.network_plans(network_plan_id),
    store_id UUID REFERENCES core.stores(store_id),
    candidate_site_id UUID REFERENCES expansion.candidate_sites(candidate_site_id),
    action_type VARCHAR(50) NOT NULL DEFAULT 'keep', -- open/keep/improve/move/exit
    quarter VARCHAR(20) NOT NULL,
    expected_gm_delta NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    capital_required NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    risk_level VARCHAR(50) NOT NULL DEFAULT 'low',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------
-- Schema: audit
-- ---------------------------------------------------------

-- 30. audit.audit_events
CREATE TABLE audit.audit_events (
    audit_event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_id VARCHAR(255) NOT NULL,
    actor_type VARCHAR(100) NOT NULL DEFAULT 'service', -- user/service/system
    action VARCHAR(100) NOT NULL, -- create/update/delete/approve/reject/export/run_model
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    before_hash VARCHAR(64),
    after_hash VARCHAR(64),
    ip_address VARCHAR(45) NOT NULL,
    correlation_id VARCHAR(255) NOT NULL
);
CREATE INDEX idx_audit_events_actor ON audit.audit_events(actor_id);
CREATE INDEX idx_audit_events_entity ON audit.audit_events(entity_type, entity_id);
CREATE INDEX idx_audit_events_occurred ON audit.audit_events(occurred_at);

-- 31. audit.data_snapshots
CREATE TABLE audit.data_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    snapshot_type VARCHAR(100) NOT NULL DEFAULT 'raw', -- raw/canonical/model_ready/training
    source_id VARCHAR(100) NOT NULL,
    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    storage_uri VARCHAR(512) NOT NULL,
    schema_version VARCHAR(50) NOT NULL,
    row_count BIGINT NOT NULL DEFAULT 0,
    quality_score NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    created_by_run_id VARCHAR(255) NOT NULL
);
