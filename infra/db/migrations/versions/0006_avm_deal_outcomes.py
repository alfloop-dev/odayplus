"""avm deal outcomes and valuation calibration schema

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-01 08:30:00.000000

Phase: Gap Remediation Wave 1
Task: ODP-AVM-DEAL-OUTCOME-001

Expand-only migration: creates ``avm`` schema and ``avm.deal_outcomes`` table.
Stores authoritative deal outcomes, settlement prices, no-deal reasons, and maps
them to valuation baselines via valuation_id.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, "../000014_avm_deal_outcomes.sql")

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
    # Expand-only: downgrade preserves the schema and table.
    op.execute(sa.text("SELECT 1"))
