from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.assisted_listing_intake.worker import handle_assisted_listing_intake
from shared.auth import Role
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
from shared.jobs.queue import JobRequest, JobStatus
from tests.integration._authz import auth_headers

HEADERS = {
    **auth_headers(Role.EXPANSION_USER),
    "x-tenant-id": "tenant-a",
}


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "intake_durable_reliability.sqlite3")


def test_backpressure_threshold(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        app = create_app(persistence=bundle)
        client = TestClient(app)

        # Enqueue 200 dummy jobs to trigger backpressure
        for i in range(200):
            bundle.job_queue.enqueue(
                JobRequest(
                    job_type="assisted-listing-intake",
                    payload={"intake_id": f"IN-DUMMY-{i}", "url": f"https://www.example.com/{i}"},
                ),
                correlation_id="corr-dummy",
            )

        # Attempt to submit another job
        url = "https://www.synthetic.example/detail-77120345.html"
        resp = client.post(
            "/api/v1/operator/network-listings/intake/submit",
            json={"url": url, "heatZoneId": "HZ-01"},
            headers={
                **HEADERS,
                "X-Correlation-Id": "corr-async-bp",
                "Idempotency-Key": "idem-async-bp",
                "X-Async-Intake": "true",
            },
        )
        assert resp.status_code == 503
        assert resp.json()["detail"] == "BACKPRESSURE_ACTIVE"
        assert resp.headers["Retry-After"] == "30"
    finally:
        bundle.engine.close()


def test_fencing_optimistic_locking(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        # Enqueue an intake job
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={
                    "intake_id": "IN-FENCE-1",
                    "url": "https://www.synthetic.example/detail-77120345.html",
                },
            ),
            correlation_id="corr-fence",
        )

        # Worker 1 claims job
        claimed_w1 = bundle.job_queue.claim_next(worker_id="worker-1")
        assert claimed_w1 is not None
        assert claimed_w1.fence_token == 1
        assert claimed_w1.version == 2

        # Worker 2 claims job (simulated lease timeout claim by modifying DB)
        bundle.engine.execute(
            "UPDATE durable_jobs SET fence_token = ?, version = ? WHERE job_id = ?",
            (2, 3, job.job_id),
        )

        # Worker 1 attempts to update status using its stale fence token 1
        with pytest.raises(JobFenceRejectedError):
            bundle.job_queue.update_status(
                job.job_id,
                JobStatus.RUNNING,
                expected_version=claimed_w1.version,
                fence_token=claimed_w1.fence_token,
            )

        # Worker 1 heartbeat should also fail
        with pytest.raises(JobFenceRejectedError):
            bundle.job_queue.heartbeat(
                job.job_id, expected_version=claimed_w1.version, fence_token=claimed_w1.fence_token
            )
    finally:
        bundle.engine.close()


def test_lease_expiration_claiming(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        # Enqueue an intake job
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={
                    "intake_id": "IN-LEASE-1",
                    "url": "https://www.synthetic.example/detail-77120345.html",
                },
            ),
            correlation_id="corr-lease",
        )

        # Worker 1 claims job
        claimed_w1 = bundle.job_queue.claim_next(worker_id="worker-1")
        assert claimed_w1 is not None

        # Modify database to make lease expired (e.g. lease_expires_at is in the past)
        past_time = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        bundle.engine.execute(
            "UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?", (past_time, job.job_id)
        )

        # Worker 2 should be able to claim the expired job!
        claimed_w2 = bundle.job_queue.claim_next(worker_id="worker-2")
        assert claimed_w2 is not None
        assert claimed_w2.fence_token == claimed_w1.fence_token + 1
        assert claimed_w2.locked_by == "worker-2"

    finally:
        bundle.engine.close()


