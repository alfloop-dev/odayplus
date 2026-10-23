"""Invocation-local canonical rows for the complete successful manual proof.

Only parser's fixed SQL budget uses this transport. Every invocation, including
the final positive boundary, reloads the rows. This contains no proven parent,
grant, fabricated historical object or transaction-wide cache.
"""
from __future__ import annotations

from copy import deepcopy

from modules.listing.application.native_intake_assisted_entry_request_proof import (
    _object,
    request_audit_id,
)
from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_producer_discovery import _native_read_surface
from modules.listing.application.native_intake_producer_reads import (
    _PHYSICAL_COLUMNS,
    _native_root_is_current,
    producer_read_view,
)
from modules.listing.application.native_intake_retrieval_policy import canonical_sha256
from modules.listing.application.native_intake_terminal_evidence import _native_context
from shared.jobs.queue import JobFenceRejectedError

_SETTINGS_PHYSICAL_SQL = (
    "SELECT current_setting('transaction_isolation') AS isolation,"
    "current_setting('app.tenant_id',true) AS tenant,"
    "d.job_id AS history_physical_job_id,d.job_type AS history_physical_job_type,"
    "d.status AS history_physical_status,d.delivery_state AS history_physical_delivery_state,"
    "d.correlation_id AS history_physical_correlation_id,d.idempotency_key AS history_physical_idempotency_key,"
    "d.payload_json AS history_physical_payload_json,d.created_at AS history_physical_created_at,"
    "d.fence_token AS history_physical_fence_token,d.version AS history_physical_version,"
    "d.locked_by AS history_physical_locked_by,d.heartbeat_at AS history_physical_heartbeat_at,"
    "d.lease_expires_at AS history_physical_lease_expires_at,d.attempts AS history_physical_attempts,"
    "d.error_message AS history_physical_error_message,d.leased_until AS history_physical_leased_until,"
    "d.available_at AS history_physical_available_at,d.max_retries AS history_physical_max_retries "
    "FROM (SELECT ?::text AS job_id,?::uuid AS tenant_id) AS wanted "
    "LEFT JOIN durable_jobs d ON d.job_id=wanted.job_id "
    "WHERE current_setting('transaction_isolation')='serializable' "
    "AND current_setting('app.tenant_id',true)=wanted.tenant_id::text"
)


def _reject():
    raise JobFenceRejectedError("NATIVE_ASSISTED_TERMINAL_ROWS_REJECTED")


def read_success_history_rows(graph, job, payload, result, before):
    # Lazy import: parser prerequisites themselves depend on the manual reader.
    from modules.listing.application.native_intake_parser_prerequisites import (
        NativeParserCommandBudget,
        NativeParserSQLClock,
    )

    if (type(before) is not NativeParserCommandBudget
            or type(before.source) is not NativeParserSQLClock
            or before.source.engine is not graph.engine):
        return None
    return _SuccessHistoryRows(graph, job, payload, result, before)


def history_physical(rows, job):
    """Use only this invocation's actual native acquisition, never a DTO."""
    if type(rows) is not _SuccessHistoryRows or rows.context is None:
        return False, None
    return True, rows.physical(job)


