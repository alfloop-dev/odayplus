-- ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001: expand-only.
-- Dedicated hash-only capability; no invitation/login-budget reuse.
-- No cascade deletion and no downgrade reset: consumed/unknown stays no-retry.
BEGIN;
CREATE TABLE IF NOT EXISTS identity.dev_smoke_recovery_capabilities (
    authorization_id TEXT PRIMARY KEY CHECK (
        authorization_id = 'HUMAN-ODP-DEV-SMOKE-RECOVERY-20261011-001'
    ),
    capability_id UUID NOT NULL UNIQUE,
    execution_id UUID NOT NULL UNIQUE,
    plan_digest TEXT NOT NULL CHECK (plan_digest ~ '^sha256:[a-f0-9]{64}$'),
    root_event_id UUID NOT NULL,
    issue_event_id UUID NOT NULL UNIQUE,
    tenant_id UUID NOT NULL CHECK (tenant_id = 'e34f2117-de4b-478c-82fd-13c4ef428d42'),
    target_account_id UUID NOT NULL REFERENCES identity.accounts(account_id)
        CHECK (target_account_id = '13faae19-21c6-4663-8e89-b93ea7f1107d'),
    issuer_account_id UUID NOT NULL REFERENCES identity.accounts(account_id)
        CHECK (issuer_account_id = '17e9cb99-db46-4a07-8e61-6bf9b22cf5d2'),
    issuer_session_id UUID NOT NULL REFERENCES identity.sessions(session_id),
    token_hash TEXT NOT NULL CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL CHECK (
        expires_at > issued_at AND expires_at <= issued_at + INTERVAL '600 seconds'
    ),
    original_credential_updated_at TIMESTAMPTZ NOT NULL,
    target_credential_updated_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    rotation_event_id UUID UNIQUE,
    CHECK ((consumed_at IS NULL) = (rotation_event_id IS NULL))
);
CREATE TABLE IF NOT EXISTS identity.dev_smoke_recovery_budget (
    authorization_id TEXT PRIMARY KEY CHECK (
        authorization_id = 'HUMAN-ODP-DEV-SMOKE-RECOVERY-20261011-001'
    ),
    attempt_count INTEGER NOT NULL CHECK (attempt_count BETWEEN 0 AND 5)
);
COMMIT;
