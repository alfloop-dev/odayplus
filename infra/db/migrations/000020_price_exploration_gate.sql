-- ODP-FR-PRICE-006: Price exploration gate and decision tracking (ODP-PRICE006-BANDIT-GATED-001)
--
-- Citing ODP-SD-AMD-001 §7 and ODP-SA-06-AMD-001 §3.3
-- Schema Target: pricing
--
-- Tables:
--   pricing.exploration_gates
--   pricing.exploration_decisions
--
-- Triggers:
--   trg_exploration_decisions_accrue
--   trg_exploration_decisions_append_only
--   trg_exploration_gates_controlled_update

CREATE SCHEMA IF NOT EXISTS pricing;

-- Create prerequisite unique constraints on core and workflow tables if they don't already exist
DO $$
BEGIN
    IF to_regclass('core.brands') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_brands_brand_tenant'
        ) THEN
            ALTER TABLE core.brands ADD CONSTRAINT uq_brands_brand_tenant UNIQUE (brand_id, tenant_id);
        END IF;
    END IF;
    IF to_regclass('workflow.approvals') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_approvals_decision_status'
        ) THEN
            ALTER TABLE workflow.approvals ADD CONSTRAINT uq_approvals_decision_status UNIQUE (approval_id, decision_id, approval_status);
        END IF;
    END IF;
    IF to_regclass('core.stores') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_stores_store_tenant'
        ) THEN
            ALTER TABLE core.stores ADD CONSTRAINT uq_stores_store_tenant UNIQUE (store_id, tenant_id);
        END IF;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS pricing.exploration_gates (
    gate_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id),
    scope_brand_id      UUID,
    scope_store_group   VARCHAR(100),
    scope_sku_group     VARCHAR(100),
    budget_limit        NUMERIC(18, 2) NOT NULL,
    budget_consumed     NUMERIC(18, 2) NOT NULL DEFAULT 0,
    effective_from      TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to        TIMESTAMP WITH TIME ZONE NOT NULL,
    approved_by         VARCHAR(255) NOT NULL,
    approval_decision_id UUID NOT NULL REFERENCES workflow.decisions(decision_id),
    approval_id         UUID NOT NULL,
    approval_source_status VARCHAR(50) GENERATED ALWAYS AS ('approved') STORED,
    rollback_condition  TEXT NOT NULL,
    revoked_at          TIMESTAMP WITH TIME ZONE,
    decision_policy_version_id VARCHAR(100) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_exploration_gates_gate_tenant UNIQUE (gate_id, tenant_id),
    CONSTRAINT fk_exploration_gates_workflow_approval
        FOREIGN KEY (approval_id, approval_decision_id, approval_source_status)
        REFERENCES workflow.approvals (approval_id, decision_id, approval_status)
        MATCH FULL,
    CONSTRAINT fk_exploration_gates_brand_tenant
        FOREIGN KEY (scope_brand_id, tenant_id)
        REFERENCES core.brands(brand_id, tenant_id),
    CONSTRAINT fk_exploration_gates_decision_policy
        FOREIGN KEY (decision_policy_version_id, tenant_id)
        REFERENCES workflow.decision_policies(policy_version_id, tenant_id),
    CONSTRAINT chk_gate_window CHECK (effective_to > effective_from),
    CONSTRAINT chk_gate_budget_limit CHECK (budget_limit > 0),
    CONSTRAINT chk_gate_budget_consumed CHECK (
        budget_consumed >= 0 AND budget_consumed <= budget_limit
    ),
    CONSTRAINT chk_gate_rollback_condition CHECK (rollback_condition <> ''),
    CONSTRAINT chk_gate_revoke_order CHECK (
        revoked_at IS NULL OR revoked_at >= effective_from
    )
);

CREATE INDEX IF NOT EXISTS idx_exploration_gate_active
    ON pricing.exploration_gates (tenant_id, effective_from, effective_to)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS pricing.exploration_decisions (
    decision_id         UUID PRIMARY KEY REFERENCES workflow.decisions(decision_id),
    gate_id             UUID NOT NULL,
    tenant_id           UUID NOT NULL,
    sku_id              VARCHAR(100) NOT NULL,
    store_id            UUID,
    baseline_price      NUMERIC(18, 2) NOT NULL,
    explored_price      NUMERIC(18, 2) NOT NULL,
    budget_consumed     NUMERIC(18, 2) NOT NULL,
    algorithm           VARCHAR(50) NOT NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_exploration_decisions_store_tenant
        FOREIGN KEY (store_id, tenant_id)
        REFERENCES core.stores(store_id, tenant_id),
    CONSTRAINT fk_exploration_decisions_gate_tenant
        FOREIGN KEY (gate_id, tenant_id)
        REFERENCES pricing.exploration_gates(gate_id, tenant_id),
    CONSTRAINT fk_exploration_decisions_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES core.tenants(tenant_id),
    CONSTRAINT chk_exploration_decision_prices CHECK (
        baseline_price > 0 AND explored_price > 0
    ),
    CONSTRAINT chk_exploration_decision_budget CHECK (budget_consumed >= 0)
);

