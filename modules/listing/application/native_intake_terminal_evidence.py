"""Fresh native terminal facts for one invocation of an existing verifier.

One guarded SELECT reads the actual job pair and referenced admission audit,
transition, result audit and outbox row. The existing owners still evaluate
their complete predicates. Closed fact projections use the same original
monotonic deadline; they issue no SQL or authority. Nothing is shared with a
later logical verifier, transaction, action or permission check.
"""
from __future__ import annotations

from copy import deepcopy

from modules.listing.application.native_intake_job_runtime import native_intake_uuid
from modules.listing.application.native_intake_producer_reads import (
    _PHYSICAL_COLUMNS,
    _native_producer_noop,
    _native_root_is_current,
    _ProducerReadView,
)
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.postgresql import PostgresEngine
from shared.jobs.queue import JobFenceRejectedError

_QUEUE_GET = DurableJobQueue.get
_QUEUE_DECODE = DurableJobQueue._row_to_record
_AUDIT = (
    "tenant_id", "audit_event_id", "resource_type", "resource_id", "actor_subject_id",
    "service_principal", "actor_role", "action", "result", "causation_id", "correlation_id", "after_value",
)
_TRANSITION = (
    "tenant_id", "transition_id", "intake_id", "job_id", "from_state", "to_state",
    "resulting_version", "expected_version", "actor_subject_id", "service_principal",
    "permission", "source_policy_version", "reason_code", "causation_id", "correlation_id",
)
_OUTBOX = (
    "tenant_id", "event_id", "event_type", "aggregate_type", "aggregate_id",
    "aggregate_version", "correlation_id", "payload",
)
_GROUPS = {
    "physical": ("d", _PHYSICAL_COLUMNS, "job_id"),
    "admission": ("a", _AUDIT, "audit_event_id"),
    "transition": ("t", _TRANSITION, "transition_id"),
    "audit": ("r", _AUDIT, "audit_event_id"),
    "outbox": ("o", _OUTBOX, "event_id"),
}