class _SuccessHistoryRows:
    def __init__(self, graph, job, payload, result, before):
        engine = graph.engine
        self.engine, self.before = engine, before
        view = producer_read_view(graph, before, JobFenceRejectedError)
        self.context = _native_context(view) if _native_read_surface(view) else None
        self.connection = getattr(getattr(engine, "_local", None), "connection", None)
        if getattr(engine, "dialect", None) != "postgresql" or self.connection is None:
            _reject()
        self.tenant = native_intake_uuid(job["tenant_id"])
        self.audit_ids = {
            "request": request_audit_id(payload["request_id"]),
            "admission": _object(payload["admission_receipt"])["audit_event_id"],
            "acceptance": _object(payload["acceptance_receipt"])["audit_event_id"],
            "completion": result["audit_event_id"],
        }
        self.event_ids = {"request": payload["request_id"], "state": result["outbox_event_id"],
            "snapshot": result["snapshot_created_event_id"]}
        for ids in (self.audit_ids, self.event_ids):
            for value in ids.values():
                native_intake_uuid(value)
            if len(set(ids.values())) != len(ids):
                _reject()
        before()
        if self.context is None:
            self.settings = engine.query_one("SELECT current_setting('transaction_isolation') AS isolation, "
                "current_setting('app.tenant_id',true) AS tenant")
        else:
            # The existing settings acquisition also reads all real physical
            # cells. Two later reads inside this one complete history proof
            # consume these facts; every subsequent invocation reloads them.
            row = engine.query_one(_SETTINGS_PHYSICAL_SQL,
                (native_intake_uuid(job["job_id"]), self.tenant))
            if (row is None or not {"isolation", "tenant"} <= row.keys()
                    or not {"history_physical_" + name for name in _PHYSICAL_COLUMNS} <= row.keys()):
                _reject()
            self.settings = {name: row[name] for name in ("isolation", "tenant")}
            physical = {name: row["history_physical_" + name] for name in _PHYSICAL_COLUMNS}
            if physical["job_id"] is not None and physical["job_id"] != job["job_id"]:
                _reject()
            self._physical = None if physical["job_id"] is None else self.context[1]._row_to_record(physical)
            self._job_sha256 = canonical_sha256(job)
            self._physical_consumed = 0
        self._check()
        if self.settings != {"isolation": "serializable", "tenant": self.tenant}:
            _reject()
        self.events = self._load("SELECT * FROM workflow.outbox_events WHERE tenant_id=? AND event_id=ANY(?::uuid[])",
            "event_id", self.event_ids)
        self.audits = self._load("SELECT * FROM audit.audit_events WHERE tenant_id=? AND audit_event_id=ANY(?::uuid[])",
            "audit_event_id", self.audit_ids)
        self.consumed = set()
        self.failed = set()

    def _check(self):
        self.before.check_in_memory()
        if getattr(getattr(self.engine, "_local", None), "connection", None) is not self.connection:
            _reject()
        if self.context is not None and not _native_root_is_current(
                self.engine, self.connection, self.context[4]):
            _reject()

    def physical(self, job):
        self._check()
        if (self.context is None or canonical_sha256(job) != self._job_sha256
                or self._physical_consumed >= 2):
            _reject()
        self._physical_consumed += 1
        result = deepcopy(self._physical)
        self._check()
        return result

    def _load(self, statement, id_column, roles):
        # Only the two fixed statements above; IDs remain parameters.
        self.before()
        rows = self.engine.query(statement, (self.tenant, list(roles.values())))
        self._check()
        indexed = {}
        for row in rows:
            key = row[id_column]
            if row["tenant_id"] != self.tenant or key not in roles.values() or key in indexed:
                _reject()
            indexed[key] = row
        if set(indexed) != set(roles.values()):
            _reject()
        return indexed

    def _take(self, kind, role, rows, ids, expected):
        self._check()
        key = kind, role
        if key in self.consumed or role not in ids:
            _reject()
        self.consumed.add(key)
        row = rows[ids[role]]
        if not all(k in row and row[k] == v for k, v in expected.items()):
            self.failed.add(key)
            return None
        return row

    def root(self, tenant):
        self._check()
        if ("root", "settings") in self.consumed or tenant != self.tenant:
            _reject()
        self.consumed.add(("root", "settings"))

    def audit(self, role, **expected):
        return self._take("audit", role, self.audits, self.audit_ids, expected)

    def event(self, role, **expected):
        return self._take("event", role, self.events, self.event_ids, expected)

    def request_reason(self):
        # The very same request row already passed the complete origin proof.
        self._check()
        if (("audit", "request") not in self.consumed or ("audit", "request") in self.failed
                or ("reason", "request") in self.consumed):
            _reject()
        self.consumed.add(("reason", "request"))
        return self.audits[self.audit_ids["request"]]

    def finish(self):
        self._check()
        expected = {("root", "settings"), ("reason", "request"),
            *(("audit", role) for role in self.audit_ids), *(("event", role) for role in self.event_ids)}
        if self.failed or self.consumed != expected:
            _reject()
        if self.context is not None and self._physical_consumed != 2:
            _reject()
