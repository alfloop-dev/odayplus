-- ODP-DECISION-POLICY-CORE-001: versioned decision policies (ADR ODP-SD-AMD-001 §2)
--
-- ODP-SA-07 §8 requires every Decision Policy to carry policy_id,
-- policy_version, effective_from/to, owner, approved_by, input/output
-- contracts, change_reason and rollback_policy_version, and requires every
-- formal Decision to record which policy_id/policy_version produced it.
--
-- None of that existed. Policies were program constants: the ForecastOps
-- four-light thresholds are literals inside _alert_for() (-0.35/-0.20/-0.10),
-- which makes ODP-AC-BR-003 (policy changes must version and retain the old
-- version) and ODP-AC-BR-004 (a decision can be traced to its policy version)
-- unachievable -- a historical alert cannot answer "which policy called this
-- red". Changing an operational threshold also required a code change and a
-- deploy.
--
-- Two columns deserve note because they were absent everywhere in the tree:
-- `change_reason` (why this version exists) and `rollback_policy_version`
-- (where to retreat to). Without them a reader knows the current version but
-- not what changed or what to fall back to when it misbehaves.
--
-- `declared_inputs` exists because a policy must state which inputs it
-- actually consults. ODP-SA-07 §5 lists ten inputs the four-light policy
-- should weigh; the implementation uses one. Declaring the subset makes that
-- gap visible in data rather than implied by reading the code.
--
-- Rerunnable: every statement is IF NOT EXISTS.

CREATE SCHEMA IF NOT EXISTS governance;

CREATE TABLE IF NOT EXISTS governance.decision_policies (
    policy_id               TEXT        NOT NULL,
    policy_version          TEXT        NOT NULL,      -- semver
    policy_kind             TEXT        NOT NULL,      -- 'forecast_alert' | 'heatzone_merge' | ...
    tenant_id               TEXT        NOT NULL,
    effective_from          TIMESTAMPTZ NOT NULL,
    effective_to            TIMESTAMPTZ,               -- NULL = the version in force
    owner_role              TEXT        NOT NULL,
    approved_by             TEXT        NOT NULL,
    approved_at             TIMESTAMPTZ NOT NULL,
    input_contract          TEXT        NOT NULL,
    output_contract         TEXT        NOT NULL,
    change_reason           TEXT        NOT NULL,
    rollback_policy_version TEXT,
    parameters              JSONB       NOT NULL,      -- thresholds and weights
    declared_inputs         TEXT[]      NOT NULL,      -- inputs this version actually reads
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_id, policy_version),
    CONSTRAINT decision_policy_window_ordered
        CHECK (effective_to IS NULL OR effective_to > effective_from)
);

-- At most one version in force per policy per tenant. Versioning is
-- close-and-insert: the outgoing version's effective_to is set to the
-- incoming version's effective_from. Nothing else about a retired version is
-- ever rewritten -- ODP-AC-BR-003 requires the old version to be retained as
-- it stood.
CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_policy_in_force
    ON governance.decision_policies (policy_id, tenant_id)
    WHERE effective_to IS NULL;

-- Resolution is point-in-time, not latest-version: a decision made three
-- months ago must re-resolve to the version that was in force then, or it
-- cannot be reproduced.
CREATE INDEX IF NOT EXISTS idx_decision_policy_point_in_time
    ON governance.decision_policies (policy_kind, tenant_id, effective_from DESC);
