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
