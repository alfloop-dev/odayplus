-- Record each SLA pause interval's own expected resume instant.
-- Old intervals remain NULL/unknown; nothing is inferred from resumed_at,
-- the current SLA resume_at, a later schedule or today's policy/calendar.
-- Engineering schema only: no source activation or rollout/cutover approval.
BEGIN;

DO $workflow_pause_expectation$
DECLARE
  target regclass := to_regclass('workflow.sla_pause_intervals');
  present integer;
BEGIN
  IF target IS NULL THEN
    RAISE EXCEPTION 'Original SLA pause interval table is required';
  END IF;
  SELECT count(*) INTO present FROM pg_attribute
    WHERE attrelid=target AND NOT attisdropped AND attname='expected_resume_at';
  IF present=0 THEN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=target
        AND conname='ck_sla_pause_expected_after_start') THEN
      RAISE EXCEPTION 'Partial pause expectation constraint is incompatible';
    END IF;
    ALTER TABLE workflow.sla_pause_intervals ADD COLUMN expected_resume_at timestamptz;
    ALTER TABLE workflow.sla_pause_intervals ADD CONSTRAINT ck_sla_pause_expected_after_start
      CHECK (expected_resume_at IS NULL OR expected_resume_at > paused_at);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=target
      AND attname='expected_resume_at' AND atttypid='timestamptz'::regtype
      AND NOT attisdropped AND NOT attnotnull AND NOT atthasdef
      AND attgenerated='' AND attidentity='') THEN
    RAISE EXCEPTION 'Pause expectation column is incompatible';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=target
      AND conname='ck_sla_pause_expected_after_start' AND contype='c' AND convalidated
      AND regexp_replace(pg_get_constraintdef(oid),'[\s()]','','g') =
          'CHECKexpected_resume_atISNULLORexpected_resume_at>paused_at') THEN
    RAISE EXCEPTION 'Pause expectation constraint is incompatible';
  END IF;
END
$workflow_pause_expectation$;

COMMIT;
