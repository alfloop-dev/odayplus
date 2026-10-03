"""Add record lifecycle and timestamps to core.machine_status_events.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-03

Task: ODP-CDC-MACHINE-EVENT-LIFECYCLE-001
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_file_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "../000027_machine_status_events_record_lifecycle.sql",
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
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'core' AND table_name = 'machine_status_events'
                ) THEN
                    ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS record_status;
                    ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS created_at;
                    ALTER TABLE core.machine_status_events DROP COLUMN IF EXISTS updated_at;
                    DROP INDEX IF EXISTS core.idx_machine_status_events_record_status;
                END IF;
            END $$;
            """
        )
    )
