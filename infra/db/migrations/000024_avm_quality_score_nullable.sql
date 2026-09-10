-- ODP-AVM-QUALITY-NULLABLE-001
-- Make snapshot quality absence explicit without rewriting historical values.
-- Rows created before this migration retain their original quality_score (including
-- 1.00) and are marked legacy_unknown because the old schema did not distinguish
-- an observed perfect score from an omitted score.

ALTER TABLE IF EXISTS audit.data_snapshots
    ADD COLUMN IF NOT EXISTS quality_score_status VARCHAR(32) NOT NULL DEFAULT 'legacy_unknown';

ALTER TABLE IF EXISTS audit.data_snapshots
    ALTER COLUMN quality_score DROP NOT NULL;

ALTER TABLE IF EXISTS audit.data_snapshots
    ALTER COLUMN quality_score DROP DEFAULT;
