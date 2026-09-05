-- ODP-INT-MANUAL-CORRECTION-AUDIT-001: Manual corrections, audit trail and rollback schema
-- Creates odp_runtime.durable_manual_corrections and ensures core.address_locations carries tenant_id and revision.

CREATE SCHEMA IF NOT EXISTS odp_runtime;

CREATE TABLE IF NOT EXISTS odp_runtime.durable_manual_corrections (
    correction_id      TEXT PRIMARY KEY,
    entity_type        TEXT NOT NULL,
    entity_id          TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    field_name         TEXT NOT NULL,
    old_value_json     TEXT NOT NULL,
    new_value_json     TEXT NOT NULL,
    reason             TEXT NOT NULL CHECK (char_length(btrim(reason)) >= 5),
    actor_id           TEXT NOT NULL,
    occurred_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_revision    INTEGER NOT NULL DEFAULT 1,
    applied_revision   INTEGER NOT NULL DEFAULT 2,
    status             TEXT NOT NULL DEFAULT 'applied' CHECK (status IN ('applied', 'rolled_back')),
    correlation_id     TEXT,
    decision_card_json TEXT,
    audit_event_id     TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runtime_manual_corrections_entity
    ON odp_runtime.durable_manual_corrections(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_runtime_manual_corrections_tenant
    ON odp_runtime.durable_manual_corrections(tenant_id);

CREATE INDEX IF NOT EXISTS idx_runtime_manual_corrections_correlation
    ON odp_runtime.durable_manual_corrections(correlation_id);

DO $$
BEGIN
    IF to_regclass('core.address_locations') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'address_locations' AND column_name = 'tenant_id'
        ) THEN
            ALTER TABLE core.address_locations ADD COLUMN tenant_id UUID REFERENCES core.tenants(tenant_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'address_locations' AND column_name = 'revision'
        ) THEN
            ALTER TABLE core.address_locations ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
        END IF;
    END IF;
END $$;
