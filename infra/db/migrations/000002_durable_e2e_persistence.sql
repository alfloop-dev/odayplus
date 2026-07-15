-- Durable E2E Persistence Schema for ODay Plus (ODP-PV-009)
-- Citing ODP-SD-05 (Database & Storage Design) and PRODUCT_GRADE_E2E_EXECUTION_WAVE.
--
-- The canonical production target is PostgreSQL + PostGIS (see
-- 000001_baseline_canonical_schema.sql). This companion schema is the
-- restart-survivable storage used to take the product API off in-memory
-- repositories during Product-Grade E2E validation. It is intentionally
-- engine-neutral (SQLite-compatible DDL, no extensions, no schemas) so the
-- E2E lane can persist real data paths without standing up a Postgres
-- instance. shared/infrastructure/persistence/engine.py executes this file
-- verbatim when it bootstraps a durable store, so this DDL and the runtime
-- engine never drift.

-- ---------------------------------------------------------
-- durable_audit_events
-- Columnar audit trail. correlation_id is indexed so decision/audit
-- metadata can be resolved per request after a process restart.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS durable_audit_events (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id       TEXT NOT NULL UNIQUE,
    event_type     TEXT NOT NULL,
    actor          TEXT NOT NULL,
    action         TEXT NOT NULL,
    resource       TEXT NOT NULL,
    outcome        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    job_id         TEXT,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    occurred_at    TEXT NOT NULL,
    sequence       INTEGER,
    previous_hash  TEXT,
    event_hash     TEXT,
    signature_key_id TEXT,
    signature_version TEXT,
    signature_alg  TEXT,
    worm_sink_id   TEXT
);
CREATE INDEX IF NOT EXISTS idx_durable_audit_correlation
    ON durable_audit_events(correlation_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_durable_audit_sequence
    ON durable_audit_events(sequence)
    WHERE sequence IS NOT NULL;

-- ---------------------------------------------------------
-- durable_jobs
-- Job queue records with an idempotency index so retried submissions
-- replay the original job after a restart instead of duplicating work.
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS durable_jobs (
    job_id          TEXT PRIMARY KEY,
    job_type        TEXT NOT NULL,
    status          TEXT NOT NULL,
    correlation_id  TEXT NOT NULL,
    idempotency_key TEXT,
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_durable_jobs_idempotency
    ON durable_jobs(idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- ---------------------------------------------------------
-- durable_documents
-- Generic aggregate store for the module repositories. Each row is one
-- domain aggregate serialized as an opaque blob (full-fidelity round-trip
-- of the frozen dataclasses the in-memory repositories hold today).
--   collection  logical repository partition (e.g. "avm.cases")
--   doc_id      unique aggregate id within the collection
--   group_key   secondary grouping (e.g. case_id / store_id) for history
--   seq         version / ordering within a group_key
--   ordinal     monotonic insertion order for stable list iteration
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS durable_documents (
    collection     TEXT NOT NULL,
    doc_id         TEXT NOT NULL,
    group_key      TEXT,
    seq            INTEGER,
    ordinal        INTEGER NOT NULL,
    correlation_id TEXT,
    data           BLOB NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (collection, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_durable_documents_group
    ON durable_documents(collection, group_key, seq);
CREATE INDEX IF NOT EXISTS idx_durable_documents_ordinal
    ON durable_documents(collection, ordinal);

-- Monotonic counter for durable_documents.ordinal (SQLite has no portable
-- sequence object; this keeps insertion order stable across restarts).
CREATE TABLE IF NOT EXISTS durable_sequences (
    name    TEXT PRIMARY KEY,
    counter INTEGER NOT NULL DEFAULT 0
);