_TERMINAL_SQL = (
    'SELECT w.*,d.job_id AS terminal_physical_job_id,d.job_type AS '
    'terminal_physical_job_type,d.status AS terminal_physical_status,d.delivery_state AS '
    'terminal_physical_delivery_state,d.correlation_id AS '
    'terminal_physical_correlation_id,d.idempotency_key AS '
    'terminal_physical_idempotency_key,d.payload_json AS terminal_physical_payload_json,d.created_at '
    'AS terminal_physical_created_at,d.fence_token AS terminal_physical_fence_token,d.version AS '
    'terminal_physical_version,d.locked_by AS terminal_physical_locked_by,d.heartbeat_at AS '
    'terminal_physical_heartbeat_at,d.lease_expires_at AS '
    'terminal_physical_lease_expires_at,d.attempts AS terminal_physical_attempts,d.error_message AS '
    'terminal_physical_error_message,d.leased_until AS terminal_physical_leased_until,d.available_at '
    'AS terminal_physical_available_at,d.max_retries AS terminal_physical_max_retries,a.tenant_id AS '
    'terminal_admission_tenant_id,a.audit_event_id AS '
    'terminal_admission_audit_event_id,a.resource_type AS '
    'terminal_admission_resource_type,a.resource_id AS '
    'terminal_admission_resource_id,a.actor_subject_id AS '
    'terminal_admission_actor_subject_id,a.service_principal AS '
    'terminal_admission_service_principal,a.actor_role AS terminal_admission_actor_role,a.action AS '
    'terminal_admission_action,a.result AS terminal_admission_result,a.causation_id AS '
    'terminal_admission_causation_id,a.correlation_id AS '
    'terminal_admission_correlation_id,a.after_value AS terminal_admission_after_value,t.tenant_id AS '
    'terminal_transition_tenant_id,t.transition_id AS terminal_transition_transition_id,t.intake_id '
    'AS terminal_transition_intake_id,t.job_id AS terminal_transition_job_id,t.from_state AS '
    'terminal_transition_from_state,t.to_state AS terminal_transition_to_state,t.resulting_version AS '
    'terminal_transition_resulting_version,t.expected_version AS '
    'terminal_transition_expected_version,t.actor_subject_id AS '
    'terminal_transition_actor_subject_id,t.service_principal AS '
    'terminal_transition_service_principal,t.permission AS '
    'terminal_transition_permission,t.source_policy_version AS '
    'terminal_transition_source_policy_version,t.reason_code AS '
    'terminal_transition_reason_code,t.causation_id AS '
    'terminal_transition_causation_id,t.correlation_id AS '
    'terminal_transition_correlation_id,r.tenant_id AS terminal_audit_tenant_id,r.audit_event_id AS '
    'terminal_audit_audit_event_id,r.resource_type AS terminal_audit_resource_type,r.resource_id AS '
    'terminal_audit_resource_id,r.actor_subject_id AS '
    'terminal_audit_actor_subject_id,r.service_principal AS '
    'terminal_audit_service_principal,r.actor_role AS terminal_audit_actor_role,r.action AS '
    'terminal_audit_action,r.result AS terminal_audit_result,r.causation_id AS '
    'terminal_audit_causation_id,r.correlation_id AS terminal_audit_correlation_id,r.after_value AS '
    'terminal_audit_after_value,o.tenant_id AS terminal_outbox_tenant_id,o.event_id AS '
    'terminal_outbox_event_id,o.event_type AS terminal_outbox_event_type,o.aggregate_type AS '
    'terminal_outbox_aggregate_type,o.aggregate_id AS '
    'terminal_outbox_aggregate_id,o.aggregate_version AS '
    'terminal_outbox_aggregate_version,o.correlation_id AS terminal_outbox_correlation_id,o.payload '
    'AS terminal_outbox_payload FROM (SELECT ?::uuid AS tenant_id,?::uuid AS job_id) AS wanted LEFT '
    'JOIN workflow.jobs w ON w.tenant_id=wanted.tenant_id AND w.job_id=wanted.job_id LEFT JOIN '
    'durable_jobs d ON d.job_id=? LEFT JOIN audit.audit_events a ON a.tenant_id=wanted.tenant_id AND '
    "w.status='SUCCEEDED' AND a.audit_event_id=CASE WHEN "
    "jsonb_typeof(w.payload->'admission_receipt'->'audit_event_id')='string' AND "
    "(w.payload->'admission_receipt'->'audit_event_id'#>>'{}')~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    "[0-9a-f]{4}-[0-9a-f]{12}$' THEN (w.payload->'admission_receipt'->'audit_event_id'#>>'{}')::uuid "
    'END LEFT JOIN intake.intake_stage_transitions t ON t.tenant_id=wanted.tenant_id AND '
    "w.status='SUCCEEDED' AND t.transition_id=CASE WHEN "
    "jsonb_typeof(w.result->'transition_id')='string' AND "
    "(w.result->'transition_id'#>>'{}')~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12"
    "}$' THEN (w.result->'transition_id'#>>'{}')::uuid END LEFT JOIN audit.audit_events r ON "
    "r.tenant_id=wanted.tenant_id AND w.status='SUCCEEDED' AND r.audit_event_id=CASE WHEN "
    "jsonb_typeof(w.result->'audit_event_id')='string' AND "
    "(w.result->'audit_event_id'#>>'{}')~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{1"
    "2}$' THEN (w.result->'audit_event_id'#>>'{}')::uuid END LEFT JOIN workflow.outbox_events o ON "
    "o.tenant_id=wanted.tenant_id AND w.status='SUCCEEDED' AND o.event_id=CASE WHEN "
    "jsonb_typeof(w.result->'outbox_event_id')='string' AND "
    "(w.result->'outbox_event_id'#>>'{}')~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{"
    "12}$' THEN (w.result->'outbox_event_id'#>>'{}')::uuid END WHERE "
    "current_setting('transaction_isolation')='serializable' AND "
    "current_setting('app.tenant_id',true)=wanted.tenant_id::text"
)


def _reject():
    raise JobFenceRejectedError("NATIVE_PARENT_TERMINAL_EVIDENCE_REJECTED") from None


