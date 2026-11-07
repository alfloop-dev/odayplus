-- Engineering schema extension; this grants no service or source authority.
BEGIN;

DO $decision_actor$
DECLARE
  decision_table regclass := to_regclass('identity.match_decisions');
  actor_column record;
  actor_check text := $check$CHECK (
CASE
    WHEN (proposer_service_principal IS NULL) THEN (proposer_subject_id IS NOT NULL)
    ELSE ((proposer_service_principal = 'native-intake-identity'::text)
      AND (proposer_subject_id IS NULL) AND (decision_type = 'DUPLICATE'::text)
      AND (reviewer_subject_id IS NULL))
END)$check$;
  actor_body text := $body$
BEGIN
  IF NEW.proposer_subject_id IS DISTINCT FROM OLD.proposer_subject_id
     OR NEW.proposer_service_principal IS DISTINCT FROM OLD.proposer_service_principal THEN
    RAISE EXCEPTION 'The recorded decision proposer cannot be changed'
      USING ERRCODE = '23514', CONSTRAINT = 'ck_match_decisions_proposer_immutable';
  END IF;
  RETURN NEW;
END
$body$;
  actor_function regprocedure;
BEGIN
  IF decision_table IS NULL OR NOT EXISTS (
    SELECT 1 FROM pg_attribute WHERE attrelid = decision_table
      AND attname = 'proposer_subject_id' AND atttypid = 'uuid'::regtype AND NOT attisdropped
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conrelid = decision_table AND contype = 'c'
      AND convalidated AND pg_get_expr(conbin, conrelid) =
        '((reviewer_subject_id IS NULL) OR (reviewer_subject_id <> proposer_subject_id))'
  ) THEN
    RAISE EXCEPTION 'The original decision actor and independent reviewer contract is required';
  END IF;

  SELECT * INTO actor_column FROM pg_attribute
    WHERE attrelid = decision_table AND attname = 'proposer_service_principal' AND NOT attisdropped;
  IF NOT FOUND THEN
    IF NOT (SELECT attnotnull FROM pg_attribute WHERE attrelid = decision_table AND attname = 'proposer_subject_id')
      OR EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = decision_table AND conname = 'ck_match_decisions_proposer_actor')
      OR to_regprocedure('identity.protect_match_decision_proposer_actor()') IS NOT NULL
      OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid = decision_table AND tgname = 'trg_match_decision_proposer_immutable') THEN
      RAISE EXCEPTION 'Partial decision actor migration is incompatible';
    END IF;
    ALTER TABLE identity.match_decisions ADD COLUMN proposer_service_principal text;
    ALTER TABLE identity.match_decisions ALTER COLUMN proposer_subject_id DROP NOT NULL;
    ALTER TABLE identity.match_decisions ADD CONSTRAINT ck_match_decisions_proposer_actor CHECK (
      CASE WHEN proposer_service_principal IS NULL THEN proposer_subject_id IS NOT NULL
      ELSE proposer_service_principal = 'native-intake-identity'
        AND proposer_subject_id IS NULL AND decision_type = 'DUPLICATE'
        AND reviewer_subject_id IS NULL END
    );
    EXECUTE format('CREATE FUNCTION identity.protect_match_decision_proposer_actor() RETURNS trigger LANGUAGE plpgsql AS %L', actor_body);
    CREATE TRIGGER trg_match_decision_proposer_immutable BEFORE UPDATE ON identity.match_decisions
      FOR EACH ROW EXECUTE FUNCTION identity.protect_match_decision_proposer_actor();
  END IF;

  actor_function := to_regprocedure('identity.protect_match_decision_proposer_actor()');
  IF NOT EXISTS (
    SELECT 1 FROM pg_attribute WHERE attrelid = decision_table
      AND attname = 'proposer_service_principal' AND atttypid = 'text'::regtype
      AND NOT attnotnull AND NOT atthasdef AND attgenerated = '' AND attidentity = '' AND NOT attisdropped
  ) OR (SELECT attnotnull FROM pg_attribute WHERE attrelid = decision_table AND attname = 'proposer_subject_id')
    OR NOT EXISTS (
      SELECT 1 FROM pg_constraint WHERE conrelid = decision_table AND conname = 'ck_match_decisions_proposer_actor'
        AND contype = 'c' AND convalidated
        AND regexp_replace(pg_get_constraintdef(oid), '\s', '', 'g') = regexp_replace(actor_check, '\s', '', 'g')
    ) OR NOT EXISTS (
      SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
        WHERE p.oid = actor_function AND p.prosrc = actor_body AND l.lanname = 'plpgsql'
          AND NOT p.prosecdef AND p.proconfig IS NULL AND p.prorettype = 'trigger'::regtype
    ) OR NOT EXISTS (
      SELECT 1 FROM pg_trigger WHERE tgrelid = decision_table AND tgname = 'trg_match_decision_proposer_immutable'
        AND tgfoid = actor_function AND tgtype = 19 AND tgenabled = 'O' AND NOT tgisinternal
        AND tgnargs = 0 AND tgqual IS NULL AND tgattr::text = ''
    ) THEN
    RAISE EXCEPTION 'Decision service actor schema is incompatible; no repair was attempted';
  END IF;
END
$decision_actor$;

COMMIT;
