"""Make snapshot quality nullable and preserve legacy quality semantics.

Revision ID: 0011
Revises: 0010
Task: ODP-AVM-QUALITY-NULLABLE-001

Existing rows are not rewritten. Their old ``1.00`` values remain available, but
``quality_score_status`` identifies them as ``legacy_unknown`` because the former
schema could not tell an omitted score from a measured perfect score.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sql_file_path = Path(__file__).resolve().parents[1] / "000017_avm_quality_score_nullable.sql"
    op.execute(sa.text(sql_file_path.read_text(encoding="utf-8")))


def downgrade() -> None:
    # Expand-only: reverting would silently reintroduce the perfect-score fallback.
    op.execute(sa.text("SELECT 1"))
