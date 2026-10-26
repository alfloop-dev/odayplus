-- Expand the existing workflow tables for the recorded bootstrap schedule.
-- Old rows remain unknown; this migration supplies no historical policy/time.
-- Engineering schema only: no source activation or rollout/cutover approval.
BEGIN;

DO $workflow_schedule$
DECLARE
  target regclass := to_regclass('workflow.sla_instances');
  assignment_table regclass := to_regclass('workflow.assignments');
  present integer;
  column_spec record;
  constraint_spec record;
BEGIN
  IF target IS NULL OR assignment_table IS NULL THEN
    RAISE EXCEPTION 'Original Assignment/SLA tables are required';
  END IF;
  SELECT count(*) INTO present FROM pg_attribute
    WHERE attrelid=target AND NOT attisdropped
      AND attname IN ('assignment_id','calendar_version','due_soon_at','breach_at');
  IF present NOT IN (0,4) THEN
    RAISE EXCEPTION 'Partial workflow schedule schema is incompatible';
  END IF;
  IF present=0 THEN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE
        (conrelid=assignment_table AND conname='uq_assignment_intake_tenant') OR
        (conrelid=target AND conname='fk_sla_assignment_intake_tenant')) THEN
      RAISE EXCEPTION 'Partial workflow schedule constraints are incompatible';
    END IF;
    ALTER TABLE workflow.sla_instances
      ADD COLUMN assignment_id uuid,
      ADD COLUMN calendar_version text,
      ADD COLUMN due_soon_at timestamptz,
      ADD COLUMN breach_at timestamptz;
    ALTER TABLE workflow.assignments ADD CONSTRAINT uq_assignment_intake_tenant
      UNIQUE (tenant_id,intake_id,assignment_id);
    ALTER TABLE workflow.sla_instances ADD CONSTRAINT fk_sla_assignment_intake_tenant
      FOREIGN KEY (tenant_id,intake_id,assignment_id)
      REFERENCES workflow.assignments (tenant_id,intake_id,assignment_id);
  END IF;
  FOR column_spec IN SELECT * FROM (VALUES
      ('assignment_id','uuid'),('calendar_version','text'),
      ('due_soon_at','timestamptz'),('breach_at','timestamptz')
    ) AS expected(name,type_name)
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=target
        AND attname=column_spec.name AND atttypid=column_spec.type_name::regtype
        AND NOT attisdropped AND NOT attnotnull AND NOT atthasdef
        AND attgenerated='' AND attidentity='') THEN
      RAISE EXCEPTION 'Workflow schedule column is incompatible: %', column_spec.name;
    END IF;
  END LOOP;
  FOR constraint_spec IN SELECT * FROM (VALUES
      (assignment_table,'uq_assignment_intake_tenant','UNIQUE (tenant_id, intake_id, assignment_id)'),
      (target,'fk_sla_assignment_intake_tenant',
       'FOREIGN KEY (tenant_id, intake_id, assignment_id) REFERENCES workflow.assignments(tenant_id, intake_id, assignment_id)')
    ) AS expected(relation,name,definition)
  LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid=constraint_spec.relation
        AND conname=constraint_spec.name AND convalidated AND NOT condeferrable AND NOT condeferred
        AND regexp_replace(pg_get_constraintdef(oid),'\s','','g') =
            regexp_replace(constraint_spec.definition,'\s','','g')) THEN
      RAISE EXCEPTION 'Workflow schedule constraint is incompatible: %', constraint_spec.name;
    END IF;
  END LOOP;
END
$workflow_schedule$;

COMMIT;
