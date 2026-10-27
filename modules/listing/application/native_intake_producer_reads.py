"""Bounded producer-only reads, including adjacent terminal job rows.

Each terminal-pair call executes fresh SQL. It changes only acquisition of
the two adjacent native rows; existing terminal verifiers still own every
predicate. No row, result, permission or proof is cached or reused by a later
logical read, invocation or positive boundary.
"""
from __future__ import annotations

from types import SimpleNamespace

from psycopg import Transaction
from psycopg.pq import TransactionStatus

from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobFenceRejectedError

_QUEUE_GET = DurableJobQueue.get
_QUEUE_DECODE = DurableJobQueue._row_to_record
_PHYSICAL_COLUMNS = (
    "job_id", "job_type", "status", "delivery_state", "correlation_id", "idempotency_key",
    "payload_json", "created_at", "fence_token", "version", "locked_by", "heartbeat_at",
    "lease_expires_at", "attempts", "error_message", "leased_until", "available_at", "max_retries",
)
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


def _native_root_is_current(engine, connection, root_transaction):
    """Observe the actual engine-owned psycopg transaction, never caller flags."""
    local = getattr(engine, "_local", None)
    return (type(engine) is PostgresEngine and type(root_transaction) is Transaction
            and root_transaction.connection is connection
            and getattr(local, "root_transaction", None) is root_transaction
            and getattr(local, "connection", None) is connection
            and not connection.closed and root_transaction.status is Transaction.Status.ACTIVE
            and connection.info.transaction_status is TransactionStatus.INTRANS)


def _reject():
    raise JobFenceRejectedError("NATIVE_PARENT_TERMINAL_ROWS_REJECTED") from None


class _ProducerReadView:
    """The same guarded read surface used by the original producer verifiers."""

    def __init__(self, graph, before, conflict):
        self._graph, self._before, self._conflict = graph, before, conflict
        self.engine = SimpleNamespace(dialect="postgresql", query=self._query, query_one=self._query_one)
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

    def _one(self, sql, params):
        return self._read(self._graph._one, sql, params)

    def _get(self, job_id):
        self._check_native_root()
        self._before()
        return self._graph.queue.get(job_id)

    def _terminal_pair(self, tenant, job_id):
        base = self._native_base
        engine, queue = getattr(base, "engine", None), getattr(base, "queue", None)
        # Alternate repositories, per-instance fault/read adapters and changed
        # queue readers keep their original two calls. No failed query or
        # predicate ever falls back in search of a successful outcome.
        if (type(engine) is not PostgresEngine or type(queue) is not DurableJobQueue
                or queue._engine is not engine
                or getattr(queue.get, "__func__", None) is not _QUEUE_GET
                or queue._row_to_record is not _QUEUE_DECODE
                or any(name in vars(engine) for name in ("query", "query_one", "execute"))
                or self._connection is None):
            return None
        if not _native_root_is_current(engine, self._connection, self._root_transaction):
            _reject()
        tenant, job_id = native_intake_uuid(tenant), native_intake_uuid(job_id)
        # This invokes every original enclosing view's guard before the one
        # actual SELECT. Neither the SQL clock nor its two GUCs are combined
        # with the fact query, throttled, restarted or replaced.
        rows = self._query(_PAIR_SQL, (tenant, job_id, job_id))
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


def producer_read_view(graph, before, conflict):
    return _ProducerReadView(graph, before, conflict)


def native_terminal_pair(graph, tenant, job_id):
    """Return freshly read (physical, workflow), or None for original readers."""
    if type(graph) is not _ProducerReadView:
        return None
    return graph._terminal_pair(tenant, job_id)
