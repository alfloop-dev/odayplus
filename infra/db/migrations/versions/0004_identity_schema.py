"""identity schema expand migration

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-30 08:09:00.000000

Phase: P1 (Wave Auth 1 — Identity Core)
Task: ODP-WEB-LOCAL-IDENTITY-CORE-001
Contract: ODP-WEB-PASSWORD-FIRST-AUTH-CONTRACT-001 §2.2, §9

Expand-only migration: creates ``identity`` schema and all contract tables.
No existing tables, schemas, or data are modified. Rollback preserves empty
tables and disables the new code path (§9 P1 rollback strategy).
"""
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: str = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, '../000011_identity_schema.sql')

    with open(sql_file_path, encoding='utf-8') as f:
        sql_content = f.read()

    op.execute(sa.text(sql_content))


def downgrade() -> None:
    # Expand-only: downgrade preserves the schema for P1 rollback
    # (Contract §9 — P1–P4 不得執行破壞性 down migration).
    # The tables are empty and the code path is controlled by
    # ODP_AUTH_LOCAL_PASSWORD_ENABLED (default false).
    # 與 0003 一致：回退保留已落地結構，以關閉新路徑取代刪除。
    op.execute(sa.text("SELECT 1"))
