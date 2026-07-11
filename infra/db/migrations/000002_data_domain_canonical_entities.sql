-- Canonical data-domain entities for ODay Plus.
-- This migration is intentionally idempotent so it can run after the baseline
-- migration or bootstrap an environment that only has non-data schemas.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis";

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS geo;
CREATE SCHEMA IF NOT EXISTS expansion;
CREATE SCHEMA IF NOT EXISTS learning;
CREATE SCHEMA IF NOT EXISTS operations;
CREATE SCHEMA IF NOT EXISTS asset;
CREATE SCHEMA IF NOT EXISTS network;
CREATE SCHEMA IF NOT EXISTS audit;

-- Supporting canonical dimensions required by the data-domain FKs.
CREATE TABLE IF NOT EXISTS core.tenants (
    tenant_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_name VARCHAR(255) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.brands (
    brand_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES core.tenants(tenant_id),
    brand_code VARCHAR(100) NOT NULL UNIQUE,
    brand_name VARCHAR(255) NOT NULL,
    brand_type VARCHAR(50) NOT NULL DEFAULT 'owned',
    brand_capture_group VARCHAR(100),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS core.address_locations (
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
    geocode_precision VARCHAR(50) NOT NULL DEFAULT 'manual',
    geocode_confidence NUMERIC(3, 2),
    h3_res_8 VARCHAR(15),
    h3_res_9 VARCHAR(15),
    h3_res_10 VARCHAR(15),
    manual_override_flag BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- data_snapshot
CREATE TABLE IF NOT EXISTS audit.data_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    snapshot_type VARCHAR(100) NOT NULL DEFAULT 'raw',
    source_id VARCHAR(100) NOT NULL,
    snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    storage_uri VARCHAR(512) NOT NULL,
    schema_version VARCHAR(50) NOT NULL,
    row_count BIGINT NOT NULL DEFAULT 0,
    quality_score NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    created_by_run_id VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- store
CREATE TABLE IF NOT EXISTS core.stores (
    store_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    brand_id UUID NOT NULL REFERENCES core.brands(brand_id),
    source_store_id VARCHAR(255),
    store_name VARCHAR(255) NOT NULL,
    store_status VARCHAR(50) NOT NULL DEFAULT 'planned',
    ownership_type VARCHAR(50) NOT NULL DEFAULT 'owned',
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

-- machine
CREATE TABLE IF NOT EXISTS core.machines (
    machine_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    source_machine_id VARCHAR(255),
    machine_serial_no VARCHAR(255),
    equipment_brand_id VARCHAR(100),
    machine_family VARCHAR(50) NOT NULL DEFAULT 'washer',
    machine_type VARCHAR(100),
    capacity_kg NUMERIC(5, 2),
    capacity_band VARCHAR(50) NOT NULL DEFAULT 'medium',
    installed_on DATE,
    removed_on DATE,
    machine_status VARCHAR(50) NOT NULL DEFAULT 'active',
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    effective_to TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- transaction
CREATE TABLE IF NOT EXISTS core.transactions (
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
    transaction_status VARCHAR(50) NOT NULL DEFAULT 'succeeded',
    refund_of_transaction_id UUID REFERENCES core.transactions(transaction_id),
    price_schedule_id VARCHAR(255),
    promotion_id VARCHAR(255),
    source_system VARCHAR(100) NOT NULL,
    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- machine_cycle
CREATE TABLE IF NOT EXISTS core.machine_cycles (
    cycle_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    transaction_id UUID REFERENCES core.transactions(transaction_id),
    cycle_start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    cycle_end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    cycle_type VARCHAR(50) NOT NULL DEFAULT 'wash',
    duration_sec INTEGER NOT NULL DEFAULT 0,
    cycle_status VARCHAR(50) NOT NULL DEFAULT 'started',
    error_code VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- geo_cell
CREATE TABLE IF NOT EXISTS geo.h3_cells (
    geo_cell_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    h3_index VARCHAR(15) NOT NULL UNIQUE,
    h3_resolution INTEGER NOT NULL DEFAULT 8,
    parent_h3_index VARCHAR(15),
    centroid_latitude NUMERIC(10, 7) NOT NULL,
    centroid_longitude NUMERIC(10, 7) NOT NULL,
    geom GEOMETRY(Polygon, 4326),
    admin_city VARCHAR(100),
    admin_district VARCHAR(100),
    service_area_id VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- poi
CREATE TABLE IF NOT EXISTS geo.pois (
    poi_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_poi_id VARCHAR(255) NOT NULL,
    poi_name VARCHAR(255) NOT NULL,
    poi_category VARCHAR(100) NOT NULL,
    poi_subcategory VARCHAR(100),
    address_id UUID REFERENCES core.address_locations(address_id),
    geo_cell_id UUID REFERENCES geo.h3_cells(geo_cell_id),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    confidence NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    snapshot_id UUID REFERENCES audit.data_snapshots(snapshot_id),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- competitor
CREATE TABLE IF NOT EXISTS geo.competitor_stores (
    competitor_store_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    brand_name VARCHAR(255) NOT NULL,
    store_name VARCHAR(255) NOT NULL,
    address_id UUID REFERENCES core.address_locations(address_id),
    geo_cell_id UUID REFERENCES geo.h3_cells(geo_cell_id),
    estimated_capacity NUMERIC(5, 2) NOT NULL DEFAULT 0.00,
    distance_to_nearest_oday_m NUMERIC(10, 2),
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    confidence NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    last_verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- listing
CREATE TABLE IF NOT EXISTS expansion.listings (
    listing_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_listing_id VARCHAR(255) NOT NULL,
    source_id VARCHAR(100) NOT NULL,
    listing_status VARCHAR(50) NOT NULL DEFAULT 'active',
    address_id UUID REFERENCES core.address_locations(address_id),
    rent_amount NUMERIC(12, 2) NOT NULL DEFAULT 0.00,
    currency VARCHAR(10) NOT NULL DEFAULT 'TWD',
    area_ping NUMERIC(8, 2) NOT NULL DEFAULT 0.00,
    floor VARCHAR(50),
    frontage_m NUMERIC(5, 2),
    depth_m NUMERIC(5, 2),
    corner_flag BOOLEAN NOT NULL DEFAULT FALSE,
    parking_flag BOOLEAN NOT NULL DEFAULT FALSE,
    utility_electricity_flag BOOLEAN NOT NULL DEFAULT FALSE,
    utility_drainage_flag BOOLEAN NOT NULL DEFAULT FALSE,
    utility_gas_flag BOOLEAN NOT NULL DEFAULT FALSE,
    available_from DATE,
    snapshot_id UUID REFERENCES audit.data_snapshots(snapshot_id),
    confidence NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- candidate_site
CREATE TABLE IF NOT EXISTS expansion.candidate_sites (
    candidate_site_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    listing_id UUID REFERENCES expansion.listings(listing_id),
    address_id UUID REFERENCES core.address_locations(address_id),
    target_format_code VARCHAR(100) NOT NULL,
    site_status VARCHAR(50) NOT NULL DEFAULT 'new',
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- model_version
CREATE TABLE IF NOT EXISTS learning.model_versions (
    model_version_id VARCHAR(100) PRIMARY KEY,
    model_name VARCHAR(255) NOT NULL,
    model_family VARCHAR(50) NOT NULL,
    registry_uri VARCHAR(512) NOT NULL,
    training_dataset_snapshot_id UUID REFERENCES audit.data_snapshots(snapshot_id),
    feature_view_version VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'development',
    released_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- prediction_run
CREATE TABLE IF NOT EXISTS learning.prediction_runs (
    prediction_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_version_id VARCHAR(100) REFERENCES learning.model_versions(model_version_id),
    feature_snapshot_time TIMESTAMP WITH TIME ZONE NOT NULL,
    prediction_origin_time TIMESTAMP WITH TIME ZONE NOT NULL,
    prediction_horizon VARCHAR(50) NOT NULL,
    input_snapshot_id UUID REFERENCES audit.data_snapshots(snapshot_id),
    output_uri VARCHAR(512),
    run_status VARCHAR(50) NOT NULL DEFAULT 'queued',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- prediction
CREATE TABLE IF NOT EXISTS learning.predictions (
    prediction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prediction_run_id UUID NOT NULL REFERENCES learning.prediction_runs(prediction_run_id),
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(255) NOT NULL,
    target_name VARCHAR(100) NOT NULL,
    p10_value NUMERIC(16, 4) NOT NULL,
    p50_value NUMERIC(16, 4) NOT NULL,
    p90_value NUMERIC(16, 4) NOT NULL,
    unit VARCHAR(50),
    explanation_json JSONB,
    confidence NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- forecast_output
CREATE TABLE IF NOT EXISTS operations.forecast_outputs (
    forecast_output_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    prediction_run_id UUID NOT NULL REFERENCES learning.prediction_runs(prediction_run_id),
    horizon_days INTEGER NOT NULL DEFAULT 28,
    target_metric VARCHAR(100) NOT NULL DEFAULT 'revenue',
    p10 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    p50 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    p90 NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    trajectory_class VARCHAR(50) NOT NULL DEFAULT 'plateau',
    turning_point_probability NUMERIC(3, 2) NOT NULL DEFAULT 0.00,
    sitescore_gap_ratio NUMERIC(5, 2) NOT NULL DEFAULT 0.00,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- valuation_run
CREATE TABLE IF NOT EXISTS asset.valuation_runs (
    valuation_run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    prediction_run_id UUID REFERENCES learning.prediction_runs(prediction_run_id),
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
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- network_plan
CREATE TABLE IF NOT EXISTS network.network_plans (
    network_plan_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    data_snapshot_id UUID REFERENCES audit.data_snapshots(snapshot_id),
    prediction_run_id UUID REFERENCES learning.prediction_runs(prediction_run_id),
    planning_period_start DATE NOT NULL,
    planning_period_end DATE NOT NULL,
    scenario_name VARCHAR(100) NOT NULL DEFAULT 'base',
    objective_value NUMERIC(14, 2) NOT NULL DEFAULT 0.00,
    solver_status VARCHAR(50) NOT NULL DEFAULT 'optimal',
    constraint_summary_json JSONB,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Bring existing baseline tables up to the data-domain timestamp surface.
ALTER TABLE IF EXISTS audit.data_snapshots
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS audit.data_snapshots
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS core.transactions
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS core.transactions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS core.machine_cycles
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS geo.h3_cells
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS geo.h3_cells
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS geo.pois
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS geo.competitor_stores
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS expansion.listings
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS learning.model_versions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS learning.predictions
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS operations.forecast_outputs
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS asset.valuation_runs
    ADD COLUMN IF NOT EXISTS prediction_run_id UUID REFERENCES learning.prediction_runs(prediction_run_id);
ALTER TABLE IF EXISTS asset.valuation_runs
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS network.network_plans
    ADD COLUMN IF NOT EXISTS data_snapshot_id UUID REFERENCES audit.data_snapshots(snapshot_id);
ALTER TABLE IF EXISTS network.network_plans
    ADD COLUMN IF NOT EXISTS prediction_run_id UUID REFERENCES learning.prediction_runs(prediction_run_id);

-- FK-backed access and time-window indexes for canonical data workflows.
CREATE INDEX IF NOT EXISTS idx_address_locations_geom ON core.address_locations USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_address_locations_h3_res_9 ON core.address_locations(h3_res_9);
CREATE INDEX IF NOT EXISTS idx_data_snapshots_source_time ON audit.data_snapshots(source_id, snapshot_time);
CREATE INDEX IF NOT EXISTS idx_stores_tenant_status ON core.stores(tenant_id, store_status);
CREATE INDEX IF NOT EXISTS idx_stores_brand_status ON core.stores(brand_id, store_status);
CREATE INDEX IF NOT EXISTS idx_stores_address ON core.stores(address_id);
CREATE INDEX IF NOT EXISTS idx_stores_effective_range ON core.stores(effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_machines_store ON core.machines(store_id);
CREATE INDEX IF NOT EXISTS idx_machines_source ON core.machines(source_machine_id);
CREATE INDEX IF NOT EXISTS idx_machines_status ON core.machines(machine_status);
CREATE INDEX IF NOT EXISTS idx_transactions_store_time ON core.transactions(store_id, event_time);
CREATE INDEX IF NOT EXISTS idx_transactions_machine_time ON core.transactions(machine_id, event_time);
CREATE INDEX IF NOT EXISTS idx_transactions_source ON core.transactions(source_transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_ingested_at ON core.transactions(ingested_at);
CREATE INDEX IF NOT EXISTS idx_machine_cycles_machine_time ON core.machine_cycles(machine_id, cycle_start_time);
CREATE INDEX IF NOT EXISTS idx_machine_cycles_store_time ON core.machine_cycles(store_id, cycle_start_time);
CREATE INDEX IF NOT EXISTS idx_machine_cycles_transaction ON core.machine_cycles(transaction_id);
CREATE INDEX IF NOT EXISTS idx_h3_cells_geom ON geo.h3_cells USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_h3_cells_parent ON geo.h3_cells(parent_h3_index);
CREATE INDEX IF NOT EXISTS idx_pois_geo_cell ON geo.pois(geo_cell_id);
CREATE INDEX IF NOT EXISTS idx_pois_source ON geo.pois(source_poi_id);
CREATE INDEX IF NOT EXISTS idx_pois_snapshot ON geo.pois(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_pois_category ON geo.pois(poi_category, status);
CREATE INDEX IF NOT EXISTS idx_competitor_stores_geo_cell ON geo.competitor_stores(geo_cell_id);
CREATE INDEX IF NOT EXISTS idx_competitor_stores_brand ON geo.competitor_stores(brand_name, status);
CREATE INDEX IF NOT EXISTS idx_listings_address ON expansion.listings(address_id);
CREATE INDEX IF NOT EXISTS idx_listings_source ON expansion.listings(source_id, source_listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_snapshot ON expansion.listings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_listings_status ON expansion.listings(listing_status);
CREATE INDEX IF NOT EXISTS idx_candidate_sites_listing ON expansion.candidate_sites(listing_id);
CREATE INDEX IF NOT EXISTS idx_candidate_sites_address ON expansion.candidate_sites(address_id);
CREATE INDEX IF NOT EXISTS idx_candidate_sites_status ON expansion.candidate_sites(site_status);
CREATE INDEX IF NOT EXISTS idx_model_versions_family_status ON learning.model_versions(model_family, status);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_model_time ON learning.prediction_runs(model_version_id, prediction_origin_time);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_snapshot ON learning.prediction_runs(input_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_predictions_run_entity ON learning.predictions(prediction_run_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_predictions_target ON learning.predictions(target_name, created_at);
CREATE INDEX IF NOT EXISTS idx_forecast_outputs_store ON operations.forecast_outputs(store_id);
CREATE INDEX IF NOT EXISTS idx_forecast_outputs_run ON operations.forecast_outputs(prediction_run_id);
CREATE INDEX IF NOT EXISTS idx_valuation_runs_store_date ON asset.valuation_runs(store_id, valuation_date);
CREATE INDEX IF NOT EXISTS idx_valuation_runs_prediction ON asset.valuation_runs(prediction_run_id);
CREATE INDEX IF NOT EXISTS idx_network_plans_period ON network.network_plans(planning_period_start, planning_period_end);
CREATE INDEX IF NOT EXISTS idx_network_plans_snapshot ON network.network_plans(data_snapshot_id);
