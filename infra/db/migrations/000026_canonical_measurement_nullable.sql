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

-- 1. geo.pois
ALTER TABLE IF EXISTS geo.pois ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE IF EXISTS geo.pois ALTER COLUMN confidence DROP DEFAULT;
ALTER TABLE IF EXISTS geo.pois ADD COLUMN IF NOT EXISTS measurement_schema_version VARCHAR(50) NOT NULL DEFAULT 'v1';

-- 2. geo.competitor_stores
ALTER TABLE IF EXISTS geo.competitor_stores ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE IF EXISTS geo.competitor_stores ALTER COLUMN confidence DROP DEFAULT;
ALTER TABLE IF EXISTS geo.competitor_stores ADD COLUMN IF NOT EXISTS measurement_schema_version VARCHAR(50) NOT NULL DEFAULT 'v1';
ALTER TABLE IF EXISTS geo.competitor_stores ADD COLUMN IF NOT EXISTS snapshot_id VARCHAR(100);
ALTER TABLE IF EXISTS geo.competitor_stores ADD COLUMN IF NOT EXISTS source_competitor_id VARCHAR(255);

-- 3. expansion.listings
ALTER TABLE IF EXISTS expansion.listings ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE IF EXISTS expansion.listings ALTER COLUMN confidence DROP DEFAULT;
ALTER TABLE IF EXISTS expansion.listings ADD COLUMN IF NOT EXISTS measurement_schema_version VARCHAR(50) NOT NULL DEFAULT 'v1';

-- 4. learning.predictions
ALTER TABLE IF EXISTS learning.predictions ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE IF EXISTS learning.predictions ALTER COLUMN confidence DROP DEFAULT;

-- 5. learning.prediction_runs (Strategy B: Run-level measurement schema version)
ALTER TABLE IF EXISTS learning.prediction_runs ADD COLUMN IF NOT EXISTS measurement_schema_version VARCHAR(50) NOT NULL DEFAULT 'v1';

-- 6. audit.data_snapshots
ALTER TABLE IF EXISTS audit.data_snapshots ALTER COLUMN quality_score DROP NOT NULL;
ALTER TABLE IF EXISTS audit.data_snapshots ALTER COLUMN quality_score DROP DEFAULT;

-- 7. expansion.heatzone_scores (Strategy C: Preventive schema version)
ALTER TABLE IF EXISTS expansion.heatzone_scores ALTER COLUMN confidence DROP DEFAULT;
ALTER TABLE IF EXISTS expansion.heatzone_scores ADD COLUMN IF NOT EXISTS measurement_schema_version VARCHAR(50) NOT NULL DEFAULT 'v1';

COMMIT;


