-- ODay Plus Assisted Listing Intake tenant isolation and lineage hardening
-- Contract patch: 0004 / effective design version 0.2.1
-- Apply after schema baseline, 0002 consistency patch, and 0003 promotion patch.
-- This patch is implementation-binding: tenant-bearing tables are FORCE RLS and
-- every cross-table lineage relation receives a tenant-equal composite FK.

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Composite keys for lineage targets not covered by patch 0002
-- ---------------------------------------------------------------------------

ALTER TABLE expansion.listing_observations
  ADD CONSTRAINT uq_listing_observations_tenant_id
  UNIQUE (tenant_id, listing_observation_id);
ALTER TABLE identity.source_identity_edges
  ADD CONSTRAINT uq_source_identity_edges_tenant_id
  UNIQUE (tenant_id, edge_id);
ALTER TABLE identity.property_redirects
  ADD CONSTRAINT uq_property_redirects_tenant_id
  UNIQUE (tenant_id, redirect_id);
ALTER TABLE identity.match_candidates
  ADD CONSTRAINT uq_match_candidates_tenant_id
  UNIQUE (tenant_id, match_candidate_id);
ALTER TABLE intake.human_corrections
  ADD CONSTRAINT uq_human_corrections_tenant_id
  UNIQUE (tenant_id, correction_id);
ALTER TABLE workflow.jobs
  ADD CONSTRAINT uq_jobs_tenant_id
  UNIQUE (tenant_id, job_id);

-- ---------------------------------------------------------------------------
-- 2. Tenant-equal lineage and current-pointer foreign keys
-- ---------------------------------------------------------------------------

ALTER TABLE intake.intakes
  ADD CONSTRAINT fk_intake_resolved_listing_tenant
  FOREIGN KEY (tenant_id, resolved_listing_id)
  REFERENCES expansion.listings (tenant_id, listing_id) NOT VALID;

ALTER TABLE intake.intake_stage_transitions
  ADD CONSTRAINT fk_transition_snapshot_tenant
  FOREIGN KEY (tenant_id, source_snapshot_id)
  REFERENCES intake.source_snapshots (tenant_id, source_snapshot_id) NOT VALID,
  ADD CONSTRAINT fk_transition_match_case_tenant
  FOREIGN KEY (tenant_id, match_case_id)
  REFERENCES identity.match_cases (tenant_id, match_case_id) NOT VALID,
  ADD CONSTRAINT fk_transition_job_tenant
  FOREIGN KEY (tenant_id, job_id)
  REFERENCES workflow.jobs (tenant_id, job_id) NOT VALID;

ALTER TABLE identity.properties
  ADD CONSTRAINT fk_property_redirect_pointer_tenant
  FOREIGN KEY (tenant_id, current_redirect_property_id)
  REFERENCES identity.properties (tenant_id, property_id) NOT VALID;

ALTER TABLE expansion.listings
  ADD CONSTRAINT fk_listing_current_revision_tenant
  FOREIGN KEY (tenant_id, current_revision_id)
  REFERENCES expansion.listing_revisions (tenant_id, listing_revision_id)
  DEFERRABLE INITIALLY DEFERRED NOT VALID,
  ADD CONSTRAINT fk_listing_current_observation_tenant
  FOREIGN KEY (tenant_id, current_observation_id)
  REFERENCES expansion.listing_observations (tenant_id, listing_observation_id)
  DEFERRABLE INITIALLY DEFERRED NOT VALID;

ALTER TABLE expansion.listing_revisions
  ADD CONSTRAINT fk_revision_supersedes_tenant
  FOREIGN KEY (tenant_id, supersedes_revision_id)
  REFERENCES expansion.listing_revisions (tenant_id, listing_revision_id) NOT VALID;

ALTER TABLE identity.source_identity_edges
  ADD CONSTRAINT fk_edge_supersedes_tenant
  FOREIGN KEY (tenant_id, supersedes_edge_id)
  REFERENCES identity.source_identity_edges (tenant_id, edge_id) NOT VALID,
  ADD CONSTRAINT fk_edge_decision_tenant
  FOREIGN KEY (tenant_id, decision_id)
  REFERENCES identity.match_decisions (tenant_id, match_decision_id) NOT VALID;

ALTER TABLE identity.property_redirects
  ADD CONSTRAINT fk_redirect_decision_tenant
  FOREIGN KEY (tenant_id, decision_id)
  REFERENCES identity.match_decisions (tenant_id, match_decision_id) NOT VALID;

