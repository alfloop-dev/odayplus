-- Idempotent migrations for ODP-PGAP-RELIABILITY-001
-- Add columns attempts, leased_until, max_retries to durable_jobs table.

ALTER TABLE durable_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE durable_jobs ADD COLUMN leased_until TEXT;
ALTER TABLE durable_jobs ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3;
