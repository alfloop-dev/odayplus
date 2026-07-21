-- Durable Outbox Schema (ODP-INTAKE-EVENTS-001)
CREATE TABLE IF NOT EXISTS durable_outbox_events (
    outbox_event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    partition_key TEXT NOT NULL,
    payload TEXT NOT NULL, -- JSON string
    sensitive_fields TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    occurred_at TEXT NOT NULL,
    published_at TEXT,
    publish_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT, -- JSON string
    retention_until TEXT NOT NULL,
    producer TEXT NOT NULL DEFAULT 'unknown',
    actor_ref TEXT,
    policy_version TEXT,
    schema_ref TEXT NOT NULL DEFAULT 'unassigned',
    published_message_id TEXT,
    available_at TEXT NOT NULL,
    locked_by TEXT,
    lock_expires_at TEXT,
    UNIQUE (tenant_id, event_id),
    UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version, event_type)
);

CREATE INDEX IF NOT EXISTS idx_durable_outbox_unpublished 
    ON durable_outbox_events (occurred_at) WHERE published_at IS NULL;