ALTER TABLE identity.match_decisions
  ADD CONSTRAINT fk_match_decision_snapshot_tenant
  FOREIGN KEY (tenant_id, source_snapshot_id)
  REFERENCES intake.source_snapshots (tenant_id, source_snapshot_id) NOT VALID,
  ADD CONSTRAINT fk_match_decision_parser_tenant
  FOREIGN KEY (tenant_id, parser_run_id)
  REFERENCES intake.parser_runs (tenant_id, parser_run_id) NOT VALID,
  ADD CONSTRAINT fk_match_decision_supersedes_tenant
  FOREIGN KEY (tenant_id, supersedes_decision_id)
  REFERENCES identity.match_decisions (tenant_id, match_decision_id) NOT VALID,
  ADD CONSTRAINT fk_match_decision_reversal_tenant
  FOREIGN KEY (tenant_id, reversal_of_decision_id)
  REFERENCES identity.match_decisions (tenant_id, match_decision_id) NOT VALID;

ALTER TABLE intake.human_corrections
  ADD CONSTRAINT fk_correction_snapshot_tenant
  FOREIGN KEY (tenant_id, source_snapshot_id)
  REFERENCES intake.source_snapshots (tenant_id, source_snapshot_id) NOT VALID,
  ADD CONSTRAINT fk_correction_parser_tenant
  FOREIGN KEY (tenant_id, parser_run_id)
  REFERENCES intake.parser_runs (tenant_id, parser_run_id) NOT VALID,
  ADD CONSTRAINT fk_correction_supersedes_tenant
  FOREIGN KEY (tenant_id, supersedes_correction_id)
  REFERENCES intake.human_corrections (tenant_id, correction_id) NOT VALID,
  ADD CONSTRAINT fk_correction_reversal_tenant
  FOREIGN KEY (tenant_id, reversal_of_correction_id)
  REFERENCES intake.human_corrections (tenant_id, correction_id) NOT VALID;

ALTER TABLE expansion.promotion_decisions
  ADD CONSTRAINT fk_promotion_candidate_tenant
  FOREIGN KEY (tenant_id, candidate_site_id)
  REFERENCES expansion.candidate_sites (tenant_id, candidate_site_id)
  DEFERRABLE INITIALLY DEFERRED NOT VALID;

ALTER TABLE audit.audit_events
  ADD CONSTRAINT fk_audit_snapshot_tenant
  FOREIGN KEY (tenant_id, source_snapshot_id)
  REFERENCES intake.source_snapshots (tenant_id, source_snapshot_id) NOT VALID,
  ADD CONSTRAINT fk_audit_parser_tenant
  FOREIGN KEY (tenant_id, parser_run_id)
  REFERENCES intake.parser_runs (tenant_id, parser_run_id) NOT VALID;

-- audit.audit_events.decision_id intentionally remains a polymorphic reference.
-- decision resource type and ID are integrity-checked by the audit writer and
-- evidence verifier because the target can be identity or promotion decisions.
COMMENT ON COLUMN audit.audit_events.decision_id IS
  'Polymorphic decision reference; resource_type identifies the authoritative decision table. Verified by audit writer and evidence export validation.';

-- ---------------------------------------------------------------------------
-- 3. Enforced fail-closed RLS policies on every tenant-bearing contract table
-- ---------------------------------------------------------------------------

DO $tenant_rls$
DECLARE
  target regclass;
  tenant_tables regclass[] := ARRAY[
    'intake.intakes'::regclass,
    'intake.intake_stage_transitions'::regclass,
    'intake.source_snapshots'::regclass,
    'intake.parser_runs'::regclass,
    'identity.properties'::regclass,
    'expansion.listings'::regclass,
    'expansion.listing_revisions'::regclass,
    'expansion.listing_observations'::regclass,
    'identity.source_identity_edges'::regclass,
    'identity.property_redirects'::regclass,
    'identity.match_cases'::regclass,
    'identity.match_candidates'::regclass,
    'identity.match_decisions'::regclass,
    'intake.human_corrections'::regclass,
    'workflow.assignments'::regclass,
    'workflow.assignment_transitions'::regclass,
    'workflow.sla_instances'::regclass,
    'workflow.sla_transitions'::regclass,
    'workflow.sla_pause_intervals'::regclass,
    'expansion.promotion_decisions'::regclass,
    'expansion.candidate_sites'::regclass,
    'workflow.idempotency_records'::regclass,
    'workflow.jobs'::regclass,
    'workflow.outbox_events'::regclass,
    'workflow.reconciliation_findings'::regclass,
    'audit.legal_holds'::regclass,
    'audit.audit_events'::regclass,
    'audit.export_manifests'::regclass
  ];
BEGIN
  FOREACH target IN ARRAY tenant_tables LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', target);
    EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', target);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', target);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %s '
      'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid) '
      'WITH CHECK (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      target
    );
  END LOOP;
END
$tenant_rls$;

COMMIT;

-- Production migration gate:
-- 1. Apply all four schema artifacts in a transaction on an empty database.
-- 2. Reconcile data and validate every NOT VALID constraint before cutover.
-- 3. Execute scripts/validate_assisted_listing_intake_schema.sql.
-- 4. Application connections must SET LOCAL app.tenant_id inside every request
--    transaction. Missing/empty tenant context fails closed under the policy.
