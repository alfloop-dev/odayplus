-- ODay Plus Assisted Listing Intake normative PostgreSQL 16 / PostGIS schema
-- Contract version: 1.0.0; proposed under ODP-SD-INTAKE-001 v0.2.0.
-- This is a contract artifact. Production migration scripts must preserve these
-- names, constraints, and semantics or document an approved compatibility map.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS intake;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS expansion;
CREATE SCHEMA IF NOT EXISTS workflow;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS intake.source_registry (
  source_id text PRIMARY KEY,
  display_name text NOT NULL,
  allowed_hosts text[] NOT NULL DEFAULT '{}',
  canonicalization_rule_version text NOT NULL,
  retrieval_mode text NOT NULL CHECK (retrieval_mode IN ('APPROVED_RETRIEVAL','ASSISTED_ENTRY_ONLY','AUTH_REQUIRED','SOURCE_BLOCKED','POLICY_UNKNOWN')),
  legal_approval_ref text,
  license_approval_ref text,
  policy_owner_subject_id uuid NOT NULL,
  review_expires_at timestamptz,
  requests_per_minute integer NOT NULL DEFAULT 0 CHECK (requests_per_minute >= 0),
  concurrent_requests integer NOT NULL DEFAULT 0 CHECK (concurrent_requests >= 0),
  kill_switch boolean NOT NULL DEFAULT true,
  production_enabled boolean NOT NULL DEFAULT false,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intake.parser_releases (
  parser_release_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id text NOT NULL REFERENCES intake.source_registry(source_id),
  package_name text NOT NULL,
  semantic_version text NOT NULL,
  input_schema_version text NOT NULL,
  output_schema_version text NOT NULL,
  artifact_uri text NOT NULL,
  artifact_sha256 char(64) NOT NULL,
  test_corpus_version text NOT NULL,
  validation_status text NOT NULL CHECK (validation_status IN ('DRAFT','VALIDATED','CANARY','PRODUCTION','DEPRECATED','ROLLED_BACK','BLOCKED')),
  canary_percent numeric(5,2) NOT NULL DEFAULT 0 CHECK (canary_percent BETWEEN 0 AND 100),
  released_by uuid,
  released_at timestamptz,
  deprecated_at timestamptz,
  rollback_to_release_id uuid REFERENCES intake.parser_releases(parser_release_id),
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, semantic_version)
);

