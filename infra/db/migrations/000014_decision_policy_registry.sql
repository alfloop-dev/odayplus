-- ODP-DECISION-POLICY-CORE-001: decision policy registry (ODP-SD-AMD-001 §3.2)
--
-- ODP-SA-07 §8 requires every Decision Policy to carry policy_id,
-- policy_version, an effective window, an owner, an approver, input/output
-- contracts, change_reason and rollback_policy_version, and requires every
-- formal Decision to record which policy version produced it.
--
-- None of that existed. Policies were program constants: the ForecastOps
-- four-light thresholds are literals inside _alert_for() (-0.35/-0.20/-0.10),
-- which makes ODP-AC-BR-003 (a policy change must version and retain the old
-- version) and ODP-AC-BR-004 (a decision can be traced to its policy version)
-- unachievable -- a historical alert cannot answer "which policy called this
-- red". Changing an operational threshold also required a code change and a
-- deploy.
--
-- This table is not a new mechanism. `workflow.decisions.policy_version_id`
-- (000001 line 360) is already `VARCHAR(100) NOT NULL` with nothing behind it:
-- a mandatory foreign-key-shaped column pointing at a table that was never
-- created. Hence `workflow`, not a new schema -- decision governance already
-- lives there -- and hence the shape of `learning.model_versions`, the other
-- VARCHAR(100)-keyed version registry, so the two read alike.
--
-- Why the key carries the tenant. Policies are created per tenant, so
-- `idx_decision_policy_active` (one version in force per policy per tenant)
-- must key on a non-null tenant: with NULL allowed, Postgres' NULL-distinct
-- semantics would let one policy hold several "current" versions unchallenged.
-- That splits identity in two, and the split is enforced rather than trusted:
--
--   policy_label       cross-tenant, human-readable  'four-light-policy-v1'
--   policy_version_id  per-tenant, the FK target     'four-light-policy-v1:<uuid>'
--
-- `chk_decision_policy_version_id_format` makes `policy_version_id =
-- policy_label || ':' || tenant_id` a database rule, and
-- `chk_decision_policy_label` forbids ':' inside the label so that
-- decomposition is unique. A writer cannot assemble an identifier that no row
-- carries, and a reader cannot mistake a label for a key.
--
-- `rollback_policy_version` is a composite self-reference on
-- (rollback_policy_version, tenant_id). A single-column reference would prove
-- only that the rollback target exists; rolling back to another tenant's
-- version means applying their thresholds to your own decisions. MATCH SIMPLE
-- is deliberate: the first version of a policy has no rollback target, and a
-- NULL there must not be checked.
--
-- `declared_inputs` exists because a policy must state which inputs it
-- actually consults. ODP-SA-07 §5 lists ten inputs for the four-light policy;
-- the implementation reads one. Declaring the subset makes that gap visible in
-- data rather than implied by reading the code.
--
-- Scope boundary, stated so it is not read as an omission: ODP-SD-AMD-001 §3.4
-- also adds `decision_policy_version_id` and `tenant_id` to four decision-
-- producing tables (operations.alerts, expansion.heatzone_scores,
-- expansion.site_score_runs, network.network_plans) with composite foreign
-- keys into this registry. Those columns belong to the module tasks that start
-- writing them -- ODP-FORECAST-ALERT-POLICY-001 first. What this migration
-- ships is the registry plus the one binding that has no other owner:
-- `workflow.decisions.policy_version_id`, the column §3.1 is about.
-- `uq_decision_policy_version_tenant` is created here because it is the
-- reference target those later composite keys need.
--
-- Rerunnable: every statement is IF NOT EXISTS, a catalog-guarded DO block, or
-- an ON CONFLICT DO NOTHING insert.

