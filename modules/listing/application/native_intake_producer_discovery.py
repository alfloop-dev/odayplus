"""Fresh native event -> transition -> producer job discovery in one read.

This replaces transport only. The calling owner retains its event validation,
complete terminal proof and recursive lineage. A private snapshot belongs to
one invocation under the actual original root and deadline; it never supplies
current authority or a reusable successful proof. Unsupported read surfaces
select the original guarded reads before any discovery SQL executes.
"""
from __future__ import annotations

import json
from copy import deepcopy

from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_producer_reads import (
    _PHYSICAL_COLUMNS,
    _native_root_is_current,
    _ProducerReadView,
)
from modules.listing.application.native_intake_terminal_evidence import _native_context
from modules.listing.domain.identity_graph import IdentityNotFoundError
from shared.infrastructure.persistence.identity_graph_sql import CanonicalIdentityGraphRepository
from shared.jobs.queue import JobFenceRejectedError

_GRAPH_ONE = CanonicalIdentityGraphRepository._one
_VIEW_METHODS = {name: getattr(_ProducerReadView, name)
                 for name in ("_read", "_query", "_query_one", "_one", "_get")}
_TRANSITION = ("tenant_id", "transition_id", "intake_id", "job_id")
_BUSINESS = ("tenant_id", "job_id", "payload")
_EVENT_REQUIRED = {
    "tenant_id", "event_id", "event_type", "event_version", "aggregate_type",
    "aggregate_id", "aggregate_version", "correlation_id", "partition_key", "payload",
}
_DISCOVERY_SQL = (
    "SELECT e.*,t.tenant_id AS discovery_transition_tenant_id,"
    "t.transition_id AS discovery_transition_transition_id,t.intake_id AS discovery_transition_intake_id,"
    "t.job_id AS discovery_transition_job_id,w.tenant_id AS discovery_business_tenant_id,"
    "w.job_id AS discovery_business_job_id,w.payload AS discovery_business_payload,"
    "d.job_id AS discovery_physical_job_id,d.job_type AS discovery_physical_job_type,"
    "d.status AS discovery_physical_status,d.delivery_state AS discovery_physical_delivery_state,"
    "d.correlation_id AS discovery_physical_correlation_id,d.idempotency_key AS discovery_physical_idempotency_key,"
    "d.payload_json AS discovery_physical_payload_json,d.created_at AS discovery_physical_created_at,"
    "d.fence_token AS discovery_physical_fence_token,d.version AS discovery_physical_version,"
    "d.locked_by AS discovery_physical_locked_by,d.heartbeat_at AS discovery_physical_heartbeat_at,"
    "d.lease_expires_at AS discovery_physical_lease_expires_at,d.attempts AS discovery_physical_attempts,"
    "d.error_message AS discovery_physical_error_message,d.leased_until AS discovery_physical_leased_until,"
    "d.available_at AS discovery_physical_available_at,d.max_retries AS discovery_physical_max_retries "
    "FROM (SELECT ?::uuid AS tenant_id,?::uuid AS event_id) AS wanted "
    "LEFT JOIN workflow.outbox_events e ON e.tenant_id=wanted.tenant_id AND e.event_id=wanted.event_id "
    "LEFT JOIN intake.intake_stage_transitions t ON t.tenant_id=wanted.tenant_id AND t.intake_id=e.aggregate_id "
    "AND t.transition_id=CASE WHEN jsonb_typeof(e.payload->'payload'->'transition_id')='string' "
    "AND (e.payload->'payload'->>'transition_id')~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "
    "THEN (e.payload->'payload'->>'transition_id')::uuid END "
    "LEFT JOIN workflow.jobs w ON w.tenant_id=wanted.tenant_id AND w.job_id=t.job_id "
    "LEFT JOIN durable_jobs d ON d.job_id=t.job_id::text "
    "WHERE current_setting('transaction_isolation')='serializable' "
    "AND current_setting('app.tenant_id',true)=wanted.tenant_id::text"
)


def _reject():
    raise JobFenceRejectedError("NATIVE_PARENT_DISCOVERY_REJECTED") from None


