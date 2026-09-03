-- 000017_intervention_adjust_lineage.sql
-- ODP-INTV006-ADJUST-WORKFLOW-001: Add lineage, predecessor/replacement relations, and adjustment metadata for interventions
--
-- When an active intervention is adjusted, operational practice stops the
-- original intervention and opens a replacement intervention to preserve
-- clean observation windows and causal attribution. This migration adds
-- predecessor_id, replacement_id, and adjustment_json to operations.interventions.

ALTER TABLE operations.interventions
    ADD COLUMN IF NOT EXISTS predecessor_id UUID REFERENCES operations.interventions(intervention_id),
    ADD COLUMN IF NOT EXISTS replacement_id UUID REFERENCES operations.interventions(intervention_id),
    ADD COLUMN IF NOT EXISTS adjustment_json JSONB;

CREATE INDEX IF NOT EXISTS idx_interventions_predecessor_id ON operations.interventions(predecessor_id);
CREATE INDEX IF NOT EXISTS idx_interventions_replacement_id ON operations.interventions(replacement_id);

COMMENT ON COLUMN operations.interventions.predecessor_id IS
    'ODP-FR-INTV-006: Predecessor intervention ID if this intervention was created as an adjustment / replacement.';

COMMENT ON COLUMN operations.interventions.replacement_id IS
    'ODP-FR-INTV-006: Replacement intervention ID if this intervention was stopped and replaced by an adjustment.';

COMMENT ON COLUMN operations.interventions.adjustment_json IS
    'ODP-FR-INTV-006: Durable adjustment audit payload containing reason, actor, policy_version, timestamp, and rollback plan.';
