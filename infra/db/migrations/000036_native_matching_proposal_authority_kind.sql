-- No-data, expand-only vocabulary extension; this issues no runtime authority.
-- No policies, approvals, identities, grants, issuer roles or defaults are seeded.
-- One DO statement makes preflight and both DDL changes atomic, including when
-- a driver executes this file in autocommit mode. Do not repair partial installs.
DO $matching_policy_kind$
DECLARE
  registry regclass := to_regclass('workflow.decision_policies');
  guard_oid oid := to_regprocedure('workflow.guard_intake_policy_version()');
  old_kinds constant text[] := ARRAY[
    'forecast_alert', 'heatzone_merge', 'heatzone_absorption',
    'sitescore_recommendation', 'price_exploration', 'netplan_action',
    'model_performance_drift', 'intake_workflow', 'intake_business_calendar',
    'intake_audit_retention'
  ];
  old_guard constant text := $prior_guard$
BEGIN
  IF OLD.policy_kind NOT IN ('intake_workflow', 'intake_business_calendar', 'intake_audit_retention') THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    IF NEW.policy_kind NOT IN ('intake_workflow', 'intake_business_calendar', 'intake_audit_retention') THEN RETURN NEW; END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Intake policy/calendar versions must be retained for history'
      USING ERRCODE = '23514', CONSTRAINT = 'chk_intake_policy_version_immutable';
  END IF;
  IF ROW(OLD.policy_version_id, OLD.policy_label, OLD.policy_id, OLD.policy_version,
         OLD.policy_kind, OLD.tenant_id, OLD.effective_from, OLD.owner_role,
         OLD.approved_by, OLD.approved_at, OLD.input_contract, OLD.output_contract,
         OLD.change_reason, OLD.rollback_policy_version, OLD.parameters, OLD.declared_inputs)
     IS DISTINCT FROM
     ROW(NEW.policy_version_id, NEW.policy_label, NEW.policy_id, NEW.policy_version,
         NEW.policy_kind, NEW.tenant_id, NEW.effective_from, NEW.owner_role,
         NEW.approved_by, NEW.approved_at, NEW.input_contract, NEW.output_contract,
         NEW.change_reason, NEW.rollback_policy_version, NEW.parameters, NEW.declared_inputs)
     OR (OLD.effective_to IS NOT NULL AND OLD.effective_to IS DISTINCT FROM NEW.effective_to) THEN
    RAISE EXCEPTION 'Create a new Intake policy/calendar version instead of rewriting history'
      USING ERRCODE = '23514', CONSTRAINT = 'chk_intake_policy_version_immutable';
  END IF;
  RETURN NEW;
END;
$prior_guard$;
  kind_expression text;
  expected_expression text;
