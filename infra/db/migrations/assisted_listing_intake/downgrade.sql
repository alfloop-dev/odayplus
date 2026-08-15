-- ODay Plus Assisted Listing Intake downgrade boundary
-- Reverses the ordered upgrade (001..004) on a greenfield / staging intake
-- database. This is a STRUCTURAL boundary only: it drops the intake bounded
-- context relations so a clean install can be replayed. It intentionally does
-- NOT reverse committed business decisions (see the migration rollout runbook
-- section 5.2 "Mechanism"); production rollback disables tenant/source flags and
-- keeps target data read-only rather than dropping tables.
--
-- Shared schemas (expansion, workflow, audit) are also used by the canonical
-- platform baseline, so this script removes only the intake-context tables it
-- created and never drops those schema namespaces. The intake-exclusive schemas
-- (intake, identity) are dropped once emptied.

BEGIN;

-- expansion (intake-context tables only; schema left intact for canonical use)
DROP TABLE IF EXISTS expansion.candidate_sites CASCADE;
DROP TABLE IF EXISTS expansion.promotion_decisions CASCADE;
DROP TABLE IF EXISTS expansion.listing_observations CASCADE;
DROP TABLE IF EXISTS expansion.listing_revisions CASCADE;
DROP TABLE IF EXISTS expansion.listings CASCADE;

-- workflow (intake-context tables only; schema left intact for canonical use)
DROP TABLE IF EXISTS workflow.reconciliation_findings CASCADE;
DROP TABLE IF EXISTS workflow.outbox_events CASCADE;
DROP TABLE IF EXISTS workflow.jobs CASCADE;
DROP TABLE IF EXISTS workflow.idempotency_records CASCADE;
DROP TABLE IF EXISTS workflow.sla_pause_intervals CASCADE;
DROP TABLE IF EXISTS workflow.sla_transitions CASCADE;
DROP TABLE IF EXISTS workflow.assignment_transitions CASCADE;
DROP TABLE IF EXISTS workflow.sla_instances CASCADE;
DROP TABLE IF EXISTS workflow.assignments CASCADE;

-- audit (intake-context tables only; schema left intact for canonical use)
DROP TABLE IF EXISTS audit.export_manifests CASCADE;
DROP TABLE IF EXISTS audit.audit_events CASCADE;
DROP TABLE IF EXISTS audit.legal_holds CASCADE;

-- identity (intake-exclusive schema)
DROP TABLE IF EXISTS identity.match_decisions CASCADE;
DROP TABLE IF EXISTS identity.match_candidates CASCADE;
DROP TABLE IF EXISTS identity.match_cases CASCADE;
DROP TABLE IF EXISTS identity.property_redirects CASCADE;
DROP TABLE IF EXISTS identity.source_identity_edges CASCADE;
DROP TABLE IF EXISTS identity.properties CASCADE;

-- intake (intake-exclusive schema)
DROP TABLE IF EXISTS intake.human_corrections CASCADE;
DROP TABLE IF EXISTS intake.parser_runs CASCADE;
DROP TABLE IF EXISTS intake.source_snapshots CASCADE;
DROP TABLE IF EXISTS intake.intake_stage_transitions CASCADE;
DROP TABLE IF EXISTS intake.intakes CASCADE;
DROP TABLE IF EXISTS intake.parser_releases CASCADE;
DROP TABLE IF EXISTS intake.source_registry CASCADE;

DROP SCHEMA IF EXISTS identity CASCADE;
DROP SCHEMA IF EXISTS intake CASCADE;

COMMIT;
