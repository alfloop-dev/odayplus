-- ============================================================================
-- ODP-WEB-LOCAL-IDENTITY-CORE-001: identity schema expand migration
-- Phase: P1 (Wave Auth 1 — Identity Core)
-- Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2
--
-- 本 migration 為純新增（expand-only），不修改任何既有 schema。
-- 回退方式：保留空表、關閉新路徑（contract §9 P1）。
-- ============================================================================

-- 建立 identity schema（冪等）
CREATE SCHEMA IF NOT EXISTS identity;

-- 啟用 uuid-ossp 擴充（若尚未啟用）
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 1. identity.accounts — 帳號主表
-- Contract §2.2: status ∈ {invited, active, disabled, locked}
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.accounts (
    account_id      UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID            NOT NULL,
    username        VARCHAR(255)    NOT NULL,
    email           VARCHAR(320)    NOT NULL,
    display_name    VARCHAR(255)    NOT NULL DEFAULT '',
    status          VARCHAR(20)     NOT NULL DEFAULT 'invited'
                    CHECK (status IN ('invited', 'active', 'disabled', 'locked')),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    created_by      TEXT            NOT NULL,
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    disabled_at     TIMESTAMPTZ,
    disabled_reason TEXT
);

-- 唯一約束：同一 tenant 下 username / email 不重複（case-insensitive）
CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_tenant_username
    ON identity.accounts (tenant_id, lower(username));

CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_tenant_email
    ON identity.accounts (tenant_id, lower(email));

-- 查詢最佳化索引
CREATE INDEX IF NOT EXISTS idx_accounts_status
    ON identity.accounts (status);

CREATE INDEX IF NOT EXISTS idx_accounts_tenant_id
    ON identity.accounts (tenant_id);

