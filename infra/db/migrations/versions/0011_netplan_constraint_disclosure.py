"""Durable receipts for accepting unmodelled network plan constraint classes.

Revision ID: 0011
Revises: 0010
Task: ODP-NETPLAN-DISCLOSURE-APPROVAL-001

Applies `000017_netplan_constraint_disclosure.sql`, which creates the
append-only acknowledgement table and seeds the `netplan_action` disclosure
policy for existing and future tenants.

`chk_decision_policy_kind` already lists `netplan_action` (it was written into
000014 and re-asserted by 0010), so unlike revision 0010 this one does not have
to drop and rebuild the check before the seed can insert.

The SQL is executed through the raw driver connection for the same reason 0010
does it: the file contains dollar-quoted function bodies, and SQLAlchemy's text
construct would try to interpret the `:` and `$` sequences inside them.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _canonical_sql() -> str:
    sql_file_path = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        "../000017_netplan_constraint_disclosure.sql",
    )
    with open(sql_file_path, encoding="utf-8") as handle:
        return handle.read()


def upgrade() -> None:
    connection = op.get_bind()
    raw_conn = getattr(connection, "connection", None)
    driver_conn = getattr(raw_conn, "driver_connection", raw_conn)
    sql_content = _canonical_sql()
    if driver_conn is not None and hasattr(driver_conn, "cursor"):
        with driver_conn.cursor() as cursor:
            cursor.execute(sql_content)
    else:
        op.execute(sa.text(sql_content))


def downgrade() -> None:
    """Drop the table, the trigger and the seed function.

    The seeded policy rows are deliberately *not* deleted. `workflow.decisions`
    and any approval that resolved this policy reference the version by id, and
    ODP-AC-BR-003 requires a retired policy version retained rather than
    removed -- a downgrade that erased them would leave approvals unable to name
    what governed them.
    """
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_seed_netplan_disclosure_policy ON core.tenants"
        )
    )
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS workflow.seed_netplan_disclosure_policy_on_tenant()"
        )
    )
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS workflow.seed_netplan_disclosure_policy(UUID)")
    )
    op.execute(
        sa.text(
            "DROP TABLE IF EXISTS network.netplan_constraint_acknowledgements CASCADE"
        )
    )
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS network.reject_netplan_disclosure_ack_rewrite()"
        )
    )
