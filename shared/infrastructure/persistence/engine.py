"""SQLite engine for durable E2E persistence (ODP-PV-009).

The production storage target is PostgreSQL + PostGIS. For Product-Grade E2E
validation we need the product API to come off in-memory repositories and run
against storage that survives a process restart, *without* requiring a live
database server in CI. A file-backed SQLite database (stdlib ``sqlite3``, WAL
journaling) gives exactly that: durable, restart-survivable, dependency-free.

The schema is owned by the engine-neutral migration files under
``infra/db/migrations`` and executed verbatim on bootstrap, so the migration
artifacts and the runtime engine can never drift.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "infra" / "db" / "migrations"

# Engine-neutral (SQLite-compatible) durable-persistence DDL, applied in order
# at bootstrap. 000001 is the canonical PostgreSQL + PostGIS schema and is
# intentionally excluded — it is not SQLite-compatible. Each durable migration
# that the E2E store depends on is listed here explicitly so adding one is a
# deliberate, reviewable step rather than a directory glob that could sweep in
# a Postgres-only file.
_SCHEMA_FILES = (
    "000002_durable_e2e_persistence.sql",
    "000003_durable_audit_evidence.sql",
    "000004_durable_product_domain.sql",
    "000005_durable_notifications.sql",
    "000006_durable_outbox.sql",
    "000007_job_lease_columns.sql",
)



class SqliteEngine:
    """Thread-safe handle to a single durable SQLite database.

    FastAPI runs sync endpoints in a thread pool, so the connection is opened
    with ``check_same_thread=False`` and every statement is serialized behind a
    lock. WAL mode plus a commit per write makes each write durable the instant
    it returns, which is what "survive process restart" requires.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        if self._path.parent and str(self._path.parent) not in ("", "."):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._bootstrap()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    def _bootstrap(self) -> None:
        with self._lock:
            for filename in _SCHEMA_FILES:
                ddl = (_MIGRATIONS_DIR / filename).read_text(encoding="utf-8")
                # Strip comments and group lines into semicolon-separated statements
                statements = []
                current = []
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

                for statement in statements:
                    statement = statement.strip()
                    if not statement:
                        continue
                    try:
                        self._conn.execute(statement)
                    except sqlite3.OperationalError as exc:
                        exc_msg = str(exc).lower()
                        if "duplicate column name" in exc_msg or "already exists" in exc_msg:
                            continue
                        raise exc
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Hold a cross-connection write lock for one read-modify-write unit."""

        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def next_ordinal(self, name: str) -> int:
        """Return the next monotonic ordinal for ``name`` (stable list order)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO durable_sequences(name, counter) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET counter = counter + 1",
                (name,),
            )
            row = self._conn.execute(
                "SELECT counter FROM durable_sequences WHERE name = ?", (name,)
            ).fetchone()
            self._conn.commit()
            return int(row["counter"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["SqliteEngine"]
