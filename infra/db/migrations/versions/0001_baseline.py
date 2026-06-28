"""baseline migration

Revision ID: 0001
Revises: None
Create Date: 2026-06-27 11:10:15.000000

"""
import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Read the DDL script
    dir_path = os.path.dirname(os.path.realpath(__file__))
    sql_file_path = os.path.join(dir_path, '../000001_baseline_canonical_schema.sql')
    
    with open(sql_file_path, encoding='utf-8') as f:
        sql_content = f.read()

    # Split by semicolon to execute individual statements, or execute as a single block
    # PostgreSQL supports running multi-statement blocks via raw execute
    op.execute(sa.text(sql_content))


def downgrade() -> None:
    # Drop schemas cascade
    op.execute("DROP SCHEMA IF EXISTS core CASCADE")
    op.execute("DROP SCHEMA IF EXISTS workflow CASCADE")
    op.execute("DROP SCHEMA IF EXISTS expansion CASCADE")
    op.execute("DROP SCHEMA IF EXISTS operations CASCADE")
    op.execute("DROP SCHEMA IF EXISTS pricing CASCADE")
    op.execute("DROP SCHEMA IF EXISTS marketing CASCADE")
    op.execute("DROP SCHEMA IF EXISTS asset CASCADE")
    op.execute("DROP SCHEMA IF EXISTS network CASCADE")
    op.execute("DROP SCHEMA IF EXISTS learning CASCADE")
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
    op.execute("DROP SCHEMA IF EXISTS geo CASCADE")