CREATE TABLE IF NOT EXISTS intake.intakes (
  intake_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  brand_id uuid,
  region_id uuid,
  assigned_area_id uuid,
  heat_zone_id uuid,
  submitter_subject_id uuid NOT NULL,
  intake_method text NOT NULL CHECK (intake_method IN ('URL','MANUAL','CSV','APPROVED_FEED','OPERATOR_SNAPSHOT')),
  original_url text,
  canonical_url text,
  canonical_url_sha256 char(64),
  source_id text REFERENCES intake.source_registry(source_id),
  source_policy_state text CHECK (source_policy_state IN ('APPROVED_RETRIEVAL','ASSISTED_ENTRY_ONLY','AUTH_REQUIRED','SOURCE_BLOCKED','POLICY_UNKNOWN')),
  processing_state text NOT NULL CHECK (processing_state IN ('SUBMITTED','CHECKING_IDENTITY','CHECKING_SOURCE_POLICY','AWAITING_ASSISTED_ENTRY','RETRIEVING','PARSING','MATCHING','NEEDS_REVIEW','READY','QUARANTINED','FAILED','CANCELLED')),
  resolved_listing_id uuid,
  queue_owner_subject_id uuid,
  queue_owner_role text,
  correlation_id uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  submitted_at timestamptz NOT NULL DEFAULT now(),
  last_transition_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  retention_class text NOT NULL DEFAULT 'BUSINESS_5Y',
  legal_hold boolean NOT NULL DEFAULT false,
  legal_hold_id uuid,
  deleted_at timestamptz,
  CHECK ((intake_method = 'URL' AND original_url IS NOT NULL) OR intake_method <> 'URL'),
  UNIQUE (tenant_id, correlation_id)
);
CREATE INDEX IF NOT EXISTS ix_intakes_queue ON intake.intakes (tenant_id, processing_state, last_transition_at, intake_id);
CREATE INDEX IF NOT EXISTS ix_intakes_scope ON intake.intakes (tenant_id, region_id, assigned_area_id, heat_zone_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_intakes_exact_url_active
  ON intake.intakes (tenant_id, source_id, canonical_url_sha256)
  WHERE canonical_url_sha256 IS NOT NULL AND processing_state <> 'CANCELLED';

CREATE TABLE IF NOT EXISTS intake.intake_stage_transitions (
  transition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  sequence_no bigint NOT NULL,
  from_state text,
  to_state text NOT NULL,
  actor_subject_id uuid,
  service_principal text,
  permission text NOT NULL,
  reason_code text,
  reason_text text,
  idempotency_key text,
  expected_version bigint,
  resulting_version bigint NOT NULL,
  job_id uuid,
  source_policy_version bigint,
  parser_release_id uuid REFERENCES intake.parser_releases(parser_release_id),
  source_snapshot_id uuid,
  match_case_id uuid,
  correlation_id uuid NOT NULL,
  causation_id uuid,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, intake_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS intake.source_snapshots (
  source_snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  source_id text NOT NULL REFERENCES intake.source_registry(source_id),
  original_url text,
  canonical_url text,
  raw_object_uri text NOT NULL,
  redacted_object_uri text,
  content_sha256 char(64) NOT NULL,
  media_type text NOT NULL,
  byte_length bigint NOT NULL CHECK (byte_length >= 0),
  captured_at timestamptz NOT NULL,
  observed_at timestamptz NOT NULL,
  stored_at timestamptz NOT NULL DEFAULT now(),
  capture_method text NOT NULL CHECK (capture_method IN ('SERVER_RETRIEVAL','OPERATOR_UPLOAD','APPROVED_FEED','MANUAL_ENTRY')),
  retention_class text NOT NULL,
  purge_after timestamptz,
  legal_hold boolean NOT NULL DEFAULT false,
  legal_hold_id uuid,
  encryption_key_ref text NOT NULL,
  version bigint NOT NULL DEFAULT 1,
  UNIQUE (tenant_id, content_sha256, source_id)
);
CREATE INDEX IF NOT EXISTS ix_snapshots_intake ON intake.source_snapshots (tenant_id, intake_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS intake.parser_runs (
  parser_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  source_snapshot_id uuid NOT NULL REFERENCES intake.source_snapshots(source_snapshot_id),
  parser_release_id uuid NOT NULL REFERENCES intake.parser_releases(parser_release_id),
  status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','PARTIAL','FAILED','CANCELLED')),
  parsed_payload jsonb,
  normalized_payload jsonb,
  field_confidence jsonb NOT NULL DEFAULT '{}',
  validation_errors jsonb NOT NULL DEFAULT '[]',
  started_at timestamptz,
  completed_at timestamptz,
  correlation_id uuid NOT NULL,
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, source_snapshot_id, parser_release_id)
);

CREATE TABLE IF NOT EXISTS identity.properties (
  property_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  region_id uuid,
  normalized_address text NOT NULL,
  address_fingerprint char(64) NOT NULL,
  latitude numeric(9,6),
  longitude numeric(9,6),
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','MERGED','SPLIT','QUARANTINED','ARCHIVED')),
  current_redirect_property_id uuid REFERENCES identity.properties(property_id),
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, property_id),
  CHECK (current_redirect_property_id IS NULL OR current_redirect_property_id <> property_id)
);
CREATE INDEX IF NOT EXISTS ix_properties_address ON identity.properties (tenant_id, address_fingerprint);

CREATE TABLE IF NOT EXISTS expansion.listings (
  listing_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  property_id uuid REFERENCES identity.properties(property_id),
  source_id text NOT NULL REFERENCES intake.source_registry(source_id),
  source_listing_id text,
  canonical_url_sha256 char(64),
  lifecycle_state text NOT NULL CHECK (lifecycle_state IN ('ACTIVE','REMOVED','EXPIRED','STALE','QUARANTINED','ARCHIVED')),
  current_revision_id uuid,
  current_observation_id uuid,
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  archived_at timestamptz,
  retention_class text NOT NULL DEFAULT 'BUSINESS_5Y',
  legal_hold boolean NOT NULL DEFAULT false,
  legal_hold_id uuid,
  UNIQUE (tenant_id, source_id, source_listing_id),
  UNIQUE (tenant_id, source_id, canonical_url_sha256)
);

CREATE TABLE IF NOT EXISTS expansion.listing_revisions (
  listing_revision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  listing_id uuid NOT NULL REFERENCES expansion.listings(listing_id),
  revision_no bigint NOT NULL CHECK (revision_no > 0),
  revision_kind text NOT NULL CHECK (revision_kind IN ('CREATED','CONTENT_CHANGED','STATUS_CHANGED','CORRECTED','RELISTED','IDENTITY_REBOUND')),
  source_snapshot_id uuid REFERENCES intake.source_snapshots(source_snapshot_id),
  parser_run_id uuid REFERENCES intake.parser_runs(parser_run_id),
  parsed_values jsonb,
  normalized_values jsonb NOT NULL,
  corrected_values jsonb,
  effective_values jsonb NOT NULL,
  material_fingerprint char(64) NOT NULL,
  supersedes_revision_id uuid REFERENCES expansion.listing_revisions(listing_revision_id),
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, listing_id, revision_no),
  UNIQUE (tenant_id, listing_id, material_fingerprint)
);
ALTER TABLE expansion.listings
  ADD CONSTRAINT fk_listing_current_revision
  FOREIGN KEY (current_revision_id) REFERENCES expansion.listing_revisions(listing_revision_id)
  DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE IF NOT EXISTS expansion.listing_observations (
  listing_observation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  listing_id uuid NOT NULL REFERENCES expansion.listings(listing_id),
  source_snapshot_id uuid REFERENCES intake.source_snapshots(source_snapshot_id),
  observation_kind text NOT NULL CHECK (observation_kind IN ('UNCHANGED','FRESHNESS_REFRESHED','REMOVED','UNAVAILABLE','BLOCKED','EXPIRED','STALE')),
  observed_at timestamptz NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, listing_id, observed_at, observation_kind)
);

