"""forecastops feedback mechanism schema

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-01 11:10:00.000000

Phase: Gap Remediation Wave 1
Task: ODP-FORECAST-FEEDBACK-001

Expand-only migration: creates ``forecastops`` schema and
``forecastops.feedback``. Holds the three feedback paths of ODP-FR-FCT-008
(CONTEXT_ANNOTATION, OUTCOME_CORRECTION, ALERT_DISPOSITION) as an audit trail;
per ODP-BR-GOV-001 no row here ever overwrites a prediction or decision column.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, "../000015_forecastops_feedback.sql")

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
