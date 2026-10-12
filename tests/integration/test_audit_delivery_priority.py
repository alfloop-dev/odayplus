"""Priority preserves receipt exclusion, leases and ordinary FIFO in local queues."""

import pytest

from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import InMemoryJobQueue, JobRequest, JobStatus


@pytest.mark.parametrize("durable", [False, True])
@pytest.mark.parametrize("method", ["lease", "claim_next"])
def test_audit_priority_keeps_receipts_nonexecutable_and_ordinary_jobs_in_order(tmp_path, durable, method):
    bundle = _durable_bundle(tmp_path / "synthetic-priority.sqlite") if durable else None
    queue = bundle.job_queue if bundle else InMemoryJobQueue()
    try:
        queue.enqueue(JobRequest("synthetic.command-receipt", {}), correlation_id="synthetic")
        first, _ = queue.enqueue(JobRequest("ordinary-one", {}), correlation_id="synthetic")
        second, _ = queue.enqueue(JobRequest("ordinary-two", {}), correlation_id="synthetic")
        audits = []
        for kind in ("canonical-audit-delivery", "store-ops-audit-delivery"):
            job, _ = queue.enqueue(JobRequest(kind, {}), correlation_id="synthetic")
            audits.append(job.job_id)
        get = (lambda: queue.lease(45)) if method == "lease" else (lambda: queue.claim_next(worker_id="synthetic-priority"))
        claimed = []
        for _ in range(4):
            job = get()
            claimed.append(job.job_id)
            if method == "lease":
                assert queue.complete(job.job_id, lease_token=job.leased_until)
            else:
                queue.update_status(job.job_id, JobStatus.SUCCEEDED, expected_version=job.version, fence_token=job.fence_token)
        assert claimed == [*audits, first.job_id, second.job_id]
        assert get() is None
    finally:
        if bundle:
            bundle.engine.close()


@pytest.mark.parametrize("durable", [False, True])
def test_audit_only_claim_never_consumes_ordinary_work_and_preserves_default_claim(tmp_path, durable):
    bundle = _durable_bundle(tmp_path / "synthetic-audit-only.sqlite") if durable else None
    queue = bundle.job_queue if bundle else InMemoryJobQueue()
    try:
        ordinary, _ = queue.enqueue(JobRequest("ordinary", {}), correlation_id="synthetic")
        receipt, _ = queue.enqueue(JobRequest("canonical-audit-delivery.receipt", {}), correlation_id="synthetic")
        before = queue.get(ordinary.job_id)
        assert queue.claim_next(worker_id="synthetic-audit-publisher", audit_only=True) is None
        assert queue.get(ordinary.job_id) == before
        for kind in ("canonical-audit-delivery", "store-ops-audit-delivery"):
            audit, _ = queue.enqueue(JobRequest(kind, {}), correlation_id="synthetic")
            claim = queue.claim_next(worker_id="synthetic-audit-publisher", audit_only=True)
            assert claim.job_id == audit.job_id and claim.locked_by == "synthetic-audit-publisher"
            queue.update_status(claim.job_id, JobStatus.SUCCEEDED, expected_version=claim.version, fence_token=claim.fence_token)
        assert queue.claim_next(worker_id="synthetic-audit-publisher", audit_only=True) is None
        assert queue.get(ordinary.job_id) == before
        assert queue.get(receipt.job_id).status == JobStatus.QUEUED
        assert queue.claim_next(worker_id="synthetic-general").job_id == ordinary.job_id
    finally:
        if bundle:
            bundle.engine.close()
