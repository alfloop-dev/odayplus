-- AUTH_REQUIRED product routing selected 2026-10-16.
-- No source/account approval is supplied by this migration.
-- The generic PostgreSQL profile may omit this optional bounded context.
-- Skip only a wholly absent context; never repair or grant a partial install.
DO $auth_wait$
BEGIN
  IF to_regclass('intake.source_registry') IS NULL
     AND to_regclass('intake.intakes') IS NULL THEN
    RETURN;
  END IF;
  IF to_regclass('intake.source_registry') IS NULL
     OR to_regclass('intake.intakes') IS NULL THEN
    RAISE EXCEPTION 'The optional Intake context is only partially installed'
      USING ERRCODE = '55000';
  END IF;

  ALTER TABLE intake.source_registry
    ADD COLUMN IF NOT EXISTS assisted_entry_approval_ref text,
    ADD COLUMN IF NOT EXISTS assisted_entry_approved_by uuid,
    ADD COLUMN IF NOT EXISTS assisted_entry_review_expires_at timestamptz;

  ALTER TABLE intake.intakes
    DROP CONSTRAINT IF EXISTS intakes_processing_state_check;
  ALTER TABLE intake.intakes
    ADD CONSTRAINT intakes_processing_state_check CHECK (processing_state IN (
      'SUBMITTED','CHECKING_IDENTITY','CHECKING_SOURCE_POLICY',
      'AWAITING_SOURCE_AUTHORIZATION','AWAITING_ASSISTED_ENTRY','RETRIEVING',
      'PARSING','MATCHING','NEEDS_REVIEW','READY','QUARANTINED','FAILED','CANCELLED'
    ));
END;
$auth_wait$;
