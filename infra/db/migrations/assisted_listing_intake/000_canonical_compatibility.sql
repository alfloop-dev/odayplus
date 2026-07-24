-- Compatibility prelude for applying the Assisted Listing Intake 001-004
-- stack after the canonical Alembic baseline. The canonical baseline already
-- owns three shared tables with older shapes. A non-empty legacy table needs a
-- governed data backfill, so this migration fails closed instead of assigning
-- tenant, identity, promotion, or audit lineage heuristically.

DO $compatibility_guard$
BEGIN
  IF to_regclass('expansion.listings') IS NOT NULL
     AND EXISTS (SELECT 1 FROM expansion.listings LIMIT 1) THEN
    RAISE EXCEPTION
      'CANONICAL_LISTING_BACKFILL_REQUIRED: expansion.listings is not empty';
  END IF;
  IF to_regclass('expansion.candidate_sites') IS NOT NULL
     AND EXISTS (SELECT 1 FROM expansion.candidate_sites LIMIT 1) THEN
    RAISE EXCEPTION
      'CANONICAL_CANDIDATE_BACKFILL_REQUIRED: expansion.candidate_sites is not empty';
  END IF;
  IF to_regclass('audit.audit_events') IS NOT NULL
     AND EXISTS (SELECT 1 FROM audit.audit_events LIMIT 1) THEN
    RAISE EXCEPTION
      'CANONICAL_AUDIT_BACKFILL_REQUIRED: audit.audit_events is not empty';
  END IF;
END
$compatibility_guard$;

ALTER TABLE IF EXISTS expansion.listings
  ADD COLUMN IF NOT EXISTS tenant_id uuid,
  ADD COLUMN IF NOT EXISTS property_id uuid,
  ADD COLUMN IF NOT EXISTS canonical_url_sha256 char(64),
  ADD COLUMN IF NOT EXISTS lifecycle_state text,
  ADD COLUMN IF NOT EXISTS current_revision_id uuid,
  ADD COLUMN IF NOT EXISTS current_observation_id uuid,
  ADD COLUMN IF NOT EXISTS version bigint DEFAULT 1,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz DEFAULT now(),
  ADD COLUMN IF NOT EXISTS archived_at timestamptz,
  ADD COLUMN IF NOT EXISTS retention_class text DEFAULT 'BUSINESS_5Y',
  ADD COLUMN IF NOT EXISTS legal_hold boolean DEFAULT false,
  ADD COLUMN IF NOT EXISTS legal_hold_id uuid;

ALTER TABLE IF EXISTS expansion.listings
  ALTER COLUMN source_listing_id DROP NOT NULL,
  ALTER COLUMN listing_status DROP NOT NULL,
  ALTER COLUMN rent_amount DROP NOT NULL,
  ALTER COLUMN currency DROP NOT NULL,
  ALTER COLUMN area_ping DROP NOT NULL,
  ALTER COLUMN snapshot_id DROP NOT NULL,
  ALTER COLUMN tenant_id SET NOT NULL,
  ALTER COLUMN lifecycle_state SET DEFAULT 'ACTIVE',
  ALTER COLUMN lifecycle_state SET NOT NULL,
  ALTER COLUMN version SET NOT NULL,
  ALTER COLUMN updated_at SET NOT NULL,
  ALTER COLUMN retention_class SET NOT NULL,
  ALTER COLUMN legal_hold SET NOT NULL;

DO $listing_checks$
BEGIN
  IF to_regclass('expansion.listings') IS NOT NULL
     AND NOT EXISTS (
       SELECT 1
       FROM pg_constraint
       WHERE conrelid = 'expansion.listings'::regclass
         AND conname = 'listings_lifecycle_state_check'
     ) THEN
    ALTER TABLE expansion.listings
      ADD CONSTRAINT listings_lifecycle_state_check
      CHECK (
        lifecycle_state IN (
          'ACTIVE','REMOVED','EXPIRED','STALE','QUARANTINED','ARCHIVED'
        )
      );
  END IF;
END
$listing_checks$;

DO $listing_indexes$
BEGIN
  IF to_regclass('expansion.listings') IS NOT NULL THEN
    CREATE UNIQUE INDEX IF NOT EXISTS ux_assisted_listing_source_identity
      ON expansion.listings (tenant_id, source_id, source_listing_id);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_assisted_listing_canonical_url
      ON expansion.listings (tenant_id, source_id, canonical_url_sha256);
  END IF;
END
$listing_indexes$;

ALTER TABLE IF EXISTS expansion.candidate_sites
  DROP CONSTRAINT IF EXISTS candidate_sites_listing_id_fkey,
  ADD COLUMN IF NOT EXISTS tenant_id uuid,
  ADD COLUMN IF NOT EXISTS property_id uuid,
  ADD COLUMN IF NOT EXISTS source_listing_id uuid,
  ADD COLUMN IF NOT EXISTS promotion_decision_id uuid,
  ADD COLUMN IF NOT EXISTS status text,
  ADD COLUMN IF NOT EXISTS version bigint DEFAULT 1;

