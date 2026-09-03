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
    model_version       VARCHAR(100) NOT NULL DEFAULT 'heatzone-composition-v1',
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
           NEW.decision_policy_version_id, NEW.model_version, NEW.override_reason, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.composition_id, OLD.zone_id, OLD.tenant_id, OLD.member_cell_id,
           OLD.composition_kind, OLD.parent_zone_id, OLD.decided_by, OLD.decided_at,
           OLD.decision_policy_version_id, OLD.model_version, OLD.override_reason, OLD.created_at)
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

-- Proposals table for Operator preview and approval workflow
CREATE TABLE IF NOT EXISTS expansion.heatzone_proposals (
    proposal_id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zone_id                         VARCHAR(100) NOT NULL,
    tenant_id                       UUID NOT NULL REFERENCES core.tenants(tenant_id),
    composition_kind                VARCHAR(50) NOT NULL,
    member_cell_ids                 JSONB NOT NULL,
    parent_zone_id                  VARCHAR(100),
    ndcg_gain                       NUMERIC(8, 4) NOT NULL DEFAULT 0.0,
    cannibalization_variance_reduction NUMERIC(8, 4) NOT NULL DEFAULT 0.0,
    correlation_rho                 NUMERIC(8, 4) NOT NULL DEFAULT 0.0,
    disconnect_index                NUMERIC(8, 4) NOT NULL DEFAULT 0.0,
    split_density_ratio             NUMERIC(8, 2),
    confidence                      NUMERIC(8, 4) NOT NULL DEFAULT 0.0,
    model_version                   VARCHAR(100) NOT NULL DEFAULT 'heatzone-composition-v1',
    policy_version_id               VARCHAR(100) NOT NULL,
    status                          VARCHAR(50) NOT NULL DEFAULT 'PROPOSED',
    reasons                         JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings                        JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at                      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_by                     VARCHAR(255),
    approved_at                     TIMESTAMP WITH TIME ZONE,
    rejection_reason                TEXT,

    CONSTRAINT chk_proposal_status CHECK (
        status IN ('PROPOSED', 'APPROVED', 'REJECTED', 'APPLIED')
    ),
    CONSTRAINT fk_heatzone_proposals_policy
        FOREIGN KEY (policy_version_id, tenant_id)
        REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_heatzone_proposals_tenant_status
    ON expansion.heatzone_proposals (tenant_id, status, created_at DESC);

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
        '{"min_observation_days": 180, "min_mature_labels": 200, "min_active_stores": 50, "min_adjacent_pairs": 30, "min_metro_clusters": 2, "min_spatial_contiguity": 0.80, "max_absorption_cv": 0.15, "max_drift_psi": 0.10, "max_wasserstein": 0.05, "min_correlation_rho": 0.75, "max_disconnect_index": 0.20, "min_split_density_ratio": 2.5, "min_ndcg_gain": 0.05, "min_cannibalization_variance_reduction": 0.20, "min_paired_periods": 6, "min_split_side_periods": 6, "allow_cross_admin_boundary": false}'::jsonb,
        ARRAY['store_daily_performance', 'heatzone_training_view', 'h3_adjacency', 'absorbed_demand', 'heatzone_absorption_outcomes']
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

-- ---------------------------------------------------------------------------
-- Trusted HZ-004 evidence for merge/split (ODP-FR-HZ-006 §5.2)
--
-- Merge/split may only reason from realised absorption the HZ-004 pipeline
-- computed, so the outcomes live in their own append-only relation rather than
-- arriving on an API request. `barrier_side` is the only admissible basis for a
-- split: without side-labelled outcomes there is no evidence of where a zone
-- would divide, and inventing a boundary from geometry is precisely what the
-- readiness ruling forbids.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS geo.h3_cell_adjacency (
    adjacency_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cell_id         UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id),
    neighbor_cell_id UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id),
    k_ring          INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Adjacency is symmetric; store one row per unordered pair so a pair cannot
    -- be counted twice when readiness tallies candidate pairs.
    CONSTRAINT chk_h3_adjacency_ordered CHECK (cell_id < neighbor_cell_id),
    CONSTRAINT chk_h3_adjacency_k_ring CHECK (k_ring >= 1),
    CONSTRAINT uq_h3_adjacency_pair UNIQUE (cell_id, neighbor_cell_id)
);

CREATE INDEX IF NOT EXISTS idx_h3_adjacency_cell
    ON geo.h3_cell_adjacency (cell_id);

CREATE TABLE IF NOT EXISTS expansion.heatzone_absorption_outcomes (
    outcome_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id),
    geo_cell_id         UUID NOT NULL REFERENCES geo.h3_cells(geo_cell_id),
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    original_demand     NUMERIC(16, 2) NOT NULL,
    absorbed_demand     NUMERIC(16, 2) NOT NULL,
    remaining_demand    NUMERIC(16, 2) NOT NULL,
    absorption_ratio    NUMERIC(6, 4) NOT NULL,
    absorbing_store_count INTEGER NOT NULL,
    under_realized      BOOLEAN NOT NULL DEFAULT FALSE,
    barrier_side        VARCHAR(1),
    barrier_description TEXT,
    basis_source_ids    JSONB NOT NULL,
    basis_at            TIMESTAMP WITH TIME ZONE NOT NULL,
    absorption_policy_version_id VARCHAR(100) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT chk_absorption_outcome_period CHECK (period_end >= period_start),
    CONSTRAINT chk_absorption_outcome_amounts CHECK (
        original_demand >= 0 AND absorbed_demand >= 0 AND remaining_demand >= 0
        AND absorbed_demand <= original_demand
    ),
    CONSTRAINT chk_absorption_outcome_ratio CHECK (
        absorption_ratio >= 0 AND absorption_ratio <= 1
    ),
    CONSTRAINT chk_absorption_outcome_stores CHECK (absorbing_store_count >= 0),
    CONSTRAINT chk_absorption_outcome_side CHECK (
        barrier_side IS NULL OR barrier_side IN ('A', 'B')
    ),
    -- An outcome with no basis snapshot is untraceable, and an untraceable
    -- outcome is indistinguishable from one somebody typed in.
    CONSTRAINT chk_absorption_outcome_basis CHECK (
        jsonb_typeof(basis_source_ids) = 'array'
        AND jsonb_array_length(basis_source_ids) > 0
    ),
    CONSTRAINT uq_absorption_outcome_period UNIQUE (
        tenant_id, geo_cell_id, period_start, period_end, barrier_side
    ),
    CONSTRAINT fk_absorption_outcome_policy
        FOREIGN KEY (absorption_policy_version_id, tenant_id)
        REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_absorption_outcomes_tenant_cell
    ON expansion.heatzone_absorption_outcomes (tenant_id, geo_cell_id, period_start);

-- Absorption history is evidence: it may be appended to, never rewritten.
CREATE OR REPLACE FUNCTION expansion.heatzone_absorption_outcomes_append_only()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION
        'heatzone_absorption_outcomes_append_only: % is not permitted on recorded HZ-004 evidence',
        TG_OP USING ERRCODE = '23514';
END $fn$;

DROP TRIGGER IF EXISTS trg_heatzone_absorption_outcomes_append_only
    ON expansion.heatzone_absorption_outcomes;
CREATE TRIGGER trg_heatzone_absorption_outcomes_append_only
    BEFORE UPDATE OR DELETE ON expansion.heatzone_absorption_outcomes
    FOR EACH ROW EXECUTE FUNCTION expansion.heatzone_absorption_outcomes_append_only();
