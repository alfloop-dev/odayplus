"""official real-estate outcome ingestion

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_path = (
        Path(__file__).resolve().parents[1]
        / "000009_official_real_estate_outcomes.sql"
    )
    op.execute(sa.text(sql_path.read_text(encoding="utf-8")))


def downgrade() -> None:
    # Official observations are immutable evidence. Downgrade intentionally
    # preserves landed rows; disabling readers is handled by release controls.
    op.execute(sa.text("SELECT 1"))
