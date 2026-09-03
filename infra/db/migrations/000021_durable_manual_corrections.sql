-- Durable Manual Corrections & Audit Trail (ODP-INT-MANUAL-CORRECTION-AUDIT-001 / INT-006)
-- Canonical write path for manual overrides with actor provenance, reason,
-- old/new value snapshots, revision lineage, and rollback support.

CREATE TABLE IF NOT EXISTS durable_manual_corrections (
    correction_id      TEXT PRIMARY KEY,
    entity_type        TEXT NOT NULL,
    entity_id          TEXT NOT NULL,
    tenant_id          TEXT NOT NULL,
    field_name         TEXT NOT NULL,
    old_value_json     TEXT NOT NULL,
    new_value_json     TEXT NOT NULL,
    reason             TEXT NOT NULL CHECK (length(trim(reason)) >= 5),
    actor_id           TEXT NOT NULL,
    occurred_at        TEXT NOT NULL,
    source_revision    INTEGER NOT NULL DEFAULT 1,
    applied_revision   INTEGER NOT NULL DEFAULT 2,
    status             TEXT NOT NULL DEFAULT 'applied' CHECK (status IN ('applied', 'rolled_back')),
    correlation_id     TEXT,
    decision_card_json TEXT,
    audit_event_id     TEXT,
    created_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manual_corrections_entity
    ON durable_manual_corrections(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_manual_corrections_tenant
    ON durable_manual_corrections(tenant_id);

CREATE INDEX IF NOT EXISTS idx_manual_corrections_correlation
    ON durable_manual_corrections(correlation_id);
