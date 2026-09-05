"""Manual corrections, audit trail and rollback schema.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-03 16:00:00.000000

Phase: ODP Remediation · W4 Blind Spots
Task: ODP-INT-MANUAL-CORRECTION-AUDIT-001

Creates odp_runtime.durable_manual_corrections table and ensures revision/tenant_id
columns exist on core.address_locations per INT-006.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, "../000021_manual_corrections_audit_schema.sql")

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
    connection = op.get_bind()
    raw_conn = getattr(connection, "connection", None)
    driver_conn = getattr(raw_conn, "driver_connection", raw_conn)
    rollback_sql = """
    DROP TABLE IF EXISTS odp_runtime.durable_manual_corrections;
    """
    if driver_conn is not None and hasattr(driver_conn, "cursor"):
        with driver_conn.cursor() as cursor:
            cursor.execute(rollback_sql)
    else:
        op.execute(sa.text(rollback_sql))