CREATE INDEX IF NOT EXISTS idx_exploration_decisions_gate
    ON pricing.exploration_decisions (gate_id, created_at);

-- Budget accrual trigger
CREATE OR REPLACE FUNCTION pricing.exploration_decisions_accrue_budget()
RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE
    accrued pricing.exploration_gates%ROWTYPE;
BEGIN
    UPDATE pricing.exploration_gates
       SET budget_consumed = budget_consumed + NEW.budget_consumed
     WHERE gate_id   = NEW.gate_id
       AND tenant_id = NEW.tenant_id
       AND revoked_at IS NULL
       AND NEW.created_at >= effective_from
       AND NEW.created_at <  effective_to
    RETURNING * INTO accrued;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'exploration_decisions_accrue_budget: gate % is not active for tenant % at %',
            NEW.gate_id, NEW.tenant_id, NEW.created_at USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END $fn$;

DROP TRIGGER IF EXISTS trg_exploration_decisions_accrue
    ON pricing.exploration_decisions;
CREATE TRIGGER trg_exploration_decisions_accrue
    AFTER INSERT ON pricing.exploration_decisions
    FOR EACH ROW EXECUTE FUNCTION pricing.exploration_decisions_accrue_budget();

-- Append-only trigger for exploration decisions
CREATE OR REPLACE FUNCTION pricing.exploration_decisions_append_only()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION
        'exploration_decisions_append_only: % is not permitted on pricing.exploration_decisions',
        TG_OP USING ERRCODE = '23514';
END $fn$;

DROP TRIGGER IF EXISTS trg_exploration_decisions_append_only
    ON pricing.exploration_decisions;
CREATE TRIGGER trg_exploration_decisions_append_only
    BEFORE UPDATE OR DELETE ON pricing.exploration_decisions
    FOR EACH ROW EXECUTE FUNCTION pricing.exploration_decisions_append_only();

-- Controlled update trigger for exploration gates
CREATE OR REPLACE FUNCTION pricing.exploration_gates_controlled_update()
RETURNS trigger LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'exploration_gates_controlled_update: DELETE is not permitted (gate_id=%)',
            OLD.gate_id USING ERRCODE = '23514';
    END IF;

    IF ROW(NEW.gate_id, NEW.tenant_id, NEW.scope_brand_id, NEW.scope_store_group,
           NEW.scope_sku_group, NEW.budget_limit, NEW.effective_from,
           NEW.approved_by, NEW.approval_id, NEW.approval_decision_id,
           NEW.rollback_condition,
           NEW.decision_policy_version_id, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.gate_id, OLD.tenant_id, OLD.scope_brand_id, OLD.scope_store_group,
           OLD.scope_sku_group, OLD.budget_limit, OLD.effective_from,
           OLD.approved_by, OLD.approval_id, OLD.approval_decision_id,
           OLD.rollback_condition,
           OLD.decision_policy_version_id, OLD.created_at)
    THEN
        RAISE EXCEPTION
            'exploration_gates_controlled_update: authorisation fields are immutable (gate_id=%); issue a new gate instead',
            OLD.gate_id USING ERRCODE = '23514';
    END IF;

    IF NEW.budget_consumed < OLD.budget_consumed THEN
        RAISE EXCEPTION
            'exploration_gates_controlled_update: budget_consumed must not decrease (gate_id=%, % -> %)',
            OLD.gate_id, OLD.budget_consumed, NEW.budget_consumed USING ERRCODE = '23514';
    END IF;

    IF NEW.effective_to > OLD.effective_to THEN
        RAISE EXCEPTION
            'exploration_gates_controlled_update: effective_to must not be extended (gate_id=%, % -> %)',
            OLD.gate_id, OLD.effective_to, NEW.effective_to USING ERRCODE = '23514';
    END IF;

    IF OLD.revoked_at IS NOT NULL
       AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
    THEN
        RAISE EXCEPTION
            'exploration_gates_controlled_update: revocation is irreversible (gate_id=%)',
            OLD.gate_id USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END $fn$;

DROP TRIGGER IF EXISTS trg_exploration_gates_controlled_update
    ON pricing.exploration_gates;
CREATE TRIGGER trg_exploration_gates_controlled_update
    BEFORE UPDATE OR DELETE ON pricing.exploration_gates
    FOR EACH ROW EXECUTE FUNCTION pricing.exploration_gates_controlled_update();
