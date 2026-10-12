-- Expand-only, SQLite durable audit publication intent, never a business outbox.
CREATE TABLE IF NOT EXISTS durable_audit_worm_outbox (
    event_id TEXT PRIMARY KEY REFERENCES durable_audit_events(event_id),
    sink_id TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'attempted', 'delivered', 'unknown'))
);
CREATE INDEX IF NOT EXISTS idx_audit_worm_pending ON durable_audit_worm_outbox(sink_id, state);
