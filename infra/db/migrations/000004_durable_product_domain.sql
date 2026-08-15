-- SQLite-compatible durable product-domain schema for ODay Plus E2E
-- Excludes Postgres schemas and GIS extensions, using TEXT/REAL affinities.

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    tenant_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brands (
    brand_id TEXT PRIMARY KEY,
    tenant_id TEXT REFERENCES tenants(tenant_id),
    brand_code TEXT NOT NULL UNIQUE,
    brand_name TEXT NOT NULL,
    brand_type TEXT NOT NULL DEFAULT 'owned',
    brand_capture_group TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS address_locations (
    address_id TEXT PRIMARY KEY,
    raw_address TEXT NOT NULL,
    normalized_address TEXT,
    city TEXT,
    district TEXT,
    village TEXT,
    road TEXT,
    latitude REAL,
    longitude REAL,
    geom TEXT,
    geocode_precision TEXT NOT NULL DEFAULT 'manual',
    geocode_confidence REAL,
    h3_res_8 TEXT,
    h3_res_9 TEXT,
    h3_res_10 TEXT,
    manual_override_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS data_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    snapshot_type TEXT NOT NULL DEFAULT 'raw',
    source_id TEXT NOT NULL,
    snapshot_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    storage_uri TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    quality_score REAL NOT NULL DEFAULT 1.00,
    created_by_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stores (
    store_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
    brand_id TEXT NOT NULL REFERENCES brands(brand_id),
    source_store_id TEXT,
    store_name TEXT NOT NULL,
    store_status TEXT NOT NULL DEFAULT 'planned',
    ownership_type TEXT NOT NULL DEFAULT 'owned',
    store_format_code TEXT,
    opened_on TEXT,
    closed_on TEXT,
    address_id TEXT REFERENCES address_locations(address_id),
    region_code TEXT,
    service_start_time TEXT NOT NULL DEFAULT '00:00:00',
    service_end_time TEXT NOT NULL DEFAULT '23:59:59',
    effective_from TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    effective_to TEXT NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    is_current INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS machines (
    machine_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL REFERENCES stores(store_id),
    source_machine_id TEXT,
    machine_serial_no TEXT,
    equipment_brand_id TEXT,
    machine_family TEXT NOT NULL DEFAULT 'washer',
    machine_type TEXT,
    capacity_kg REAL,
    capacity_band TEXT NOT NULL DEFAULT 'medium',
    installed_on TEXT,
    removed_on TEXT,
    machine_status TEXT NOT NULL DEFAULT 'active',
    effective_from TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    effective_to TEXT NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    source_transaction_id TEXT,
    store_id TEXT NOT NULL REFERENCES stores(store_id),
    machine_id TEXT REFERENCES machines(machine_id),
    member_id TEXT,
    event_time TEXT NOT NULL,
    observation_time TEXT NOT NULL,
    payment_time TEXT,
    gross_amount REAL NOT NULL DEFAULT 0.00,
    discount_amount REAL NOT NULL DEFAULT 0.00,
    net_amount REAL NOT NULL DEFAULT 0.00,
    currency TEXT NOT NULL DEFAULT 'TWD',
    payment_method TEXT NOT NULL DEFAULT 'cash',
    transaction_status TEXT NOT NULL DEFAULT 'succeeded',
    refund_of_transaction_id TEXT REFERENCES transactions(transaction_id),
    price_schedule_id TEXT,
    promotion_id TEXT,
    source_system TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS machine_cycles (
    cycle_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL REFERENCES stores(store_id),
    machine_id TEXT NOT NULL REFERENCES machines(machine_id),
    transaction_id TEXT REFERENCES transactions(transaction_id),
    cycle_start_time TEXT NOT NULL,
    cycle_end_time TEXT NOT NULL,
    cycle_type TEXT NOT NULL DEFAULT 'wash',
    duration_sec INTEGER NOT NULL DEFAULT 0,
    cycle_status TEXT NOT NULL DEFAULT 'started',
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS h3_cells (
    geo_cell_id TEXT PRIMARY KEY,
    h3_index TEXT NOT NULL UNIQUE,
    h3_resolution INTEGER NOT NULL DEFAULT 8,
    parent_h3_index TEXT,
    centroid_latitude REAL NOT NULL,
    centroid_longitude REAL NOT NULL,
    geom TEXT,
    admin_city TEXT,
    admin_district TEXT,
    service_area_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pois (
    poi_id TEXT PRIMARY KEY,
    source_poi_id TEXT NOT NULL,
    poi_name TEXT NOT NULL,
    poi_category TEXT NOT NULL,
    poi_subcategory TEXT,
    address_id TEXT REFERENCES address_locations(address_id),
    geo_cell_id TEXT REFERENCES h3_cells(geo_cell_id),
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 1.00,
    snapshot_id TEXT REFERENCES data_snapshots(snapshot_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS competitor_stores (
    competitor_store_id TEXT PRIMARY KEY,
    brand_name TEXT NOT NULL,
    store_name TEXT NOT NULL,
    address_id TEXT REFERENCES address_locations(address_id),
    geo_cell_id TEXT REFERENCES h3_cells(geo_cell_id),
    estimated_capacity REAL NOT NULL DEFAULT 0.00,
    distance_to_nearest_oday_m REAL,
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL NOT NULL DEFAULT 1.00,
    last_verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS listings (
    listing_id TEXT PRIMARY KEY,
    source_listing_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    listing_status TEXT NOT NULL DEFAULT 'active',
    address_id TEXT REFERENCES address_locations(address_id),
    rent_amount REAL NOT NULL DEFAULT 0.00,
    currency TEXT NOT NULL DEFAULT 'TWD',
    area_ping REAL NOT NULL DEFAULT 0.00,
    floor TEXT,
    frontage_m REAL,
    depth_m REAL,
    corner_flag INTEGER NOT NULL DEFAULT 0,
    parking_flag INTEGER NOT NULL DEFAULT 0,
    utility_electricity_flag INTEGER NOT NULL DEFAULT 0,
    utility_drainage_flag INTEGER NOT NULL DEFAULT 0,
    utility_gas_flag INTEGER NOT NULL DEFAULT 0,
    available_from TEXT,
    snapshot_id TEXT REFERENCES data_snapshots(snapshot_id),
    confidence REAL NOT NULL DEFAULT 1.00,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidate_sites (
    candidate_site_id TEXT PRIMARY KEY,
    listing_id TEXT REFERENCES listings(listing_id),
    address_id TEXT REFERENCES address_locations(address_id),
    target_format_code TEXT NOT NULL,
    site_status TEXT NOT NULL DEFAULT 'new',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_versions (
    model_version_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_family TEXT NOT NULL,
    registry_uri TEXT NOT NULL,
    training_dataset_snapshot_id TEXT REFERENCES data_snapshots(snapshot_id),
    feature_view_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'development',
    released_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    prediction_run_id TEXT PRIMARY KEY,
    model_version_id TEXT REFERENCES model_versions(model_version_id),
    feature_snapshot_time TEXT NOT NULL,
    prediction_origin_time TEXT NOT NULL,
    prediction_horizon TEXT NOT NULL,
    input_snapshot_id TEXT REFERENCES data_snapshots(snapshot_id),
    output_uri TEXT,
    run_status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    prediction_run_id TEXT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    p10_value REAL NOT NULL,
    p50_value REAL NOT NULL,
    p90_value REAL NOT NULL,
    unit TEXT,
    explanation_json TEXT,
    confidence REAL NOT NULL DEFAULT 1.00,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS forecast_outputs (
    forecast_output_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL REFERENCES stores(store_id),
    prediction_run_id TEXT NOT NULL REFERENCES prediction_runs(prediction_run_id),
    horizon_days INTEGER NOT NULL DEFAULT 28,
    target_metric TEXT NOT NULL DEFAULT 'revenue',
    p10 REAL NOT NULL DEFAULT 0.00,
    p50 REAL NOT NULL DEFAULT 0.00,
    p90 REAL NOT NULL DEFAULT 0.00,
    trajectory_class TEXT NOT NULL DEFAULT 'plateau',
    turning_point_probability REAL NOT NULL DEFAULT 0.00,
    sitescore_gap_ratio REAL NOT NULL DEFAULT 0.00,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS valuation_runs (
    valuation_run_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL REFERENCES stores(store_id),
    prediction_run_id TEXT REFERENCES prediction_runs(prediction_run_id),
    valuation_date TEXT NOT NULL,
    normalized_gm_ttm REAL NOT NULL DEFAULT 0.00,
    gm_fwd_p50 REAL NOT NULL DEFAULT 0.00,
    income_value_p10 REAL NOT NULL DEFAULT 0.00,
    income_value_p50 REAL NOT NULL DEFAULT 0.00,
    income_value_p90 REAL NOT NULL DEFAULT 0.00,
    asset_value_p50 REAL NOT NULL DEFAULT 0.00,
    market_value_p50 REAL NOT NULL DEFAULT 0.00,
    fair_price_p50 REAL NOT NULL DEFAULT 0.00,
    reserve_price REAL NOT NULL DEFAULT 0.00,
    asking_price REAL NOT NULL DEFAULT 0.00,
    report_uri TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS network_plans (
    network_plan_id TEXT PRIMARY KEY,
    data_snapshot_id TEXT REFERENCES data_snapshots(snapshot_id),
    prediction_run_id TEXT REFERENCES prediction_runs(prediction_run_id),
    planning_period_start TEXT NOT NULL,
    planning_period_end TEXT NOT NULL,
    scenario_name TEXT NOT NULL DEFAULT 'base',
    objective_value REAL NOT NULL DEFAULT 0.00,
    solver_status TEXT NOT NULL DEFAULT 'optimal',
    constraint_summary_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_address_locations_h3_res_9 ON address_locations(h3_res_9);
CREATE INDEX IF NOT EXISTS idx_data_snapshots_source_time ON data_snapshots(source_id, snapshot_time);
CREATE INDEX IF NOT EXISTS idx_stores_tenant_status ON stores(tenant_id, store_status);
CREATE INDEX IF NOT EXISTS idx_stores_brand_status ON stores(brand_id, store_status);
CREATE INDEX IF NOT EXISTS idx_stores_address ON stores(address_id);
CREATE INDEX IF NOT EXISTS idx_stores_effective_range ON stores(effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_machines_store ON machines(store_id);
CREATE INDEX IF NOT EXISTS idx_machines_source ON machines(source_machine_id);
CREATE INDEX IF NOT EXISTS idx_machines_status ON machines(machine_status);
CREATE INDEX IF NOT EXISTS idx_transactions_store_time ON transactions(store_id, event_time);
CREATE INDEX IF NOT EXISTS idx_transactions_machine_time ON transactions(machine_id, event_time);
CREATE INDEX IF NOT EXISTS idx_transactions_source ON transactions(source_transaction_id);
CREATE INDEX IF NOT EXISTS idx_transactions_ingested_at ON transactions(ingested_at);
CREATE INDEX IF NOT EXISTS idx_machine_cycles_machine_time ON machine_cycles(machine_id, cycle_start_time);
CREATE INDEX IF NOT EXISTS idx_machine_cycles_store_time ON machine_cycles(store_id, cycle_start_time);
CREATE INDEX IF NOT EXISTS idx_machine_cycles_transaction ON machine_cycles(transaction_id);
CREATE INDEX IF NOT EXISTS idx_h3_cells_parent ON h3_cells(parent_h3_index);
CREATE INDEX IF NOT EXISTS idx_pois_geo_cell ON pois(geo_cell_id);
CREATE INDEX IF NOT EXISTS idx_pois_source ON pois(source_poi_id);
CREATE INDEX IF NOT EXISTS idx_pois_snapshot ON pois(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_pois_category ON pois(poi_category, status);
CREATE INDEX IF NOT EXISTS idx_competitor_stores_geo_cell ON competitor_stores(geo_cell_id);
CREATE INDEX IF NOT EXISTS idx_competitor_stores_brand ON competitor_stores(brand_name, status);
CREATE INDEX IF NOT EXISTS idx_listings_address ON listings(address_id);
CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source_id, source_listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_snapshot ON listings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(listing_status);
CREATE INDEX IF NOT EXISTS idx_candidate_sites_listing ON candidate_sites(listing_id);
CREATE INDEX IF NOT EXISTS idx_candidate_sites_address ON candidate_sites(address_id);
CREATE INDEX IF NOT EXISTS idx_candidate_sites_status ON candidate_sites(site_status);
CREATE INDEX IF NOT EXISTS idx_model_versions_family_status ON model_versions(model_family, status);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_model_time ON prediction_runs(model_version_id, prediction_origin_time);
CREATE INDEX IF NOT EXISTS idx_prediction_runs_snapshot ON prediction_runs(input_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_predictions_run_entity ON predictions(prediction_run_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_name, created_at);
CREATE INDEX IF NOT EXISTS idx_forecast_outputs_store ON forecast_outputs(store_id);
CREATE INDEX IF NOT EXISTS idx_forecast_outputs_run ON forecast_outputs(prediction_run_id);
CREATE INDEX IF NOT EXISTS idx_valuation_runs_store_date ON valuation_runs(store_id, valuation_date);
CREATE INDEX IF NOT EXISTS idx_valuation_runs_prediction ON valuation_runs(prediction_run_id);
CREATE INDEX IF NOT EXISTS idx_network_plans_period ON network_plans(planning_period_start, planning_period_end);
CREATE INDEX IF NOT EXISTS idx_network_plans_snapshot ON network_plans(data_snapshot_id);
