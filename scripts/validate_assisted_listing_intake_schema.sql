\set ON_ERROR_STOP on

-- Executed after all schema artifacts are applied to PostgreSQL 16.
-- Raises an exception on any tenant-bearing table without FORCE RLS and a
-- fail-closed tenant policy, or any tenant-scoped FK without a composite
-- tenant-qualified counterpart.

DO $validate_rls$
DECLARE
  failures text;
BEGIN
  SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ' ORDER BY n.nspname, c.relname)
    INTO failures
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind = 'r'
    AND n.nspname IN ('intake','identity','expansion','workflow','audit')
    AND EXISTS (
      SELECT 1 FROM pg_attribute a
      WHERE a.attrelid = c.oid AND a.attname = 'tenant_id' AND NOT a.attisdropped
    )
    AND (
      NOT c.relrowsecurity
      OR NOT c.relforcerowsecurity
      OR NOT EXISTS (
        SELECT 1
        FROM pg_policy p
        WHERE p.polrelid = c.oid
          AND p.polname = 'tenant_isolation'
          AND pg_get_expr(p.polqual, p.polrelid) LIKE '%app.tenant_id%'
          AND pg_get_expr(p.polwithcheck, p.polrelid) LIKE '%app.tenant_id%'
      )
    );

  IF failures IS NOT NULL THEN
    RAISE EXCEPTION 'RLS_POLICY_INCOMPLETE: %', failures;
  END IF;
END
$validate_rls$;

DO $validate_tenant_fks$
DECLARE
  failures text;
BEGIN
  WITH fks AS (
    SELECT con.oid,
           con.conname,
           con.conrelid,
           con.confrelid,
           con.conkey,
           con.confkey,
           child_ns.nspname AS child_schema,
           child.relname AS child_table,
           parent_ns.nspname AS parent_schema,
           parent.relname AS parent_table,
           child_tenant.attnum AS child_tenant_attnum,
           parent_tenant.attnum AS parent_tenant_attnum
    FROM pg_constraint con
    JOIN pg_class child ON child.oid = con.conrelid
    JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
    JOIN pg_class parent ON parent.oid = con.confrelid
    JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
    JOIN pg_attribute child_tenant
      ON child_tenant.attrelid = child.oid
     AND child_tenant.attname = 'tenant_id'
     AND NOT child_tenant.attisdropped
    JOIN pg_attribute parent_tenant
      ON parent_tenant.attrelid = parent.oid
     AND parent_tenant.attname = 'tenant_id'
     AND NOT parent_tenant.attisdropped
    WHERE con.contype = 'f'
      AND child_ns.nspname IN ('intake','identity','expansion','workflow','audit')
      AND parent_ns.nspname IN ('intake','identity','expansion','workflow','audit')
  ), unqualified AS (
    SELECT *
    FROM fks
    WHERE NOT (
      child_tenant_attnum = ANY(conkey)
      AND parent_tenant_attnum = ANY(confkey)
    )
  ), uncovered AS (
    SELECT u.*
    FROM unqualified u
    WHERE NOT EXISTS (
      SELECT 1
      FROM fks q
      WHERE q.conrelid = u.conrelid
        AND q.confrelid = u.confrelid
        AND q.child_tenant_attnum = ANY(q.conkey)
        AND q.parent_tenant_attnum = ANY(q.confkey)
        AND q.conkey @> u.conkey
        AND q.confkey @> u.confkey
    )
  )
  SELECT string_agg(
           format('%I.%I.%I -> %I.%I', child_schema, child_table, conname, parent_schema, parent_table),
           ', ' ORDER BY child_schema, child_table, conname
         )
    INTO failures
  FROM uncovered;

  IF failures IS NOT NULL THEN
    RAISE EXCEPTION 'TENANT_QUALIFIED_FK_MISSING: %', failures;
  END IF;
END
$validate_tenant_fks$;

DO $validate_lineage_constraints$
DECLARE
  required_constraints text[] := ARRAY[
    'fk_intake_resolved_listing_tenant',
    'fk_transition_snapshot_tenant',
    'fk_transition_match_case_tenant',
    'fk_transition_job_tenant',
    'fk_property_redirect_pointer_tenant',
    'fk_listing_current_revision_tenant',
    'fk_listing_current_observation_tenant',
    'fk_revision_supersedes_tenant',
    'fk_edge_supersedes_tenant',
    'fk_edge_decision_tenant',
    'fk_redirect_decision_tenant',
    'fk_match_decision_snapshot_tenant',
    'fk_match_decision_parser_tenant',
    'fk_match_decision_supersedes_tenant',
    'fk_match_decision_reversal_tenant',
    'fk_correction_snapshot_tenant',
    'fk_correction_parser_tenant',
    'fk_correction_supersedes_tenant',
    'fk_correction_reversal_tenant',
    'fk_promotion_candidate_tenant',
    'fk_audit_snapshot_tenant',
    'fk_audit_parser_tenant'
  ];
  missing text;
BEGIN
  SELECT string_agg(name, ', ' ORDER BY name)
    INTO missing
  FROM unnest(required_constraints) AS name
  WHERE NOT EXISTS (
    SELECT 1 FROM pg_constraint c WHERE c.conname = name AND c.contype = 'f'
  );

  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'LINEAGE_CONSTRAINT_MISSING: %', missing;
  END IF;
END
$validate_lineage_constraints$;

SELECT 'PASS: schema, tenant RLS policies, and tenant-qualified lineage constraints validated' AS result;
