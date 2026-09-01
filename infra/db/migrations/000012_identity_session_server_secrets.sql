-- ODP-WEB-PASSWORD-FIRST-LOGIN-001: server-side session material
--
-- The web cookie is only an opaque reference.  The API bearer and display
-- subject therefore need to remain on the server-side identity.sessions row.
-- This is additive and idempotent so the P1 identity migration can be rolled
-- back by disabling the new web path without dropping any data.

ALTER TABLE identity.sessions
    ADD COLUMN IF NOT EXISTS access_token TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS session_subject TEXT,
    ADD COLUMN IF NOT EXISTS tenant_id UUID;

CREATE INDEX IF NOT EXISTS idx_sessions_accessible
    ON identity.sessions (session_id)
    WHERE revoked_at IS NULL AND access_token <> '';
