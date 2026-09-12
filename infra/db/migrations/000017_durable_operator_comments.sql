-- Durable Operator comments (ODP-OPS002-DECISION-COMMENTS-001).
-- Comments are tenant-scoped audit sidecars; they never mutate a decision or
-- approval receipt.  The same DDL is used by SQLite durable E2E and mirrors
-- odp_runtime.operator_comments in the PostgreSQL runtime migration.

CREATE TABLE IF NOT EXISTS operator_comments (
    tenant_id       TEXT NOT NULL,
    comment_id      TEXT NOT NULL,
    target_type     TEXT NOT NULL CHECK (target_type IN ('task', 'decision', 'approval')),
    target_id       TEXT NOT NULL,
    content         TEXT NOT NULL CHECK (length(trim(content)) > 0 AND length(content) <= 2000),
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_by      TEXT,
    updated_at      TEXT,
    edited          INTEGER NOT NULL DEFAULT 0 CHECK (edited IN (0, 1)),
    edit_count      INTEGER NOT NULL DEFAULT 0 CHECK (edit_count >= 0),
    idempotency_key TEXT,
    correlation_id  TEXT,
    history_json    TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (tenant_id, comment_id)
);

CREATE INDEX IF NOT EXISTS idx_operator_comments_target
    ON operator_comments(tenant_id, target_type, target_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_operator_comments_idempotency
    ON operator_comments(tenant_id, created_by, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
