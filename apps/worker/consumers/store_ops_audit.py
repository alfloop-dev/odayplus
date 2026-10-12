"""The registered worker retries one committed Store Ops audit delivery."""

from uuid import UUID

from modules.opsboard.application.store_ops_audit_delivery import COLLECTION, StoreOpsAuditDelivery
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import NonRetryableJobError


def handle_store_ops_audit_delivery(job, persistence):
    tenant_id = str(job.payload.get("tenant_id") or "")
    event_id = str(job.payload.get("audit_event_id") or "")
    try:
        UUID(tenant_id)
        UUID(event_id)
    except ValueError as exc:
        raise NonRetryableJobError("Store Ops audit delivery identifiers are invalid") from exc
    if not persistence.is_production:
        raise NonRetryableJobError("Store Ops audit delivery requires PostgreSQL")
    sink = getattr(persistence.audit_log, "_worm_sink", None)
    if sink is None or job.payload.get("sink_id") != sink.sink_id:
        raise NonRetryableJobError("Store Ops audit target differs from the configured sink")
    store = TenantScopedDocumentStore(PostgresDocumentStore(persistence.engine), tenant_id)
    row = store.get(COLLECTION, event_id)
    if row is None or row.get("sink_id") != sink.sink_id:
        raise NonRetryableJobError("Committed Store Ops delivery intent was not found")
    if row["event"].metadata.get("tenant_id") != tenant_id:
        raise NonRetryableJobError("Store Ops delivery intent tenant is invalid")
    StoreOpsAuditDelivery(store, sink).deliver_pending(event_id=event_id)
    delivered = store.get(COLLECTION, event_id)
    if delivered is None or delivered.get("status") != "delivered":
        raise RuntimeError("Store Ops WORM delivery is still pending")
