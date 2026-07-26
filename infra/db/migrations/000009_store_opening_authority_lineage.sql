-- Migration: 000009_store_opening_authority_lineage.sql
-- Store Opening Authority & Lineage Schema (ODP-STORE-OPENING-001)

CREATE SCHEMA IF NOT EXISTS data_plane;
CREATE SCHEMA IF NOT EXISTS intake;

-- Index on core.stores for tenant-scoped opened_on queries
CREATE INDEX IF NOT EXISTS idx_stores_tenant_opened_on
    ON core.stores(tenant_id, opened_on);

-- Store opening authority lineage ledger
CREATE TABLE IF NOT EXISTS intake.store_opening_authority_lineage (
    lineage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_snapshot_id UUID NOT NULL,
    source_id VARCHAR(100) NOT NULL,
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
