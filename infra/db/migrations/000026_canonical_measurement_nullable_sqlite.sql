-- Migration 000026: Make confidence/quality_score nullable in SQLite.
-- SQLite has no ALTER COLUMN; tables must be rebuilt to change NOT NULL.
-- data_snapshots.quality_score was already made nullable in 000024.
-- Existing 1.00 values are preserved — they may be genuine measurements.

PRAGMA foreign_keys = OFF;

-- ============================================================
-- 1. pois: confidence REAL NOT NULL DEFAULT 1.00 → REAL
-- ============================================================
DROP TABLE IF EXISTS pois_nullable;

CREATE TABLE pois_nullable (
    poi_id TEXT PRIMARY KEY,
    source_poi_id TEXT NOT NULL,
    poi_name TEXT NOT NULL,
    poi_category TEXT NOT NULL,
    poi_subcategory TEXT,
    address_id TEXT REFERENCES address_locations(address_id),
    geo_cell_id TEXT REFERENCES h3_cells(geo_cell_id),
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL,
    snapshot_id TEXT REFERENCES data_snapshots(snapshot_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO pois_nullable (
    poi_id, source_poi_id, poi_name, poi_category, poi_subcategory,
    address_id, geo_cell_id, status, confidence, snapshot_id, created_at, updated_at
) SELECT
    poi_id, source_poi_id, poi_name, poi_category, poi_subcategory,
    address_id, geo_cell_id, status, confidence, snapshot_id, created_at, updated_at
FROM pois;
DROP TABLE pois;
ALTER TABLE pois_nullable RENAME TO pois;

-- Recreate pois secondary indexes from 000004
CREATE INDEX IF NOT EXISTS idx_pois_geo_cell ON pois(geo_cell_id);
CREATE INDEX IF NOT EXISTS idx_pois_source ON pois(source_poi_id);
CREATE INDEX IF NOT EXISTS idx_pois_snapshot ON pois(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_pois_category ON pois(poi_category, status);

-- ============================================================
-- 2. competitor_stores: confidence REAL NOT NULL DEFAULT 1.00 → REAL
-- ============================================================
DROP TABLE IF EXISTS competitor_stores_nullable;

CREATE TABLE competitor_stores_nullable (
    competitor_store_id TEXT PRIMARY KEY,
    brand_name TEXT NOT NULL,
    store_name TEXT NOT NULL,
    address_id TEXT REFERENCES address_locations(address_id),
    geo_cell_id TEXT REFERENCES h3_cells(geo_cell_id),
    estimated_capacity REAL NOT NULL DEFAULT 0.00,
    distance_to_nearest_oday_m REAL,
    status TEXT NOT NULL DEFAULT 'active',
    confidence REAL,
    last_verified_at TEXT,
    measurement_schema_version TEXT NOT NULL DEFAULT 'v1',
    snapshot_id TEXT REFERENCES data_snapshots(snapshot_id),
    source_competitor_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO competitor_stores_nullable (
    competitor_store_id, brand_name, store_name, address_id, geo_cell_id,
    estimated_capacity, distance_to_nearest_oday_m, status, confidence,
    last_verified_at, created_at, updated_at
) SELECT
    competitor_store_id, brand_name, store_name, address_id, geo_cell_id,
    estimated_capacity, distance_to_nearest_oday_m, status, confidence,
    last_verified_at, created_at, updated_at
FROM competitor_stores;
DROP TABLE competitor_stores;
ALTER TABLE competitor_stores_nullable RENAME TO competitor_stores;

-- Recreate competitor_stores secondary indexes from 000004
CREATE INDEX IF NOT EXISTS idx_competitor_stores_geo_cell ON competitor_stores(geo_cell_id);
CREATE INDEX IF NOT EXISTS idx_competitor_stores_brand ON competitor_stores(brand_name, status);

-- ============================================================
-- 3. listings: confidence REAL NOT NULL DEFAULT 1.00 → REAL
-- ============================================================
DROP TABLE IF EXISTS listings_nullable;

CREATE TABLE listings_nullable (
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
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO listings_nullable (
    listing_id, source_listing_id, source_id, listing_status, address_id,
    rent_amount, currency, area_ping, floor, frontage_m, depth_m,
    corner_flag, parking_flag, utility_electricity_flag, utility_drainage_flag,
    utility_gas_flag, available_from, snapshot_id, confidence, created_at, updated_at
) SELECT
    listing_id, source_listing_id, source_id, listing_status, address_id,
    rent_amount, currency, area_ping, floor, frontage_m, depth_m,
    corner_flag, parking_flag, utility_electricity_flag, utility_drainage_flag,
    utility_gas_flag, available_from, snapshot_id, confidence, created_at, updated_at
FROM listings;
DROP TABLE listings;
ALTER TABLE listings_nullable RENAME TO listings;

-- Recreate listings secondary indexes from 000004
CREATE INDEX IF NOT EXISTS idx_listings_address ON listings(address_id);
CREATE INDEX IF NOT EXISTS idx_listings_source ON listings(source_id, source_listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_snapshot ON listings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings(listing_status);

-- ============================================================
-- 4. predictions: confidence REAL NOT NULL DEFAULT 1.00 → REAL
-- ============================================================
DROP TABLE IF EXISTS predictions_nullable;

CREATE TABLE predictions_nullable (
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
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO predictions_nullable (
    prediction_id, prediction_run_id, entity_type, entity_id, target_name,
    p10_value, p50_value, p90_value, unit, explanation_json, confidence,
    created_at, updated_at
) SELECT
    prediction_id, prediction_run_id, entity_type, entity_id, target_name,
    p10_value, p50_value, p90_value, unit, explanation_json, confidence,
    created_at, updated_at
FROM predictions;
DROP TABLE predictions;
ALTER TABLE predictions_nullable RENAME TO predictions;

-- Recreate predictions secondary indexes from 000004
CREATE INDEX IF NOT EXISTS idx_predictions_run_entity ON predictions(prediction_run_id, entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target_name, created_at);

PRAGMA foreign_keys = ON;
