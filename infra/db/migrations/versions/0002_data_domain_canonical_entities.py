"""data domain canonical entities

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-07 04:50:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_file_path = Path(__file__).resolve().parents[1] / "000002_data_domain_canonical_entities.sql"
    op.execute(sa.text(sql_file_path.read_text(encoding="utf-8")))


def downgrade() -> None:
    # The migration is additive and idempotent; do not drop canonical data tables
    # that may have been created by the baseline migration.
    op.execute(sa.text("SELECT 1"))
