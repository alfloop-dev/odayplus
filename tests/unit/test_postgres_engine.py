"""Unit tests for the durable PostgreSQL engine's pure dialect/DSN layer.

These cover the driver-independent surface of ODP-GAP-PERSIST-001 — placeholder
translation, DDL portability, Cloud SQL / DSN wiring, and secret redaction — so
the engine's correctness is proven without a running PostgreSQL server. The live
round-trip against real PostgreSQL is in
``tests/integration/test_postgres_persistence.py`` (marked ``requires_live_env``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.infrastructure.persistence.engine import _SCHEMA_FILES as SQLITE_SCHEMA_FILES
from shared.infrastructure.persistence.postgres_engine import (
    _SCHEMA_FILES,
    MigrationChecksumMismatch,
    PostgresConfigurationError,
    build_postgres_conninfo,
    postgres_dsn_configured,
    redact_conninfo,
    split_sql_statements,
    sqlite_ddl_to_postgres,
    translate_placeholders,
)

_CLOUDSQL_INSTANCE = "alfaloop-data-project:asia-east1:oday-plus-dev-postgres"


# -- placeholder translation ------------------------------------------------


def test_translate_placeholders_rewrites_qmark_to_pyformat():
    sql = "INSERT INTO t(a, b) VALUES (?, ?) ON CONFLICT(a) DO UPDATE SET b = ?"
    assert translate_placeholders(sql, has_params=True) == (
        "INSERT INTO t(a, b) VALUES (%s, %s) ON CONFLICT(a) DO UPDATE SET b = %s"
    )


def test_translate_placeholders_leaves_paramless_sql_untouched():
    # No parameters -> statement is sent verbatim; a literal % must NOT be doubled.
    sql = "SELECT * FROM t WHERE ratio LIKE '%pct'"
    assert translate_placeholders(sql, has_params=False) == sql


def test_translate_placeholders_escapes_literal_percent_when_binding():
    sql = "SELECT ? WHERE name LIKE '%x%'"
    assert translate_placeholders(sql, has_params=True) == "SELECT %s WHERE name LIKE '%%x%%'"


# -- DDL portability --------------------------------------------------------


def test_sqlite_ddl_to_postgres_rewrites_autoincrement_and_blob():
    ddl = "CREATE TABLE t (\n  seq INTEGER PRIMARY KEY AUTOINCREMENT,\n  data BLOB NOT NULL\n)"
    out = sqlite_ddl_to_postgres(ddl)
    assert "BIGSERIAL PRIMARY KEY" in out
    assert "AUTOINCREMENT" not in out
    assert "BYTEA NOT NULL" in out
    assert "BLOB" not in out


def test_sqlite_ddl_to_postgres_is_case_insensitive():
    out = sqlite_ddl_to_postgres("x integer primary key autoincrement, y blob")
    assert "BIGSERIAL PRIMARY KEY" in out
    assert "BYTEA" in out


def test_sqlite_ddl_to_postgres_makes_add_column_idempotent():
    # SQLite tolerates re-adding a column; Postgres needs IF NOT EXISTS to match
    # that idempotent intent (migration 000007).
    out = sqlite_ddl_to_postgres("ALTER TABLE durable_jobs ADD COLUMN attempts INTEGER;")
    assert out == "ALTER TABLE durable_jobs ADD COLUMN IF NOT EXISTS attempts INTEGER;"
    # An already-guarded statement is left unchanged (no double IF NOT EXISTS).
    guarded = "ALTER TABLE t ADD COLUMN IF NOT EXISTS c TEXT;"
    assert sqlite_ddl_to_postgres(guarded) == guarded


def test_sqlite_ddl_to_postgres_leaves_valid_postgres_alone():
    ddl = (
        "CREATE TABLE IF NOT EXISTS t (id TEXT PRIMARY KEY, n INTEGER NOT NULL);\n"
        "CREATE UNIQUE INDEX IF NOT EXISTS ix ON t(n) WHERE n IS NOT NULL;"
    )
    assert sqlite_ddl_to_postgres(ddl) == ddl


def test_every_durable_migration_translates_without_residual_sqlite_ddl():
    migrations = Path(__file__).resolve().parents[2] / "infra" / "db" / "migrations"
    for filename in _SCHEMA_FILES:
        translated = sqlite_ddl_to_postgres((migrations / filename).read_text(encoding="utf-8"))
        assert "AUTOINCREMENT" not in translated.upper(), filename
        # BLOB only survives inside comments/prose, never as a column type token.
        for statement in split_sql_statements(translated):
            assert "BLOB" not in statement.upper(), (filename, statement)


# -- statement splitting ----------------------------------------------------


def test_split_sql_statements_strips_comments_and_splits():
    ddl = (
        "-- a comment\n"
        "CREATE TABLE t (\n"
        "  a TEXT,  -- inline comment\n"
        "  b INTEGER\n"
        ");\n"
        "\n"
        "CREATE INDEX ix ON t(a);\n"
    )
    statements = split_sql_statements(ddl)
    assert len(statements) == 2
    assert statements[0].startswith("CREATE TABLE t")
    assert "comment" not in statements[0]
    assert statements[1] == "CREATE INDEX ix ON t(a)"


def test_durable_migration_list_matches_sqlite_engine():
    # Both engines must bootstrap the exact same ordered migration set.
    assert _SCHEMA_FILES == SQLITE_SCHEMA_FILES


# -- Cloud SQL / DSN wiring -------------------------------------------------


def test_build_conninfo_cloudsql_unix_socket():
    params = build_postgres_conninfo(
        {"ODP_CLOUDSQL_INSTANCE": _CLOUDSQL_INSTANCE, "ODP_DB_PASSWORD": "pw"}
    )
    assert params["host"] == f"/cloudsql/{_CLOUDSQL_INSTANCE}"
    assert params["dbname"] == "oday_plus"
    assert params["user"] == "oday_app"
    assert params["password"] == "pw"


def test_build_conninfo_custom_socket_dir_and_names():
    params = build_postgres_conninfo(
        {
            "ODP_CLOUDSQL_INSTANCE": _CLOUDSQL_INSTANCE,
            "ODP_CLOUDSQL_SOCKET_DIR": "/var/run/cloudsql",
            "ODP_DB_NAME": "other_db",
            "ODP_DB_USER": "other_user",
        }
    )
    assert params["host"] == f"/var/run/cloudsql/{_CLOUDSQL_INSTANCE}"
    assert params["dbname"] == "other_db"
    assert params["user"] == "other_user"
    # No password configured -> the key is omitted, never emitted empty.
    assert "password" not in params


def test_build_conninfo_tcp_parts():
    params = build_postgres_conninfo(
        {
            "ODP_DB_HOST": "10.0.0.5",
            "ODP_DB_PORT": "6543",
            "ODP_DB_NAME": "oday_plus",
            "ODP_DB_USER": "oday_app",
            "ODP_DB_PASSWORD": "pw",
        }
    )
    assert params["host"] == "10.0.0.5"
    assert params["port"] == "6543"
    assert params["password"] == "pw"


def test_build_conninfo_reads_password_from_secret_file(tmp_path):
    secret = tmp_path / "db-password"
    secret.write_text("file-secret\n", encoding="utf-8")
    params = build_postgres_conninfo(
        {
            "ODP_DB_HOST": "10.0.0.5",
            "ODP_DB_PASSWORD_FILE": str(secret),
            # An inline value must be ignored in favour of the mounted secret file.
            "ODP_DB_PASSWORD": "inline-should-lose",
        }
    )
    assert params["password"] == "file-secret"


def test_build_conninfo_requires_some_target():
    with pytest.raises(PostgresConfigurationError):
        build_postgres_conninfo({})


def test_postgres_dsn_configured():
    assert postgres_dsn_configured({"ODP_DB_DSN": "postgresql://h/db"}) is True
    assert postgres_dsn_configured({"ODP_CLOUDSQL_INSTANCE": _CLOUDSQL_INSTANCE}) is True
    assert postgres_dsn_configured({"ODP_DB_HOST": "h"}) is True
    assert postgres_dsn_configured({}) is False
    # A bare durable request with no Postgres target stays on SQLite.
    assert postgres_dsn_configured({"ODP_PERSISTENCE": "durable"}) is False


# -- secret safety ----------------------------------------------------------


def test_redact_conninfo_hides_password():
    rendered = redact_conninfo(
        {"host": "10.0.0.5", "user": "oday_app", "password": "top-secret", "dbname": "oday_plus"}
    )
    assert "top-secret" not in rendered
    assert "password=***" in rendered
    assert "user=oday_app" in rendered


def test_migration_checksum_mismatch_is_runtime_error():
    assert issubclass(MigrationChecksumMismatch, RuntimeError)
