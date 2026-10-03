-- Migration 000027: Add record lifecycle and timestamp columns to core.machine_status_events.
--
-- Task: ODP-CDC-MACHINE-EVENT-LIFECYCLE-001
-- Requirement: ODP-FR-INT-001 / H07 Decision 4 (Soft delete + audit tombstone in parallel)
--
-- Purpose:
--   Add record lifecycle column `record_status` ('active'/'voided') and audit timestamp columns
--   `created_at`, `updated_at` to `core.machine_status_events`.
--   The existing `status_type` column represents device physical state (online/offline/error/...),
--   NOT the database row lifecycle state.
--
-- Rollback strategy:
--   ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS record_status;
--   ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS created_at;
--   ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS updated_at;
--   DROP INDEX IF EXISTS core.idx_machine_status_events_record_status;

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'core' AND table_name = 'machine_status_events'
    ) THEN
        ALTER TABLE core.machine_status_events
            ADD COLUMN IF NOT EXISTS record_status VARCHAR(50) NOT NULL DEFAULT 'active',
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP;

        CREATE INDEX IF NOT EXISTS idx_machine_status_events_record_status
            ON core.machine_status_events (store_id, record_status);
    END IF;
END $$;

COMMIT;
