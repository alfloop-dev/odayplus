"""Expand-only committed audit WORM outbox; never backfill delivery claims.

Revision ID: 0025
Revises: 0024
Task: ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001

The deployment migration job reaches this FK before runtime bootstrap. Install
its authoritative, idempotent runtime prerequisite inside the same transaction;
never invent publication intent for pre-existing events. Preserve search_path
for later revisions and Alembic's version write.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    migrations = Path(__file__).resolve().parents[1]
    prerequisite = (migrations / "000008_postgresql_runtime_persistence.sql").read_text(encoding="utf-8")
    outbox = (migrations / "000032_audit_worm_outbox.sql").read_text(encoding="utf-8")
    connection = op.get_bind()
    saved_search_path = connection.execute(
        sa.text("SELECT current_setting('search_path')")
    ).scalar_one()
    raw = getattr(connection, "connection", None)
    driver = getattr(raw, "driver_connection", raw)
    if driver is not None and hasattr(driver, "cursor"):
        with driver.cursor() as cursor:
            cursor.execute(prerequisite)
            cursor.execute(outbox)
    else:
        op.execute(sa.text(prerequisite))
        op.execute(sa.text(outbox))
    connection.execute(
        sa.text("SELECT set_config('search_path', :value, true)"),
        {"value": saved_search_path},
    )


def downgrade() -> None:
    # Never remove publication intent/unknown records or reopen attempts.
    op.execute(sa.text("SELECT 1"))
