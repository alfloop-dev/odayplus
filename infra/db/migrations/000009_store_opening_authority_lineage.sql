-- Migration: 000009_store_opening_authority_lineage.sql
-- Store Opening Authority & Lineage Schema (ODP-STORE-OPENING-001)

CREATE SCHEMA IF NOT EXISTS data_plane;
CREATE SCHEMA IF NOT EXISTS intake;

-- Index on core.stores for tenant-scoped opened_on queries
CREATE INDEX IF NOT EXISTS idx_stores_tenant_opened_on
    ON core.stores(tenant_id, opened_on);

-- Data plane control schema dependencies
CREATE TABLE IF NOT EXISTS data_plane.ingestion_runs (
    run_id UUID PRIMARY KEY,
    source_database TEXT NOT NULL DEFAULT 'fongniao_prod',
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
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ,
    UNIQUE (source_kind, partition_key, run_id)
);

CREATE TABLE IF NOT EXISTS data_plane.canonical_lineage (
    source_snapshot_id UUID NOT NULL,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES data_plane.ingestion_runs(run_id),
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    canonical_table TEXT NOT NULL,
    canonical_id UUID NOT NULL,
    projected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_snapshot_id, canonical_table, canonical_id)
);

-- Store opening authority lineage ledger
CREATE TABLE IF NOT EXISTS intake.store_opening_authority_lineage (
    lineage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_snapshot_id UUID NOT NULL REFERENCES intake.source_snapshots(source_snapshot_id),
    source_id TEXT NOT NULL REFERENCES intake.source_registry(source_id),
    tenant_id UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_id UUID NOT NULL REFERENCES core.stores(store_id),
    opened_on DATE NOT NULL,
    authority_type VARCHAR(100) NOT NULL,
    provenance_note TEXT,
    content_sha256 VARCHAR(64) NOT NULL,
    projected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_store_opening_snapshot UNIQUE (source_snapshot_id, store_id)
);

CREATE INDEX IF NOT EXISTS idx_store_opening_lineage_tenant
    ON intake.store_opening_authority_lineage(tenant_id, store_id);

-- Register schema migration if odp_runtime exists
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'odp_runtime' AND table_name = 'schema_migrations') THEN
        INSERT INTO odp_runtime.schema_migrations (migration_id, applied_at)
        VALUES ('000009_store_opening_authority_lineage', CURRENT_TIMESTAMP)
        ON CONFLICT (migration_id) DO NOTHING;
    END IF;
END $$;
