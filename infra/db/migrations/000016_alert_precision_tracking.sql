-- ODP-FR-FCT-006: Forecast alert precision and lead time tracking (ODP-FORECAST-ALERT-PRECISION-001)
--
-- Adds deterioration_confirmed_at and disposition columns to operations.alerts.
-- Tracks alert precision and advance warning lead time.

DO $$
BEGIN
    IF to_regclass('operations.alerts') IS NOT NULL THEN
        ALTER TABLE operations.alerts
            ADD COLUMN IF NOT EXISTS deterioration_confirmed_at TIMESTAMP WITH TIME ZONE,
            ADD COLUMN IF NOT EXISTS disposition VARCHAR(100);

        CREATE INDEX IF NOT EXISTS idx_alerts_disposition
            ON operations.alerts (disposition);
    END IF;
END $$;
