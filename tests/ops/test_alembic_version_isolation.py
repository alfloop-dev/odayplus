"""Exercise the real Alembic env against isolated PostgreSQL databases.

The tiny revision chain isolates version bookkeeping from PostGIS/product DDL.
It is not evidence that any live database has been inspected or migrated.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.requires_live_env
ROOT = Path(__file__).resolve().parents[2]
APP_TABLE = "oday_plus_alembic_version"
DAGSTER_REVISION = "29b539ebc72a"


@pytest.fixture
def migration_env(tmp_path, intake_blank_db):
    scripts = tmp_path / "migrations"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    shutil.copyfile(ROOT / "infra/db/migrations/env.py", scripts / "env.py")
    (versions / "0001.py").write_text(
        "from alembic import op\n"
        "revision = '0001'\ndown_revision = None\n"
        "def upgrade():\n"
        "    op.execute('CREATE TABLE public.app_probe (id integer PRIMARY KEY)')\n"
        "def downgrade():\n"
        "    op.execute('DROP TABLE public.app_probe')\n"
    )
    (versions / "0002.py").write_text(
        "from alembic import op\n"
        "revision = '0002'\ndown_revision = '0001'\n"
        "def upgrade():\n"
        "    op.execute('ALTER TABLE public.app_probe ADD COLUMN value text')\n"
        "def downgrade():\n"
        "    op.execute('ALTER TABLE public.app_probe DROP COLUMN value')\n"
    )
    config = Config()
    config.set_main_option("script_location", str(scripts))
    config.set_main_option(
        "sqlalchemy.url", intake_blank_db.url(driver="psycopg").replace("%", "%%")
    )
    return config, intake_blank_db


def seed_versions(db, revisions, table="alembic_version"):
    assert table in {"alembic_version", APP_TABLE}
    with db.connect() as conn:
        conn.execute(f"CREATE TABLE public.{table} (version_num varchar(32) PRIMARY KEY)")
        for revision in revisions:
            conn.execute(f"INSERT INTO public.{table} VALUES (%s)", (revision,))


def read_versions(db, table):
    assert table in {"alembic_version", APP_TABLE}
    with db.connect() as conn:
        return {row[0] for row in conn.execute(f"SELECT version_num FROM public.{table}")}


def test_foreign_version_and_data_survive_app_upgrade_and_downgrade(migration_env):
    config, db = migration_env
    seed_versions(db, [DAGSTER_REVISION])
    with db.connect() as conn:
        conn.execute("CREATE TABLE public.runs (id integer PRIMARY KEY)")
        conn.execute("INSERT INTO public.runs VALUES (42)")

    command.upgrade(config, "head")
    assert read_versions(db, APP_TABLE) == {"0002"}
    assert read_versions(db, "alembic_version") == {DAGSTER_REVISION}
    command.downgrade(config, "-1")
    assert read_versions(db, APP_TABLE) == {"0001"}
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # A normal repeated deploy must be a no-op.
    with db.connect() as conn:
        assert conn.execute("SELECT id FROM public.runs").fetchall() == [(42,)]
    assert read_versions(db, "alembic_version") == {DAGSTER_REVISION}


def test_fresh_database_uses_application_version_table(migration_env):
    config, db = migration_env
    command.upgrade(config, "head")
    assert read_versions(db, APP_TABLE) == {"0002"}
    with db.connect() as conn:
        assert conn.execute("SELECT to_regclass('public.alembic_version')").fetchone() == (None,)


def test_existing_application_legacy_history_is_not_replayed(migration_env):
    config, db = migration_env
    seed_versions(db, ["0001"])
    with db.connect() as conn:
        conn.execute("CREATE TABLE public.app_probe (id integer PRIMARY KEY)")
        conn.execute("INSERT INTO public.app_probe VALUES (7)")
    command.upgrade(config, "head")
    assert read_versions(db, "alembic_version") == {"0002"}
    with db.connect() as conn:
        assert conn.execute("SELECT id FROM public.app_probe").fetchall() == [(7,)]
        assert conn.execute(f"SELECT to_regclass('public.{APP_TABLE}')").fetchone() == (None,)


def test_mixed_legacy_revisions_fail_without_writes(migration_env):
    config, db = migration_env
    seed_versions(db, ["0001", DAGSTER_REVISION])
    with pytest.raises(RuntimeError, match="mixed"):
        command.upgrade(config, "head")
    assert read_versions(db, "alembic_version") == {"0001", DAGSTER_REVISION}
    with db.connect() as conn:
        assert conn.execute(f"SELECT to_regclass('public.{APP_TABLE}')").fetchone() == (None,)


def test_two_application_version_tables_fail_without_writes(migration_env):
    config, db = migration_env
    seed_versions(db, ["0001"])
    seed_versions(db, ["0002"], APP_TABLE)
    with pytest.raises(RuntimeError, match="both"):
        command.upgrade(config, "head")
    assert read_versions(db, "alembic_version") == {"0001"}
    assert read_versions(db, APP_TABLE) == {"0002"}


def test_foreign_history_with_existing_app_schema_requires_diagnosis(migration_env):
    config, db = migration_env
    seed_versions(db, [DAGSTER_REVISION])
    with db.connect() as conn:
        conn.execute("CREATE SCHEMA core")
        conn.execute("CREATE TABLE core.tenants (tenant_id integer PRIMARY KEY)")
        conn.execute("INSERT INTO core.tenants VALUES (9)")
    with pytest.raises(RuntimeError, match="existing application schema"):
        command.upgrade(config, "head")
    assert read_versions(db, "alembic_version") == {DAGSTER_REVISION}
    with db.connect() as conn:
        assert conn.execute("SELECT tenant_id FROM core.tenants").fetchall() == [(9,)]
        assert conn.execute(f"SELECT to_regclass('public.{APP_TABLE}')").fetchone() == (None,)


def test_unknown_application_revision_remains_an_error(migration_env):
    config, db = migration_env
    seed_versions(db, ["unknown-app-revision"], APP_TABLE)
    with pytest.raises(CommandError, match="locate revision"):
        command.upgrade(config, "head")
    assert read_versions(db, APP_TABLE) == {"unknown-app-revision"}


def test_failed_upgrade_rolls_back_app_tables_and_preserves_foreign_history(migration_env):
    config, db = migration_env
    seed_versions(db, [DAGSTER_REVISION])
    versions = Path(config.get_main_option("script_location")) / "versions"
    (versions / "0003.py").write_text(
        "from alembic import op\n"
        "revision = '0003'\ndown_revision = '0002'\n"
        "def upgrade():\n    op.execute('SELECT 1 / 0')\n"
        "def downgrade():\n    pass\n"
    )
    with pytest.raises(DBAPIError):
        command.upgrade(config, "head")
    assert read_versions(db, "alembic_version") == {DAGSTER_REVISION}
    with db.connect() as conn:
        assert conn.execute(f"SELECT to_regclass('public.{APP_TABLE}')").fetchone() == (None,)
        assert conn.execute("SELECT to_regclass('public.app_probe')").fetchone() == (None,)


def test_search_path_does_not_select_another_schema_version_table(migration_env):
    config, db = migration_env
    seed_versions(db, [DAGSTER_REVISION])
    with db.connect() as conn:
        conn.execute("CREATE SCHEMA shadow")
        conn.execute("CREATE TABLE shadow.alembic_version (version_num varchar(32))")
        conn.execute("INSERT INTO shadow.alembic_version VALUES ('0001')")
    url = make_url(db.url(driver="psycopg")).update_query_dict(
        {"options": "-csearch_path=shadow"}
    )
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    command.upgrade(config, "head")
    assert read_versions(db, APP_TABLE) == {"0002"}
    assert read_versions(db, "alembic_version") == {DAGSTER_REVISION}
    with db.connect() as conn:
        assert conn.execute("SELECT version_num FROM shadow.alembic_version").fetchall() == [("0001",)]
