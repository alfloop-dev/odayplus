"""WorkOrder root_cause column reserved disposition.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-03 12:00:00.000000

Phase: ODP Remediation · W4 Blind Spots
Task: ODP-FCT-ROOT-CAUSE-CONTRACT-001

Documentation and metadata migration: marks core.work_orders.root_cause as
RESERVED (unproduced) per ODP-FR-FCT-004 disposition.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, "../000018_work_orders_root_cause_disposition.sql")

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
    # Clear the column comment on rollback while preserving the nullable column.
    connection = op.get_bind()
    raw_conn = getattr(connection, "connection", None)
    driver_conn = getattr(raw_conn, "driver_connection", raw_conn)
    rollback_sql = "COMMENT ON COLUMN core.work_orders.root_cause IS NULL;"
    if driver_conn is not None and hasattr(driver_conn, "cursor"):
        with driver_conn.cursor() as cursor:
            cursor.execute(rollback_sql)
    else:
        op.execute(sa.text(rollback_sql))
