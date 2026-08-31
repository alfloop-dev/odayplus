"""identity server-side session material

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-30 16:30:00.000000

Phase: P2 (Wave Auth 1 — Password-first Web)
Task: ODP-WEB-PASSWORD-FIRST-LOGIN-001

Expand-only migration: adds the server-side bearer and identity fields used by
the opaque Web session cookie.  The SQL remains idempotent so environments
that received the additive schema during an earlier controlled rehearsal can
still advance their Alembic head safely.
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(
        dir_path,
        "../000012_identity_session_server_secrets.sql",
    )
    with open(sql_file_path, encoding="utf-8") as sql_file:
        op.execute(sa.text(sql_file.read()))


def downgrade() -> None:
    # Expand-only: rollback disables the password-first path without dropping
    # session data or columns that a concurrently running revision may use.
    op.execute(sa.text("SELECT 1"))
