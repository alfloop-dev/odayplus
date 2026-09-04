"""Price exploration gate and decision tracking schema

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-03 12:00:00.000000

Phase: ODP Remediation · W6 Missing Capability
Task: ODP-PRICE006-BANDIT-GATED-001

Creates pricing.exploration_gates and pricing.exploration_decisions tables with
budget accrual and authorization immutability triggers per ODP-FR-PRICE-006.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, "../000017_price_exploration_gate.sql")

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
    op.execute(sa.text("SELECT 1"))
