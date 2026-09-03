-- ODP-NETPLAN-DISCLOSURE-APPROVAL-001: durable receipts for accepting the
-- constraint classes a network plan was never checked against.
--
-- ODP-FR-NET-002 names eight hard-constraint classes. The solver binds at most
-- six of them and has reported the shortfall as
-- `unmodelled_constraint_classes` since PR #1133 -- but `decide()` read that
-- list and approved regardless, so a plan validated against capital alone
-- reached APPROVED on exactly the same terms as one validated against six.
-- The disclosure existed; nothing consumed it.
--
-- What this migration adds is the durable half of consuming it: the signature
-- that lets a plan through with a class unmodelled, stored so that "who
-- accepted this exposure, for which plan, under which rules" survives the
-- scenario being re-solved and the policy being superseded.
--
-- Two design points that are not obvious from the column list:
--
-- **`solver_problem_hash` is the anti-reuse mechanism, and it is not a
-- foreign key.** It is the same hash `ScenarioSolveRecord.is_stale` compares,
-- so a re-solve moves it and every prior signature stops matching without
-- anything being deleted, expired or swept. Modelling this as a reference to
-- a solve row would have meant the signature survived the row being updated in
-- place; hashing the problem means it cannot.
--
-- **The composite foreign key into the registry carries the tenant.** Same
-- reason as `fk_alerts_decision_policy_version` in 000014: a single-column
-- reference would prove the policy version exists, not that it is this
-- tenant's. A signature taken under another tenant's -- possibly far more
-- permissive -- disclosure policy is exactly the confusion the split identity
-- in `workflow.decision_policies` exists to prevent.
--
-- Immutability is a trigger rather than a convention. `receipt_hash` already
-- makes a rewrite *detectable* in the domain, but detection assumes someone
-- reads the record back through code that checks. Refusing the UPDATE means a
-- direct `psql` edit fails at the point it is attempted, and the two mechanisms
-- fail independently: the trigger stops the write, the hash catches a write
-- that reached the row some other way (a restore, a replica, a superuser
-- disabling triggers).
--
-- Rerunnable: every statement is IF NOT EXISTS, CREATE OR REPLACE, a
-- catalog-guarded DO block, or an ON CONFLICT DO NOTHING insert.

CREATE TABLE IF NOT EXISTS network.netplan_constraint_acknowledgements (
    acknowledgement_id      VARCHAR(100) PRIMARY KEY,
    scenario_id             VARCHAR(100) NOT NULL,
    tenant_id               UUID         NOT NULL REFERENCES core.tenants(tenant_id),

    -- What was signed for. A set, not a boolean: "the plan has unmodelled
    -- constraints and someone accepted that" is not a record of anything,
    -- because it does not say which exposure was shown to the signer.
    acknowledged_classes    TEXT[]       NOT NULL,

    -- Who signed, and under what authority. `actor_role` is copied from the
    -- verified management approval receipt rather than supplied by the caller;
    -- stored here so that revoking a role later does not rewrite what the
    -- authority attested at the time.
    actor_id                VARCHAR(255) NOT NULL,
    actor_role              VARCHAR(100) NOT NULL,
    reason                  TEXT         NOT NULL,

    -- Which rules were in force. `policy_version_id` is the per-tenant registry
    -- key; the label and semver are denormalised so a reader can name the
    -- policy without a join, and so the record still reads if the registry row
    -- is ever archived.
    policy_version_id       VARCHAR(100) NOT NULL,
    policy_label            VARCHAR(100) NOT NULL,
    policy_version          VARCHAR(50)  NOT NULL,

    -- Which plan. See the note above on why this is a hash and not a reference.
    solver_problem_hash     VARCHAR(128) NOT NULL,
    model_version           VARCHAR(100) NOT NULL,

    -- The management approval receipt whose readback established the signer's
    -- identity and role.
    approval_receipt_id     VARCHAR(100) NOT NULL,

    acknowledged_at         TIMESTAMP WITH TIME ZONE NOT NULL,
    -- Canonical digest over every column above. Recomputed by
    -- `ConstraintDisclosureAcknowledgement.integrity_verified`; a row that does
    -- not recompute is treated as absent, not as a weaker signature.
    receipt_hash            VARCHAR(128) NOT NULL,
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- An empty reason is a click, not an acceptance. ODP-FR-NET-002's residual
    -- risk has to be attributable to a stated rationale or the receipt records
    -- only that a dialog was dismissed.
    CONSTRAINT chk_netplan_disclosure_ack_reason CHECK (btrim(reason) <> ''),
    CONSTRAINT chk_netplan_disclosure_ack_classes CHECK (
        cardinality(acknowledged_classes) > 0
    ),
    -- Only the classes the current formulation structurally cannot express are
    -- ever signable. CONSTRUCTION, EQUIPMENT, LABOUR, COVERAGE and DILUTION are
    -- solvable today when the caller supplies a cap, so a signature against one
    -- of them would convert a withheld input into an accepted risk. The
    -- application enforces this from policy data, which can be superseded; this
    -- check is the floor that a policy edit cannot lower.
    CONSTRAINT chk_netplan_disclosure_ack_class_names CHECK (
        acknowledged_classes <@ ARRAY['LEASE', 'SEQUENCING']::TEXT[]
    ),
    CONSTRAINT chk_netplan_disclosure_ack_hashes CHECK (
        btrim(solver_problem_hash) <> '' AND btrim(receipt_hash) <> ''
    )
);