def test_poison_isolation_retry_limits(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        # Enqueue job
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={
                    "intake_id": "IN-POISON-1",
                    "url": "https://www.synthetic.example/detail-77120345.html",
                },
            ),
            correlation_id="corr-poison",
        )

        # Pretend stage Retries exceeded
        # Identity Check has limit of 3
        job_claimed = bundle.job_queue.claim_next(worker_id="worker-1")

        # Set attempts to 3 in payload
        payload = dict(job_claimed.payload)
        payload["stage_attempts"] = {"CHECKING_IDENTITY": 3}
        bundle.job_queue.update_status(
            job_claimed.job_id,
            JobStatus.RUNNING,
            payload=payload,
            expected_version=job_claimed.version,
            fence_token=job_claimed.fence_token,
        )

        # Run handler and verify it aborts with RuntimeError (exceeded max attempts)
        job_updated = bundle.job_queue.get(job_claimed.job_id)
        with pytest.raises(RuntimeError, match="CHECKING_IDENTITY exceeded max attempts"):
            handle_assisted_listing_intake(job_updated, bundle)

    finally:
        bundle.engine.close()


def test_job_cancellation_and_replay(db_path) -> None:
    bundle = _durable_bundle(db_path)
    try:
        # Enqueue job
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={
                    "intake_id": "IN-CANCEL-1",
                    "url": "https://www.synthetic.example/detail-77120345.html",
                },
            ),
            correlation_id="corr-cancel",
        )

        # Claim job (status -> RUNNING)
        claimed = bundle.job_queue.claim_next(worker_id="worker-1")
        assert claimed is not None

        # Cancel job (status -> CANCELLED)
        bundle.job_queue.update_status(job.job_id, JobStatus.CANCELLED)

        # Verify executing handle raises JobFenceRejectedError
        with pytest.raises(JobFenceRejectedError):
            handle_assisted_listing_intake(claimed, bundle)

        # Verify replay resetting attempts and status
        replayed = bundle.job_queue.replay(job.job_id)
        assert replayed.status == JobStatus.QUEUED
        assert replayed.attempts == 0
        assert replayed.error_message is None

    finally:
        bundle.engine.close()