def _native_context(graph):
    if type(graph) is not _ProducerReadView:
        return None
    base, guards, cursor = graph._native_base, [], graph
    engine, queue = getattr(base, "engine", None), getattr(base, "queue", None)
    if (type(engine) is not PostgresEngine or type(queue) is not DurableJobQueue
            or queue._engine is not engine
            or getattr(queue.get, "__func__", None) is not _QUEUE_GET
            or queue._row_to_record is not _QUEUE_DECODE
            or any(name in vars(engine) for name in ("query", "query_one", "execute"))
            or graph._connection is None):
        return None
    if not _native_root_is_current(engine, graph._connection, graph._root_transaction):
        _reject()
    # Unknown callbacks/adapters retain all their original live reads. Only
    # native budgets explicitly support closed-row in-memory proof checks.
    from modules.listing.application.native_intake_parser_prerequisites import (
        NativeParserCommandBudget,
        NativeParserSQLClock,
    )
    from modules.listing.application.native_intake_pending_commands import NativeIntakeCommandBudget
    from modules.listing.application.native_intake_source_policy import (
        NativeIntakeSourcePolicyOwner,
    )
    from modules.listing.application.native_intake_source_policy_budget import (
        NativeSourcePolicyAdmissionBudget,
    )

    budget_types = (NativeIntakeCommandBudget, NativeParserCommandBudget, NativeSourcePolicyAdmissionBudget)
    clock_types = (NativeParserSQLClock, NativeIntakeSourcePolicyOwner)
    while type(cursor) is _ProducerReadView:
        before = cursor._before
        if (cursor._connection is not graph._connection
                or cursor._root_transaction is not graph._root_transaction):
            _reject()
        if before is _native_producer_noop:
            # This exact internal no-op never stood for a deadline.
            # All enclosing actual budgets still guard the real SELECT.
            cursor = cursor._graph
            continue
        if (type(before) not in budget_types
                or type(before).__call__ is not NativeIntakeCommandBudget.__call__
                or any(name in vars(before) for name in ("__call__", "check_in_memory"))):
            return None
        source = before.source
        if (type(source) not in clock_types
                or source.engine is not engine
                or any(name in vars(source) for name in ("_clock", "_admission_budget"))):
            return None
        for method in (source._clock, source._admission_budget):
            if (getattr(method, "__module__", None) != type(source).__module__
                    or hasattr(method, "__wrapped__")):
                return None
        guards.append(before)
        cursor = cursor._graph
    # A no-op-only read surface cannot close fact projections.
    return (engine, queue, graph._connection, tuple(guards), graph._root_transaction) if guards else None


class _TerminalEvidence:
    """A private factual snapshot, with no fabricated Job/Intake or grants."""

    def __init__(self, context, row, tenant, job_id):
        self._engine, queue, self._connection, self._guards, self._root_transaction = context
        self._live()
        expected = {f"terminal_{group}_{column}"
                    for group, (_, columns, _) in _GROUPS.items() for column in columns}
        if not expected <= row.keys():
            _reject()
        self._rows = {}
        for group, (_, columns, identity) in _GROUPS.items():
            fact = {column: row[f"terminal_{group}_{column}"] for column in columns}
            self._rows[group] = None if fact[identity] is None else fact
        job = {key: value for key, value in row.items() if key not in expected}
        self._job = None if job.get("job_id") is None else job
        if self._job is not None and (job["tenant_id"] != tenant or job["job_id"] != job_id):
            _reject()
        physical = self._rows["physical"]
        if physical is not None and physical["job_id"] != job_id:
            _reject()
        self._physical = None if physical is None else queue._row_to_record(physical)

    def _live(self):
        if not _native_root_is_current(self._engine, self._connection, self._root_transaction):
            _reject()
        for before in self._guards:
            before.check_in_memory()

    def pair(self):
        self._live()
        result = deepcopy(self._physical), deepcopy(self._job)
        self._live()
        return result

    def _select(self, group, expected, projection):
        self._live()
        if (type(expected) is not dict or not expected
                or not expected.keys() <= set(_GROUPS[group][1])):
            _reject()
        row = self._rows[group]
        # All original parameter predicates use SQL equality: a NULL
        # parameter never matches. Only actor_subject_id is the original
        # literal IS NULL predicate in these eleven native owner reads.
        if (row is None
                or any(value is None and key != "actor_subject_id" for key, value in expected.items())
                or any(type(row[key]) is not type(value) or row[key] != value
                       for key, value in expected.items())):
            return None
        result = {key: deepcopy(row[key]) for key in projection}
        self._live()
        return result

    def transition(self, expected):
        return self._select("transition", expected, ("transition_id",))

    def admission_audit(self, expected):
        return self._select("admission", expected, ("after_value",))

    def result_audit(self, expected):
        return self._select("audit", expected, ("after_value",))

    def outbox(self, expected):
        return self._select("outbox", expected, ("payload",))

    def finish(self):
        self._live()


def native_terminal_evidence(graph, tenant, job_id):
    """Read fresh actual facts, or select legacy readers before any SQL.

    No failed query or fact validation can fall back. The caller's original
    guards set the actual SQL timeouts for this one SELECT. Subsequent closed
    fact projections keep its original monotonic deadline and current root.
    """
    context = _native_context(graph)
    if context is None:
        return None
    tenant, job_id = native_intake_uuid(tenant), native_intake_uuid(job_id)
    engine, _, connection, guards, root_transaction = context
    if not _native_root_is_current(engine, connection, root_transaction):
        _reject()
    for before in guards:
        before.check_in_memory()
    rows = graph._query(_TERMINAL_SQL, (tenant, job_id, job_id))
    if len(rows) != 1:
        _reject()
    return _TerminalEvidence(context, rows[0], tenant, job_id)


__all__ = ["native_terminal_evidence"]
