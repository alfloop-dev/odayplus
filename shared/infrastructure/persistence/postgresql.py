"""PostgreSQL 16 runtime engine for production persistence.

The existing durable repositories intentionally use a small DB-API-like
surface.  This adapter preserves that surface while adding pooled connections,
transaction-bound locks, PostgreSQL placeholder conversion, row normalization,
and idempotent runtime-schema bootstrap.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import UUID

from shared.infrastructure.persistence.document_store import SqliteDocumentStore

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "infra"
    / "db"
    / "migrations"
    / "000008_postgresql_runtime_persistence.sql"
)
_POSTGRES_SCHEMES = {"postgres", "postgresql"}
_PRAGMA_TABLE_INFO = re.compile(
    r"^\s*PRAGMA\s+table_info\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\)\s*;?\s*$",
    re.IGNORECASE,
)
_REQUIRED_RELATIONS = (
    "odp_runtime.durable_audit_events",
    "odp_runtime.durable_documents",
    "odp_runtime.durable_evidence_bundles",
    "odp_runtime.durable_jobs",
    "odp_runtime.durable_outbox_events",
    "odp_runtime.notification_deduplication",
    "odp_runtime.notification_preferences",
    "odp_runtime.notification_receipts",
    "core.address_locations",
    "core.brands",
    "core.machine_cycles",
    "core.machines",
    "core.stores",
    "core.tenants",
    "core.transactions",
)


class PostgreSQLConfigurationError(RuntimeError):
    """Raised when production PostgreSQL cannot be configured safely."""


class PostgreSQLSchemaError(RuntimeError):
    """Raised when the runtime or canonical schema is incomplete."""


class _Cursor(Protocol):
    rowcount: int

    def fetchall(self) -> list[Any]: ...

    def fetchone(self) -> Any | None: ...


class _Connection(Protocol):
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor: ...

    def transaction(self) -> Any: ...


class _Pool(Protocol):
    def connection(self) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ExecutionResult:
    """Stable result returned after the underlying pooled cursor is closed."""

    rowcount: int


class _TransactionalLock:
    """Serialize a multi-statement repository operation inside one transaction."""

    def __init__(self, engine: PostgresEngine) -> None:
        self._engine = engine
        self._lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self) -> _TransactionalLock:
        self._lock.acquire()
        try:
            transaction = self._engine.transaction()
            transaction.__enter__()
            stack = getattr(self._local, "stack", None)
            if stack is None:
                stack = []
                self._local.stack = stack
            stack.append(transaction)
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        try:
            stack = getattr(self._local, "stack", None)
            if not stack:
                return False
            transaction = stack.pop()
            return bool(transaction.__exit__(exc_type, exc, traceback))
        finally:
            self._lock.release()


class PostgresEngine:
    """Pooled PostgreSQL engine compatible with the durable repository surface."""

    dialect = "postgresql"
    is_production = True

    def __init__(
        self,
        database_url: str,
        *,
        pool: _Pool | None = None,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        bootstrap: bool = True,
        validate_schema: bool = True,
    ) -> None:
        self._database_url = _validate_database_url(database_url)
        self._local = threading.local()
        self._owns_pool = pool is None
        self._pool = pool or _build_pool(
            self._database_url,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
        )
        self._lock = _TransactionalLock(self)
        if bootstrap:
            self.apply_runtime_migration()
        if validate_schema:
            self.validate_schema()

    @property
    def database_url(self) -> str:
        return self._database_url

    @property
    def lock(self) -> _TransactionalLock:
        return self._lock

    @contextmanager
    def transaction(self) -> Iterator[PostgresEngine]:
        """Bind all calls on this thread to one pooled PostgreSQL transaction."""

        active_connection = getattr(self._local, "connection", None)
        if active_connection is not None:
            self._local.depth += 1
            try:
                yield self
            finally:
                self._local.depth -= 1
            return

        with self._pool.connection() as connection:
            with connection.transaction():
                self._local.connection = connection
                self._local.depth = 1
                try:
                    connection.execute(
                        "SET LOCAL search_path TO odp_runtime, core, public"
                    )
                    yield self
                finally:
                    self._local.connection = None
                    self._local.depth = 0

    def execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> ExecutionResult:
        with self._connection() as connection:
            cursor = connection.execute(_convert_qmark_placeholders(sql), params)
            return ExecutionResult(rowcount=int(getattr(cursor, "rowcount", -1)))

    def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        pragma_match = _PRAGMA_TABLE_INFO.match(sql)
        if pragma_match:
            return self._table_info(pragma_match.group(1))
        with self._connection() as connection:
            cursor = connection.execute(_convert_qmark_placeholders(sql), params)
            return [_normalize_row(row) for row in cursor.fetchall()]

    def query_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        pragma_match = _PRAGMA_TABLE_INFO.match(sql)
        if pragma_match:
            rows = self._table_info(pragma_match.group(1))
            return rows[0] if rows else None
        with self._connection() as connection:
            cursor = connection.execute(_convert_qmark_placeholders(sql), params)
            row = cursor.fetchone()
            return None if row is None else _normalize_row(row)

    def next_ordinal(self, name: str) -> int:
        """Atomically allocate a monotonic document ordinal."""

        with self.lock:
            row = self.query_one(
                "INSERT INTO durable_sequences(name, counter) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET counter = durable_sequences.counter + 1 "
                "RETURNING counter",
                (name,),
            )
            if row is None:
                raise RuntimeError(f"failed to allocate ordinal for {name!r}")
            return int(row["counter"])

    def apply_runtime_migration(self) -> None:
        """Apply the PostgreSQL-only runtime migration inside one transaction."""

        ddl = _MIGRATION_PATH.read_text(encoding="utf-8")
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(ddl)

    def validate_schema(self) -> None:
        """Fail closed unless runtime tables and canonical production tables exist."""

        missing: list[str] = []
        with self.transaction():
            for relation in _REQUIRED_RELATIONS:
                row = self.query_one("SELECT to_regclass(?) AS relation", (relation,))
                if row is None or row["relation"] is None:
                    missing.append(relation)
        if missing:
            raise PostgreSQLSchemaError(
                "PostgreSQL production schema is incomplete; missing relations: "
                + ", ".join(missing)
            )

    def close(self) -> None:
        if self._owns_pool:
            self._pool.close()

    @contextmanager
    def _connection(self) -> Iterator[_Connection]:
        active_connection = getattr(self._local, "connection", None)
        if active_connection is not None:
            yield active_connection
            return
        with self.transaction():
            yield self._local.connection

    def _table_info(self, table_name: str) -> list[dict[str, Any]]:
        row = self.query_one("SELECT to_regclass(?) AS relation", (table_name,))
        if row is None or row["relation"] is None:
            return []
        return self.query(
            "SELECT attribute.attname AS name "
            "FROM pg_attribute AS attribute "
            "WHERE attribute.attrelid = to_regclass(?) "
            "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
            "ORDER BY attribute.attnum",
            (table_name,),
        )


class PostgresDocumentStore(SqliteDocumentStore):
    """PostgreSQL-backed document store using the shared repository semantics."""

    def __init__(self, engine: PostgresEngine) -> None:
        super().__init__(engine)  # type: ignore[arg-type]

    @property
    def engine(self) -> PostgresEngine:
        return self._engine  # type: ignore[return-value]


def _build_pool(
    database_url: str,
    *,
    min_pool_size: int,
    max_pool_size: int,
) -> _Pool:
    if min_pool_size < 1:
        raise PostgreSQLConfigurationError("min_pool_size must be at least 1")
    if max_pool_size < min_pool_size:
        raise PostgreSQLConfigurationError(
            "max_pool_size must be greater than or equal to min_pool_size"
        )
    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise PostgreSQLConfigurationError(
            "PostgreSQL persistence requires psycopg[binary,pool]"
        ) from exc

    try:
        return ConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            kwargs={
                "autocommit": False,
                "row_factory": dict_row,
            },
            open=True,
        )
    except Exception as exc:
        raise PostgreSQLConfigurationError(
            "unable to initialize PostgreSQL connection pool"
        ) from exc


def _validate_database_url(database_url: str) -> str:
    value = database_url.strip()
    if not value:
        raise PostgreSQLConfigurationError(
            "ODAY_DATABASE_URL is required for PostgreSQL persistence"
        )
    parsed = urlparse(value)
    if parsed.scheme.lower() not in _POSTGRES_SCHEMES:
        raise PostgreSQLConfigurationError(
            "ODAY_DATABASE_URL must use postgres:// or postgresql://"
        )
    if not parsed.path or parsed.path == "/":
        raise PostgreSQLConfigurationError(
            "ODAY_DATABASE_URL must name a PostgreSQL database"
        )
    return value


def _convert_qmark_placeholders(sql: str) -> str:
    """Convert SQLite qmark parameters without touching quoted SQL text."""

    converted: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            converted.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    converted.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            converted.append(char)
        elif char == "?":
            converted.append("%s")
        else:
            converted.append(char)
        index += 1
    return "".join(converted)


def _normalize_row(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        items = row.items()
    elif hasattr(row, "keys"):
        items = ((key, row[key]) for key in row.keys())
    else:
        raise TypeError("PostgreSQL connections must use a mapping row factory")
    return {str(key): _normalize_value(value) for key, value in items}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"), sort_keys=True)
    return value


__all__ = [
    "ExecutionResult",
    "PostgresDocumentStore",
    "PostgresEngine",
    "PostgreSQLConfigurationError",
    "PostgreSQLSchemaError",
]
