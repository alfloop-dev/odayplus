"""Expand-only durable legacy OIDC session migration/revocation identity.

Revision ID: 0026
Revises: 0025
"""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (Path(__file__).resolve().parents[1] / "000033_legacy_oidc_session_migrations.sql").read_text()
    connection = op.get_bind()
    raw = getattr(connection, "connection", None)
    driver = getattr(raw, "driver_connection", raw)
    if driver is not None and hasattr(driver, "cursor"):
        with driver.cursor() as cursor:
            cursor.execute(sql)
    else:
        op.execute(sa.text(sql))


def downgrade() -> None:
    # Removing tombstones would permit legacy-cookie resurrection.
    op.execute(sa.text("SELECT 1"))
