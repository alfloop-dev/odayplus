"""Add explicit manual-entry approval and durable account-authorization waiting."""

from pathlib import Path

from alembic import op

revision: str = "0027"
down_revision: str = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = (
        Path(__file__).resolve().parents[1] / "000034_native_intake_auth_wait.sql"
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
    # Expand-only: retain approval references and waiting-state evidence.
    op.execute("SELECT 1")