DO $$
BEGIN
    -- Composite: a signature must cite *this tenant's* version of the policy.
    -- NOT VALID is not used here -- unlike the retrofitted bindings in 000014,
    -- this table has no historical rows to grandfather, so the constraint is
    -- checked from the first insert.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_netplan_disclosure_ack_policy_version'
    ) THEN
        ALTER TABLE network.netplan_constraint_acknowledgements
            ADD CONSTRAINT fk_netplan_disclosure_ack_policy_version
            FOREIGN KEY (policy_version_id, tenant_id)
            REFERENCES workflow.decision_policies(policy_version_id, tenant_id);
    END IF;
END $$;

-- Approval resolves the signature by (scenario, plan, policy). Indexed in that
-- order because the scenario is always known and the other two narrow it.
CREATE INDEX IF NOT EXISTS idx_netplan_disclosure_ack_lookup
    ON network.netplan_constraint_acknowledgements
       (scenario_id, solver_problem_hash, policy_version_id);

CREATE INDEX IF NOT EXISTS idx_netplan_disclosure_ack_tenant_time
    ON network.netplan_constraint_acknowledgements (tenant_id, acknowledged_at DESC);

-- A signature is a statement someone made at a moment. Editing it produces a
-- record of a statement nobody made; deleting it removes the evidence that a
-- plan with known unmodelled constraints was approved. Superseding is done by
-- issuing a new acknowledgement, which the (scenario, hash, policy) lookup
-- picks up on its own.
CREATE OR REPLACE FUNCTION network.reject_netplan_disclosure_ack_rewrite()
RETURNS trigger
LANGUAGE plpgsql
AS $reject_netplan_disclosure_ack_rewrite$
BEGIN
    RAISE EXCEPTION
        'network.netplan_constraint_acknowledgements is append-only: % on % is '
        'refused. Issue a new acknowledgement instead of rewriting one.',
        TG_OP, COALESCE(OLD.acknowledgement_id, '<unknown>')
        USING ERRCODE = 'restrict_violation';
END;
$reject_netplan_disclosure_ack_rewrite$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_netplan_disclosure_ack_immutable'
          AND tgrelid = 'network.netplan_constraint_acknowledgements'::regclass
    ) THEN
        CREATE TRIGGER trg_netplan_disclosure_ack_immutable
            BEFORE UPDATE OR DELETE ON network.netplan_constraint_acknowledgements
            FOR EACH ROW
            EXECUTE FUNCTION network.reject_netplan_disclosure_ack_rewrite();
    END IF;
END $$;

-- The policy itself. Seeded per tenant by a function with two callers -- the
-- backfill below and the tenant-onboarding trigger -- for the same reason
-- `seed_forecast_alert_policy` is: tenants are created by the data plane at
-- runtime, strictly after `alembic upgrade head`, so a migration-time backfill
-- alone would leave every later tenant resolving nothing. Here that is not a
-- degraded mode but a total stop, because `decide()` refuses when the policy
-- cannot be resolved.
--
-- The parameter values mirror `shared/governance/netplan_disclosure.py`
-- exactly. Where the two could drift, they would drift into permitting
-- different things.
CREATE OR REPLACE FUNCTION workflow.seed_netplan_disclosure_policy(p_tenant_id UUID)
RETURNS void
LANGUAGE sql
AS $seed_netplan_disclosure_policy$
    INSERT INTO workflow.decision_policies (
        policy_version_id, policy_label, policy_id, policy_version, policy_kind,
        tenant_id, effective_from, effective_to,
        owner_role, approved_by, approved_at,
        input_contract, output_contract, change_reason,
        rollback_policy_version, parameters, declared_inputs
    )
    VALUES (
        'netplan-constraint-disclosure-policy-v1:' || p_tenant_id::text,
        'netplan-constraint-disclosure-policy-v1',
        'netplan-constraint-disclosure-policy',
        '1.0.0',
        'netplan_action',
        p_tenant_id,
        '2026-09-01 00:00:00+00',
        NULL,
        'network-planning-authority',
        'architecture_owner',
        '2026-09-01 00:00:00+00',
        'NetworkPlanSolveResult',
        'ApprovalRecord',
        'Bind network plan approval to the solver constraint disclosure: block the classes the model could have bound, require a signed acknowledgement for the two it structurally cannot',
        NULL,
        -- required_classes is all eight because ODP-FR-NET-002 requires all
        -- eight. The split below is about how each unmet one is handled, not
        -- about whether it was required.
        '{"required_classes": ["CAPITAL", "LEASE", "CONSTRUCTION", "EQUIPMENT", "LABOUR", "COVERAGE", "DILUTION", "SEQUENCING"],
          "acknowledgeable_classes": ["LEASE", "SEQUENCING"],
          "authorized_acknowledgement_roles": ["network-planning-authority", "network_planning_authority"]}'::jsonb,
        -- The evaluator reads the solve's unmodelled class set and the role on
        -- the verified approval receipt. Nothing else, and the row says so.
        ARRAY['unmodelled_constraint_classes', 'approval_principal_role']
    )
    ON CONFLICT (policy_version_id) DO NOTHING;
$seed_netplan_disclosure_policy$;

SELECT workflow.seed_netplan_disclosure_policy(t.tenant_id) FROM core.tenants t;

CREATE OR REPLACE FUNCTION workflow.seed_netplan_disclosure_policy_on_tenant()
RETURNS trigger
LANGUAGE plpgsql
AS $seed_netplan_disclosure_policy_on_tenant$
BEGIN
    PERFORM workflow.seed_netplan_disclosure_policy(NEW.tenant_id);
    RETURN NULL;
END;
$seed_netplan_disclosure_policy_on_tenant$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_seed_netplan_disclosure_policy'
          AND tgrelid = 'core.tenants'::regclass
    ) THEN
        CREATE TRIGGER trg_seed_netplan_disclosure_policy
            AFTER INSERT ON core.tenants
            FOR EACH ROW
            EXECUTE FUNCTION workflow.seed_netplan_disclosure_policy_on_tenant();
    END IF;
END $$;
