-- Authoritative Taiwan government real-estate sale outcomes for AVM training.
-- Sources are restricted to MOI dataset 25119 and the compatible NTPC dataset.
-- This migration stores observed government records; it creates no labels or rows.

CREATE SCHEMA IF NOT EXISTS external_data;

CREATE TABLE IF NOT EXISTS external_data.real_estate_ingestion_runs (
    run_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    metadata_url TEXT NOT NULL,
    publisher TEXT NOT NULL,
    license_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    content_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    schema_sha256 TEXT NOT NULL CHECK (schema_sha256 ~ '^[0-9a-f]{64}$'),
    source_snapshot_id TEXT NOT NULL,
    content_length_bytes BIGINT NOT NULL CHECK (
        content_length_bytes > 0
        AND content_length_bytes <= 104857600
    ),
    etag TEXT,
    source_published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'FAILED')),
    parsed_row_count INTEGER NOT NULL DEFAULT 0 CHECK (
        parsed_row_count >= 0
        AND parsed_row_count <= 250000
    ),
    inserted_row_count INTEGER NOT NULL DEFAULT 0 CHECK (
        inserted_row_count >= 0
        AND inserted_row_count <= parsed_row_count
    ),
    updated_row_count INTEGER NOT NULL DEFAULT 0 CHECK (
        updated_row_count >= 0
        AND updated_row_count <= parsed_row_count
    ),
    error_code TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_id, content_sha256, parser_version),
    CHECK (
        (
            source_id = 'tw_moi_lvr_land_a'
            AND dataset_id = '25119'
            AND source_url =
                'https://plvr.land.moi.gov.tw/opendata/lvr_landAcsv.zip'
            AND metadata_url = 'https://data.gov.tw/dataset/25119'
            AND publisher =
                'Ministry of the Interior, Department of Land Administration'
            AND content_type = 'application/zip'
        )
        OR
        (
            source_id = 'tw_ntpc_real_estate_sales'
            AND dataset_id = 'acce802d-58cc-4dff-9e7a-9ecc517f78be'
            AND source_url = 'https://data.ntpc.gov.tw/api/datasets/acce802d-58cc-4dff-9e7a-9ecc517f78be/csv/file'
            AND metadata_url = 'https://data.ntpc.gov.tw/datasets/acce802d-58cc-4dff-9e7a-9ecc517f78be'
            AND publisher =
                'New Taipei City Government Land Administration Department'
            AND content_type = 'text/csv'
        )
    ),
    CHECK (license_id = 'government-open-data-license-v1'),
    CHECK (
        (
            status = 'SUCCEEDED'
            AND parsed_row_count > 0
            AND completed_at IS NOT NULL
            AND error_code IS NULL
            AND error_message IS NULL
        )
        OR status IN ('RUNNING', 'FAILED')
    ),
    CHECK (
        (status = 'FAILED' AND error_code IS NOT NULL AND completed_at IS NOT NULL)
        OR status <> 'FAILED'
    )
);

COMMENT ON TABLE external_data.real_estate_ingestion_runs IS
    'Bounded official-source snapshots with dataset, license, checksum and parser provenance.';