-- ============================================================================
-- 2. identity.password_credentials — 密碼憑證（Argon2id PHC）
-- Contract §2.2: algorithm 固定 argon2id；不得儲存明文、可逆加密或密碼提示
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.password_credentials (
    account_id      UUID            PRIMARY KEY
                    REFERENCES identity.accounts(account_id) ON DELETE CASCADE,
    algorithm       VARCHAR(20)     NOT NULL DEFAULT 'argon2id'
                    CHECK (algorithm = 'argon2id'),
    phc_hash        TEXT            NOT NULL,
    params          JSONB           NOT NULL DEFAULT '{}'::jsonb,
    must_change     BOOLEAN         NOT NULL DEFAULT false,
    last_rotated_at TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

-- ============================================================================
-- 3. identity.account_roles — 角色授予
-- Contract §2.2: role 必須屬於 shared.auth.Role 列舉
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.account_roles (
    account_id  UUID            NOT NULL
                REFERENCES identity.accounts(account_id) ON DELETE CASCADE,
    role        VARCHAR(50)     NOT NULL,
    granted_at  TIMESTAMPTZ     NOT NULL DEFAULT now(),
    granted_by  TEXT            NOT NULL,
    PRIMARY KEY (account_id, role)
);

-- ============================================================================
-- 4. identity.account_scopes — 資料範圍
-- Contract §2.2: 空集合表示「該軸不額外限制」
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.account_scopes (
    account_id          UUID            PRIMARY KEY
                        REFERENCES identity.accounts(account_id) ON DELETE CASCADE,
    brand_ids           JSONB           NOT NULL DEFAULT '[]'::jsonb,
    region_ids          JSONB           NOT NULL DEFAULT '[]'::jsonb,
    store_ids           JSONB           NOT NULL DEFAULT '[]'::jsonb,
    assigned_area_ids   JSONB           NOT NULL DEFAULT '[]'::jsonb,
    heat_zone_ids       JSONB           NOT NULL DEFAULT '[]'::jsonb,
    modules             JSONB           NOT NULL DEFAULT '[]'::jsonb,
    clearance           VARCHAR(30)     NOT NULL DEFAULT 'CONFIDENTIAL'
);

-- ============================================================================
-- 5. identity.sessions — 持久 session 與撤銷
-- Contract §2.2, §5: provider ∈ {local_password, oidc}
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.sessions (
    session_id          UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id          UUID            NOT NULL
                        REFERENCES identity.accounts(account_id) ON DELETE CASCADE,
    provider            VARCHAR(20)     NOT NULL
                        CHECK (provider IN ('local_password', 'oidc')),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ     NOT NULL DEFAULT now(),
    idle_expires_at     TIMESTAMPTZ     NOT NULL,
    absolute_expires_at TIMESTAMPTZ     NOT NULL,
    revoked_at          TIMESTAMPTZ,
    revoked_reason      TEXT,
    rotated_from        UUID            REFERENCES identity.sessions(session_id)
);

-- 活躍 session 查詢索引
CREATE INDEX IF NOT EXISTS idx_sessions_account_id
    ON identity.sessions (account_id);

CREATE INDEX IF NOT EXISTS idx_sessions_active
    ON identity.sessions (account_id)
    WHERE revoked_at IS NULL;

-- ============================================================================
-- 6. identity.invitations — 邀請 token（只存雜湊）
-- Contract §2.2, §7: 單次使用，TTL ≤ 72 小時
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.invitations (
    invitation_id   UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID            NOT NULL,
    email           VARCHAR(320)    NOT NULL,
    token_hash      TEXT            NOT NULL,
    preset_roles    JSONB           NOT NULL DEFAULT '[]'::jsonb,
    preset_scope    JSONB           NOT NULL DEFAULT '{}'::jsonb,
    created_by      TEXT            NOT NULL,
    expires_at      TIMESTAMPTZ     NOT NULL,
    accepted_at     TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_invitations_token_hash
    ON identity.invitations (token_hash);

CREATE INDEX IF NOT EXISTS idx_invitations_email
    ON identity.invitations (tenant_id, lower(email));

-- ============================================================================
-- 7. identity.federated_identities — OIDC 聯合身份對應
-- Contract §2.2: UNIQUE (issuer, subject)
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.federated_identities (
    account_id  UUID            NOT NULL
                REFERENCES identity.accounts(account_id) ON DELETE CASCADE,
    issuer      TEXT            NOT NULL,
    subject     TEXT            NOT NULL,
    linked_at   TIMESTAMPTZ     NOT NULL DEFAULT now(),
    linked_by   TEXT            NOT NULL,
    PRIMARY KEY (account_id, issuer, subject),
    UNIQUE (issuer, subject)
);

-- ============================================================================
-- 8. identity.login_attempts — 登入節流與鎖定
-- Contract §2.2, §6.4: attempt_key 為帳號鍵或來源 IP 雜湊
--   • 帳號維度：'account:<account_id>'
--   • IP 維度：'ip:<sha256/hmac-sha256 hex>'，**不得**落地明文 client IP
-- lockout_count 為 §6.4 「每次再鎖定加倍（上限 60 分鐘）」所需的跨視窗狀態：
-- 指數退避的倍數來自「已鎖定輪次」而非單一視窗內的失敗次數，
-- 若不持久化，視窗一過期就會退回基礎鎖定時間，加倍永遠不會發生。
-- ============================================================================
CREATE TABLE IF NOT EXISTS identity.login_attempts (
    attempt_key         TEXT            PRIMARY KEY,
    window_started_at   TIMESTAMPTZ     NOT NULL DEFAULT now(),
    failure_count       INTEGER         NOT NULL DEFAULT 0,
    locked_until        TIMESTAMPTZ,
    lockout_count       INTEGER         NOT NULL DEFAULT 0
);

-- expand-only（Contract §9）：上方的 CREATE TABLE IF NOT EXISTS 對於已建表的
-- 環境不會補上新欄位，因此以幂等 ALTER 補齊；不修改也不刪除任何現存欄位。
ALTER TABLE identity.login_attempts
    ADD COLUMN IF NOT EXISTS lockout_count INTEGER NOT NULL DEFAULT 0;

-- ============================================================================
-- 觸發器：accounts.updated_at 自動更新
-- ============================================================================
CREATE OR REPLACE FUNCTION identity.update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_accounts_updated_at'
          AND tgrelid = 'identity.accounts'::regclass
    ) THEN
        CREATE TRIGGER trg_accounts_updated_at
            BEFORE UPDATE ON identity.accounts
            FOR EACH ROW
            EXECUTE FUNCTION identity.update_timestamp();
    END IF;
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_password_credentials_updated_at'
          AND tgrelid = 'identity.password_credentials'::regclass
    ) THEN
        CREATE TRIGGER trg_password_credentials_updated_at
            BEFORE UPDATE ON identity.password_credentials
            FOR EACH ROW
            EXECUTE FUNCTION identity.update_timestamp();
    END IF;
END;
$$;