CREATE TABLE IF NOT EXISTS workflow.decision_policies (
    policy_version_id       VARCHAR(100) PRIMARY KEY,          -- 'four-light-policy-v1:11111111-1111-1111-1111-111111111111'
    policy_label            VARCHAR(100) NOT NULL,             -- cross-tenant label: 'four-light-policy-v1'
    policy_id               VARCHAR(100) NOT NULL,             -- 'four-light-policy'
    policy_version          VARCHAR(50)  NOT NULL,             -- semver, or a retrofit marker
    policy_kind             VARCHAR(100) NOT NULL,
    tenant_id               UUID         NOT NULL REFERENCES core.tenants(tenant_id),
    effective_from          TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to            TIMESTAMP WITH TIME ZONE,          -- NULL = the version in force
    owner_role              VARCHAR(100) NOT NULL,
    approved_by             VARCHAR(255) NOT NULL,
    approved_at             TIMESTAMP WITH TIME ZONE NOT NULL,
    input_contract          VARCHAR(100) NOT NULL,
    output_contract         VARCHAR(100) NOT NULL,
    change_reason           TEXT         NOT NULL,
    rollback_policy_version VARCHAR(100),                      -- see fk_decision_policy_rollback_tenant
    parameters              JSONB        NOT NULL,
    declared_inputs         TEXT[]       NOT NULL,
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_decision_policy_tenant_id_version UNIQUE (tenant_id, policy_id, policy_version),
    -- The naming rule is enforced, not conventional:
    -- policy_version_id = policy_label || ':' || tenant_id
    CONSTRAINT chk_decision_policy_version_id_format CHECK (
        policy_version_id = policy_label || ':' || tenant_id::text
    ),
    -- The label may not contain the separator, or the decomposition above is
    -- not unique.
    CONSTRAINT chk_decision_policy_label CHECK (
        policy_label <> '' AND position(':' in policy_label) = 0
    ),
    CONSTRAINT chk_decision_policy_kind CHECK (
        policy_kind IN ('forecast_alert', 'heatzone_merge', 'heatzone_absorption',
                        'sitescore_recommendation', 'price_exploration', 'netplan_action')
    ),
    CONSTRAINT chk_decision_policy_window CHECK (
        effective_to IS NULL OR effective_to > effective_from
    ),
    CONSTRAINT chk_decision_policy_reason CHECK (change_reason <> ''),
    CONSTRAINT chk_decision_policy_inputs CHECK (cardinality(declared_inputs) > 0),
    CONSTRAINT chk_decision_policy_params CHECK (jsonb_typeof(parameters) = 'object')
);

-- At most one version in force per (policy_id, tenant_id). Versioning is
-- close-and-insert: the outgoing version's effective_to is set to the incoming
-- version's effective_from, and nothing else about it is ever rewritten --
-- ODP-AC-BR-003 requires the retired version retained as it stood.
CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_policy_active
    ON workflow.decision_policies (policy_id, tenant_id)
    WHERE effective_to IS NULL;

-- Resolution is point-in-time, not latest-version: re-running a decision made
-- three months ago must re-resolve to the version in force then, or the
-- decision cannot be reproduced.
CREATE INDEX IF NOT EXISTS idx_decision_policy_kind_window
    ON workflow.decision_policies (policy_kind, tenant_id, effective_from);

DO $$
BEGIN
    -- The primary key already implies this pair is unique. It is declared so
    -- that (policy_version_id, tenant_id) becomes a referenceable target for
    -- the composite bindings in ODP-SD-AMD-001 §3.4.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_decision_policy_version_tenant'
    ) THEN
        ALTER TABLE workflow.decision_policies
            ADD CONSTRAINT uq_decision_policy_version_tenant
            UNIQUE (policy_version_id, tenant_id);
    END IF;
    -- A rollback target must be a real version *of the same tenant*: rolling
    -- back across tenants applies someone else's thresholds to your decisions.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_decision_policy_rollback_tenant'
    ) THEN
        ALTER TABLE workflow.decision_policies
            ADD CONSTRAINT fk_decision_policy_rollback_tenant
            FOREIGN KEY (rollback_policy_version, tenant_id)
            REFERENCES workflow.decision_policies(policy_version_id, tenant_id);
    END IF;
    -- ODP-SD-AMD-001 §3.1: the foreign key workflow.decisions.policy_version_id
    -- always implied. NOT VALID so existing rows -- written while no registry
    -- existed -- do not block the migration; every write from here on is
    -- checked.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_decisions_policy_version'
    ) THEN
        ALTER TABLE workflow.decisions
            ADD CONSTRAINT fk_decisions_policy_version
            FOREIGN KEY (policy_version_id)
            REFERENCES workflow.decision_policies(policy_version_id)
            NOT VALID;
    END IF;
END $$;