ALTER TABLE IF EXISTS expansion.candidate_sites
  ALTER COLUMN site_status DROP NOT NULL,
  ALTER COLUMN created_by DROP NOT NULL,
  ALTER COLUMN tenant_id SET NOT NULL,
  ALTER COLUMN property_id SET NOT NULL,
  ALTER COLUMN source_listing_id SET NOT NULL,
  ALTER COLUMN promotion_decision_id SET NOT NULL,
  ALTER COLUMN status SET NOT NULL,
  ALTER COLUMN version SET NOT NULL;

DO $candidate_checks$
BEGIN
  IF to_regclass('expansion.candidate_sites') IS NOT NULL
     AND NOT EXISTS (
       SELECT 1
       FROM pg_constraint
       WHERE conrelid = 'expansion.candidate_sites'::regclass
         AND conname = 'candidate_sites_status_check'
     ) THEN
    ALTER TABLE expansion.candidate_sites
      ADD CONSTRAINT candidate_sites_status_check
      CHECK (
        status IN (
          'NEW','SCREENED','SCORING','SCORED','VISITED','REJECTED',
          'APPROVED','OPENED','SCORING_FAILED'
        )
      );
  END IF;
END
$candidate_checks$;

ALTER TABLE IF EXISTS audit.audit_events
  ADD COLUMN IF NOT EXISTS tenant_id uuid,
  ADD COLUMN IF NOT EXISTS sequence_no bigint,
  ADD COLUMN IF NOT EXISTS event_type text,
  ADD COLUMN IF NOT EXISTS actor_subject_id uuid,
  ADD COLUMN IF NOT EXISTS service_principal text,
  ADD COLUMN IF NOT EXISTS actor_role text,
  ADD COLUMN IF NOT EXISTS resource_type text,
  ADD COLUMN IF NOT EXISTS resource_id uuid,
  ADD COLUMN IF NOT EXISTS decision_id uuid,
  ADD COLUMN IF NOT EXISTS source_snapshot_id uuid,
  ADD COLUMN IF NOT EXISTS parser_run_id uuid,
  ADD COLUMN IF NOT EXISTS before_value jsonb,
  ADD COLUMN IF NOT EXISTS after_value jsonb,
  ADD COLUMN IF NOT EXISTS reason text,
  ADD COLUMN IF NOT EXISTS result text,
  ADD COLUMN IF NOT EXISTS reason_code text,
  ADD COLUMN IF NOT EXISTS causation_id uuid,
  ADD COLUMN IF NOT EXISTS previous_event_sha256 char(64),
  ADD COLUMN IF NOT EXISTS event_sha256 char(64),
  ADD COLUMN IF NOT EXISTS worm_object_uri text,
  ADD COLUMN IF NOT EXISTS retained_until timestamptz,
  ADD COLUMN IF NOT EXISTS legal_hold boolean DEFAULT false;

ALTER TABLE IF EXISTS audit.audit_events
  ALTER COLUMN actor_id DROP NOT NULL,
  ALTER COLUMN actor_type DROP NOT NULL,
  ALTER COLUMN entity_type DROP NOT NULL,
  ALTER COLUMN entity_id DROP NOT NULL,
  ALTER COLUMN ip_address DROP NOT NULL,
  ALTER COLUMN correlation_id TYPE uuid USING correlation_id::uuid,
  ALTER COLUMN tenant_id SET NOT NULL,
  ALTER COLUMN sequence_no SET NOT NULL,
  ALTER COLUMN event_type SET NOT NULL,
  ALTER COLUMN resource_type SET NOT NULL,
  ALTER COLUMN resource_id SET NOT NULL,
  ALTER COLUMN result SET NOT NULL,
  ALTER COLUMN event_sha256 SET NOT NULL,
  ALTER COLUMN retained_until SET NOT NULL,
  ALTER COLUMN legal_hold SET NOT NULL;

DO $audit_checks$
BEGIN
  IF to_regclass('audit.audit_events') IS NOT NULL
     AND NOT EXISTS (
       SELECT 1
       FROM pg_constraint
       WHERE conrelid = 'audit.audit_events'::regclass
         AND conname = 'audit_events_result_check'
     ) THEN
    ALTER TABLE audit.audit_events
      ADD CONSTRAINT audit_events_result_check
      CHECK (result IN ('ALLOWED','DENIED','SUCCEEDED','FAILED','MASKED'));
  END IF;
END
$audit_checks$;

DO $audit_indexes$
BEGIN
  IF to_regclass('audit.audit_events') IS NOT NULL THEN
    CREATE UNIQUE INDEX IF NOT EXISTS ux_assisted_audit_sequence
      ON audit.audit_events (tenant_id, sequence_no);
    CREATE UNIQUE INDEX IF NOT EXISTS ux_assisted_audit_event_hash
      ON audit.audit_events (tenant_id, event_sha256);
  END IF;
END
$audit_indexes$;
