"""CI tripwire for the decision policy registry migration.

What the constraints *do* is proved in
``tests/contract/test_decision_policy_registry_schema.py``, which applies this
migration to a real PostgreSQL 16 and tries to write the rows each constraint
claims to reject. That suite is marked ``requires_live_env`` and is excluded
from the default CI marker expression, so CI would otherwise carry no signal
about this file at all.

This module is that signal, and nothing more: it pins the structural decisions
the design ratified in ODP-SD-AMD-001 §3.2, so that a revision drifting back to
the shape this task first shipped -- a separate ``governance`` schema keyed on
``(policy_id, policy_version)``, with no tenant in the key and no composite
rollback reference -- fails in CI rather than at review.
"""

from __future__ import annotations

from pathlib import Path

MIGRATION = Path("infra/db/migrations/000014_decision_policy_registry.sql")


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_the_registry_lives_in_the_workflow_schema() -> None:
    """`workflow.decisions.policy_version_id` is the column this table is behind
    (ODP-SD-AMD-001 §3.1), so the table belongs in the same schema. An earlier
    revision created a `governance` schema of its own."""
    sql = _sql()

    assert "CREATE TABLE IF NOT EXISTS workflow.decision_policies" in sql
    assert "CREATE SCHEMA" not in sql
    assert "governance.decision_policies" not in sql


def test_the_key_carries_the_tenant_and_the_label_stays_separate() -> None:
    """Two identity layers, both enforced: the per-tenant key every foreign key
    points at, and the cross-tenant label documents and module constants use."""
    sql = _sql()

    required = (
        "policy_version_id       VARCHAR(100) PRIMARY KEY",
        "policy_label            VARCHAR(100) NOT NULL",
        "tenant_id               UUID         NOT NULL REFERENCES core.tenants(tenant_id)",
        "CONSTRAINT chk_decision_policy_version_id_format",
        "policy_version_id = policy_label || ':' || tenant_id::text",
        "CONSTRAINT chk_decision_policy_label",
    )
    for fragment in required:
        assert fragment in sql


def test_policy_bindings_are_tenant_scoped() -> None:
    """A single-column reference proves a policy version exists; it does not
    prove it belongs to the tenant using it."""
    sql = _sql()

    required = (
        "ADD CONSTRAINT uq_decision_policy_version_tenant",
        "UNIQUE (policy_version_id, tenant_id)",
        "ADD CONSTRAINT fk_decision_policy_rollback_tenant",
        "FOREIGN KEY (rollback_policy_version, tenant_id)",
        "REFERENCES workflow.decision_policies(policy_version_id, tenant_id)",
        "ADD CONSTRAINT fk_decisions_policy_version",
    )
    for fragment in required:
        assert fragment in sql


def test_one_version_in_force_per_policy_per_tenant() -> None:
    sql = _sql()

    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_decision_policy_active" in sql
    assert "ON workflow.decision_policies (policy_id, tenant_id)" in sql
    assert "WHERE effective_to IS NULL" in sql


def test_model_performance_drift_policy_is_seeded_with_all_production_rows() -> None:
    sql = _sql()

    required = (
        "'model_performance_drift'",
        "CREATE OR REPLACE FUNCTION workflow.seed_model_performance_drift_policy(p_tenant_id UUID)",
        "SELECT workflow.seed_model_performance_drift_policy(t.tenant_id) FROM core.tenants t;",
        "CREATE TRIGGER trg_seed_model_performance_drift_policy",
        '"metric_thresholds_by_model"',
        '"max_degradation": 0.05',
    )
    for fragment in required:
        assert fragment in sql


def test_seeding_is_one_definition_shared_by_backfill_and_onboarding() -> None:
    """`core.tenants` rows are written by the data plane at runtime, strictly
    after `alembic upgrade head`. A migration-time backfill therefore covers
    only the tenants that already exist -- on a fresh database, none -- so the
    onboarding path needs the same seed, and the two must not be able to drift
    into issuing different thresholds."""
    sql = _sql()

    assert "CREATE OR REPLACE FUNCTION workflow.seed_forecast_alert_policy(p_tenant_id UUID)" in sql
    assert "SELECT workflow.seed_forecast_alert_policy(t.tenant_id) FROM core.tenants t;" in sql
    assert "PERFORM workflow.seed_forecast_alert_policy(NEW.tenant_id);" in sql
    assert "CREATE TRIGGER trg_seed_forecast_alert_policy" in sql
    assert "AFTER INSERT ON core.tenants" in sql

    # One definition means the thresholds appear once per policy version, in
    # the function -- not once in the function and again in an inline backfill.
    assert sql.count("'four-light-policy-0.0.0-retrofit',") == 1
    assert sql.count("'four-light-policy-v1',") == 1


def test_the_retrofit_version_carries_evaluable_thresholds() -> None:
    """Point-in-time resolution to the retrofit row has to reproduce the light
    a pre-mechanism alert would have shown. `_policy_thresholds()` rejects a
    threshold missing `input` or `op`, so a retrofit row without them makes
    every historical re-evaluation raise instead of reproduce."""
    sql = _sql()
    retrofit = sql.split("'0.0.0-retrofit',", 1)[1].split("ON CONFLICT", 1)[0]

    for level, value in (("RED", "-0.35"), ("ORANGE", "-0.20"), ("YELLOW", "-0.10")):
        fragment = (
            f'{{"level": "{level}", "input": "sitescore_gap_ratio", '
            f'"op": "<=", "value": {value}}}'
        )
        assert fragment in retrofit
