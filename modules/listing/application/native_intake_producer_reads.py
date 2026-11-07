"""Bounded producer-only reads, including adjacent terminal job rows.

Each terminal-pair call executes fresh SQL. It changes only acquisition of
the two adjacent native rows; existing terminal verifiers still own every
predicate. No row, result, permission or proof is cached or reused by a later
logical read, invocation or positive boundary.
"""
from __future__ import annotations

from functools import partial
from types import SimpleNamespace

from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from shared.infrastructure.persistence.identity_graph_sql import (
    NATIVE_JOB_COLUMNS,
    CanonicalIdentityGraphRepository,
)
from shared.infrastructure.persistence.job_queue import (
    DURABLE_JOB_COLUMNS as _PHYSICAL_COLUMNS,
)
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import (
    PostgresEngine,
)
from shared.infrastructure.persistence.postgresql import (
    postgresql_root_is_current as _native_root_is_current,
)
from shared.jobs.queue import JobFenceRejectedError

_QUEUE_GET = DurableJobQueue.get
_QUEUE_DECODE = DurableJobQueue._row_to_record
# Fixed transport aliases preserve native cell types. Independently joined
# physical rows remain visible even when the tenant workflow row is missing.
_PAIR_SQL = (
    "SELECT w.*,d.job_id AS physical_job_id,d.job_type AS physical_job_type,"
    "d.status AS physical_status,d.delivery_state AS physical_delivery_state,"
    "d.correlation_id AS physical_correlation_id,d.idempotency_key AS physical_idempotency_key,"
    "d.payload_json AS physical_payload_json,d.created_at AS physical_created_at,"
    "d.fence_token AS physical_fence_token,d.version AS physical_version,"
    "d.locked_by AS physical_locked_by,d.heartbeat_at AS physical_heartbeat_at,"
    "d.lease_expires_at AS physical_lease_expires_at,d.attempts AS physical_attempts,"
    "d.error_message AS physical_error_message,d.leased_until AS physical_leased_until,"
    "d.available_at AS physical_available_at,d.max_retries AS physical_max_retries "
    "FROM (SELECT ?::uuid AS tenant_id,?::uuid AS job_id) AS wanted "
    "LEFT JOIN workflow.jobs w ON w.tenant_id=wanted.tenant_id AND w.job_id=wanted.job_id "
    "LEFT JOIN durable_jobs d ON d.job_id=? "
    "WHERE current_setting('transaction_isolation')='serializable' "
    "AND current_setting('app.tenant_id',true)=wanted.tenant_id::text"
)


def _native_producer_noop():
    """Only the native owners' absent optional guard; not a root budget."""
    return None


def _reject():
    raise JobFenceRejectedError("NATIVE_PARENT_TERMINAL_ROWS_REJECTED") from None


