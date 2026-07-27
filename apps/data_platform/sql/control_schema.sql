-- ODay Plus production Mongo -> PostgreSQL data-plane control schema.
-- The installer substitutes {{control_schema}} with a validated SQL identifier.

CREATE SCHEMA IF NOT EXISTS {{control_schema}};

CREATE TABLE IF NOT EXISTS {{control_schema}}.ingestion_runs (
    run_id UUID PRIMARY KEY,
    source_database TEXT NOT NULL CHECK (source_database = 'fongniao_prod'),
    source_kind TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'RECONCILIATION_FAILED')
    ),
    resumed_from TEXT,
    final_cursor TEXT,
    processed_count BIGINT NOT NULL DEFAULT 0 CHECK (processed_count >= 0),
    valid_loaded BIGINT NOT NULL DEFAULT 0 CHECK (valid_loaded >= 0),
    quarantined_count BIGINT NOT NULL DEFAULT 0 CHECK (quarantined_count >= 0),
    source_checksum TEXT,
    raw_checksum TEXT,
    canonical_checksum TEXT,
    error_type TEXT,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    UNIQUE (source_kind, partition_key, run_id)
);
CREATE INDEX IF NOT EXISTS ix_data_plane_runs_partition
    ON {{control_schema}}.ingestion_runs(source_kind, partition_key, started_at DESC);

CREATE TABLE IF NOT EXISTS {{control_schema}}.checkpoints (
    source_kind TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    source_cursor TEXT NOT NULL,
    source_updated_at TIMESTAMPTZ,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    processed_count BIGINT NOT NULL CHECK (processed_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_kind, partition_key)
);

CREATE TABLE IF NOT EXISTS {{control_schema}}.canonical_lineage (
    source_snapshot_id UUID NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    canonical_table TEXT NOT NULL,
    canonical_id UUID NOT NULL,
    projected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_snapshot_id, canonical_table, canonical_id)
);
CREATE INDEX IF NOT EXISTS ix_data_plane_lineage_run
    ON {{control_schema}}.canonical_lineage(run_id, source_kind);
CREATE INDEX IF NOT EXISTS ix_data_plane_lineage_tenant
    ON {{control_schema}}.canonical_lineage(tenant_id, canonical_table, canonical_id);

CREATE TABLE IF NOT EXISTS {{control_schema}}.projection_failures (
    failure_id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    source_kind TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    source_snapshot_ids UUID[] NOT NULL DEFAULT '{}',
    error_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    retryable BOOLEAN NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS {{control_schema}}.quarantined_records (
    source_snapshot_id UUID PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    partition_key TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT NOT NULL,
    retryable BOOLEAN NOT NULL DEFAULT FALSE,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_data_plane_quarantine_run
    ON {{control_schema}}.quarantined_records(run_id, source_kind, reason_code);

CREATE TABLE IF NOT EXISTS {{control_schema}}.store_daily_facts (
    source_snapshot_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    gross_amount NUMERIC(16, 2) NOT NULL,
    transaction_count BIGINT NOT NULL CHECK (transaction_count >= 0),
    gateway TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (period_end > period_start)
);
CREATE INDEX IF NOT EXISTS ix_data_plane_daily_facts_store_time
    ON {{control_schema}}.store_daily_facts(tenant_id, store_id, period_start);

CREATE TABLE IF NOT EXISTS {{control_schema}}.forecast_inputs (
    source_snapshot_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    forecast_date TIMESTAMPTZ NOT NULL,
    predicted_value NUMERIC(16, 2) NOT NULL,
    output_class TEXT NOT NULL DEFAULT 'legacy_external_model_output'
        CHECK (output_class = 'legacy_external_model_output'),
    source_model_version TEXT,
    source_model_run_id TEXT,
    source_freshness_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_model_version IS NULL AND source_model_run_id IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_data_plane_forecast_inputs_store_date
    ON {{control_schema}}.forecast_inputs(tenant_id, store_id, forecast_date);

CREATE TABLE IF NOT EXISTS {{control_schema}}.domain_inputs (
    source_snapshot_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    input_kind TEXT NOT NULL,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_id UUID REFERENCES core.stores(store_id),
    effective_at TIMESTAMPTZ NOT NULL,
    input_payload JSONB NOT NULL,
    source_freshness_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (jsonb_typeof(input_payload) = 'object')
);
CREATE INDEX IF NOT EXISTS ix_data_plane_domain_inputs
    ON {{control_schema}}.domain_inputs(input_kind, tenant_id, effective_at);

CREATE TABLE IF NOT EXISTS {{control_schema}}.learning_import_lineage (
    source_snapshot_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    run_date TIMESTAMPTZ NOT NULL,
    source_account_ref_hash TEXT NOT NULL CHECK (length(source_account_ref_hash) = 64),
    feature_snapshot JSONB NOT NULL,
    segment_id TEXT NOT NULL,
    segment_labels JSONB NOT NULL,
    output_class TEXT NOT NULL DEFAULT 'legacy_external_model_output'
        CHECK (output_class = 'legacy_external_model_output'),
    source_model_version TEXT,
    source_model_run_id TEXT,
    source_freshness_at TIMESTAMPTZ,
    observed_at TIMESTAMPTZ NOT NULL,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (source_model_version IS NULL AND source_model_run_id IS NULL),
    CHECK (jsonb_typeof(feature_snapshot) = 'object'),
    CHECK (jsonb_typeof(segment_labels) = 'array')
);
CREATE INDEX IF NOT EXISTS ix_data_plane_learning_import
    ON {{control_schema}}.learning_import_lineage(tenant_id, run_date, segment_id);

CREATE TABLE IF NOT EXISTS {{control_schema}}.transaction_authority (
    transaction_id UUID PRIMARY KEY REFERENCES core.transactions(transaction_id),
    source_kind TEXT NOT NULL CHECK (source_kind IN ('orders', 'transaction', 'trade')),
    authority_rank SMALLINT NOT NULL CHECK (authority_rank BETWEEN 1 AND 3),
    source_snapshot_id UUID NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS {{control_schema}}.place_geography (
    source_snapshot_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    raw_address TEXT,
    latitude NUMERIC(10, 7),
    longitude NUMERIC(10, 7),
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE TABLE IF NOT EXISTS {{control_schema}}.machine_status_event_evidence (
    source_snapshot_id UUID PRIMARY KEY,
    source_id TEXT NOT NULL,
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    machine_id UUID NOT NULL REFERENCES core.machines(machine_id),
    status_event_id UUID NOT NULL UNIQUE
        REFERENCES core.machine_status_events(status_event_id),
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    redaction_profile TEXT NOT NULL DEFAULT 'device_log_minimized_v1'
        CHECK (redaction_profile = 'device_log_minimized_v1'),
    observation_time TIMESTAMPTZ NOT NULL,
    source_freshness_at TIMESTAMPTZ,
    run_id UUID NOT NULL REFERENCES {{control_schema}}.ingestion_runs(run_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_data_plane_machine_status_event_evidence
    ON {{control_schema}}.machine_status_event_evidence(
        tenant_id, machine_id, observation_time DESC
    );