CREATE TABLE IF NOT EXISTS external_data.real_estate_transactions (
    transaction_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL CHECK (
        source_id IN ('tw_moi_lvr_land_a', 'tw_ntpc_real_estate_sales')
    ),
    authority_partition TEXT NOT NULL CHECK (authority_partition <> ''),
    source_record_id TEXT NOT NULL CHECK (source_record_id <> ''),
    source_variant_id TEXT NOT NULL CHECK (source_variant_id <> ''),
    municipality TEXT NOT NULL,
    district TEXT NOT NULL,
    transaction_target TEXT NOT NULL,
    address TEXT,
    transaction_date DATE NOT NULL,
    transaction_count_summary TEXT,
    land_area_sqm NUMERIC(16, 4) CHECK (land_area_sqm IS NULL OR land_area_sqm >= 0),
    urban_land_use TEXT,
    non_urban_land_use TEXT,
    non_urban_land_designation TEXT,
    transferred_floor TEXT,
    total_floors TEXT,
    building_type TEXT,
    main_use TEXT,
    main_material TEXT,
    completion_date DATE,
    completion_year INTEGER CHECK (
        completion_year IS NULL OR completion_year BETWEEN 1800 AND 2200
    ),
    completion_month INTEGER CHECK (
        completion_month IS NULL OR completion_month BETWEEN 1 AND 12
    ),
    building_area_sqm NUMERIC(16, 4) CHECK (
        building_area_sqm IS NULL OR building_area_sqm >= 0
    ),
    room_count INTEGER CHECK (room_count IS NULL OR room_count >= 0),
    hall_count INTEGER CHECK (hall_count IS NULL OR hall_count >= 0),
    bathroom_count INTEGER CHECK (bathroom_count IS NULL OR bathroom_count >= 0),
    has_partition BOOLEAN,
    has_management BOOLEAN,
    total_price_twd BIGINT NOT NULL CHECK (total_price_twd > 0),
    unit_price_twd_sqm BIGINT CHECK (
        unit_price_twd_sqm IS NULL OR unit_price_twd_sqm >= 0
    ),
    parking_type TEXT,
    parking_area_sqm NUMERIC(16, 4) CHECK (
        parking_area_sqm IS NULL OR parking_area_sqm >= 0
    ),
    parking_price_twd BIGINT CHECK (
        parking_price_twd IS NULL OR parking_price_twd >= 0
    ),
    remarks TEXT,
    main_building_area_sqm NUMERIC(16, 4) CHECK (
        main_building_area_sqm IS NULL OR main_building_area_sqm >= 0
    ),
    auxiliary_building_area_sqm NUMERIC(16, 4) CHECK (
        auxiliary_building_area_sqm IS NULL OR auxiliary_building_area_sqm >= 0
    ),
    balcony_area_sqm NUMERIC(16, 4) CHECK (
        balcony_area_sqm IS NULL OR balcony_area_sqm >= 0
    ),
    has_elevator BOOLEAN,
    transfer_number TEXT,
    first_seen_run_id UUID NOT NULL REFERENCES
        external_data.real_estate_ingestion_runs(run_id),
    last_seen_run_id UUID NOT NULL REFERENCES
        external_data.real_estate_ingestion_runs(run_id),
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_observed_at TIMESTAMPTZ NOT NULL,
    raw_record_sha256 TEXT NOT NULL CHECK (
        raw_record_sha256 ~ '^[0-9a-f]{64}$'
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (
        source_id,
        authority_partition,
        source_record_id,
        source_variant_id
    ),
    CHECK (last_observed_at >= first_observed_at),
    CHECK (
        completion_month IS NULL
        OR completion_year IS NOT NULL
    )
);

COMMENT ON TABLE external_data.real_estate_transactions IS
    'Latest parsed value for each source-local official real-estate sale record.';

CREATE TABLE IF NOT EXISTS external_data.real_estate_transaction_observations (
    run_id UUID NOT NULL REFERENCES
        external_data.real_estate_ingestion_runs(run_id) ON DELETE RESTRICT,
    transaction_id UUID NOT NULL REFERENCES
        external_data.real_estate_transactions(transaction_id) ON DELETE RESTRICT,
    source_id TEXT NOT NULL,
    authority_partition TEXT NOT NULL CHECK (authority_partition <> ''),
    source_record_id TEXT NOT NULL CHECK (source_record_id <> ''),
    source_variant_id TEXT NOT NULL CHECK (source_variant_id <> ''),
    source_file TEXT NOT NULL CHECK (source_file <> ''),
    source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
    raw_record_sha256 TEXT NOT NULL CHECK (
        raw_record_sha256 ~ '^[0-9a-f]{64}$'
    ),
    raw_fields JSONB NOT NULL CHECK (jsonb_typeof(raw_fields) = 'object'),
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, transaction_id),
    FOREIGN KEY (
        source_id,
        authority_partition,
        source_record_id,
        source_variant_id
    )
        REFERENCES external_data.real_estate_transactions(
            source_id,
            authority_partition,
            source_record_id,
            source_variant_id
        )
        ON DELETE RESTRICT
);

COMMENT ON TABLE external_data.real_estate_transaction_observations IS
    'Immutable per-snapshot row evidence, including original official CSV fields.';

CREATE INDEX IF NOT EXISTS idx_real_estate_runs_source_fetched
    ON external_data.real_estate_ingestion_runs(source_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_real_estate_runs_snapshot
    ON external_data.real_estate_ingestion_runs(source_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_real_estate_transactions_date
    ON external_data.real_estate_transactions(transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_real_estate_transactions_market
    ON external_data.real_estate_transactions(
        municipality,
        district,
        transaction_target,
        transaction_date DESC
    );
CREATE INDEX IF NOT EXISTS idx_real_estate_transactions_last_run
    ON external_data.real_estate_transactions(last_seen_run_id);
CREATE INDEX IF NOT EXISTS idx_real_estate_observations_record
    ON external_data.real_estate_transaction_observations(
        source_id,
        authority_partition,
        source_record_id,
        source_variant_id,
        observed_at DESC
    );
