"""Consume only the exact committed retained-evidence owner and job."""
from shared.audit.retained_delivery import RetainedEvidenceDelivery
from shared.jobs.queue import NonRetryableJobError


def handle_retained_evidence_delivery(job, persistence):
    tenant_id = job.payload.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise NonRetryableJobError("Retained delivery requires its committed tenant")
    try:
        owner = RetainedEvidenceDelivery(engine=persistence.engine, tenant_id=tenant_id,
                                        queue=persistence.job_queue,
                                        evidence_store=persistence.evidence_store,
                                        audit_log=persistence.audit_log)
    except (ValueError, AttributeError) as exc:
        raise NonRetryableJobError("Retained delivery owner is unavailable") from exc
    return owner.deliver(job)
