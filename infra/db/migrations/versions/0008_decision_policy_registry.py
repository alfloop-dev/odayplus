"""decision policy registry and forecast alert policy binding

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-01 14:40:00.000000

Phase: Gap Remediation Wave 2
Task: ODP-FORECAST-ALERT-POLICY-001

Expand-only migration: applies ``000014_decision_policy_registry.sql``, which
creates ``workflow.decision_policies``, binds
``workflow.decisions.policy_version_id`` and the ForecastOps alert columns to
it, and seeds the ``four-light-policy`` retrofit and v1 rows per tenant.

The DDL landed with ODP-DECISION-POLICY-CORE-001 but no revision applied it, so
``alembic upgrade head`` stopped at 0007 and a freshly provisioned database
never got the table. ``SqlDecisionPolicyRepository`` then has nothing to
resolve against, and because the four-light path is fail-closed
(``PolicyResolutionError`` rather than built-in thresholds) every forecast
would refuse to raise an alert. Reachability from head is the deliverable here.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, "../000014_decision_policy_registry.sql")

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


def downgrade() -> None:
    # Expand-only: downgrade preserves the registry and the seeded policy rows.
    # Dropping them would strip the policy version that existing alerts cite.
    op.execute(sa.text("SELECT 1"))