CREATE TABLE IF NOT EXISTS identity.source_identity_edges (
  edge_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  source_id text NOT NULL,
  source_entity_id text NOT NULL,
  listing_id uuid REFERENCES expansion.listings(listing_id),
  property_id uuid NOT NULL REFERENCES identity.properties(property_id),
  match_strategy text NOT NULL,
  confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  decision_id uuid,
  supersedes_edge_id uuid REFERENCES identity.source_identity_edges(edge_id),
  effective_from timestamptz NOT NULL DEFAULT now(),
  effective_to timestamptz,
  edge_version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (effective_to IS NULL OR effective_to > effective_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_effective_source_identity
  ON identity.source_identity_edges (tenant_id, source_id, source_entity_id)
  WHERE effective_to IS NULL;
CREATE INDEX IF NOT EXISTS ix_identity_property_history
  ON identity.source_identity_edges (tenant_id, property_id, effective_from DESC);

CREATE TABLE IF NOT EXISTS identity.property_redirects (
  redirect_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  from_property_id uuid NOT NULL REFERENCES identity.properties(property_id),
  to_property_id uuid NOT NULL REFERENCES identity.properties(property_id),
  decision_id uuid NOT NULL,
  effective_from timestamptz NOT NULL DEFAULT now(),
  reversed_at timestamptz,
  version bigint NOT NULL DEFAULT 1,
  CHECK (from_property_id <> to_property_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_effective_property_redirect
  ON identity.property_redirects (tenant_id, from_property_id)
  WHERE reversed_at IS NULL;

CREATE TABLE IF NOT EXISTS identity.match_cases (
  match_case_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  outcome text NOT NULL CHECK (outcome IN ('NEW','EXACT_DUPLICATE','REVISION','POSSIBLE_MATCH','QUARANTINED')),
  status text NOT NULL CHECK (status IN ('PROPOSED','PENDING_REVIEW','APPROVED','REJECTED','EXECUTING','EXECUTED','FAILED','REVERSAL_PENDING','REVERSED')),
  confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  supporting_signals jsonb NOT NULL DEFAULT '[]',
  contradictory_signals jsonb NOT NULL DEFAULT '[]',
  proposed_by text NOT NULL,
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, intake_id)
);

CREATE TABLE IF NOT EXISTS identity.match_candidates (
  match_candidate_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  match_case_id uuid NOT NULL REFERENCES identity.match_cases(match_case_id),
  property_id uuid REFERENCES identity.properties(property_id),
  listing_id uuid REFERENCES expansion.listings(listing_id),
  rank integer NOT NULL CHECK (rank > 0),
  confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL,
  UNIQUE (tenant_id, match_case_id, rank)
);

CREATE TABLE IF NOT EXISTS identity.match_decisions (
  match_decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  match_case_id uuid NOT NULL REFERENCES identity.match_cases(match_case_id),
  decision_type text NOT NULL CHECK (decision_type IN ('CREATE','REVISE','DUPLICATE','QUARANTINE','REJECT','REOPEN','MERGE','SPLIT','UNMERGE')),
  status text NOT NULL CHECK (status IN ('DRAFT','PENDING_REVIEW','APPROVED','REJECTED','EXECUTING','EXECUTED','FAILED','REVERSAL_PENDING','REVERSED','SUPERSEDED')),
  proposer_subject_id uuid NOT NULL,
  reviewer_subject_id uuid,
  reason text NOT NULL,
  risk_acknowledged boolean NOT NULL DEFAULT false,
  before_graph jsonb NOT NULL,
  after_graph jsonb NOT NULL,
  source_snapshot_id uuid REFERENCES intake.source_snapshots(source_snapshot_id),
  parser_run_id uuid REFERENCES intake.parser_runs(parser_run_id),
  supersedes_decision_id uuid REFERENCES identity.match_decisions(match_decision_id),
  reversal_of_decision_id uuid REFERENCES identity.match_decisions(match_decision_id),
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_at timestamptz,
  executed_at timestamptz,
  CHECK (reviewer_subject_id IS NULL OR reviewer_subject_id <> proposer_subject_id)
);

CREATE TABLE IF NOT EXISTS intake.human_corrections (
  correction_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  listing_id uuid REFERENCES expansion.listings(listing_id),
  field_path text NOT NULL,
  field_classification text NOT NULL CHECK (field_classification IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
  parsed_value jsonb,
  normalized_value jsonb,
  corrected_value jsonb NOT NULL,
  before_effective_value jsonb,
  after_effective_value jsonb NOT NULL,
  reason text NOT NULL,
  proposed_by uuid NOT NULL,
  reviewed_by uuid,
  status text NOT NULL CHECK (status IN ('PROPOSED','APPLIED','REJECTED','SUPERSEDED','REVERSED')),
  identity_affecting boolean NOT NULL DEFAULT false,
  source_snapshot_id uuid REFERENCES intake.source_snapshots(source_snapshot_id),
  parser_run_id uuid REFERENCES intake.parser_runs(parser_run_id),
  supersedes_correction_id uuid REFERENCES intake.human_corrections(correction_id),
  reversal_of_correction_id uuid REFERENCES intake.human_corrections(correction_id),
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_at timestamptz,
  CHECK (NOT identity_affecting OR reason <> '')
);

CREATE TABLE IF NOT EXISTS workflow.assignments (
  assignment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  status text NOT NULL CHECK (status IN ('UNASSIGNED','ASSIGNED','CLAIMED','TRANSFERRED','ESCALATED','COMPLETED')),
  queue_key text NOT NULL,
  owner_subject_id uuid,
  owner_role text,
  previous_owner_subject_id uuid,
  assigned_by uuid,
  handoff_note text,
  due_at timestamptz,
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_current_assignment
  ON workflow.assignments (tenant_id, intake_id)
  WHERE status <> 'COMPLETED';

CREATE TABLE IF NOT EXISTS workflow.sla_instances (
  sla_instance_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  policy_version text NOT NULL,
  state text NOT NULL CHECK (state IN ('ON_TRACK','DUE_SOON','OVERDUE','BREACHED','PAUSED','COMPLETED')),
  started_at timestamptz NOT NULL,
  due_at timestamptz NOT NULL,
  paused_at timestamptz,
  resume_at timestamptz,
  completed_at timestamptz,
  escalation_level integer NOT NULL DEFAULT 0 CHECK (escalation_level >= 0),
  version bigint NOT NULL DEFAULT 1,
  UNIQUE (tenant_id, intake_id)
);

CREATE TABLE IF NOT EXISTS expansion.promotion_decisions (
  promotion_decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  intake_id uuid NOT NULL REFERENCES intake.intakes(intake_id),
  listing_id uuid NOT NULL REFERENCES expansion.listings(listing_id),
  property_id uuid NOT NULL REFERENCES identity.properties(property_id),
  target_format_code text NOT NULL,
  status text NOT NULL CHECK (status IN ('REQUESTED','VALIDATING','REJECTED','APPROVED','CANDIDATE_CREATING','CANDIDATE_CREATED','SCORE_QUEUED','COMPLETED','FAILED','SCORE_FAILED')),
  proposer_subject_id uuid NOT NULL,
  reviewer_subject_id uuid,
  reason text NOT NULL,
  risk_acknowledged boolean NOT NULL DEFAULT false,
  gate_snapshot jsonb NOT NULL,
  candidate_site_id uuid,
  site_score_job_id uuid,
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  reviewed_at timestamptz,
  executed_at timestamptz,
  CHECK (reviewer_subject_id IS NULL OR reviewer_subject_id <> proposer_subject_id)
);

CREATE TABLE IF NOT EXISTS expansion.candidate_sites (
  candidate_site_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  property_id uuid NOT NULL REFERENCES identity.properties(property_id),
  source_listing_id uuid NOT NULL REFERENCES expansion.listings(listing_id),
  promotion_decision_id uuid NOT NULL REFERENCES expansion.promotion_decisions(promotion_decision_id),
  target_format_code text NOT NULL,
  status text NOT NULL CHECK (status IN ('NEW','SCREENED','SCORING','SCORED','VISITED','REJECTED','APPROVED','OPENED','SCORING_FAILED')),
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_candidate_property_format
  ON expansion.candidate_sites (tenant_id, property_id, target_format_code)
  WHERE status NOT IN ('REJECTED','OPENED');
ALTER TABLE expansion.promotion_decisions
  ADD CONSTRAINT fk_promotion_candidate
  FOREIGN KEY (candidate_site_id) REFERENCES expansion.candidate_sites(candidate_site_id)
  DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE IF NOT EXISTS workflow.idempotency_records (
  idempotency_record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  actor_subject_id uuid NOT NULL,
  operation text NOT NULL,
  idempotency_key text NOT NULL,
  request_sha256 char(64) NOT NULL,
  response_status integer,
  response_headers jsonb,
  response_body jsonb,
  resource_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  UNIQUE (tenant_id, actor_subject_id, operation, idempotency_key)
);

CREATE TABLE IF NOT EXISTS workflow.jobs (
  job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  job_type text NOT NULL,
  aggregate_type text NOT NULL,
  aggregate_id uuid NOT NULL,
  status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED','RETRYING','DEAD_LETTER')),
  checkpoint text NOT NULL,
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  max_attempts integer NOT NULL CHECK (max_attempts > 0),
  fence_token bigint NOT NULL DEFAULT 1,
  heartbeat_at timestamptz,
  lease_expires_at timestamptz,
  next_attempt_at timestamptz,
  timeout_at timestamptz NOT NULL,
  cancellation_requested_at timestamptz,
  payload jsonb NOT NULL,
  result jsonb,
  last_error jsonb,
  correlation_id uuid NOT NULL,
  idempotency_key text,
  version bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, job_type, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_jobs_claim ON workflow.jobs (status, next_attempt_at, created_at);

CREATE TABLE IF NOT EXISTS workflow.outbox_events (
  outbox_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  event_id uuid NOT NULL,
  event_type text NOT NULL,
  event_version integer NOT NULL CHECK (event_version > 0),
  aggregate_type text NOT NULL,
  aggregate_id uuid NOT NULL,
  aggregate_version bigint NOT NULL,
  partition_key text NOT NULL,
  payload jsonb NOT NULL,
  sensitive_fields text[] NOT NULL DEFAULT '{}',
  correlation_id uuid NOT NULL,
  causation_id uuid,
  occurred_at timestamptz NOT NULL,
  published_at timestamptz,
  publish_attempts integer NOT NULL DEFAULT 0,
  last_error jsonb,
  retention_until timestamptz NOT NULL,
  UNIQUE (tenant_id, event_id),
  UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version, event_type)
);
CREATE INDEX IF NOT EXISTS ix_outbox_unpublished ON workflow.outbox_events (occurred_at) WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS audit.legal_holds (
  legal_hold_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  subject_type text NOT NULL,
  subject_id uuid NOT NULL,
  reason text NOT NULL,
  placed_by uuid NOT NULL,
  approved_by uuid NOT NULL,
  placed_at timestamptz NOT NULL DEFAULT now(),
  released_by uuid,
  released_at timestamptz,
  version bigint NOT NULL DEFAULT 1,
  CHECK (placed_by <> approved_by)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_legal_hold
  ON audit.legal_holds (tenant_id, subject_type, subject_id)
  WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS audit.audit_events (
  audit_event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  sequence_no bigint NOT NULL,
  event_type text NOT NULL,
  actor_subject_id uuid,
  service_principal text,
  actor_role text,
  action text NOT NULL,
  resource_type text NOT NULL,
  resource_id uuid NOT NULL,
  decision_id uuid,
  source_snapshot_id uuid,
  parser_run_id uuid,
  before_value jsonb,
  after_value jsonb,
  reason text,
  result text NOT NULL CHECK (result IN ('ALLOWED','DENIED','SUCCEEDED','FAILED','MASKED')),
  reason_code text,
  correlation_id uuid NOT NULL,
  causation_id uuid,
  previous_event_sha256 char(64),
  event_sha256 char(64) NOT NULL,
  worm_object_uri text,
  occurred_at timestamptz NOT NULL,
  retained_until timestamptz NOT NULL,
  legal_hold boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, sequence_no),
  UNIQUE (tenant_id, event_sha256)
);
CREATE INDEX IF NOT EXISTS ix_audit_resource ON audit.audit_events (tenant_id, resource_type, resource_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS audit.export_manifests (
  export_manifest_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL,
  requested_by uuid NOT NULL,
  approved_by uuid NOT NULL,
  purpose text NOT NULL,
  scope jsonb NOT NULL,
  field_mask jsonb NOT NULL,
  source_snapshot_ids uuid[] NOT NULL DEFAULT '{}',
  audit_event_ids uuid[] NOT NULL DEFAULT '{}',
  object_uri text NOT NULL,
  content_sha256 char(64) NOT NULL,
  watermark text NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  CHECK (requested_by <> approved_by)
);

-- Row-level security is mandatory in production migrations. Application queries
-- must additionally use backend ABAC; RLS is defense in depth, not a substitute.
ALTER TABLE intake.intakes ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake.source_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE expansion.listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE expansion.candidate_sites ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity.properties ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow.assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit.audit_events ENABLE ROW LEVEL SECURITY;

-- Production migrations must create tenant policies using the request-scoped
-- `app.tenant_id` setting, e.g. tenant_id = current_setting('app.tenant_id')::uuid.
-- Migration CI must reject any business table without tenant_id, version,
-- authoritative timestamps, and the required tenant-inclusive uniqueness.
