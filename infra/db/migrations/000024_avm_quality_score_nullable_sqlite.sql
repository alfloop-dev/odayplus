-- ODP-AVM-QUALITY-NULLABLE-001
-- SQLite has no ALTER COLUMN support, so rebuild the table while preserving every
-- historical quality_score. The status marks pre-migration rows as legacy_unknown;
-- their 1.00 values are intentionally not converted to NULL.

ALTER TABLE data_snapshots
    ADD COLUMN quality_score_status TEXT NOT NULL DEFAULT 'legacy_unknown';

PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS data_snapshots_nullable;

CREATE TABLE data_snapshots_nullable (
    snapshot_id TEXT PRIMARY KEY,
    snapshot_type TEXT NOT NULL DEFAULT 'raw',
    source_id TEXT NOT NULL,
    snapshot_time TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    storage_uri TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    quality_score REAL,
    created_by_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    quality_score_status TEXT NOT NULL DEFAULT 'legacy_unknown'
);

INSERT INTO data_snapshots_nullable (
    snapshot_id,
    snapshot_type,
    source_id,
    snapshot_time,
    storage_uri,
    schema_version,
    row_count,
    quality_score,
    created_by_run_id,
    created_at,
    updated_at,
    quality_score_status
)
SELECT
    snapshot_id,
    snapshot_type,
    source_id,
    snapshot_time,
    storage_uri,
    schema_version,
    row_count,
    quality_score,
    created_by_run_id,
    created_at,
    updated_at,
    quality_score_status
FROM data_snapshots;

DROP TABLE data_snapshots;

ALTER TABLE data_snapshots_nullable RENAME TO data_snapshots;

CREATE INDEX IF NOT EXISTS idx_data_snapshots_source_time
    ON data_snapshots(source_id, snapshot_time);

PRAGMA foreign_keys = ON;