-- ODP-FORECAST-ALERT-POLICY-001: bind the ForecastOps alert record to the
-- policy registry. The baseline alert table predates the registry, so these
-- columns remain nullable for historical rows; every new domain Alert carries
-- all three values. The composite foreign key is what prevents a policy key
-- from being applied across tenants once a writer supplies the binding.
DO $$
BEGIN
    IF to_regclass('operations.alerts') IS NOT NULL THEN
        ALTER TABLE operations.alerts
            ADD COLUMN IF NOT EXISTS tenant_id UUID,
            ADD COLUMN IF NOT EXISTS policy_id VARCHAR(100),
            ADD COLUMN IF NOT EXISTS policy_version VARCHAR(100),
            ADD COLUMN IF NOT EXISTS decision_policy_version_id VARCHAR(100);

        -- Existing canonical alerts already have a tenant through their
        -- store. Backfill that scope before new rows start using the FK.
        UPDATE operations.alerts AS alert
        SET tenant_id = store.tenant_id
        FROM core.stores AS store
        WHERE alert.store_id = store.store_id
          AND alert.tenant_id IS NULL;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'fk_alerts_decision_policy_version'
        ) THEN
            ALTER TABLE operations.alerts
                ADD CONSTRAINT fk_alerts_decision_policy_version
                FOREIGN KEY (decision_policy_version_id, tenant_id)
                REFERENCES workflow.decision_policies(policy_version_id, tenant_id)
                NOT VALID;
        END IF;
        CREATE INDEX IF NOT EXISTS idx_alerts_decision_policy_version
            ON operations.alerts(decision_policy_version_id, tenant_id);
    END IF;
END $$;

-- Initial policy rows, one pair per tenant. The retrofit row is inserted first:
-- it is the rollback target of the row that follows it, and the composite
-- self-reference is checked at insert time.
--
-- The retrofit version is a placeholder for alerts raised before the mechanism
-- existed. It closes exactly where v1 opens, so the half-open windows leave no
-- instant uncovered and no instant covered twice.
INSERT INTO workflow.decision_policies (
    policy_version_id, policy_label, policy_id, policy_version, policy_kind,
    tenant_id, effective_from, effective_to,
    owner_role, approved_by, approved_at,
    input_contract, output_contract, change_reason,
    rollback_policy_version, parameters, declared_inputs
)
SELECT
    'four-light-policy-0.0.0-retrofit:' || t.tenant_id::text,
    'four-light-policy-0.0.0-retrofit',
    'four-light-policy',
    '0.0.0-retrofit',
    'forecast_alert',
    t.tenant_id,
    '2020-01-01 00:00:00+00',
    '2026-09-01 00:00:00+00',
    'system',
    'system_bootstrap',
    '2026-09-01 00:00:00+00',
    'ForecastOutput',
    'Alert',
    '歷史警示回填佔位，記錄機制導入前判定',
    NULL,
    '{"thresholds": [{"level": "RED", "value": -0.35}, {"level": "ORANGE", "value": -0.20}, {"level": "YELLOW", "value": -0.10}]}'::jsonb,
    ARRAY['sitescore_gap_ratio']
FROM core.tenants t
ON CONFLICT (policy_version_id) DO NOTHING;

INSERT INTO workflow.decision_policies (
    policy_version_id, policy_label, policy_id, policy_version, policy_kind,
    tenant_id, effective_from, effective_to,
    owner_role, approved_by, approved_at,
    input_contract, output_contract, change_reason,
    rollback_policy_version, parameters, declared_inputs
)
SELECT
    'four-light-policy-v1:' || t.tenant_id::text,
    'four-light-policy-v1',
    'four-light-policy',
    '1.0.0',
    'forecast_alert',
    t.tenant_id,
    '2026-09-01 00:00:00+00',
    NULL,
    'ops',
    'architecture_owner',
    '2026-09-01 00:00:00+00',
    'ForecastOutput',
    'Alert',
    '機制導入，門檻沿用常數，納入資料品質守衛',
    'four-light-policy-0.0.0-retrofit:' || t.tenant_id::text,
    '{"thresholds": [{"level": "RED", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.35}, {"level": "ORANGE", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.20}, {"level": "YELLOW", "input": "sitescore_gap_ratio", "op": "<=", "value": -0.10}], "data_quality_guard": {"max_staleness_days": 2, "on_violation": "SUPPRESS_HIGH_CONFIDENCE"}}'::jsonb,
    -- The runtime evaluator reads sitescore_gap_ratio. The quality guard is
    -- policy metadata evaluated against the forecast's derived freshness
    -- context, not a second declared decision input.
    ARRAY['sitescore_gap_ratio']
FROM core.tenants t
ON CONFLICT (policy_version_id) DO NOTHING;
