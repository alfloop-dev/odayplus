-- ODay Plus Assisted Listing Intake schema consistency patch
-- Contract patch: 0002 / effective design version 0.2.1
-- Apply after ODAY_PLUS_ASSISTED_LISTING_INTAKE_SCHEMA.sql.
-- Existing data must pass reconciliation before constraints are validated.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Correct intake and snapshot identity semantics
-- ---------------------------------------------------------------------------

DROP INDEX IF EXISTS intake.ux_intakes_exact_url_active;
CREATE INDEX IF NOT EXISTS ix_intakes_canonical_url_history
  ON intake.intakes (tenant_id, source_id, canonical_url_sha256, submitted_at DESC)
  WHERE canonical_url_sha256 IS NOT NULL;

ALTER TABLE intake.source_snapshots
  DROP CONSTRAINT IF EXISTS source_snapshots_tenant_id_content_sha256_source_id_key;
ALTER TABLE intake.source_snapshots
  ADD CONSTRAINT uq_snapshot_per_intake_content
  UNIQUE (tenant_id, intake_id, source_id, content_sha256);

ALTER TABLE intake.source_snapshots
  ADD COLUMN IF NOT EXISTS object_generation bigint,
  ADD COLUMN IF NOT EXISTS residency_mode text NOT NULL DEFAULT 'TW_ONLY'
    CHECK (residency_mode IN ('TW_ONLY','APPROVED_APAC_DR'));

-- ---------------------------------------------------------------------------
-- 2. Promotion migration lineage and decision type
-- ---------------------------------------------------------------------------

ALTER TABLE expansion.promotion_decisions
  ADD COLUMN IF NOT EXISTS decision_type text NOT NULL DEFAULT 'STANDARD'
    CHECK (decision_type IN ('STANDARD','LEGACY_RECONCILED')),
  ADD COLUMN IF NOT EXISTS migration_ref text,
  ADD COLUMN IF NOT EXISTS rejection_codes text[] NOT NULL DEFAULT '{}';

ALTER TABLE expansion.promotion_decisions
  ADD CONSTRAINT ck_legacy_reconciled_has_migration_ref
  CHECK (decision_type <> 'LEGACY_RECONCILED' OR migration_ref IS NOT NULL)
  NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS ux_active_promotion_request
  ON expansion.promotion_decisions
    (tenant_id, intake_id, listing_id, target_format_code)
  WHERE status NOT IN ('REJECTED','COMPLETED','FAILED','SCORE_FAILED');

