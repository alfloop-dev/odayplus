-- Expand-only audit-retention vocabulary. No business records are seeded.
-- Expand the existing registry vocabulary without seeding business values.
-- Intake policy and calendar versions retain the existing tenant-qualified
-- identity, approval, effective-window and immutability constraints.
ALTER TABLE workflow.decision_policies
  DROP CONSTRAINT chk_decision_policy_kind;
ALTER TABLE workflow.decision_policies
  ADD CONSTRAINT chk_decision_policy_kind CHECK (
    policy_kind IN (
      'forecast_alert', 'heatzone_merge', 'heatzone_absorption',
      'sitescore_recommendation', 'price_exploration', 'netplan_action',
      'model_performance_drift', 'intake_workflow', 'intake_business_calendar', 'intake_audit_retention'
    )
  );

-- Protect audit retention together with the existing Intake kinds. Historical policy/calendar bindings may be
-- closed once for supersession; their meaning cannot change under the same ID.
CREATE OR REPLACE FUNCTION workflow.guard_intake_policy_version()
RETURNS trigger LANGUAGE plpgsql AS $$
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
$$;