BEGIN
  -- Surviving registry functions without its table indicate a partial install.
  IF registry IS NULL THEN
    IF EXISTS (
      SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'workflow' AND p.proname IN (
        'guard_intake_policy_version', 'seed_forecast_alert_policy',
        'seed_forecast_alert_policy_on_tenant', 'seed_model_performance_drift_policy',
        'seed_model_performance_drift_policy_on_tenant',
        'seed_netplan_disclosure_policy', 'seed_netplan_disclosure_policy_on_tenant',
        'seed_heatzone_merge_policy', 'seed_heatzone_absorption_policy',
        'on_tenant_insert_seed_heatzone_merge_policy'
      )
    ) THEN
      RAISE EXCEPTION 'The decision-policy registry is only partially installed or incompatible'
        USING ERRCODE = '55000';
    END IF;
    RETURN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE oid = registry AND relkind = 'r') THEN
    RAISE EXCEPTION 'The decision-policy registry is only partially installed or incompatible'
      USING ERRCODE = '55000';
  END IF;
  -- Keep the inspected table/constraint/trigger definitions stable until commit.
  -- No application rows are written; validating the expanded check may scan them.
  LOCK TABLE workflow.decision_policies IN ACCESS EXCLUSIVE MODE;

  -- The actual 000014 contract has eighteen columns, including created_at.
  -- Names alone would accept a nullable kind or unusable JSON/array fields.
  IF (SELECT count(*) FROM pg_attribute
      WHERE attrelid = registry AND attnum > 0 AND NOT attisdropped) <> 18
     OR EXISTS (
       SELECT 1 FROM (VALUES
         ('policy_version_id', 'character varying(100)', true),
         ('policy_label', 'character varying(100)', true),
         ('policy_id', 'character varying(100)', true),
         ('policy_version', 'character varying(50)', true),
         ('policy_kind', 'character varying(100)', true),
         ('tenant_id', 'uuid', true),
         ('effective_from', 'timestamp with time zone', true),
         ('effective_to', 'timestamp with time zone', false),
         ('owner_role', 'character varying(100)', true),
         ('approved_by', 'character varying(255)', true),
         ('approved_at', 'timestamp with time zone', true),
         ('input_contract', 'character varying(100)', true),
         ('output_contract', 'character varying(100)', true),
         ('change_reason', 'text', true),
         ('rollback_policy_version', 'character varying(100)', false),
         ('parameters', 'jsonb', true),
         ('declared_inputs', 'text[]', true),
         ('created_at', 'timestamp with time zone', true)
       ) AS expected(name, sql_type, required)
       LEFT JOIN pg_attribute a ON a.attrelid = registry AND a.attname = expected.name
         AND a.attnum > 0 AND NOT a.attisdropped
       WHERE a.attname IS NULL
         OR format_type(a.atttypid, a.atttypmod) <> expected.sql_type
         OR a.attnotnull <> expected.required
         OR a.attgenerated <> '' OR a.attidentity <> ''
     )
     OR EXISTS (
       SELECT 1 FROM (VALUES
         ('decision_policies_pkey', 'p'),
         ('uq_decision_policy_tenant_id_version', 'u'),
         ('uq_decision_policy_version_tenant', 'u'),
         ('decision_policies_tenant_id_fkey', 'f'),
         ('fk_decision_policy_rollback_tenant', 'f'),
         ('chk_decision_policy_version_id_format', 'c'),
         ('chk_decision_policy_label', 'c'),
         ('chk_decision_policy_kind', 'c'),
         ('chk_decision_policy_window', 'c'),
         ('chk_decision_policy_reason', 'c'),
         ('chk_decision_policy_inputs', 'c'),
         ('chk_decision_policy_params', 'c')
       ) AS expected(name, kind)
       LEFT JOIN pg_constraint c ON c.conrelid = registry AND c.conname = expected.name
       WHERE c.oid IS NULL OR c.contype::text <> expected.kind OR NOT c.convalidated
     ) THEN
    RAISE EXCEPTION 'The decision-policy registry is only partially installed or incompatible'
      USING ERRCODE = '55000';
  END IF;

  -- Existing history/tenant constraints must still mean what 000014 installed.
  -- Normalize only the catalog's text casts, whitespace and parentheses for
  -- these fixed expressions; none of the inspected text is executed.
  IF EXISTS (
    SELECT 1 FROM (VALUES
      ('chk_decision_policy_version_id_format',
        $expr$policy_version_id=policy_label||':'||tenant_id$expr$),
      ('chk_decision_policy_label', $expr$policy_label<>''andposition':'inpolicy_label=0$expr$),
      ('chk_decision_policy_window', 'effective_toisnulloreffective_to>effective_from'),
      ('chk_decision_policy_reason', $expr$change_reason<>''$expr$),
      ('chk_decision_policy_inputs', 'cardinalitydeclared_inputs>0'),
      ('chk_decision_policy_params', $expr$jsonb_typeofparameters='object'$expr$)
    ) AS expected(name, expression)
    JOIN pg_constraint c ON c.conrelid = registry AND c.conname = expected.name
    WHERE regexp_replace(replace(lower(pg_get_expr(c.conbin, c.conrelid)), '::text', ''),
      '[[:space:]()]', '', 'g') IS DISTINCT FROM expected.expression
  ) OR EXISTS (
    SELECT 1 FROM (VALUES
      ('decision_policies_pkey', ARRAY['policy_version_id'], NULL::regclass, ARRAY[]::text[]),
      ('uq_decision_policy_tenant_id_version', ARRAY['tenant_id', 'policy_id', 'policy_version'],
        NULL::regclass, ARRAY[]::text[]),
      ('uq_decision_policy_version_tenant', ARRAY['policy_version_id', 'tenant_id'],
        NULL::regclass, ARRAY[]::text[]),
      ('decision_policies_tenant_id_fkey', ARRAY['tenant_id'],
        to_regclass('core.tenants'), ARRAY['tenant_id']),
      ('fk_decision_policy_rollback_tenant', ARRAY['rollback_policy_version', 'tenant_id'],
        registry, ARRAY['policy_version_id', 'tenant_id'])
    ) AS expected(name, columns, target, target_columns)
    JOIN pg_constraint c ON c.conrelid = registry AND c.conname = expected.name
    LEFT JOIN pg_index i ON i.indexrelid = c.conindid
    WHERE ARRAY(
      SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY AS k(num, ord)
      JOIN pg_attribute a ON a.attrelid = registry AND a.attnum = k.num ORDER BY k.ord
    ) IS DISTINCT FROM expected.columns
      OR c.condeferrable OR c.condeferred
      OR (c.contype = 'f' AND (
        c.confrelid IS DISTINCT FROM expected.target::oid
        OR c.confupdtype <> 'a' OR c.confdeltype <> 'a' OR c.confmatchtype <> 's'
        OR ARRAY(
          SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY AS k(num, ord)
          JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num ORDER BY k.ord
        ) IS DISTINCT FROM expected.target_columns
      ))
      OR i.indexrelid IS NULL OR NOT i.indisvalid OR NOT i.indisready OR NOT i.indisunique
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_index i
    WHERE i.indexrelid = to_regclass('workflow.idx_decision_policy_active')
      AND i.indrelid = registry AND i.indisunique AND i.indisvalid AND i.indisready
      AND i.indnkeyatts = 2 AND i.indnatts = 2 AND i.indexprs IS NULL
      AND pg_get_expr(i.indpred, i.indrelid) = '(effective_to IS NULL)'
      AND ARRAY(
        SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY AS k(num, ord)
        JOIN pg_attribute a ON a.attrelid = registry AND a.attnum = k.num ORDER BY k.ord
      ) = ARRAY['policy_id', 'tenant_id']
  ) THEN
    RAISE EXCEPTION 'The decision-policy registry is only partially installed or incompatible'
      USING ERRCODE = '55000';
  END IF;

  -- Accept only the canonical old vocabulary or this migration's own result.
  -- Compare catalog expressions; never execute untrusted constraint text.
  SELECT pg_get_expr(conbin, conrelid) INTO kind_expression FROM pg_constraint
    WHERE conrelid = registry AND conname = 'chk_decision_policy_kind';
  SELECT '((policy_kind)::text = ANY ((ARRAY[' ||
    string_agg(quote_literal(kind) || '::character varying', ', ' ORDER BY position) ||
    '])::text[]))' INTO expected_expression
    FROM unnest(old_kinds) WITH ORDINALITY AS kinds(kind, position);
  IF regexp_replace(kind_expression, '[[:space:]]', '', 'g') IS DISTINCT FROM
       regexp_replace(expected_expression, '[[:space:]]', '', 'g')
     AND regexp_replace(kind_expression, '[[:space:]]', '', 'g') IS DISTINCT FROM
       regexp_replace(replace(expected_expression, '])::text[]))',
         ', ''native_matching_proposal_authority''::character varying])::text[]))'),
         '[[:space:]]', '', 'g') THEN
    RAISE EXCEPTION 'The decision-policy registry is only partially installed or incompatible'
      USING ERRCODE = '55000';
  END IF;

  -- Require an unconditional, row-level BEFORE UPDATE OR DELETE trigger,
  -- enabled for normal writes and bound to the exact zero-argument guard.
  -- The only supported body change is addition to the two protected kind sets.
  IF NOT EXISTS (
    SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
    WHERE p.oid = guard_oid AND p.prorettype = 'trigger'::regtype
      AND p.pronargs = 0 AND p.prokind = 'f' AND NOT p.proretset
      AND NOT p.prosecdef AND p.proconfig IS NULL AND p.provolatile = 'v'
      AND l.lanname = 'plpgsql'
      AND (regexp_replace(p.prosrc, '[[:space:]]', '', 'g') =
             regexp_replace(old_guard, '[[:space:]]', '', 'g')
        OR regexp_replace(p.prosrc, '[[:space:]]', '', 'g') =
             regexp_replace(replace(old_guard, '''intake_audit_retention'')',
               '''intake_audit_retention'', ''native_matching_proposal_authority'')'),
               '[[:space:]]', '', 'g'))
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_trigger t
    WHERE t.tgrelid = registry AND t.tgname = 'trg_intake_policy_version_immutable'
      AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A')
      AND t.tgfoid = guard_oid AND t.tgtype = 27
      AND t.tgattr = ''::int2vector AND t.tgqual IS NULL
      AND t.tgnargs = 0 AND t.tgargs = ''::bytea AND t.tgconstraint = 0
      AND NOT t.tgdeferrable AND NOT t.tginitdeferred
  ) THEN
    RAISE EXCEPTION 'The decision-policy registry is only partially installed or incompatible'
      USING ERRCODE = '55000';
  END IF;

  ALTER TABLE workflow.decision_policies DROP CONSTRAINT chk_decision_policy_kind;
  ALTER TABLE workflow.decision_policies ADD CONSTRAINT chk_decision_policy_kind CHECK (
    policy_kind IN (
      'forecast_alert', 'heatzone_merge', 'heatzone_absorption',
      'sitescore_recommendation', 'price_exploration', 'netplan_action',
      'model_performance_drift', 'intake_workflow', 'intake_business_calendar',
      'intake_audit_retention', 'native_matching_proposal_authority'
    )
  );

  CREATE OR REPLACE FUNCTION workflow.guard_intake_policy_version()
  RETURNS trigger LANGUAGE plpgsql AS $matching_policy_guard$