-- ---------------------------------------------------------------------------
-- 3. Assignment, SLA, and pause history
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS workflow.assignment_transitions (
  assignment_transition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  assignment_id uuid NOT NULL,
  sequence_no bigint NOT NULL,
  from_status text,
  to_status text NOT NULL CHECK (to_status IN ('UNASSIGNED','ASSIGNED','CLAIMED','TRANSFERRED','ESCALATED','COMPLETED')),
  actor_subject_id uuid,
  service_principal text,
  permission text NOT NULL,
  reason text,
  owner_before uuid,
  owner_after uuid,
  expected_version bigint,
  resulting_version bigint NOT NULL,
  idempotency_key text,
  correlation_id uuid NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, assignment_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS workflow.sla_transitions (
  sla_transition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  sla_instance_id uuid NOT NULL,
  sequence_no bigint NOT NULL,
  from_state text,
  to_state text NOT NULL CHECK (to_state IN ('ON_TRACK','DUE_SOON','OVERDUE','BREACHED','PAUSED','COMPLETED')),
  actor_subject_id uuid,
  service_principal text,
  permission text NOT NULL,
  reason text,
  due_at_before timestamptz,
  due_at_after timestamptz,
  expected_version bigint,
  resulting_version bigint NOT NULL,
  idempotency_key text,
  correlation_id uuid NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, sla_instance_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS workflow.sla_pause_intervals (
  sla_pause_interval_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  sla_instance_id uuid NOT NULL,
  paused_at timestamptz NOT NULL,
  resumed_at timestamptz,
  reason text NOT NULL,
  approved_by uuid NOT NULL,
  created_by uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  CHECK (resumed_at IS NULL OR resumed_at > paused_at),
  CHECK (approved_by <> created_by)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_sla_pause
  ON workflow.sla_pause_intervals (tenant_id, sla_instance_id)
  WHERE resumed_at IS NULL;

-- ---------------------------------------------------------------------------
-- 4. Complete job, outbox, and export envelope fields
-- ---------------------------------------------------------------------------

ALTER TABLE workflow.jobs
  ADD COLUMN IF NOT EXISTS lease_owner text,
  ADD COLUMN IF NOT EXISTS available_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS acknowledged_at timestamptz,
  ADD COLUMN IF NOT EXISTS dead_lettered_at timestamptz,
  ADD COLUMN IF NOT EXISTS cloud_task_name text,
  ADD COLUMN IF NOT EXISTS attempt_token_sha256 char(64);

ALTER TABLE workflow.outbox_events
  ADD COLUMN IF NOT EXISTS producer text NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS actor_ref text,
  ADD COLUMN IF NOT EXISTS policy_version text,
  ADD COLUMN IF NOT EXISTS schema_ref text NOT NULL DEFAULT 'unassigned',
  ADD COLUMN IF NOT EXISTS published_message_id text,
  ADD COLUMN IF NOT EXISTS available_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS locked_by text,
  ADD COLUMN IF NOT EXISTS lock_expires_at timestamptz;

ALTER TABLE audit.export_manifests
  ADD COLUMN IF NOT EXISTS query_filter jsonb NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS row_count bigint NOT NULL DEFAULT 0 CHECK (row_count >= 0),
  ADD COLUMN IF NOT EXISTS file_count integer NOT NULL DEFAULT 1 CHECK (file_count > 0),
  ADD COLUMN IF NOT EXISTS destination_residency text NOT NULL DEFAULT 'TW_ONLY'
    CHECK (destination_residency IN ('TW_ONLY','APPROVED_APAC_DR')),
  ADD COLUMN IF NOT EXISTS worm_object_uri text,
  ADD COLUMN IF NOT EXISTS worm_receipt_sha256 char(64);

-- ---------------------------------------------------------------------------
-- 5. Persist reconciliation findings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS workflow.reconciliation_findings (
  finding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  migration_id text NOT NULL,
  tenant_id uuid NOT NULL,
  source_kind text NOT NULL CHECK (source_kind IN ('legacy_listing','legacy_candidate','snapshot','identity','audit','job','scope','schema')),
  source_id text NOT NULL,
  target_ids uuid[] NOT NULL DEFAULT '{}',
  finding_type text NOT NULL CHECK (finding_type IN ('COUNT_MISMATCH','CHECKSUM_MISMATCH','AMBIGUOUS_IDENTITY','DUPLICATE_CANDIDATE','MISSING_EVIDENCE','INVALID_SCOPE','ORPHAN_REFERENCE','STATE_MAPPING_CONFLICT','CROSS_TENANT_REFERENCE')),
  severity text NOT NULL CHECK (severity IN ('INFO','WARNING','BLOCKING')),
  expected jsonb NOT NULL DEFAULT '{}',
  actual jsonb NOT NULL DEFAULT '{}',
  owner_role text NOT NULL,
  status text NOT NULL CHECK (status IN ('OPEN','RESOLVED','QUARANTINED','WAIVED')),
  resolution_reason text,
  approved_waiver_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  resolved_at timestamptz,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0)
);
CREATE INDEX IF NOT EXISTS ix_reconciliation_open
  ON workflow.reconciliation_findings (tenant_id, migration_id, severity, status)
  WHERE status = 'OPEN';

-- ---------------------------------------------------------------------------
-- 6. Tenant-qualified candidate key naming
-- ---------------------------------------------------------------------------

COMMENT ON COLUMN expansion.candidate_sites.source_listing_id IS
  'Canonical expansion.listings.listing_id. Historical column name retained for compatibility; API name is listing_id.';

-- ---------------------------------------------------------------------------
-- 7. Tenant-qualified unique keys required by composite foreign keys
-- ---------------------------------------------------------------------------

ALTER TABLE intake.intakes
  ADD CONSTRAINT uq_intakes_tenant_id UNIQUE (tenant_id, intake_id);
ALTER TABLE intake.source_snapshots
  ADD CONSTRAINT uq_source_snapshots_tenant_id UNIQUE (tenant_id, source_snapshot_id);
ALTER TABLE intake.parser_runs
  ADD CONSTRAINT uq_parser_runs_tenant_id UNIQUE (tenant_id, parser_run_id);
ALTER TABLE identity.properties
  ADD CONSTRAINT uq_properties_tenant_id UNIQUE (tenant_id, property_id);
ALTER TABLE expansion.listings
  ADD CONSTRAINT uq_listings_tenant_id UNIQUE (tenant_id, listing_id);
ALTER TABLE expansion.listing_revisions
  ADD CONSTRAINT uq_listing_revisions_tenant_id UNIQUE (tenant_id, listing_revision_id);
ALTER TABLE identity.match_cases
  ADD CONSTRAINT uq_match_cases_tenant_id UNIQUE (tenant_id, match_case_id);
ALTER TABLE identity.match_decisions
  ADD CONSTRAINT uq_match_decisions_tenant_id UNIQUE (tenant_id, match_decision_id);
ALTER TABLE workflow.assignments
  ADD CONSTRAINT uq_assignments_tenant_id UNIQUE (tenant_id, assignment_id);
ALTER TABLE workflow.sla_instances
  ADD CONSTRAINT uq_sla_instances_tenant_id UNIQUE (tenant_id, sla_instance_id);
ALTER TABLE expansion.promotion_decisions
  ADD CONSTRAINT uq_promotion_decisions_tenant_id UNIQUE (tenant_id, promotion_decision_id);
ALTER TABLE expansion.candidate_sites
  ADD CONSTRAINT uq_candidate_sites_tenant_id UNIQUE (tenant_id, candidate_site_id);

-- ---------------------------------------------------------------------------
-- 8. Tenant-equal foreign keys. Added NOT VALID for online reconciliation.
-- ---------------------------------------------------------------------------

ALTER TABLE intake.intake_stage_transitions
  ADD CONSTRAINT fk_transition_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID;

ALTER TABLE intake.source_snapshots
  ADD CONSTRAINT fk_snapshot_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID;

ALTER TABLE intake.parser_runs
  ADD CONSTRAINT fk_parser_run_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID,
  ADD CONSTRAINT fk_parser_run_snapshot_tenant
  FOREIGN KEY (tenant_id, source_snapshot_id)
  REFERENCES intake.source_snapshots (tenant_id, source_snapshot_id) NOT VALID;

ALTER TABLE expansion.listings
  ADD CONSTRAINT fk_listing_property_tenant
  FOREIGN KEY (tenant_id, property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID;

ALTER TABLE expansion.listing_revisions
  ADD CONSTRAINT fk_revision_listing_tenant
  FOREIGN KEY (tenant_id, listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID,
  ADD CONSTRAINT fk_revision_snapshot_tenant
  FOREIGN KEY (tenant_id, source_snapshot_id)
  REFERENCES intake.source_snapshots (tenant_id, source_snapshot_id) NOT VALID,
  ADD CONSTRAINT fk_revision_parser_tenant
  FOREIGN KEY (tenant_id, parser_run_id)
  REFERENCES intake.parser_runs (tenant_id, parser_run_id) NOT VALID;

ALTER TABLE expansion.listing_observations
  ADD CONSTRAINT fk_observation_listing_tenant
  FOREIGN KEY (tenant_id, listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID,
  ADD CONSTRAINT fk_observation_snapshot_tenant
  FOREIGN KEY (tenant_id, source_snapshot_id)
  REFERENCES intake.source_snapshots (tenant_id, source_snapshot_id) NOT VALID;

ALTER TABLE identity.source_identity_edges
  ADD CONSTRAINT fk_edge_listing_tenant
  FOREIGN KEY (tenant_id, listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID,
  ADD CONSTRAINT fk_edge_property_tenant
  FOREIGN KEY (tenant_id, property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID;

ALTER TABLE identity.property_redirects
  ADD CONSTRAINT fk_redirect_from_property_tenant
  FOREIGN KEY (tenant_id, from_property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID,
  ADD CONSTRAINT fk_redirect_to_property_tenant
  FOREIGN KEY (tenant_id, to_property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID;

ALTER TABLE identity.match_cases
  ADD CONSTRAINT fk_match_case_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID;

ALTER TABLE identity.match_candidates
  ADD CONSTRAINT fk_match_candidate_case_tenant
  FOREIGN KEY (tenant_id, match_case_id)
  REFERENCES identity.match_cases (tenant_id, match_case_id) NOT VALID,
  ADD CONSTRAINT fk_match_candidate_property_tenant
  FOREIGN KEY (tenant_id, property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID,
  ADD CONSTRAINT fk_match_candidate_listing_tenant
  FOREIGN KEY (tenant_id, listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID;

ALTER TABLE identity.match_decisions
  ADD CONSTRAINT fk_match_decision_case_tenant
  FOREIGN KEY (tenant_id, match_case_id)
  REFERENCES identity.match_cases (tenant_id, match_case_id) NOT VALID;

ALTER TABLE intake.human_corrections
  ADD CONSTRAINT fk_correction_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID,
  ADD CONSTRAINT fk_correction_listing_tenant
  FOREIGN KEY (tenant_id, listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID;

ALTER TABLE workflow.assignments
  ADD CONSTRAINT fk_assignment_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID;

ALTER TABLE workflow.sla_instances
  ADD CONSTRAINT fk_sla_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID;

ALTER TABLE workflow.assignment_transitions
  ADD CONSTRAINT fk_assignment_transition_tenant
  FOREIGN KEY (tenant_id, assignment_id)
  REFERENCES workflow.assignments (tenant_id, assignment_id) NOT VALID;

ALTER TABLE workflow.sla_transitions
  ADD CONSTRAINT fk_sla_transition_tenant
  FOREIGN KEY (tenant_id, sla_instance_id)
  REFERENCES workflow.sla_instances (tenant_id, sla_instance_id) NOT VALID;

ALTER TABLE workflow.sla_pause_intervals
  ADD CONSTRAINT fk_sla_pause_tenant
  FOREIGN KEY (tenant_id, sla_instance_id)
  REFERENCES workflow.sla_instances (tenant_id, sla_instance_id) NOT VALID;

ALTER TABLE expansion.promotion_decisions
  ADD CONSTRAINT fk_promotion_intake_tenant
  FOREIGN KEY (tenant_id, intake_id)
  REFERENCES intake.intakes (tenant_id, intake_id) NOT VALID,
  ADD CONSTRAINT fk_promotion_listing_tenant
  FOREIGN KEY (tenant_id, listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID,
  ADD CONSTRAINT fk_promotion_property_tenant
  FOREIGN KEY (tenant_id, property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID;

ALTER TABLE expansion.candidate_sites
  ADD CONSTRAINT fk_candidate_property_tenant
  FOREIGN KEY (tenant_id, property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID,
  ADD CONSTRAINT fk_candidate_listing_tenant
  FOREIGN KEY (tenant_id, source_listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID,
  ADD CONSTRAINT fk_candidate_promotion_tenant
  FOREIGN KEY (tenant_id, promotion_decision_id)
  REFERENCES expansion.promotion_decisions (tenant_id, promotion_decision_id) NOT VALID;

-- ---------------------------------------------------------------------------
-- 9. RLS on every tenant-bearing contract table
-- ---------------------------------------------------------------------------

ALTER TABLE intake.intake_stage_transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake.parser_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE expansion.listing_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE expansion.listing_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.source_identity_edges ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.property_redirects ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.match_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.match_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.match_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake.human_corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.sla_instances ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.assignment_transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.sla_transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.sla_pause_intervals ENABLE ROW LEVEL SECURITY;
ALTER TABLE expansion.promotion_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.outbox_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.reconciliation_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.legal_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.export_manifests ENABLE ROW LEVEL SECURITY;

COMMIT;

-- Migration procedure:
-- 1. Apply structures and NOT VALID constraints in staging.
-- 2. Populate reconciliation findings for every invalid relationship.
-- 3. Resolve all BLOCKING findings.
-- 4. VALIDATE CONSTRAINT in tenant/source partitions.
-- 5. Create tenant RLS policies using request-scoped app.tenant_id.
-- 6. Only then enable authoritative writes.
