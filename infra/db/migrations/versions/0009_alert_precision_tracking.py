"""alert precision and lead time tracking schema

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-01 16:30:00.000000

Phase: Gap Remediation Wave 2
Task: ODP-FORECAST-ALERT-PRECISION-001

Expand-only migration: adds deterioration_confirmed_at and disposition columns
to operations.alerts per ODP-FR-FCT-006.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, "../000016_alert_precision_tracking.sql")

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
    # Expand-only: downgrade preserves the columns.
    op.execute(sa.text("SELECT 1"))
