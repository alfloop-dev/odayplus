"""Registered background consumer for committed canonical audit facts."""

from shared.audit.canonical_delivery import CanonicalAuditDelivery
from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
from shared.jobs.queue import NonRetryableJobError


def handle_canonical_audit_delivery(job, persistence):
    if not persistence.is_production:
        raise NonRetryableJobError("Canonical audit delivery requires PostgreSQL")
    sink = getattr(persistence.audit_log, "_worm_sink", None)
    if sink is None:
        raise NonRetryableJobError("Canonical audit delivery sink is not configured")
    return CanonicalAuditDelivery(
        CanonicalPostgresAuditLog(persistence.engine), persistence.job_queue, sink
    ).deliver(job)