class _ProducerReadView:
    """The same guarded read surface used by the original producer verifiers."""

    def __init__(self, graph, before, conflict):
        self._graph, self._before, self._conflict = graph, before, conflict
        self.engine = SimpleNamespace(dialect="postgresql", query=self._query, query_one=self._query_one,
            native_query=self._native_query)
        self.queue = SimpleNamespace(get=self._get)
        base = graph._native_base if type(graph) is _ProducerReadView else graph
        self._native_base = base
        engine = getattr(base, "engine", None)
        self._connection = getattr(getattr(engine, "_local", None), "connection", None)
        self._root_transaction = getattr(getattr(engine, "_local", None), "root_transaction", None)

    def _check_native_root(self):
        engine = getattr(self._native_base, "engine", None)
        if type(engine) is PostgresEngine and not _native_root_is_current(
                engine, self._connection, self._root_transaction):
            raise self._conflict("NATIVE_SOURCE_POLICY_PRODUCER_CURRENT_ROOT_REQUIRED")

    def _read(self, method, sql, params=()):
        if not sql.lstrip().upper().startswith("SELECT ") or "FOR UPDATE" in sql.upper():
            raise self._conflict("NATIVE_SOURCE_POLICY_PRODUCER_READ_ONLY_REQUIRED")
        self._check_native_root()
        self._before()
        return method(sql, params)

    def _query(self, sql, params=()):
        return self._read(self._graph.engine.query, sql, params)

    def _query_one(self, sql, params=()):
        return self._read(self._graph.engine.query_one, sql, params)

    def _native_query(self, sql, params=(), *, columns, groups=None, one=False, order_by=()):
        return self._read(partial(self._graph.engine.native_query,
            columns=columns, groups=groups, one=one, order_by=order_by), sql, params)

    def _one(self, sql, params, *, native_columns=None, native_groups=None):
        reader = self._graph._one if native_columns is None else partial(
            self._graph.native_one, columns=native_columns, groups=native_groups)
        return self._read(reader, sql, params)

    def native_one(self, sql, params, *, columns, groups=None):
        return self._one(sql, params, native_columns=columns, native_groups=groups)

    def _get(self, job_id):
        self._check_native_root()
        self._before()
        engine = getattr(self._native_base, "engine", None)
        if isinstance(engine, PostgresEngine):
            engine.enable_native_acquisition()
        return self._graph.queue.get(job_id)

    def _terminal_pair(self, tenant, job_id):
        base = self._native_base
        engine, queue = getattr(base, "engine", None), getattr(base, "queue", None)
        # Alternate repositories, per-instance fault/read adapters and changed
        # queue readers keep their original two calls. No failed query or
        # predicate ever falls back in search of a successful outcome.
        if (not _native_read_surface(self)
                or type(engine) is not PostgresEngine or type(queue) is not DurableJobQueue
                or queue._engine is not engine
                or getattr(queue.get, "__func__", None) is not _QUEUE_GET
                or queue._row_to_record is not _QUEUE_DECODE
                or any(name in vars(engine) for name in ("query", "query_one", "execute", "native_query"))
                or self._connection is None):
            return None
        if not _native_root_is_current(engine, self._connection, self._root_transaction):
            _reject()
        tenant, job_id = native_intake_uuid(tenant), native_intake_uuid(job_id)
        # This invokes every original enclosing view's guard before the one
        # actual SELECT. Neither the SQL clock nor its two GUCs are combined
        # with the fact query, throttled, restarted or replaced.
        physical_columns = tuple("physical_" + name for name in _PHYSICAL_COLUMNS)
        rows = self._native_query(_PAIR_SQL, (tenant, job_id, job_id),
            columns=(*NATIVE_JOB_COLUMNS, *physical_columns), groups=(NATIVE_JOB_COLUMNS, physical_columns))
        if not _native_root_is_current(engine, self._connection, self._root_transaction) or len(rows) != 1:
            _reject()
        row = rows[0]
        physical_keys = {"physical_" + name for name in _PHYSICAL_COLUMNS}
        if not physical_keys <= row.keys():
            _reject()
        job = {key: value for key, value in row.items() if key not in physical_keys}
        if job.get("job_id") is None:
            job = None
        elif job.get("job_id") != job_id or job.get("tenant_id") != tenant:
            _reject()
        physical = {name: row["physical_" + name] for name in _PHYSICAL_COLUMNS}
        if physical["job_id"] is None:
            current = None
        else:
            if physical["job_id"] != job_id:
                _reject()
            # The actual native decoder receives every real physical column.
            # Its legacy normalization is unchanged; no JobRecord default is
            # used to fill omitted transport fields.
            current = queue._row_to_record(physical)
        return current, job


_GRAPH_ONE = CanonicalIdentityGraphRepository._one
_GRAPH_NATIVE_ONE = CanonicalIdentityGraphRepository.native_one
_VIEW_METHODS = {name: getattr(_ProducerReadView, name)
    for name in ("_read", "_query", "_query_one", "_native_query", "_one", "native_one", "_get")}


def _native_read_surface(graph):
    """One declaration of the original guarded acquisition surface."""
    cursor = graph
    while type(cursor) is _ProducerReadView:
        if any(getattr(getattr(cursor, name), "__func__", None) is not original
               for name, original in _VIEW_METHODS.items()):
            return False
        if any(getattr(method, "__self__", None) is not cursor
                or getattr(method, "__func__", None) is not _VIEW_METHODS[name]
                for method, name in ((cursor.engine.query, "_query"),
                    (cursor.engine.query_one, "_query_one"),
                    (cursor.engine.native_query, "_native_query"), (cursor.queue.get, "_get"))):
            return False
        cursor = cursor._graph
    return (type(cursor) is CanonicalIdentityGraphRepository
        and getattr(cursor._one, "__func__", None) is _GRAPH_ONE
        and getattr(cursor.native_one, "__func__", None) is _GRAPH_NATIVE_ONE)


def producer_read_view(graph, before, conflict):
    return _ProducerReadView(graph, before, conflict)


def native_terminal_pair(graph, tenant, job_id):
    """Return freshly read (physical, workflow), or None for original readers."""
    if type(graph) is not _ProducerReadView:
        return None
    return graph._terminal_pair(tenant, job_id)
