-- Idempotent migrations for ODP-PGAP-RELIABILITY-001
-- Add columns attempts, leased_until, max_retries, and newer fencing columns if not present to durable_jobs table.

ALTER TABLE durable_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE durable_jobs ADD COLUMN leased_until TEXT;
ALTER TABLE durable_jobs ADD COLUMN max_retries INTEGER NOT NULL DEFAULT 3;
ALTER TABLE durable_jobs ADD COLUMN fence_token INTEGER NOT NULL DEFAULT 0;
ALTER TABLE durable_jobs ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE durable_jobs ADD COLUMN locked_by TEXT;
ALTER TABLE durable_jobs ADD COLUMN heartbeat_at TEXT;
ALTER TABLE durable_jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE durable_jobs ADD COLUMN error_message TEXT;
