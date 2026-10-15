-- Expand-only, transactional audit publication intent; no historical delivery claims.
CREATE TABLE IF NOT EXISTS odp_runtime.durable_audit_worm_outbox (
    event_id TEXT PRIMARY KEY REFERENCES odp_runtime.durable_audit_events(event_id),
    sink_id TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'attempted', 'delivered', 'unknown'))
);
CREATE INDEX IF NOT EXISTS idx_audit_worm_pending ON odp_runtime.durable_audit_worm_outbox(sink_id, state);
