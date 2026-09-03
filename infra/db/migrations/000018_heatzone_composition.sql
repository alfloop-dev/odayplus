-- ODP-HZ006-MERGE-SPLIT-IMPLEMENTATION-001: HeatZone merge/split composition persistence (ODP-SD-AMD-001 §5.2)
--
-- Implements append-only storage for heat-zone composition, lineage tracking,
-- operator human override, and soft rollback.

CREATE TABLE IF NOT EXISTS expansion.heatzone_composition (
    composition_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zone_id             VARCHAR(100) NOT NULL,   -- Merged zone identifier, format 'MZ-{hash16}'
    tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id),
    member_cell_id      UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id),
    composition_kind    VARCHAR(50) NOT NULL,    -- 'MERGED', 'SPLIT_CHILD', 'ATOMIC'
    parent_zone_id      VARCHAR(100),            -- Points to parent zone if SPLIT_CHILD
    decided_by          VARCHAR(255) NOT NULL,   -- 'system' or operator identifier
    decided_at          TIMESTAMP WITH TIME ZONE NOT NULL,
    decision_policy_version_id VARCHAR(100) NOT NULL,
    override_reason     TEXT,                    -- Required when decided_by <> 'system'
    reverted_at         TIMESTAMP WITH TIME ZONE,-- Revert timestamp; NULL = currently active
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_composition_kind CHECK (
        composition_kind IN ('MERGED', 'SPLIT_CHILD', 'ATOMIC')
    ),
    -- SPLIT_CHILD must have parent_zone_id; others must not
    CONSTRAINT chk_composition_parent CHECK (
        (composition_kind =  'SPLIT_CHILD' AND parent_zone_id IS NOT NULL)
     OR (composition_kind <> 'SPLIT_CHILD' AND parent_zone_id IS NULL)
    ),
    -- Human decision requires override_reason; automated decision must not have override_reason
    CONSTRAINT chk_composition_override_reason CHECK (
        (decided_by =  'system' AND override_reason IS NULL)
     OR (decided_by <> 'system' AND override_reason IS NOT NULL AND override_reason <> '')
    ),
    CONSTRAINT chk_composition_revert_order CHECK (
        reverted_at IS NULL OR reverted_at >= decided_at
    ),
    -- Merged zone ID must start with MZ- and contain 16 hex chars
    CONSTRAINT chk_composition_zone_id_format CHECK (zone_id ~ '^MZ-[0-9a-f]{16}$'),
    -- Tenant-scoped decision policy binding
    CONSTRAINT fk_heatzone_composition_decision_policy
        FOREIGN KEY (decision_policy_version_id, tenant_id)
        REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
);

-- Active unique index: a member cell belongs to at most one active zone at a time per tenant
CREATE UNIQUE INDEX IF NOT EXISTS idx_heatzone_composition_active_member
    ON expansion.heatzone_composition (tenant_id, member_cell_id)
    WHERE reverted_at IS NULL;

-- Audit index: support point-in-time reconstruction and lineage inspection
CREATE INDEX IF NOT EXISTS idx_heatzone_composition_audit
    ON expansion.heatzone_composition (tenant_id, zone_id, decided_at);

-- Database-enforced append-only trigger:
-- The only permitted UPDATE is transitioning reverted_at from NULL to a timestamp.
-- All other fields are immutable; DELETE is strictly prohibited.
CREATE OR REPLACE FUNCTION expansion.heatzone_composition_append_only()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: DELETE is not permitted (composition_id=%)',
            OLD.composition_id USING ERRCODE = '23514';
    END IF;
    IF OLD.reverted_at IS NOT NULL THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: composition_id=% is already reverted',
            OLD.composition_id USING ERRCODE = '23514';
    END IF;
    IF NEW.reverted_at IS NULL THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: the only permitted UPDATE is setting reverted_at'
            USING ERRCODE = '23514';
    END IF;
    IF ROW(NEW.composition_id, NEW.zone_id, NEW.tenant_id, NEW.member_cell_id,
           NEW.composition_kind, NEW.parent_zone_id, NEW.decided_by, NEW.decided_at,
           NEW.decision_policy_version_id, NEW.override_reason, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.composition_id, OLD.zone_id, OLD.tenant_id, OLD.member_cell_id,
           OLD.composition_kind, OLD.parent_zone_id, OLD.decided_by, OLD.decided_at,
           OLD.decision_policy_version_id, OLD.override_reason, OLD.created_at)
    THEN
        RAISE EXCEPTION
            'heatzone_composition_append_only: only reverted_at may change (composition_id=%)',
            OLD.composition_id USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $fn$;

