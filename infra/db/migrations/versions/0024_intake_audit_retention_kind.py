"""Permit versioned Intake audit retention records without supplying defaults."""

from pathlib import Path

from alembic import op

revision: str = "0024"
down_revision: str = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[1] / "000030_intake_audit_retention_kind.sql"
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
    # Expand-only: retaining kinds preserves versions cited by existing SLAs.
    op.execute("SELECT 1")
