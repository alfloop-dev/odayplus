"""Price exploration gate and decision tracking schema tests on real PostgreSQL 16 (ODP-FR-PRICE-006)."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_POLICY = REPO_ROOT / "infra/db/migrations/000014_decision_policy_registry.sql"
MIGRATION_GATE = REPO_ROOT / "infra/db/migrations/000020_price_exploration_gate.sql"

live = pytest.mark.requires_live_env

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "11111111-1111-1111-1111-222222222222"

BASELINE_STUB = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS workflow;
CREATE SCHEMA IF NOT EXISTS pricing;

CREATE TABLE IF NOT EXISTS core.tenants (
    tenant_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_name VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS core.brands (
    brand_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID REFERENCES core.tenants(tenant_id),
    brand_code  VARCHAR(100) NOT NULL,
    brand_name  VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS core.stores (
    store_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES core.tenants(tenant_id),
    store_name  VARCHAR(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow.decisions (
    decision_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_type     VARCHAR(100) NOT NULL DEFAULT 'price',
    entity_type       VARCHAR(100) NOT NULL,
    entity_id         VARCHAR(255) NOT NULL,
    decision_status   VARCHAR(50)  NOT NULL DEFAULT 'proposed',
    policy_version_id VARCHAR(100) NOT NULL,
    created_by        VARCHAR(255) NOT NULL,
    created_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow.approvals (
    approval_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id     UUID NOT NULL REFERENCES workflow.decisions(decision_id),
    approver_id     VARCHAR(255) NOT NULL,
    approval_status VARCHAR(50) NOT NULL DEFAULT 'pending',
    approved_at     TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def gate_db(intake_blank_db):
    """A blank PostgreSQL 16 database with baseline stub, decision policy migration, and exploration gate migration applied."""
    with intake_blank_db.connect(autocommit=True) as conn:
        conn.execute(BASELINE_STUB)
        conn.execute(
            "INSERT INTO core.tenants (tenant_id, tenant_name) VALUES (%s, %s), (%s, %s)",
            (TENANT_A, "tenant-a", TENANT_B, "tenant-b"),
        )
        conn.execute(MIGRATION_POLICY.read_text(encoding="utf-8"))
        conn.execute(MIGRATION_GATE.read_text(encoding="utf-8"))
    return intake_blank_db


@live
class TestExplorationGateSchema:
    def test_tables_created_in_pricing_schema(self, gate_db) -> None:
        with gate_db.connect() as conn:
            tables = conn.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'pricing' AND table_name IN ('exploration_gates', 'exploration_decisions')
                ORDER BY table_name
                """
            ).fetchall()
        assert [r[0] for r in tables] == ["exploration_decisions", "exploration_gates"]

    def test_gate_creation_and_budget_accrual_trigger(self, gate_db) -> None:
        psycopg = gate_db.server.psycopg
        gate_id = str(uuid4())
        dec_id = str(uuid4())
        appr_id = str(uuid4())
        policy_v_id = f"four-light-policy-v1:{TENANT_A}"

        with gate_db.connect(autocommit=True) as conn:
            # Create decision and approval
            conn.execute(
                """
                INSERT INTO workflow.decisions (decision_id, entity_type, entity_id, policy_version_id, created_by)
                VALUES (%s, 'price_plan', 'plan-1', %s, 'admin')
                """,
                (dec_id, policy_v_id),
            )
            conn.execute(
                """
                INSERT INTO workflow.approvals (approval_id, decision_id, approver_id, approval_status, approved_at)
                VALUES (%s, %s, 'pricing_manager', 'approved', CURRENT_TIMESTAMP)
                """,
                (appr_id, dec_id),
            )

            # Insert Gate
            conn.execute(
                """
                INSERT INTO pricing.exploration_gates
                    (gate_id, tenant_id, budget_limit, effective_from, effective_to, approved_by,
                     approval_decision_id, approval_id, rollback_condition, decision_policy_version_id)
                VALUES
                    (%s, %s, 1000.00, '2026-01-01 00:00:00+00', '2026-12-31 23:59:59+00', 'pricing_manager',
                     %s, %s, 'gross_margin_drop > 0.05', %s)
                """,
                (gate_id, TENANT_A, dec_id, appr_id, policy_v_id),
            )

            # Initial budget consumed is 0
            consumed = conn.execute(
                "SELECT budget_consumed FROM pricing.exploration_gates WHERE gate_id = %s", (gate_id,)
            ).fetchone()[0]
            assert consumed == 0

            # Insert decision 1 (budget consumed 300)
            exp_dec_1 = str(uuid4())
            conn.execute(
                """
                INSERT INTO workflow.decisions (decision_id, entity_type, entity_id, policy_version_id, created_by)
                VALUES (%s, 'price_exploration', 'item-1', %s, 'bandit')
                """,
                (exp_dec_1, policy_v_id),
            )
            conn.execute(
                """
                INSERT INTO pricing.exploration_decisions
                    (decision_id, gate_id, tenant_id, sku_id, baseline_price, explored_price, budget_consumed, algorithm)
                VALUES
                    (%s, %s, %s, 'sku-101', 20.00, 22.00, 300.00, 'THOMPSON_SAMPLING')
                """,
                (exp_dec_1, gate_id, TENANT_A),
            )

            # Trigger accrued budget to 300
            consumed = conn.execute(
                "SELECT budget_consumed FROM pricing.exploration_gates WHERE gate_id = %s", (gate_id,)
            ).fetchone()[0]
            assert consumed == 300.00

            # Insert decision 2 (budget consumed 700) -> reaches limit 1000
            exp_dec_2 = str(uuid4())
            conn.execute(
                """
                INSERT INTO workflow.decisions (decision_id, entity_type, entity_id, policy_version_id, created_by)
                VALUES (%s, 'price_exploration', 'item-2', %s, 'bandit')
                """,
                (exp_dec_2, policy_v_id),
            )
            conn.execute(
                """
                INSERT INTO pricing.exploration_decisions
                    (decision_id, gate_id, tenant_id, sku_id, baseline_price, explored_price, budget_consumed, algorithm)
                VALUES
                    (%s, %s, %s, 'sku-102', 20.00, 24.00, 700.00, 'THOMPSON_SAMPLING')
                """,
                (exp_dec_2, gate_id, TENANT_A),
            )

            consumed = conn.execute(
                "SELECT budget_consumed FROM pricing.exploration_gates WHERE gate_id = %s", (gate_id,)
            ).fetchone()[0]
            assert consumed == 1000.00

            # Decision 3 exceeds budget limit -> rejected
            exp_dec_3 = str(uuid4())
            conn.execute(
                """
                INSERT INTO workflow.decisions (decision_id, entity_type, entity_id, policy_version_id, created_by)
                VALUES (%s, 'price_exploration', 'item-3', %s, 'bandit')
                """,
                (exp_dec_3, policy_v_id),
            )
            with pytest.raises(psycopg.errors.DatabaseError):
                conn.execute(
                    """
                    INSERT INTO pricing.exploration_decisions
                        (decision_id, gate_id, tenant_id, sku_id, baseline_price, explored_price, budget_consumed, algorithm)
                    VALUES
                        (%s, %s, %s, 'sku-103', 20.00, 25.00, 50.00, 'THOMPSON_SAMPLING')
                    """,
                    (exp_dec_3, gate_id, TENANT_A),
                )

    def test_decisions_are_append_only(self, gate_db) -> None:
        psycopg = gate_db.server.psycopg
        gate_id = str(uuid4())
        dec_id = str(uuid4())
        appr_id = str(uuid4())
        exp_dec_1 = str(uuid4())
        policy_v_id = f"four-light-policy-v1:{TENANT_A}"

        with gate_db.connect(autocommit=True) as conn:
            conn.execute(
                "INSERT INTO workflow.decisions (decision_id, entity_type, entity_id, policy_version_id, created_by) VALUES (%s, 'price_plan', 'plan-1', %s, 'admin')",
                (dec_id, policy_v_id),
            )
            conn.execute(
                "INSERT INTO workflow.approvals (approval_id, decision_id, approver_id, approval_status, approved_at) VALUES (%s, %s, 'pricing_manager', 'approved', CURRENT_TIMESTAMP)",
                (appr_id, dec_id),
            )
            conn.execute(
                """
                INSERT INTO pricing.exploration_gates
                    (gate_id, tenant_id, budget_limit, effective_from, effective_to, approved_by,
                     approval_decision_id, approval_id, rollback_condition, decision_policy_version_id)
                VALUES
                    (%s, %s, 500.00, '2026-01-01 00:00:00+00', '2026-12-31 23:59:59+00', 'pricing_manager',
                     %s, %s, 'gross_margin_drop > 0.05', %s)
                """,
                (gate_id, TENANT_A, dec_id, appr_id, policy_v_id),
            )
            conn.execute(
                "INSERT INTO workflow.decisions (decision_id, entity_type, entity_id, policy_version_id, created_by) VALUES (%s, 'price_exploration', 'item-1', %s, 'bandit')",
                (exp_dec_1, policy_v_id),
            )
            conn.execute(
                """
                INSERT INTO pricing.exploration_decisions
                    (decision_id, gate_id, tenant_id, sku_id, baseline_price, explored_price, budget_consumed, algorithm)
                VALUES
                    (%s, %s, %s, 'sku-101', 20.00, 22.00, 100.00, 'THOMPSON_SAMPLING')
                """,
                (exp_dec_1, gate_id, TENANT_A),
            )

            # UPDATE rejected
            with pytest.raises(psycopg.errors.DatabaseError):
                conn.execute(
                    "UPDATE pricing.exploration_decisions SET budget_consumed = 50.00 WHERE decision_id = %s",
                    (exp_dec_1,),
                )

            # DELETE rejected
            with pytest.raises(psycopg.errors.DatabaseError):
                conn.execute(
                    "DELETE FROM pricing.exploration_decisions WHERE decision_id = %s",
                    (exp_dec_1,),
                )

    def test_gate_controlled_update(self, gate_db) -> None:
        psycopg = gate_db.server.psycopg
        gate_id = str(uuid4())
        dec_id = str(uuid4())
        appr_id = str(uuid4())
        policy_v_id = f"four-light-policy-v1:{TENANT_A}"

        with gate_db.connect(autocommit=True) as conn:
            conn.execute(
                "INSERT INTO workflow.decisions (decision_id, entity_type, entity_id, policy_version_id, created_by) VALUES (%s, 'price_plan', 'plan-1', %s, 'admin')",
                (dec_id, policy_v_id),
            )
            conn.execute(
                "INSERT INTO workflow.approvals (approval_id, decision_id, approver_id, approval_status, approved_at) VALUES (%s, %s, 'pricing_manager', 'approved', CURRENT_TIMESTAMP)",
                (appr_id, dec_id),
            )
            conn.execute(
                """
                INSERT INTO pricing.exploration_gates
                    (gate_id, tenant_id, budget_limit, effective_from, effective_to, approved_by,
                     approval_decision_id, approval_id, rollback_condition, decision_policy_version_id)
                VALUES
                    (%s, %s, 500.00, '2026-01-01 00:00:00+00', '2026-12-31 23:59:59+00', 'pricing_manager',
                     %s, %s, 'gross_margin_drop > 0.05', %s)
                """,
                (gate_id, TENANT_A, dec_id, appr_id, policy_v_id),
            )

            # DELETE gate rejected
            with pytest.raises(psycopg.errors.DatabaseError):
                conn.execute("DELETE FROM pricing.exploration_gates WHERE gate_id = %s", (gate_id,))

            # Extending effective_to rejected
            with pytest.raises(psycopg.errors.DatabaseError):
                conn.execute(
                    "UPDATE pricing.exploration_gates SET effective_to = '2027-12-31 23:59:59+00' WHERE gate_id = %s",
                    (gate_id,),
                )

            # Shortening effective_to allowed
            conn.execute(
                "UPDATE pricing.exploration_gates SET effective_to = '2026-06-30 23:59:59+00' WHERE gate_id = %s",
                (gate_id,),
            )

            # Revoking gate allowed once
            conn.execute(
                "UPDATE pricing.exploration_gates SET revoked_at = CURRENT_TIMESTAMP WHERE gate_id = %s",
                (gate_id,),
            )

            # Un-revoking gate rejected
            with pytest.raises(psycopg.errors.DatabaseError):
                conn.execute(
                    "UPDATE pricing.exploration_gates SET revoked_at = NULL WHERE gate_id = %s",
                    (gate_id,),
                )
