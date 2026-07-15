-- Durable Audit Evidence Store and Export Retention (ODP-PV-011)
-- Citing ODP-SD-09 §11 (audit retention), the subsidy evidence matrix, and
-- PRODUCT_GRADE_E2E_EXECUTION_WAVE.
--
-- Companion to 000002_durable_e2e_persistence.sql: ODP-PV-009 made audit
-- *events* restart-survivable; this migration makes the *evidence bundles* an
-- export produces durable too. The bundle is stored columnar on its queryable
-- dimensions (program, checksum, correlation, privacy scope, retention) plus a
-- JSON blob preserving the full bundle for byte-for-byte reproduction after a
-- restart. It is engine-neutral (SQLite-compatible) and executed verbatim by
-- shared/infrastructure/persistence/engine.py at bootstrap, so this artifact
-- and the runtime schema cannot drift.

-- ---------------------------------------------------------
-- durable_evidence_bundles
-- One row per exported audit-evidence bundle.
--   bundle_checksum     content hash of the exported bundle
--   requested_by        actor who produced the export
--   purpose             reason the export was produced
--   data_classification / sensitive / export_scope  privacy scope
--   retention_class / retain_until / legal_hold      retention controls
--   bundle_json         full bundle payload (reproducible export)
-- ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS durable_evidence_bundles (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    export_id           TEXT NOT NULL UNIQUE,
    program_id          TEXT NOT NULL,
    purpose             TEXT NOT NULL,
    requested_by        TEXT NOT NULL,
    audit_event_id      TEXT NOT NULL,
    bundle_checksum     TEXT NOT NULL,
    data_classification TEXT NOT NULL,
    sensitive           INTEGER NOT NULL DEFAULT 0,
    export_scope        TEXT NOT NULL,
    retention_class     TEXT NOT NULL,
    retain_until        TEXT NOT NULL,
    legal_hold          INTEGER NOT NULL DEFAULT 0,
    generated_at        TEXT NOT NULL,
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    correlation_id      TEXT NOT NULL,
    bundle_json         TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    sequence            INTEGER,
    previous_hash       TEXT,
    record_hash         TEXT,
    signature_key_id    TEXT,
    signature_version   TEXT,
    signature_alg       TEXT,
    worm_sink_id        TEXT,
    governance_log_json TEXT NOT NULL DEFAULT '[]',
    governance_hash     TEXT,
    purged_at           TEXT,
    tombstone_hash      TEXT
);
CREATE INDEX IF NOT EXISTS idx_durable_evidence_program
    ON durable_evidence_bundles(program_id);
CREATE INDEX IF NOT EXISTS idx_durable_evidence_correlation
    ON durable_evidence_bundles(correlation_id);
CREATE INDEX IF NOT EXISTS idx_durable_evidence_checksum
    ON durable_evidence_bundles(bundle_checksum);
-- Retention sweeps scan by expiry; index retain_until so purge stays cheap.
CREATE INDEX IF NOT EXISTS idx_durable_evidence_retention
    ON durable_evidence_bundles(retain_until);
CREATE UNIQUE INDEX IF NOT EXISTS idx_durable_evidence_sequence
    ON durable_evidence_bundles(sequence)
    WHERE sequence IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_durable_evidence_record_hash
    ON durable_evidence_bundles(record_hash);
CREATE INDEX IF NOT EXISTS idx_durable_evidence_purged_at
    ON durable_evidence_bundles(purged_at);
