"""Durable PostgreSQL engine for the product API (ODP-GAP-PERSIST-001).

``SqliteEngine`` (ODP-PV-009) gives the API restart-survivable storage without a
database server, and remains the default for tests and Product-Grade E2E. The
*production* durability target, however, is the Cloud SQL PostgreSQL instance
that already runs for this project — nothing was writing to it. This module adds
a Postgres-backed engine that is a drop-in for :class:`SqliteEngine`: it exposes
the same ``execute`` / ``query`` / ``query_one`` / ``next_ordinal`` /
``table_columns`` / ``close`` surface and the same ``lock`` property, so every
durable repository, the audit log, the job queue, and the document store run
against Postgres unchanged.

The engine is the single dialect chokepoint. Repositories are written with
SQLite ``?`` placeholders; this engine rewrites them to psycopg ``%s`` and
translates the engine-neutral durable DDL (``INTEGER PRIMARY KEY AUTOINCREMENT``
-> ``BIGSERIAL``, ``BLOB`` -> ``BYTEA``) at bootstrap, so no repository needs to
know which backend it is talking to.

Selection is by environment (see :mod:`shared.infrastructure.persistence.factory`
and :func:`postgres_dsn_configured`): ``ODP_PERSISTENCE=durable`` plus a Postgres
DSN / Cloud SQL instance selects this engine; ``ODP_PERSISTENCE=durable`` with no
DSN keeps SQLite. Secrets (the DB password) are only ever read from the
environment or a Secret-Manager-mounted file — never hardcoded, never logged.

``psycopg`` is imported lazily so this module still imports cleanly in the
minimal CI environment that has no database driver.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "infra" / "db" / "migrations"

# The engine-neutral durable migrations, applied in order. This is the same set
# SqliteEngine bootstraps (000001 is the Postgres+PostGIS canonical baseline and
# is intentionally excluded from the durable runtime bootstrap on both engines).
# Adding one is a deliberate, reviewable edit rather than a directory glob.
_SCHEMA_FILES = (
    "000002_durable_e2e_persistence.sql",
    "000003_durable_audit_evidence.sql",
    "000004_durable_product_domain.sql",
    "000005_durable_notifications.sql",
    "000006_durable_outbox.sql",
    "000007_job_lease_columns.sql",
)

_AUTOINCREMENT_RE = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE
)
_BLOB_RE = re.compile(r"\bBLOB\b", re.IGNORECASE)
# The durable migrations lean on SQLite swallowing "duplicate column" so their
# ``ADD COLUMN`` steps are idempotent. Postgres needs that intent made explicit.
_ADD_COLUMN_RE = re.compile(
    r"\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)", re.IGNORECASE
)


class MigrationChecksumMismatch(RuntimeError):
    """A tracked migration's on-disk checksum no longer matches what was applied.

    Once-only tracking means a migration file must be immutable after it has run
    against a database. A changed checksum signals schema drift and is refused
    rather than silently reapplied.
    """


class PostgresConfigurationError(ValueError):
    """Durable Postgres was requested but the DSN/Cloud SQL wiring is incomplete."""


# --------------------------------------------------------------------------
# Pure translation helpers (no driver required — unit tested directly).
# --------------------------------------------------------------------------


def translate_placeholders(sql: str, *, has_params: bool) -> str:
    """Rewrite SQLite ``?`` placeholders to psycopg ``%s``.

    psycopg only performs ``%``-based client-side binding when parameters are
    supplied, and then any literal ``%`` in the SQL must be doubled. When there
    are no parameters the statement is sent verbatim, so no rewriting is done.
    """
    if not has_params:
        return sql
    return sql.replace("%", "%%").replace("?", "%s")


def sqlite_ddl_to_postgres(ddl: str) -> str:
    """Translate engine-neutral (SQLite-shaped) durable DDL to PostgreSQL.

    Only the two constructs the durable migrations actually use are rewritten;
    everything else (``TEXT``, ``INTEGER``, ``CREATE TABLE IF NOT EXISTS``,
    partial ``CREATE UNIQUE INDEX ... WHERE``, ``ON CONFLICT ... DO UPDATE``)
    is already valid PostgreSQL.
    """
    ddl = _AUTOINCREMENT_RE.sub("BIGSERIAL PRIMARY KEY", ddl)
    ddl = _BLOB_RE.sub("BYTEA", ddl)
    ddl = _ADD_COLUMN_RE.sub("ADD COLUMN IF NOT EXISTS ", ddl)
    return ddl


def split_sql_statements(ddl: str) -> list[str]:
    """Strip ``--`` comments and split a DDL file into executable statements."""
    statements: list[str] = []
    current: list[str] = []
    for line in ddl.splitlines():
        stripped = line.split("--")[0].strip()
        if not stripped:
            continue
        current.append(stripped)
        if stripped.endswith(";"):
            statements.append(" ".join(current))
            current = []
    if current:
        statements.append(" ".join(current))
    cleaned = []
    for statement in statements:
        text = statement.strip().rstrip(";").strip()
        if text:
            cleaned.append(text)
    return cleaned


def _revision_of(filename: str) -> str:
    return filename.split("_", 1)[0]


# --------------------------------------------------------------------------
# Cloud SQL / DSN wiring (no driver required — unit tested directly).
# --------------------------------------------------------------------------


def _read_password(env: Mapping[str, str]) -> str | None:
    """Resolve the DB password from env or a Secret-Manager-mounted file.

    ``ODP_DB_PASSWORD_FILE`` (a file path, e.g. a mounted secret volume) takes
    precedence over the inline ``ODP_DB_PASSWORD`` so production never has to put
    the secret value in an environment variable at all. The value is returned to
    the caller and never logged.
    """
    password_file = env.get("ODP_DB_PASSWORD_FILE")
    if password_file:
        text = Path(password_file).read_text(encoding="utf-8")
        return text.strip() or None
    password = env.get("ODP_DB_PASSWORD")
    return password or None


def postgres_dsn_configured(env: Mapping[str, str]) -> bool:
    """True if the environment carries enough to build a Postgres connection.

    A full ``ODP_DB_DSN`` / ``ODP_DATABASE_URL``, a Cloud SQL instance
    (``ODP_CLOUDSQL_INSTANCE``), or an explicit host is each sufficient.
    """
    if env.get("ODP_DB_DSN") or env.get("ODP_DATABASE_URL"):
        return True
    if env.get("ODP_CLOUDSQL_INSTANCE"):
        return True
    return bool(env.get("ODP_DB_HOST"))


def build_postgres_conninfo(env: Mapping[str, str]) -> dict[str, Any]:
    """Build a psycopg keyword connection mapping from the environment.

    Supports three wirings, in priority order:

    1. A complete DSN in ``ODP_DB_DSN`` / ``ODP_DATABASE_URL`` (used verbatim;
       an inline/file password fills in only if the DSN omits one).
    2. Cloud SQL via unix socket: ``ODP_CLOUDSQL_INSTANCE=<project:region:inst>``
       connects over ``/cloudsql/<instance>`` (the socket directory Cloud Run
       mounts), which needs no proxy inside the managed runtime.
    3. Explicit TCP parts: ``ODP_DB_HOST`` / ``ODP_DB_PORT`` / ``ODP_DB_NAME`` /
       ``ODP_DB_USER``.

    The password always comes from ``ODP_DB_PASSWORD_FILE`` or ``ODP_DB_PASSWORD``
    (never a literal in code). Raises :class:`PostgresConfigurationError` when the
    wiring is incomplete.
    """
    password = _read_password(env)

    dsn = env.get("ODP_DB_DSN") or env.get("ODP_DATABASE_URL")
    if dsn:
        import psycopg.conninfo as _conninfo

        params = _conninfo.conninfo_to_dict(dsn)
        if password and not params.get("password"):
            params["password"] = password
        return params

    dbname = env.get("ODP_DB_NAME", "oday_plus")
    user = env.get("ODP_DB_USER", "oday_app")

    instance = env.get("ODP_CLOUDSQL_INSTANCE")
    if instance:
        socket_dir = env.get("ODP_CLOUDSQL_SOCKET_DIR", "/cloudsql")
        params = {
            "host": f"{socket_dir}/{instance}",
            "dbname": dbname,
            "user": user,
        }
        if password:
            params["password"] = password
        return params

    host = env.get("ODP_DB_HOST")
    if not host:
        raise PostgresConfigurationError(
            "durable Postgres requested but no ODP_DB_DSN, ODP_CLOUDSQL_INSTANCE, "
            "or ODP_DB_HOST is set"
        )
    params = {
        "host": host,
        "port": env.get("ODP_DB_PORT", "5432"),
        "dbname": dbname,
        "user": user,
    }
    if password:
        params["password"] = password
    return params


def redact_conninfo(params: Mapping[str, Any]) -> str:
    """Render a connection mapping for logs with the password removed."""
    parts = []
    for key in sorted(params):
        if key == "password":
            parts.append("password=***")
            continue
        parts.append(f"{key}={params[key]}")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class PostgresEngine:
    """Durable PostgreSQL handle, interface-compatible with ``SqliteEngine``.

    A single connection is opened in autocommit mode and every statement is
    serialized behind a re-entrant lock, matching ``SqliteEngine``: each write is
    durable the instant it returns, and ``record`` / ``next_ordinal`` can do a
    read-then-write under ``engine.lock`` without another writer interleaving.
    """

    def __init__(
        self,
        conninfo: Mapping[str, Any] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        migrations_dir: Path | None = None,
    ) -> None:
        import os

        import psycopg
        from psycopg.rows import dict_row

        resolved = dict(conninfo) if conninfo is not None else build_postgres_conninfo(
            env if env is not None else os.environ
        )
        self._conninfo = resolved
        self._migrations_dir = migrations_dir or _MIGRATIONS_DIR
        self._lock = threading.RLock()
        self._conn = psycopg.connect(
            autocommit=True, row_factory=dict_row, **resolved
        )
        self._bootstrap()

    # -- properties -------------------------------------------------------

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @property
    def dsn(self) -> str:
        """Password-redacted connection description, safe to log."""
        return redact_conninfo(self._conninfo)

    # -- bootstrap / migrations ------------------------------------------

    def _bootstrap(self) -> None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "  revision   TEXT PRIMARY KEY,"
                "  filename   TEXT NOT NULL,"
                "  checksum   TEXT NOT NULL,"
                "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            )
            for filename in _SCHEMA_FILES:
                raw = (self._migrations_dir / filename).read_text(encoding="utf-8")
                checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                revision = _revision_of(filename)
                row = cur.execute(
                    "SELECT checksum FROM schema_migrations WHERE revision = %s",
                    (revision,),
                ).fetchone()
                if row is not None:
                    if row["checksum"] != checksum:
                        raise MigrationChecksumMismatch(
                            f"migration {filename} (revision {revision}) checksum "
                            f"changed after it was applied; refusing to reapply"
                        )
                    continue  # once-only: already applied, skip
                for statement in split_sql_statements(sqlite_ddl_to_postgres(raw)):
                    cur.execute(statement)
                cur.execute(
                    "INSERT INTO schema_migrations(revision, filename, checksum) "
                    "VALUES (%s, %s, %s) ON CONFLICT(revision) DO NOTHING",
                    (revision, filename, checksum),
                )

    def applied_revisions(self) -> list[str]:
        rows = self.query("SELECT revision FROM schema_migrations ORDER BY revision")
        return [row["revision"] for row in rows]

    # -- statement execution ---------------------------------------------

    def _prepared(self, sql: str, params: tuple) -> tuple:
        has_params = bool(params)
        translated = translate_placeholders(sql, has_params=has_params)
        return (translated, params) if has_params else (translated,)

    def execute(self, sql: str, params: tuple = ()) -> Any:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(*self._prepared(sql, params))
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[Any]:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(*self._prepared(sql, params))
            return list(cur.fetchall())

    def query_one(self, sql: str, params: tuple = ()) -> Any | None:
        with self._lock, self._conn.cursor() as cur:
            cur.execute(*self._prepared(sql, params))
            return cur.fetchone()

    def next_ordinal(self, name: str) -> int:
        """Return the next monotonic ordinal for ``name`` (stable list order)."""
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO durable_sequences(name, counter) VALUES (%s, 1) "
                "ON CONFLICT(name) DO UPDATE SET "
                "counter = durable_sequences.counter + 1",
                (name,),
            )
            row = cur.execute(
                "SELECT counter FROM durable_sequences WHERE name = %s", (name,)
            ).fetchone()
            return int(row["counter"])

    def table_columns(self, table: str) -> set[str]:
        """Column names of ``table`` in the current schema (engine-neutral)."""
        rows = self.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? AND table_schema = current_schema()",
            (table,),
        )
        return {row["column_name"] for row in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "MigrationChecksumMismatch",
    "PostgresConfigurationError",
    "PostgresEngine",
    "build_postgres_conninfo",
    "postgres_dsn_configured",
    "redact_conninfo",
    "split_sql_statements",
    "sqlite_ddl_to_postgres",
    "translate_placeholders",
]
