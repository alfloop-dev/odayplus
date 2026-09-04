"""CI tripwire for the price exploration gate and decision tracking migration (ODP-FR-PRICE-006)."""

from __future__ import annotations

from pathlib import Path

MIGRATION = Path("infra/db/migrations/000017_price_exploration_gate.sql")


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_creates_pricing_tables() -> None:
    sql = _sql()
    assert "CREATE TABLE IF NOT EXISTS pricing.exploration_gates" in sql
    assert "CREATE TABLE IF NOT EXISTS pricing.exploration_decisions" in sql


def test_exploration_gates_has_required_columns_and_constraints() -> None:
    sql = _sql()
    required = (
        "gate_id             UUID PRIMARY KEY DEFAULT gen_random_uuid()",
        "tenant_id           UUID NOT NULL REFERENCES core.tenants(tenant_id)",
        "budget_limit        NUMERIC(18, 2) NOT NULL",
        "budget_consumed     NUMERIC(18, 2) NOT NULL DEFAULT 0",
        "effective_from      TIMESTAMP WITH TIME ZONE NOT NULL",
        "effective_to        TIMESTAMP WITH TIME ZONE NOT NULL",
        "approved_by         VARCHAR(255) NOT NULL",
        "approval_decision_id UUID NOT NULL REFERENCES workflow.decisions(decision_id)",
        "approval_source_status VARCHAR(50) GENERATED ALWAYS AS ('approved') STORED",
        "rollback_condition  TEXT NOT NULL",
        "decision_policy_version_id VARCHAR(100) NOT NULL",
        "CONSTRAINT uq_exploration_gates_gate_tenant UNIQUE (gate_id, tenant_id)",
        "CONSTRAINT fk_exploration_gates_decision_policy",
        "CONSTRAINT chk_gate_window CHECK (effective_to > effective_from)",
        "CONSTRAINT chk_gate_budget_limit CHECK (budget_limit > 0)",
        "CONSTRAINT chk_gate_budget_consumed",
    )
    for fragment in required:
        assert fragment in sql


def test_triggers_installed() -> None:
    sql = _sql()
    assert "CREATE OR REPLACE FUNCTION pricing.exploration_decisions_accrue_budget()" in sql
    assert "CREATE TRIGGER trg_exploration_decisions_accrue" in sql
    assert "CREATE OR REPLACE FUNCTION pricing.exploration_decisions_append_only()" in sql
    assert "CREATE TRIGGER trg_exploration_decisions_append_only" in sql
    assert "CREATE OR REPLACE FUNCTION pricing.exploration_gates_controlled_update()" in sql
    assert "CREATE TRIGGER trg_exploration_gates_controlled_update" in sql
