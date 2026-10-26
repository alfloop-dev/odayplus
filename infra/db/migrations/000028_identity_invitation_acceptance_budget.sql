-- ODP-DEV-SMOKE-ACCOUNT-PROVISIONING-001: expand-only invitation abuse budget.
-- This is NOT login throttling. The Web loginThrottle remains the sole writer
-- of identity.login_attempts; invitation attempts never change login state.
-- At most one global row plus one row per existing invitation is written.
-- Rollback preserves reservations and disables the invitation route, never
-- deletes counters to permit immediate replay after a failed acceptance.
BEGIN;

CREATE TABLE IF NOT EXISTS identity.invitation_acceptance_budget (
    attempt_key TEXT PRIMARY KEY CHECK (
        attempt_key = 'invitation-accept:global'
        OR attempt_key ~ '^invitation-accept:[a-f0-9]{64}$'
    ),
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0)
);

COMMIT;
