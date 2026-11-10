-- Anonymous global control data, separate from tenant workflow payloads.
-- No source, tenant, storage, account or runtime activation is performed.
-- A tenant job may retain an opaque reservation UUID one way. These ledgers
-- must never gain tenant/job/Intake/actor/URL/content or reverse business links.
DO $retrieval_quota$
BEGIN
  IF to_regclass('intake.source_registry') IS NULL
     AND to_regclass('intake.intakes') IS NULL
     AND to_regclass('intake.native_retrieval_rate_reservations') IS NULL
     AND to_regclass('intake.native_retrieval_inflight_slots') IS NULL THEN
    RETURN;
  END IF;

  IF (to_regclass('intake.native_retrieval_rate_reservations') IS NULL)
     <> (to_regclass('intake.native_retrieval_inflight_slots') IS NULL) THEN
    RAISE EXCEPTION 'Native retrieval quota controls are only partially installed'
      USING ERRCODE = '55000';
  END IF;
  IF to_regclass('intake.source_registry') IS NULL
     OR to_regclass('intake.intakes') IS NULL THEN
    RAISE EXCEPTION 'The optional Intake context is only partially installed'
      USING ERRCODE = '55000';
  END IF;

  CREATE TABLE IF NOT EXISTS intake.native_retrieval_rate_reservations (
    reservation_id uuid PRIMARY KEY,
    source_id text NOT NULL CHECK (length(source_id) BETWEEN 1 AND 256),
    admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > admitted_at)
  );
  CREATE INDEX IF NOT EXISTS native_retrieval_rate_source_expiry
    ON intake.native_retrieval_rate_reservations (source_id, expires_at);

  CREATE TABLE IF NOT EXISTS intake.native_retrieval_inflight_slots (
    reservation_id uuid PRIMARY KEY,
    source_id text NOT NULL CHECK (length(source_id) BETWEEN 1 AND 256),
    admitted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > admitted_at)
  );
  CREATE INDEX IF NOT EXISTS native_retrieval_inflight_source_expiry
    ON intake.native_retrieval_inflight_slots (source_id, expires_at);

  COMMENT ON TABLE intake.native_retrieval_rate_reservations IS
    'Anonymous global source rate controls; independent random UUID, source_id and DB timestamps only; no tenant/business reverse mapping; deliberately not RLS';
  COMMENT ON TABLE intake.native_retrieval_inflight_slots IS
    'Anonymous global IO concurrency controls; independent random UUID, source_id and DB timestamps only; conservatively expire after IO and cleanup; deliberately not RLS';
END;
$retrieval_quota$;
