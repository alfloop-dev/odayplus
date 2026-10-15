-- ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001: expand-only.
-- Non-secret monotonic revision, not a password hash/digest in a session.
BEGIN;
ALTER TABLE identity.password_credentials
    ADD COLUMN IF NOT EXISTS credential_revision BIGINT NOT NULL DEFAULT 1
    CHECK (credential_revision > 0);
ALTER TABLE identity.sessions
    ADD COLUMN IF NOT EXISTS verified_credential_revision BIGINT
    CHECK (verified_credential_revision > 0);
ALTER TABLE identity.dev_smoke_recovery_capabilities
    ADD COLUMN IF NOT EXISTS original_credential_revision BIGINT,
    ADD COLUMN IF NOT EXISTS target_credential_revision BIGINT;

CREATE OR REPLACE FUNCTION identity.advance_credential_revision()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Neither a same-transaction timestamp nor a caller-assigned number can
    -- stand in for the revision of the PHC that was actually verified.
    IF NEW.phc_hash IS DISTINCT FROM OLD.phc_hash
       OR NEW.must_change IS DISTINCT FROM OLD.must_change
       OR NEW.algorithm IS DISTINCT FROM OLD.algorithm THEN
        NEW.credential_revision := OLD.credential_revision + 1;
    ELSE
        NEW.credential_revision := OLD.credential_revision;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_credential_revision ON identity.password_credentials;
CREATE TRIGGER trg_credential_revision BEFORE UPDATE ON identity.password_credentials
    FOR EACH ROW EXECUTE FUNCTION identity.advance_credential_revision();

CREATE OR REPLACE FUNCTION identity.fence_dev_smoke_session_insert()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_revision BIGINT;
BEGIN
    IF NEW.account_id = '13faae19-21c6-4663-8e89-b93ea7f1107d'::uuid
       OR NEW.verified_credential_revision IS NOT NULL THEN
        -- Same row lock as recovery's credential update: an insert either
        -- commits before rotation (and gets revoked), or checks AFTER it.
        SELECT credential_revision INTO current_revision
        FROM identity.password_credentials WHERE account_id = NEW.account_id
        FOR SHARE;
        IF current_revision IS NULL
           OR (NEW.verified_credential_revision IS NOT NULL
               AND NEW.verified_credential_revision <> current_revision)
           OR (NEW.account_id = '13faae19-21c6-4663-8e89-b93ea7f1107d'::uuid
               AND NEW.verified_credential_revision IS NULL
               AND EXISTS (SELECT 1 FROM identity.dev_smoke_recovery_capabilities
                           WHERE target_account_id = NEW.account_id AND consumed_at IS NOT NULL)) THEN
            RAISE EXCEPTION USING ERRCODE = '28000', MESSAGE = 'SESSION_CREDENTIAL_REVISION_REFUSED';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_dev_smoke_session_fence ON identity.sessions;
CREATE TRIGGER trg_dev_smoke_session_fence BEFORE INSERT ON identity.sessions
    FOR EACH ROW EXECUTE FUNCTION identity.fence_dev_smoke_session_insert();
COMMIT;