BEGIN
  IF OLD.policy_kind NOT IN ('intake_workflow', 'intake_business_calendar', 'intake_audit_retention', 'native_matching_proposal_authority') THEN
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    IF NEW.policy_kind NOT IN ('intake_workflow', 'intake_business_calendar', 'intake_audit_retention', 'native_matching_proposal_authority') THEN RETURN NEW; END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Intake policy/calendar versions must be retained for history'
      USING ERRCODE = '23514', CONSTRAINT = 'chk_intake_policy_version_immutable';
  END IF;
  IF ROW(OLD.policy_version_id, OLD.policy_label, OLD.policy_id, OLD.policy_version,
         OLD.policy_kind, OLD.tenant_id, OLD.effective_from, OLD.owner_role,
         OLD.approved_by, OLD.approved_at, OLD.input_contract, OLD.output_contract,
         OLD.change_reason, OLD.rollback_policy_version, OLD.parameters, OLD.declared_inputs)
     IS DISTINCT FROM
     ROW(NEW.policy_version_id, NEW.policy_label, NEW.policy_id, NEW.policy_version,
         NEW.policy_kind, NEW.tenant_id, NEW.effective_from, NEW.owner_role,
         NEW.approved_by, NEW.approved_at, NEW.input_contract, NEW.output_contract,
         NEW.change_reason, NEW.rollback_policy_version, NEW.parameters, NEW.declared_inputs)
     OR (OLD.effective_to IS NOT NULL AND OLD.effective_to IS DISTINCT FROM NEW.effective_to) THEN
    RAISE EXCEPTION 'Create a new Intake policy/calendar version instead of rewriting history'
      USING ERRCODE = '23514', CONSTRAINT = 'chk_intake_policy_version_immutable';
  END IF;
  RETURN NEW;
END;
$matching_policy_guard$;
END;
$matching_policy_kind$;
