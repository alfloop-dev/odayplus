"""Persist intervention adjustment lineage and audit metadata.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-05

Task: ODP-INTV006-ADJUST-WORKFLOW-001
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_file_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "../000023_intervention_adjust_lineage.sql",
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
    # Expand-only: adjustment lineage is part of the audit record and must not
    # be silently removed by an ordinary downgrade.
    op.execute(sa.text("SELECT 1"))