def test_retrieval_stage_local_retry_and_timeout(db_path) -> None:
    from unittest.mock import patch

    bundle = _durable_bundle(db_path)
    try:
        from shared.infrastructure.persistence.document_store import SqliteDocumentStore
        from shared.infrastructure.persistence.operator_network_listings import (
            DurableAssistedIntakeRepository,
        )

        doc_store = SqliteDocumentStore(bundle.engine)
        intake_repo = DurableAssistedIntakeRepository(doc_store)
        intake_repo.save_intake(
            {
                "id": "IN-RETRY-1",
                "originalUrl": "https://www.synthetic.example/detail-77120345.html",
                "canonicalUrl": "https://www.synthetic.example/detail-77120345.html",
                "stage": "SUBMITTED",
                "auditEvents": [],
                "idempotencyKey": "idem-retry-1",
            }
        )

        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={
                    "intake_id": "IN-RETRY-1",
                    "url": "https://www.synthetic.example/detail-77120345.html",
                },
            ),
            correlation_id="corr-retry",
        )
        claimed = bundle.job_queue.claim_next(worker_id="worker-1")

        from modules.external_data.application.assisted_intake import SourcePolicyDecision

        policy = SourcePolicyDecision(
            policy="APPROVED_RETRIEVAL",
            source_id="src-1",
            policy_label="Approved",
            policy_reason="Ok",
            quarantines=False,
            source_name="src-1",
            may_retrieve=True,
        )

        call_count = 0
        from modules.external_data.application.assisted_intake import (
            RetrievalFailure,
            RetrievalResult,
        )

        captured_str = datetime.now(UTC).isoformat()
        dummy_retrieval_ok = RetrievalResult(
            snapshot_id="snap-1",
            captured_at=captured_str,
            raw={"html": "<html></html>"},
            failure=None,
        )
        failure_obj = RetrievalFailure(
            code="TEMP_ERROR", summary="Temp error", next_action="retry", retryable=True
        )
        dummy_retrieval_fail = RetrievalResult(
            snapshot_id="", captured_at=captured_str, raw={}, failure=failure_obj
        )

        def mock_retrieve(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return dummy_retrieval_fail
            return dummy_retrieval_ok

        with (
            patch(
                "modules.external_data.application.assisted_intake.normalize_url",
                return_value="https://www.synthetic.example/detail-77120345.html",
            ),
            patch(
                "modules.external_data.application.assisted_intake.resolve_source_policy",
                return_value=policy,
            ),
            patch(
                "modules.external_data.application.assisted_intake.retrieve",
                side_effect=mock_retrieve,
            ),
            patch("time.sleep"),
        ):
            from modules.external_data.application.assisted_intake import MatchResult

            dummy_fields = {
                "title": {"value": "Test", "correctedValue": None},
                "rent": {"value": 1000.0, "correctedValue": None},
                "areaPing": {"value": 10.0, "correctedValue": None},
                "floor": {"value": "3F", "correctedValue": None},
                "description": {"value": "Hello", "correctedValue": None},
            }
            dummy_match = MatchResult(
                outcome="NEW", confidence=1.0, target_listing_id=None, signals=(), summary=""
            )

            with (
                patch(
                    "modules.external_data.application.assisted_intake.parse_snapshot",
                    return_value=dummy_fields,
                ),
                patch(
                    "modules.external_data.application.assisted_intake.match_listing",
                    return_value=dummy_match,
                ),
            ):
                handle_assisted_listing_intake(claimed, bundle)

        # Verify retrieve was called 3 times (3 in RETRIEVING stage (2 fails, 1 success))
        assert call_count == 3

    finally:
        bundle.engine.close()


def test_stage_hard_timeout_interruption(db_path) -> None:
    from unittest.mock import patch

    bundle = _durable_bundle(db_path)
    try:
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={
                    "intake_id": "IN-TIMEOUT-1",
                    "url": "https://www.synthetic.example/detail-77120345.html",
                },
            ),
            correlation_id="corr-timeout",
        )
        claimed = bundle.job_queue.claim_next(worker_id="worker-1")

        with patch("threading.Thread.join"), patch("threading.Thread.is_alive", return_value=True):
            with pytest.raises(TimeoutError, match="hard timeout exceeded: hung stage interrupted"):
                handle_assisted_listing_intake(claimed, bundle)
    finally:
        bundle.engine.close()


def test_poison_dlq_metrics_and_alerts(db_path) -> None:
    from shared.jobs.queue import NonRetryableJobError

    bundle = _durable_bundle(db_path)
    try:
        job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type="assisted-listing-intake",
                payload={
                    "intake_id": "IN-METRICS-1",
                    "url": "https://www.synthetic.example/detail-77120345.html",
                },
            ),
            correlation_id="corr-metrics",
        )
        claimed = bundle.job_queue.claim_next(worker_id="worker-1")

        # Set CHECKING_IDENTITY stage attempt to 3 (which equals max attempts)
        payload = dict(claimed.payload)
        payload["stage_attempts"] = {"CHECKING_IDENTITY": 3}
        bundle.job_queue.update_status(
            claimed.job_id,
            JobStatus.RUNNING,
            payload=payload,
            expected_version=claimed.version,
            fence_token=claimed.fence_token,
        )

        claimed_updated = bundle.job_queue.get(claimed.job_id)

        # Verify handle_assisted_listing_intake raises NonRetryableJobError
        with pytest.raises(NonRetryableJobError):
            handle_assisted_listing_intake(claimed_updated, bundle)

        # Verify that outbox has a job.dead_lettered event
        events = bundle.outbox_repository.get_unpublished_events()
        assert len(events) >= 1
        dlq_event = [e for e in events if e.event_type == "job.dead_lettered"][0]
        assert dlq_event.payload["job_id"] == job.job_id
        assert dlq_event.payload["checkpoint"] == "CHECKING_IDENTITY"
        assert dlq_event.payload["error_code"] == "MAX_ATTEMPTS_EXCEEDED"

        # Verify metrics registry updated
        from shared.observability import default_registry

        snapshot = default_registry().snapshot()
        assert "dlq_message_count" in snapshot

    finally:
        bundle.engine.close()
