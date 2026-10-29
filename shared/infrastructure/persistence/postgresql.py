"""PostgreSQL 16 runtime engine for production persistence.

The existing durable repositories intentionally use a small DB-API-like
surface.  This adapter preserves that surface while adding pooled connections,
transaction-bound locks, PostgreSQL placeholder conversion, row normalization,
and idempotent runtime-schema bootstrap.
"""

from __future__ import annotations

import hashlib
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
from shared.jobs.queue import JobFenceRejectedError

# Native proof acquisition is an explicit transport operation. These are new
# engineering limits on necessary PostgreSQL JSON projections, not schema,
# source/actor authority, wire-size or Python/server-memory guarantees.
NATIVE_ACQUISITION_ROW_MAX_BYTES = 4 * 1024 * 1024
NATIVE_ACQUISITION_ROOT_MAX_BYTES = 32 * 1024 * 1024
_NATIVE_REMAINING = "_oday_native_acquisition_remaining_bytes"
_NATIVE_COLUMNS = re.compile(r"[a-z_][a-z_0-9]*\Z")
_NATIVE_META = (
    "_odp_native_allowed", "_odp_native_bytes", "_odp_native_rows", "_odp_native_present",
)


def postgresql_root_is_current(engine, connection, root_transaction):
    """Observe the original engine-owned root; no caller flag creates one."""
    from psycopg import Transaction
    from psycopg.pq import TransactionStatus

    local = getattr(engine, "_local", None)
    return (type(engine) is PostgresEngine and type(root_transaction) is Transaction
        and root_transaction.connection is connection
        and getattr(local, "root_transaction", None) is root_transaction
        and getattr(local, "connection", None) is connection
        and not connection.closed and root_transaction.status is Transaction.Status.ACTIVE
        and connection.info.transaction_status is TransactionStatus.INTRANS)