def _native_read_surface(graph):
    cursor = graph
    while type(cursor) is _ProducerReadView:
        if any(getattr(getattr(cursor, name), "__func__", None) is not original
               for name, original in _VIEW_METHODS.items()):
            return False
        if (getattr(cursor.engine.query, "__self__", None) is not cursor
                or getattr(cursor.engine.query, "__func__", None) is not _VIEW_METHODS["_query"]
                or getattr(cursor.engine.query_one, "__self__", None) is not cursor
                or getattr(cursor.engine.query_one, "__func__", None) is not _VIEW_METHODS["_query_one"]
                or getattr(cursor.queue.get, "__self__", None) is not cursor
                or getattr(cursor.queue.get, "__func__", None) is not _VIEW_METHODS["_get"]):
            return False
        cursor = cursor._graph
    return (type(cursor) is CanonicalIdentityGraphRepository
            and getattr(cursor._one, "__func__", None) is _GRAPH_ONE)


class _ProducerDiscovery:
    def __init__(self, context, row, tenant, event_id):
        self._engine, self._queue, self._connection, self._guards, self._root = context
        self._live()
        groups = {"transition": _TRANSITION, "business": _BUSINESS, "physical": _PHYSICAL_COLUMNS}
        aliases = {"discovery_" + group + "_" + name for group, names in groups.items() for name in names}
        if not aliases <= row.keys() or not _EVENT_REQUIRED <= row.keys():
            _reject()
        self._parts = {group: {name: row["discovery_" + group + "_" + name] for name in names}
                       for group, names in groups.items()}
        event = {name: value for name, value in row.items() if name not in aliases}
        if event["event_id"] is not None and (event["event_id"] != event_id or event["tenant_id"] != tenant):
            _reject()
        self._event = None if event["event_id"] is None else event
        transition, business, physical = (self._parts[g] for g in ("transition", "business", "physical"))
        if transition["transition_id"] is not None:
            if self._event is None or transition["tenant_id"] != tenant or transition["intake_id"] != event["aggregate_id"]:
                _reject()
        if business["job_id"] is not None and (business["tenant_id"] != tenant
                or business["job_id"] != transition["job_id"]):
            _reject()
        if physical["job_id"] is not None and physical["job_id"] != transition["job_id"]:
            _reject()
        self._live()

    def _live(self):
        if not _native_root_is_current(self._engine, self._connection, self._root):
            _reject()
        for before in self._guards:
            before.check_in_memory()

    def _one(self, row, expected, projection=None):
        self._live()
        if row is None or any(value is None or type(row.get(name)) is not type(value)
                              or row[name] != value for name, value in expected.items()):
            raise IdentityNotFoundError("CANONICAL_IDENTITY_RESOURCE_NOT_FOUND")
        result = deepcopy(row if projection is None else {name: row[name] for name in projection})
        # Match the actual graph._one codec. Physical payload_json is decoded
        # separately by the unchanged native queue decoder.
        if isinstance(result.get("payload"), str):
            result["payload"] = json.loads(result["payload"])
        self._live()
        return result

    def event(self):
        return self._one(self._event, {})

    def transition(self, tenant, transition_id, intake_id):
        return self._one(self._parts["transition"],
            {"tenant_id": tenant, "transition_id": transition_id, "intake_id": intake_id}, ("job_id",))

    def business(self, tenant, job_id):
        return self._one(self._parts["business"], {"tenant_id": tenant, "job_id": job_id}, ("payload",))

    def delivery(self, job_id):
        self._live()
        physical = self._parts["physical"]
        if physical["job_id"] is None:
            result = None
        else:
            if physical["job_id"] != job_id:
                _reject()
            result = self._queue._row_to_record(deepcopy(physical))
        self._live()
        return result

    def finish(self):
        self._live()


def native_producer_discovery(graph, tenant, event_id):
    """Acquire fresh native facts, or choose original reads before any SQL."""
    if type(graph) is not _ProducerReadView or not _native_read_surface(graph):
        return None
    context = _native_context(graph)
    if context is None:
        return None
    tenant, event_id = native_intake_uuid(tenant), native_intake_uuid(event_id)
    for before in context[3]:
        before.check_in_memory()
    rows = graph._query(_DISCOVERY_SQL, (tenant, event_id))
    if len(rows) != 1:
        _reject()
    return _ProducerDiscovery(context, rows[0], tenant, event_id)


__all__ = ["native_producer_discovery"]
