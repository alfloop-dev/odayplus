-- Migration 000026: Make six canonical measurement columns nullable.
--
-- Columns changed:
--   pois.confidence               NOT NULL DEFAULT 1.00  →  NULL  (DROP DEFAULT)
--   competitor_stores.confidence   NOT NULL DEFAULT 1.00  →  NULL  (DROP DEFAULT)
--   listings.confidence            NOT NULL DEFAULT 1.00  →  NULL  (DROP DEFAULT)
--   predictions.confidence         NOT NULL DEFAULT 1.00  →  NULL  (DROP DEFAULT)
--   data_snapshots.quality_score   NOT NULL DEFAULT 1.00  →  NULL  (DROP DEFAULT)
--   expansion.heatzone_scores.confidence  DEFAULT 1.00   →  DROP DEFAULT
--
-- Existing rows that carry 1.00 are NOT modified to NULL: they may
-- represent genuinely measured perfect scores or legacy substituted values.
-- The migration only changes the column constraint so new writes can express
-- absence as NULL.  Legacy disambiguation is handled by schema_version /
-- legacy_unknown markers at the application layer, not by a bulk UPDATE.
--
-- Rollback: re-add NOT NULL DEFAULT 1.00 and UPDATE ... SET ... = 1.00
-- WHERE ... IS NULL.  This is a lossy rollback (collapses absence and
-- measured-perfect back together).

BEGIN;

-- 1. pois.confidence
ALTER TABLE pois ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE pois ALTER COLUMN confidence DROP DEFAULT;

-- 2. competitor_stores.confidence
ALTER TABLE competitor_stores ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE competitor_stores ALTER COLUMN confidence DROP DEFAULT;

-- 3. listings.confidence
ALTER TABLE listings ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE listings ALTER COLUMN confidence DROP DEFAULT;

-- 4. predictions.confidence
ALTER TABLE predictions ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE predictions ALTER COLUMN confidence DROP DEFAULT;

-- 5. data_snapshots.quality_score
ALTER TABLE data_snapshots ALTER COLUMN quality_score DROP NOT NULL;
ALTER TABLE data_snapshots ALTER COLUMN quality_score DROP DEFAULT;

-- 6. expansion.heatzone_scores.confidence (already nullable, just drop default)
ALTER TABLE expansion.heatzone_scores ALTER COLUMN confidence DROP DEFAULT;

COMMIT;