def _native_projection_sql(statement, columns, groups, order_by):
    """Wrap an explicitly selected, typed projection, never intercept SQL.

    Each original owner supplies its fixed SELECT and necessary column groups.
    JSON is used only to measure each group's UTF-8 text in PostgreSQL. Returned
    cells retain their original SQL types; an oversized group or whole batch
    makes every data cell NULL before the driver sees it. The empty sentinel is
    transport metadata and is removed before any business reader/decoder.
    """
    if (type(columns) is not tuple or not columns or len(set(columns)) != len(columns)
            or any(type(c) is not str or not _NATIVE_COLUMNS.fullmatch(c) or c in _NATIVE_META
                for c in columns) or type(groups) is not tuple or not groups
            or any(type(g) is not tuple or not g for g in groups)):
        raise JobFenceRejectedError("NATIVE_ACQUISITION_PROJECTION_REQUIRED")
    grouped = tuple(c for group in groups for c in group)
    if len(grouped) != len(columns) or set(grouped) != set(columns):
        raise JobFenceRejectedError("NATIVE_ACQUISITION_PROJECTION_REQUIRED")
    if (type(order_by) is not tuple or any(type(key) is not tuple or len(key) != 2
            or key[0] not in columns or type(key[1]) is not bool for key in order_by)):
        raise JobFenceRejectedError("NATIVE_ACQUISITION_ORDER_REQUIRED")
    sizes = []
    for index, group in enumerate(groups):
        selected = ",".join("f." + c for c in group)
        sizes.append("(SELECT octet_length(row_to_json(p)::text)::bigint FROM (SELECT " +
            selected + ") p) AS _odp_native_group_" + str(index))
    names = ["_odp_native_group_" + str(i) for i in range(len(groups))]
    acceptable = " AND ".join(n + "<=" + str(NATIVE_ACQUISITION_ROW_MAX_BYTES) for n in names)
    total = "+".join(names)
    returned = ",".join("CASE WHEN q.allowed THEN s." + c + " END AS " + c for c in columns)
    ordering = (" ORDER BY " + ",".join("s." + key + (" DESC" if descending else " ASC")
        for key, descending in order_by)) if order_by else ""
    return ("WITH facts AS MATERIALIZED (" + statement + "),sized AS MATERIALIZED (SELECT f.*,"
        "true AS _odp_native_present," + ",".join(sizes) + " FROM facts f),"
        "totals AS MATERIALIZED (SELECT COALESCE(sum(" + total + "),0) AS bytes,"
        "COALESCE(bool_and(" + acceptable + "),true) AS groups_ok,count(*)::bigint AS rows FROM sized),"
        "quota AS MATERIALIZED (SELECT groups_ok AND bytes<=? AS allowed,"
        "LEAST(bytes,?::bigint+1)::bigint AS bytes,rows FROM totals) SELECT " + returned + ","
        "q.allowed AS _odp_native_allowed,q.bytes AS _odp_native_bytes,q.rows AS _odp_native_rows,"
        "COALESCE(s._odp_native_present,false) AS _odp_native_present "
        "FROM quota q LEFT JOIN sized s ON true" + ordering)

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
_QMARK_SQL_TOKEN = re.compile(r"'(?:''|[^'])*(?:'|$)|\"(?:\"\"|[^\"])*(?:\"|$)|\?")
_REQUIRED_RELATIONS = (
    "odp_runtime.durable_sequences",
    "odp_runtime.durable_audit_events",
    "odp_runtime.durable_documents",
    "odp_runtime.operator_comments",
    "odp_runtime.durable_manual_corrections",
    "odp_runtime.durable_evidence_bundles",
    "odp_runtime.durable_jobs",
    "odp_runtime.durable_outbox_events",
    "odp_runtime.notification_deduplication",
    "odp_runtime.notification_preferences",
    "odp_runtime.notification_receipts",
    "odp_runtime.notification_inapp_inbox",
    "core.address_locations",
    "core.brands",
    "core.machine_cycles",
    "core.machines",
    "core.stores",
    "core.tenants",
    "core.transactions",
    "workflow.decision_policies",
    "workflow.decisions",
    "workflow.approvals",
    "pricing.exploration_gates",
    "pricing.exploration_decisions",
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
    def pooled_connection(self) -> Iterator[Any]:
        """Lend one pooled connection and return it to the pool on exit.

        Repositories outside the ``odp_runtime`` document-store path (the
        ``identity`` schema stores) need a raw connection rather than the
        thread-bound transaction. Borrowing via ``getconn()`` without the
        matching ``putconn()`` leaks the connection and eventually exhausts
        the pool, so the pool's own context manager owns the lifecycle here.
        """

        with self._pool.connection() as connection:
            yield connection

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

        # Keep process and database locks in the same order for the whole root,
        # including commit/rollback, while retaining the actual root witness.
        with self._lock._lock:
            with self._pool.connection() as connection:
                with connection.transaction() as root_transaction:
                    self._local.connection = connection
                    self._local.root_transaction = root_transaction
                    self._local.depth = 1
                    try:
                        connection.execute(
                            "SET LOCAL search_path TO odp_runtime, core, public"
                        )
                        yield self
                    finally:
                        self._local.connection = None
                        self._local.root_transaction = None
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

    def enable_native_acquisition(self):
        """Adopt one pure resource counter on this actual root's lifetime.

        A nested transaction/budget and a savepoint rollback retain the same
        counter. Ordinary transactions never activate it implicitly. Neither
        the counter nor this method establishes tenant isolation or grants;
        the original owners still check their root, timeouts and full proofs.
        """
        connection = getattr(self._local, "connection", None)
        root = getattr(self._local, "root_transaction", None)
        if not postgresql_root_is_current(self, connection, root):
            raise JobFenceRejectedError("NATIVE_ACQUISITION_CURRENT_ROOT_REQUIRED")
        if not hasattr(root, _NATIVE_REMAINING):
            setattr(root, _NATIVE_REMAINING, NATIVE_ACQUISITION_ROOT_MAX_BYTES)
        remaining = getattr(root, _NATIVE_REMAINING)
        if type(remaining) is not int or not 0 <= remaining <= NATIVE_ACQUISITION_ROOT_MAX_BYTES:
            raise JobFenceRejectedError("NATIVE_ACQUISITION_COUNTER_REJECTED")
        return connection, root, remaining

    def native_acquisition_active(self):
        # Presence is intentional: zero and corrupt counters must not fall
        # back to an ordinary unbounded reader. The native operation validates
        # the original witness and the counter before doing any SQL.
        root = getattr(self._local, "root_transaction", None)
        return root is not None and hasattr(root, _NATIVE_REMAINING)

    def native_query(self, statement, params=(), *, columns, groups=None, one=False, order_by=()):
        """Acquire fixed native facts with per-group and same-root SQL bounds.

        Delegate through the original query/query_one seam, preserving its
        refusal and normalization. Reserve the maximum allowed acquisition
        before it runs; an exception or malformed transport never refunds that
        reservation. Only a fully checked response refunds unused bytes. Thus
        driver/normalization failures cannot reuse an uncharged root allowance.
        Graph and queue decoders run after settlement and retain their codecs.
        """
        if type(one) is not bool:
            raise JobFenceRejectedError("NATIVE_ACQUISITION_READ_MODE_REQUIRED")
        groups = (columns,) if groups is None else groups
        sql = _native_projection_sql(statement, columns, groups, order_by)
        connection, root, remaining = self.enable_native_acquisition()
        if remaining == 0:
            raise JobFenceRejectedError("NATIVE_ACQUISITION_ROOT_EXHAUSTED")
        allowance = min(remaining, NATIVE_ACQUISITION_ROW_MAX_BYTES * len(groups)) if one else remaining
        setattr(root, _NATIVE_REMAINING, remaining - allowance)
        parameters = (*params, allowance, allowance)
        acquired = self.query_one(sql, parameters) if one else self.query(sql, parameters)
        if not postgresql_root_is_current(self, connection, root):
            raise JobFenceRejectedError("NATIVE_ACQUISITION_CURRENT_ROOT_REQUIRED")
        rows = [acquired] if one else acquired
        keys = set(columns) | set(_NATIVE_META)
        if (not isinstance(rows, list) or not rows
                or any(not isinstance(row, dict) or set(row) != keys for row in rows)):
            raise JobFenceRejectedError("NATIVE_ACQUISITION_TRANSPORT_REJECTED")
        first = rows[0]
        allowed, used, count = (first.get(k) for k in _NATIVE_META[:3])
        if (allowed is not True or type(used) is not int or not 0 <= used <= allowance
                or type(count) is not int or count < 0 or (one and count > 1)
                or len(rows) != max(count, 1)
                or any(row.get(_NATIVE_META[0]) is not True or row.get(_NATIVE_META[1]) != used
                    or type(row.get(_NATIVE_META[1])) is not int or row.get(_NATIVE_META[2]) != count
                    or type(row.get(_NATIVE_META[2])) is not int
                    or row.get(_NATIVE_META[3]) is not (count != 0) for row in rows)
                or (count > 0 and used == 0)
                or (count == 0 and (used != 0 or any(first.get(c) is not None for c in columns)))):
            raise JobFenceRejectedError("NATIVE_ACQUISITION_LIMIT_OR_TRANSPORT_REJECTED")
        current = getattr(root, _NATIVE_REMAINING)
        if type(current) is not int or not 0 <= current <= remaining - allowance:
            raise JobFenceRejectedError("NATIVE_ACQUISITION_COUNTER_REJECTED")
        setattr(root, _NATIVE_REMAINING, current + allowance - used)
        if count == 0:
            return None if one else []
        for row in rows:
            for name in _NATIVE_META:
                row.pop(name)
        return rows[0] if one else rows

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
        fingerprint = "000008_postgresql_runtime_persistence:sha256:" + hashlib.sha256(ddl.encode("utf-8")).hexdigest()
        with self._pool.connection() as connection:
            with connection.transaction():
                # CREATE IF NOT EXISTS does not serialize concurrent DDL.
                # The transaction lock covers the entire runtime migration and
                # releases automatically on commit, rollback or connection loss.
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ("odayplus:odp_runtime:migration",),
                )
                registry = connection.execute(
                    "SELECT to_regclass('odp_runtime.schema_migrations') AS relation"
                ).fetchone()
                if registry and registry["relation"] is not None:
                    applied = connection.execute(
                        "SELECT migration_id FROM odp_runtime.schema_migrations WHERE migration_id = %s",
                        (fingerprint,),
                    ).fetchone()
                    if applied:
                        # Even IF NOT EXISTS DDL takes relation locks. Running
                        # it on each new engine can deadlock against live DML.
                        # The fingerprint is retained only with the complete
                        # migration in the same transaction. Schema validation
                        # still refuses missing production relations.
                        return
                connection.execute(ddl)
                connection.execute(
                    "INSERT INTO odp_runtime.schema_migrations(migration_id) VALUES (%s) "
                    "ON CONFLICT (migration_id) DO NOTHING",
                    (fingerprint,),
                )

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
            # An idle socket may have been closed by the server without the
            # client observing it. Reject it before application work begins;
            # never reconnect or replay a transaction that has already started.
            check=ConnectionPool.check_connection,
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
    """Convert qmark parameters while preserving quoted SQL, including escapes."""
    return _QMARK_SQL_TOKEN.sub(
        lambda token: "%s" if token[0] == "?" else token[0], sql,
    )


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
