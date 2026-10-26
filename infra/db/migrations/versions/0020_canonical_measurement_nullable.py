"""Make canonical measurement columns nullable and add schema version lineage.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-13

Task: ODP-CANONICAL-MEASUREMENT-NULLABLE-CUTOVER-001
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_file_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "../000026_canonical_measurement_nullable.sql",
    )
    with open(sql_file_path, encoding="utf-8") as migration_file:
        sql_content = migration_file.read()

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
