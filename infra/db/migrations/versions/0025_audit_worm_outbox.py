"""Expand-only committed audit WORM outbox; never backfill delivery claims.

Revision ID: 0025
Revises: 0024
Task: ODP-DEV-SMOKE-CREDENTIAL-RECOVERY-IMPLEMENTATION-001
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
    sql = (Path(__file__).resolve().parents[1] / "000032_audit_worm_outbox.sql").read_text()
    connection = op.get_bind()
    raw = getattr(connection, "connection", None)
    driver = getattr(raw, "driver_connection", raw)
    if driver is not None and hasattr(driver, "cursor"):
        with driver.cursor() as cursor:
            cursor.execute(sql)
    else:
        op.execute(sa.text(sql))


def downgrade() -> None:
    # Never remove publication intent/unknown records or reopen attempts.
    op.execute(sa.text("SELECT 1"))
