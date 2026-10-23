"""Execute real receipt matching without swallowing a target business job.

Synthetic integration fixtures may precede scoring/parser work with legitimate
matching events. Audit claims keep each caller's existing delivery semantics.
"""

from copy import deepcopy
from hashlib import sha256

from apps.worker.oday_worker.handlers import build_default_registry
from modules.listing.application.saved_search import receipt_matching_facts
from shared.infrastructure.persistence.listing_search_delivery import EVENTS, JOB_TYPE
from shared.infrastructure.persistence.listing_search_matches import evidence_digest
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.jobs.queue import JobStatus


def execute_claimed_listing_search(bundle, job):
    """Return False for another type; execute and settle actual matching work."""
    if job.job_type != JOB_TYPE:
        return False
    build_default_registry().handle(job, bundle)
    current = bundle.job_queue.get(job.job_id)
    assert current is not None and current.status == JobStatus.RUNNING
    assert current.fence_token == job.fence_token
    bundle.job_queue.update_status(
        job.job_id, JobStatus.SUCCEEDED,
        expected_version=current.version, fence_token=job.fence_token,
    )
    finished = bundle.job_queue.get(job.job_id)
    assert finished is not None and finished.status == JobStatus.SUCCEEDED
    return True


def listing_search_events(bundle, tenant):
    documents = TenantScopedDocumentStore(bundle.assisted_intake_store._store, tenant)
    return sorted(deepcopy(documents.list_all(EVENTS)), key=lambda row: row["id"])


def assert_receipt_revision_intent(bundle, receipt):
    """Read only this known revision's server-owned event/job, not all deltas."""
    identity, version = receipt["intake_id"], receipt["version"]
    event_id = sha256(f"receipt:{len(identity)}:{identity}:{version}".encode()).hexdigest()
    documents = TenantScopedDocumentStore(bundle.assisted_intake_store._store, receipt["scope"]["tenant_id"])
    event = documents.get(EVENTS, event_id)
    facts = receipt_matching_facts(receipt)
    assert event is not None and event["kind"] == "receipt"
    assert event["id"] == event_id and event["facts"] == facts
    assert event["facts_sha256"] == evidence_digest(facts)
    assert event["tenant_id"] == receipt["scope"]["tenant_id"]
    job = bundle.job_queue.get(event["job_id"])
    assert job is not None and job.job_type == JOB_TYPE
    assert job.status == JobStatus.QUEUED and job.version == 1
    assert job.idempotency_key == "listing-search-internal:" + job.job_id
    assert job.payload == {"tenant_id": event["tenant_id"], "event_id": event_id,
                           "facts_sha256": event["facts_sha256"]}
    return deepcopy(event), {"job_id": job.job_id, "status": job.status.value, "version": job.version}