DROP TRIGGER IF EXISTS trg_heatzone_composition_append_only
    ON expansion.heatzone_composition;
CREATE TRIGGER trg_heatzone_composition_append_only
    BEFORE UPDATE OR DELETE ON expansion.heatzone_composition
    FOR EACH ROW EXECUTE FUNCTION expansion.heatzone_composition_append_only();

-- Seed governing decision policy for heatzone_merge
CREATE OR REPLACE FUNCTION workflow.seed_heatzone_merge_policy(p_tenant_id UUID)
RETURNS void
LANGUAGE sql
AS $seed_heatzone_merge_policy$
    INSERT INTO workflow.decision_policies (
        policy_version_id, policy_label, policy_id, policy_version, policy_kind,
        tenant_id, effective_from, effective_to,
        owner_role, approved_by, approved_at,
        input_contract, output_contract, change_reason,
        rollback_policy_version, parameters, declared_inputs
    )
    VALUES (
        'heatzone-merge-v1:' || p_tenant_id::text,
        'heatzone-merge-v1',
        'heatzone-merge',
        '1.0.0',
        'heatzone_merge',
        p_tenant_id,
        '2026-09-01 00:00:00+00',
        NULL,
        'expansion_owner',
        'architecture_review',
        '2026-09-01 00:00:00+00',
        'HeatZoneScores',
        'HeatZoneComposition',
        '熱區合併／拆分決策政策導入，依 HZ-004 實績門檻與空間異質性治理',
        NULL,
        '{"min_observation_days": 180, "min_mature_labels": 200, "min_active_stores": 50, "min_adjacent_pairs": 30, "min_metro_clusters": 2, "min_spatial_contiguity": 0.80, "max_absorption_cv": 0.15, "max_drift_psi": 0.10, "max_wasserstein": 0.05, "min_correlation_rho": 0.75, "max_disconnect_index": 0.20, "min_split_density_ratio": 2.5, "min_ndcg_gain": 0.05, "min_cannibalization_variance_reduction": 0.20, "allow_cross_admin_boundary": false}'::jsonb,
        ARRAY['store_daily_performance', 'heatzone_training_view', 'h3_adjacency', 'absorbed_demand']
    )
    ON CONFLICT (policy_version_id) DO NOTHING;
$seed_heatzone_merge_policy$;

SELECT workflow.seed_heatzone_merge_policy(t.tenant_id) FROM core.tenants t;

CREATE OR REPLACE FUNCTION workflow.on_tenant_insert_seed_heatzone_merge_policy()
RETURNS trigger
LANGUAGE plpgsql
AS $on_tenant_insert_seed_heatzone_merge_policy$
BEGIN
    PERFORM workflow.seed_heatzone_merge_policy(NEW.tenant_id);
    RETURN NEW;
END $on_tenant_insert_seed_heatzone_merge_policy$;

DROP TRIGGER IF EXISTS trg_seed_heatzone_merge_policy ON core.tenants;
CREATE TRIGGER trg_seed_heatzone_merge_policy
    AFTER INSERT ON core.tenants
    FOR EACH ROW
    EXECUTE FUNCTION workflow.on_tenant_insert_seed_heatzone_merge_policy();
