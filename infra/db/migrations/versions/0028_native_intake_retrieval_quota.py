"""Add anonymous global native retrieval rate and inflight controls."""

from pathlib import Path

from alembic import op

revision: str = "0028"
down_revision: str = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[1] / "000035_native_intake_retrieval_quota.sql"
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
    # Expand only: never erase outstanding rate/IO controls during rollback.
    op.execute("SELECT 1")
