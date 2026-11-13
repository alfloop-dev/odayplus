"""Extend native matching proposal policy vocabulary without issuing authority."""

from pathlib import Path

from alembic import op

revision: str = "0029"
down_revision: str = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[1]
        / "000036_native_matching_proposal_authority_kind.sql"
    ).read_text()
    connection = op.get_bind()
    raw = getattr(connection, "connection", None)
    driver = getattr(raw, "driver_connection", raw)
    if driver is not None and hasattr(driver, "cursor"):
        with driver.cursor() as cursor:
            cursor.execute(sql)
    else:
        op.execute(sql)


def downgrade() -> None:
    # Expand only: retain vocabulary, protected versions and all historical rows.
    op.execute("SELECT 1")
