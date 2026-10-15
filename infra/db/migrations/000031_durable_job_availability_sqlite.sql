-- Delivery scheduling is a nullable metadata column, not a business payload.
-- Existing generic jobs remain immediately eligible. The engine ignores the
-- duplicate-column error when replaying this bootstrap migration.
ALTER TABLE durable_jobs ADD COLUMN available_at TEXT;
CREATE INDEX IF NOT EXISTS idx_durable_jobs_available
    ON durable_jobs(status, available_at, created_at);
