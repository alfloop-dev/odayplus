-- Expand-only once-bound legacy OIDC migration identity. Never backfill guesses.
-- No session/account FK: this revocation identity MUST survive session cleanup or
-- account deletion, otherwise a still-valid legacy cookie could create a new SID.
CREATE TABLE IF NOT EXISTS identity.legacy_oidc_session_migrations (
    migration_key TEXT PRIMARY KEY CHECK (migration_key ~ '^[a-f0-9]{64}$'),
    session_id UUID NOT NULL UNIQUE,
    account_id UUID NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
-- No deletes/renewal/reset. Retention lasts at least through the original cookie
-- expiry; this increment adds no purge path or authority to restart migration.
