"""Register and seed the production model-performance policy.

Revision ID: 0010
Revises: 0009
Task: ODP-LEARNINGHUB-BASELINE-DRIFT-002

The decision-policy registry was already deployed by revision 0008 on some
databases. Replaying the canonical registry SQL here keeps those databases
aligned with fresh installs: it expands the policy-kind check, installs the
model-performance seed function and seeds the policy for existing tenants.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0008's original check did not know this policy kind. Rebuild it before
    # replaying the idempotent canonical registry SQL so the new seed inserts.
    op.execute(
        sa.text(
            "ALTER TABLE workflow.decision_policies "
            "DROP CONSTRAINT IF EXISTS chk_decision_policy_kind"
        )
    )
    sql_file_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "../000014_decision_policy_registry.sql",
    )
    with open(sql_file_path, encoding="utf-8") as f:
        sql_content = f.read()

    connection = op.get_bind()
    raw_conn = getattr(connection, "connection", None)
    driver_conn = getattr(raw_conn, "driver_connection", raw_conn)
    if driver_conn is not None and hasattr(driver_conn, "cursor"):
        with driver_conn.cursor() as cursor:
            cursor.execute(sql_content)
    else:
        op.execute(sa.text(sql_content))

    # On a database already at 0008, the replay above seeds the new row while
    # the old check is absent. On a fresh database, CREATE TABLE IF NOT EXISTS
    # leaves the existing check absent after the drop as well. Add the final
    # constraint only after the idempotent seed has completed.
    op.execute(
        sa.text(
            """
            ALTER TABLE workflow.decision_policies
            ADD CONSTRAINT chk_decision_policy_kind CHECK (
                policy_kind IN ('forecast_alert', 'heatzone_merge', 'heatzone_absorption',
                                'sitescore_recommendation', 'price_exploration', 'netplan_action',
                                'model_performance_drift')
            )
            """
        )
    )


def downgrade() -> None:
    # Expand-only: existing validation runs cite this policy version.
    op.execute(sa.text("SELECT 1"))
