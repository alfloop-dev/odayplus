"""Publish a committed durable audit intent through the registered worker."""

from shared.audit.durable_delivery import DurableAuditDelivery
from shared.jobs.queue import NonRetryableJobError


def handle_durable_audit_delivery(job, persistence):
    from shared.audit.platform_flag_delivery import SCOPE, PlatformFlagAuditDelivery

    if job.payload.get("scope") == SCOPE:
        sink = getattr(persistence.audit_log, "_worm_sink", None)
        if (sink is None
                or getattr(persistence.audit_log, "_engine", None) is not persistence.engine):
            raise NonRetryableJobError("Platform audit delivery requires its actual engine and sink")
        return PlatformFlagAuditDelivery(
            persistence.engine, persistence.job_queue, sink,
        ).deliver(job)
    tenant_id = str(job.payload.get("tenant_id") or "")
    sink = getattr(persistence.audit_log, "_worm_sink", None)
    if (not tenant_id or sink is None
            or getattr(persistence.audit_log, "_engine", None) is not persistence.engine):
        raise NonRetryableJobError("Durable audit delivery requires its tenant and configured sink")
    return DurableAuditDelivery(
        persistence.engine, tenant_id, persistence.job_queue, sink,
    ).deliver(job)
