"""Counterfactual acceptance test suites for durable batch PARTIAL jobs and scoped retry.

Authority: ODP-FR-SHARED-001, implementation-handoff.md §4 (Tests 1-5), and
partial-retry-contract-draft.json.

Covers:
1. State transition accuracy, itemized receipt assertion (8 success, 1 permanent failure, 1 transient failure -> PARTIAL).
2. Scoped retry (FAILED_ONLY) with zero duplication on succeeded/permanent items, exact 1 invocation on transient items, and full convergence to SUCCEEDED.
3. Orthogonal separation between delivery state and business outcome (RETRYING -> PARTIAL clears delivery_state in DB and API).
4. Restart re-readability with 100% fidelity and mid/pre-execution cancellation.
5. Replay fencing: duplicate delivery, duplicate enqueue, late-arriving stale results, cancelled item fencing, and post-restart aggregate determinism.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.handlers import (
    BATCH_LISTING_INTAKE_JOB_TYPE,
    build_default_registry,
)
from apps.worker.oday_worker.main import ODayWorker
from shared.infrastructure.persistence.factory import _durable_bundle, _memory_bundle
from shared.infrastructure.persistence.job_receipts import (
    DurableJobReceipt,
    ItemError,
    ItemReceipt,
    ItemStatus,
    TenantScopedJobReceiptStore,
    apply_item_result,
    derive_batch_status_and_summary,
)
from shared.jobs.queue import (
    JobDeliveryState,
    JobRequest,
    JobStatus,
)
from shared.jobs.registry import JobRegistry


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "test_durable_partial_batch.sqlite3")


def _sample_10_items_payload(
    *,
    transient_count: int = 1,
    permanent_count: int = 1,
    success_count: int = 8,
) -> list[dict[str, Any]]:
    """Build a deterministic set of 10 items."""
    items: list[dict[str, Any]] = []
    # Success items
    for i in range(1, success_count + 1):
        items.append(
            {
                "item_id": f"row-{i:03d}",
                "address_raw": f"台北市信義區松仁路{i}號",
                "idempotency_key": f"row-idemp-{i:02d}",
            }
        )
    # Permanent error items (e.g. missing mandatory address)
    for i in range(1, permanent_count + 1):
        idx = success_count + i
        items.append(
            {
                "item_id": f"row-{idx:03d}",
                "address_raw": "",  # missing address -> permanent failure
                "idempotency_key": f"row-idemp-{idx:02d}",
            }
        )
    # Transient error items (e.g. upstream timeout)
    for i in range(1, transient_count + 1):
        idx = success_count + permanent_count + i
        items.append(
            {
                "item_id": f"row-{idx:03d}",
                "address_raw": f"台北市大安區忠孝東路四段{idx}號",
                "simulate_timeout": True,
                "idempotency_key": f"row-idemp-{idx:02d}",
            }
        )
    return items


def _auth_headers(tenant_id: str, role: str = "expansion_user", subject: str = "exp-mgr") -> dict[str, str]:
    return {
        "x-subject-id": subject,
        "x-roles": role,
        "x-tenant-id": tenant_id,
    }


# ==============================================================================
# TEST 1: State Transition & Itemized Receipt Assertion
# ==============================================================================
def test_1_state_transition_and_itemized_receipt(db_path: str) -> None:
    """Test 1: 10 items (8 success, 1 permanent failure, 1 transient failure) -> JobStatus.PARTIAL."""
    from unittest.mock import patch

    from apps.worker.oday_worker.handlers import (
        _default_batch_listing_item_executor as orig_executor,
    )
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.operator_network_listings import (
        DurableAssistedIntakeRepository,
    )

    bundle = _durable_bundle(db_path)
    try:
        tenant_id = "tenant-tw-01"
        items_payload = _sample_10_items_payload(
            success_count=8, permanent_count=1, transient_count=1
        )
        assert len(items_payload) == 10

        job_request = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": tenant_id,
                "items": items_payload,
            },
            idempotency_key="batch-test-1-idemp",
        )
        record, created = bundle.job_queue.enqueue(job_request, correlation_id="corr-test-1")
        assert created is True
        assert record.status == JobStatus.QUEUED

        def test_1_executor(item, tenant, p):
            if item.get("simulate_timeout") or item.get("item_id") == "row-010":
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Geocoding service timed out after 3500ms",
                    retryable=True,
                    details={"endpoint": "geocode.tgos.gov.tw", "timeout_ms": 3500},
                )
            return orig_executor(item, tenant, p)

        # Run worker once with test double for upstream timeout on item 10
        worker = ODayWorker(
            persistence=bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )
        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=test_1_executor,
        ):
            executed = worker.run_once()
        assert executed is True

        # Read back job record from durable persistence
        finished_job = bundle.job_queue.get(record.job_id)
        assert finished_job is not None

        # Assert status is PARTIAL, strictly not SUCCEEDED and not FAILED
        assert finished_job.status == JobStatus.PARTIAL
        assert finished_job.status != JobStatus.SUCCEEDED
        assert finished_job.status != JobStatus.FAILED
        assert finished_job.delivery_state is None

        receipt_dict = finished_job.payload.get("receipt")
        assert receipt_dict is not None
        receipt = DurableJobReceipt.from_dict(receipt_dict)

        # Assert summary
        summary = receipt.summary
        assert summary.total_count == 10
        assert summary.succeeded_count == 8
        assert summary.failed_count == 2
        assert summary.cancelled_count == 0
        assert summary.pending_count == 0

        # Assert items
        items = receipt.items
        assert len(items) == 10

        # First 8 items are SUCCEEDED with attempt == 1, result_ref != None, error == None
        for it in items[:8]:
            assert it.item_status == ItemStatus.SUCCEEDED.value
            assert it.attempt == 1
            assert it.result_ref is not None
            assert it.error is None
            assert it.last_attempt_at is not None

        # Row 9: permanent failure (MISSING_MANDATORY_ADDRESS, retryable: False)
        perm_item = items[8]
        assert perm_item.item_id == "row-009"
        assert perm_item.item_status == ItemStatus.FAILED.value
        assert perm_item.attempt == 1
        assert perm_item.result_ref is None
        assert perm_item.error is not None
        assert perm_item.error.code == "MISSING_MANDATORY_ADDRESS"
        assert perm_item.error.retryable is False

        # Row 10: transient failure (GEOCODING_UPSTREAM_TIMEOUT, retryable: True)
        trans_item = items[9]
        assert trans_item.item_id == "row-010"
        assert trans_item.item_status == ItemStatus.FAILED.value
        assert trans_item.attempt == 1
        assert trans_item.result_ref is None
        assert trans_item.error is not None
        assert trans_item.error.code == "GEOCODING_UPSTREAM_TIMEOUT"
        assert trans_item.error.retryable is True

        # Assert real business path persistence in DurableAssistedIntakeRepository
        repo = DurableAssistedIntakeRepository(SqliteDocumentStore(bundle.engine))
        persisted_intakes = repo.list_intakes()
        assert len(persisted_intakes) == 8
        persisted_ids = {it["id"] for it in persisted_intakes}
        expected_ids = {it.result_ref for it in items[:8]}
        assert persisted_ids == expected_ids
    finally:
        bundle.engine.close()

    # Reopened bundle check for full re-readability of business entities
    reopened = _durable_bundle(db_path)
    try:
        repo2 = DurableAssistedIntakeRepository(SqliteDocumentStore(reopened.engine))
        assert len(repo2.list_intakes()) == 8
    finally:
        reopened.engine.close()


# ==============================================================================
# TEST 2: Scoped Retry & Zero Duplication Assertion
# ==============================================================================
def test_2_scoped_retry_and_zero_duplication(db_path: str) -> None:
    """Test 2: FAILED_ONLY scoped retry skips succeeded & permanent errors, retries transient error only."""
    from unittest.mock import patch

    bundle = _durable_bundle(db_path)
    try:
        tenant_id = "tenant-tw-01"
        items_payload = _sample_10_items_payload(
            success_count=8, permanent_count=1, transient_count=1
        )

        # Track execution per item with spy
        invocation_counts: dict[str, int] = {f"row-{i:03d}": 0 for i in range(1, 11)}

        def item_spy_executor(
            item: dict[str, Any], tenant: str, p: Any
        ) -> tuple[str | None, ItemError | None]:
            iid = item["item_id"]
            invocation_counts[iid] += 1
            # On second attempt for row-010, simulate recovery
            if iid == "row-010" and invocation_counts[iid] > 1:
                return f"intake-recovered-{iid}", None
            if item.get("simulate_timeout"):
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Geocoding service timed out after 3500ms",
                    retryable=True,
                )
            if not item.get("address_raw"):
                return None, ItemError(
                    code="MISSING_MANDATORY_ADDRESS",
                    message="Street address is missing",
                    retryable=False,
                )
            return f"intake-ok-{iid}", None

        job_request = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": tenant_id,
                "items": items_payload,
            },
            idempotency_key="batch-test-2-idemp",
        )
        record, _ = bundle.job_queue.enqueue(job_request, correlation_id="corr-test-2")

        worker = ODayWorker(
            persistence=bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )

        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=item_spy_executor,
        ):
            assert worker.run_once() is True

            # Initial check: all 10 items called exactly once
            for i in range(1, 11):
                assert invocation_counts[f"row-{i:03d}"] == 1

            # Now trigger scoped retry via API endpoint with proper auth headers
            app = create_app(job_queue=bundle.job_queue, audit_log=bundle.audit_log, persistence=bundle)
            client = TestClient(app)

            retry_resp = client.post(
                f"/platform/jobs/{record.job_id}/retry",
                json={"retry_scope": "FAILED_ONLY"},
                headers=_auth_headers(tenant_id),
            )
            assert retry_resp.status_code == 202
            retry_body = retry_resp.json()
            assert retry_body["job_id"] == record.job_id
            assert retry_body["status"] == "queued"
            assert retry_body["retry_scope"] == "FAILED_ONLY"
            assert retry_body["retried_items_count"] == 1  # only the retryable item

            # Run worker to process retry
            assert worker.run_once() is True

        # Read back job record
        retried_job = bundle.job_queue.get(record.job_id)
        assert retried_job is not None
        assert retried_job.status == JobStatus.PARTIAL
        assert retried_job.delivery_state is None

        # Verify Spy Invocation Counts:
        # Succeeded items 1-8: 0 additional calls (count remains 1)
        for i in range(1, 9):
            assert invocation_counts[f"row-{i:03d}"] == 1, (
                f"Row {i} was erroneously invoked during retry"
            )

        # Permanent failure row-009: 0 additional calls (count remains 1, attempt remains 1)
        assert invocation_counts["row-009"] == 1
        perm_receipt = next(
            it for it in retried_job.payload["receipt"]["items"] if it["item_id"] == "row-009"
        )
        assert perm_receipt["attempt"] == 1
        assert perm_receipt["item_status"] == ItemStatus.FAILED.value

        # Transient failure row-010: exactly 1 additional call (count becomes 2, attempt becomes 2, status becomes SUCCEEDED)
        assert invocation_counts["row-010"] == 2
        trans_receipt = next(
            it for it in retried_job.payload["receipt"]["items"] if it["item_id"] == "row-010"
        )
        assert trans_receipt["attempt"] == 2
        assert trans_receipt["item_status"] == ItemStatus.SUCCEEDED.value
        assert trans_receipt["result_ref"] == "intake-recovered-row-010"

        # Summary assertions post-retry
        summary = retried_job.payload["receipt"]["summary"]
        assert summary["total_count"] == 10
        assert summary["succeeded_count"] == 9
        assert summary["failed_count"] == 1
        assert summary["cancelled_count"] == 0
        assert summary["pending_count"] == 0
    finally:
        bundle.engine.close()


def test_2_full_convergence_subtest(db_path: str) -> None:
    """Subtest 2: 8 success + 2 transient errors on retry converge to JobStatus.SUCCEEDED."""
    from unittest.mock import patch

    bundle = _durable_bundle(db_path)
    try:
        tenant_id = "tenant-tw-01"
        items_payload = _sample_10_items_payload(
            success_count=8, permanent_count=0, transient_count=2
        )

        invocation_counts: dict[str, int] = {f"row-{i:03d}": 0 for i in range(1, 11)}

        def converging_executor(
            item: dict[str, Any], tenant: str, p: Any
        ) -> tuple[str | None, ItemError | None]:
            iid = item["item_id"]
            invocation_counts[iid] += 1
            if invocation_counts[iid] > 1:
                return f"intake-converged-{iid}", None
            if item.get("simulate_timeout"):
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Geocoding timeout",
                    retryable=True,
                )
            return f"intake-ok-{iid}", None

        job_request = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": tenant_id,
                "items": items_payload,
            },
            idempotency_key="batch-test-2-converge-idemp",
        )
        record, _ = bundle.job_queue.enqueue(job_request, correlation_id="corr-converge")

        worker = ODayWorker(
            persistence=bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )

        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=converging_executor,
        ):
            assert worker.run_once() is True

            # First run result: PARTIAL (8 ok, 2 timeout)
            job1 = bundle.job_queue.get(record.job_id)
            assert job1.status == JobStatus.PARTIAL
            assert job1.payload["receipt"]["summary"]["succeeded_count"] == 8
            assert job1.payload["receipt"]["summary"]["failed_count"] == 2

            # Retry FAILED_ONLY with proper auth headers
            app = create_app(job_queue=bundle.job_queue, audit_log=bundle.audit_log, persistence=bundle)
            client = TestClient(app)
            retry_resp = client.post(
                f"/platform/jobs/{record.job_id}/retry",
                json={"retry_scope": "FAILED_ONLY"},
                headers=_auth_headers(tenant_id),
            )
            assert retry_resp.status_code == 202
            assert retry_resp.json()["retried_items_count"] == 2

            # Worker processes retry
            assert worker.run_once() is True

        # Post-retry result: Automatically converged to JobStatus.SUCCEEDED
        job2 = bundle.job_queue.get(record.job_id)
        assert job2.status == JobStatus.SUCCEEDED
        assert job2.delivery_state is None
        summary = job2.payload["receipt"]["summary"]
        assert summary["total_count"] == 10
        assert summary["succeeded_count"] == 10
        assert summary["failed_count"] == 0
        assert summary["cancelled_count"] == 0
        assert summary["pending_count"] == 0

        # Invocations check: items 1-8 called 1 time, items 9-10 called 2 times
        for i in range(1, 9):
            assert invocation_counts[f"row-{i:03d}"] == 1
        assert invocation_counts["row-009"] == 2
        assert invocation_counts["row-010"] == 2
    finally:
        bundle.engine.close()


# ==============================================================================
# TEST 3: Delivery State & Business Outcome Orthogonality
# ==============================================================================
class _TransientProbeError(RuntimeError):
    pass


def test_3_orthogonality_and_delivery_state_clear(db_path: str) -> None:
    """Test 3: Orthogonality & RETRYING -> PARTIAL persistence clearing readback."""
    # Run for both Durable bundle and In-memory bundle to assert parity
    for is_durable in (True, False):
        bundle = _durable_bundle(db_path) if is_durable else _memory_bundle()
        try:
            # 1. Drive job into RETRYING via retryable exception
            probe_key = f"ortho-probe-{is_durable}"
            registry = JobRegistry()
            registry.register(
                "probe-job",
                lambda job, p: (_ for _ in ()).throw(
                    _TransientProbeError("Transient network timeout")
                ),
            )
            reg_job, _ = bundle.job_queue.enqueue(
                JobRequest(
                    job_type="probe-job",
                    payload={"tenant_id": "tenant-test"},
                    idempotency_key=probe_key,
                ),
                correlation_id="corr-probe",
            )
            worker = ODayWorker(
                persistence=bundle,
                registry=registry,
                heartbeat_interval_seconds=60.0,
            )
            assert worker.run_once() is True

            # Assert queue level has RETRYING and status is QUEUED
            retrying_job = bundle.job_queue.get(reg_job.job_id)
            assert retrying_job is not None
            assert retrying_job.status == JobStatus.QUEUED
            assert retrying_job.delivery_state == JobDeliveryState.RETRYING

            if is_durable:
                row = bundle.engine.query_one(
                    "SELECT status, delivery_state FROM durable_jobs WHERE job_id = ?",
                    (reg_job.job_id,),
                )
                assert row["status"] == JobStatus.QUEUED.value
                assert row["delivery_state"] == JobDeliveryState.RETRYING.value

            # 2. Complete job as JobStatus.PARTIAL with delivery_state=None
            bundle.job_queue.update_status(
                reg_job.job_id,
                JobStatus.PARTIAL,
                payload={"tenant_id": "tenant-test", "receipt": {"status": "PARTIAL"}},
                delivery_state=None,
            )

            # 3. Read directly from durable persistence / queue
            partial_job = bundle.job_queue.get(reg_job.job_id)
            assert partial_job is not None
            assert partial_job.status == JobStatus.PARTIAL
            assert partial_job.delivery_state is None

            if is_durable:
                row = bundle.engine.query_one(
                    "SELECT status, delivery_state FROM durable_jobs WHERE job_id = ?",
                    (reg_job.job_id,),
                )
                assert row["status"] == JobStatus.PARTIAL.value
                assert row["delivery_state"] is None

            # 4. Read from API GET /platform/jobs/{job_id}
            app = create_app(job_queue=bundle.job_queue, audit_log=bundle.audit_log, persistence=bundle)
            client = TestClient(app)
            api_resp = client.get(
                f"/platform/jobs/{reg_job.job_id}",
                headers=_auth_headers("tenant-test"),
            )
            assert api_resp.status_code == 200
            api_job = api_resp.json()
            assert api_job["status"] == "partial"
            assert api_job["delivery_state"] is None

            # 5. Negative assertion: FAILED + DEAD_LETTER is preserved
            bundle.job_queue.update_status(
                reg_job.job_id,
                JobStatus.FAILED,
                delivery_state=JobDeliveryState.DEAD_LETTER,
            )
            failed_job = bundle.job_queue.get(reg_job.job_id)
            assert failed_job.status == JobStatus.FAILED
            assert failed_job.delivery_state == JobDeliveryState.DEAD_LETTER

            if is_durable:
                row = bundle.engine.query_one(
                    "SELECT status, delivery_state FROM durable_jobs WHERE job_id = ?",
                    (reg_job.job_id,),
                )
                assert row["status"] == JobStatus.FAILED.value
                assert row["delivery_state"] == JobDeliveryState.DEAD_LETTER.value

        finally:
            if is_durable and bundle.engine:
                bundle.engine.close()


# ==============================================================================
# TEST 4: Restart Re-readability, Mid-batch Interruption, and Cancellation Assertion
# ==============================================================================
def test_4_restart_re_readability_and_cancellation(db_path: str) -> None:
    """Test 4: Worker restart re-readability and mid/pre-execution cancellation."""
    from unittest.mock import patch

    from apps.worker.oday_worker.handlers import (
        _default_batch_listing_item_executor as orig_executor,
    )

    bundle = _durable_bundle(db_path)
    try:
        tenant_id = "tenant-tw-01"

        # 1. Execute batch job to PARTIAL
        items_payload = _sample_10_items_payload(
            success_count=8, permanent_count=1, transient_count=1
        )
        job_req = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={"tenant_id": tenant_id, "items": items_payload},
            idempotency_key="restart-test-idemp",
        )
        record, _ = bundle.job_queue.enqueue(job_req, correlation_id="corr-restart")

        def test_4_executor(item, tenant, p):
            if item.get("simulate_timeout") or item.get("item_id") == "row-010":
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Geocoding service timed out after 3500ms",
                    retryable=True,
                )
            return orig_executor(item, tenant, p)

        worker = ODayWorker(
            persistence=bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )
        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=test_4_executor,
        ):
            assert worker.run_once() is True
    finally:
        bundle.engine.close()

    # Simulate worker crash and process restart over the same SQLite file
    reopened_bundle = _durable_bundle(db_path)
    try:
        reopened_job = reopened_bundle.job_queue.get(record.job_id)
        assert reopened_job is not None
        assert reopened_job.status == JobStatus.PARTIAL
        assert reopened_job.delivery_state is None

        receipt_store = TenantScopedJobReceiptStore(
            queue=reopened_bundle.job_queue, service="batch-listing-intake"
        )
        receipt_dict = receipt_store.get_durable_receipt(tenant_id, record.job_id)
        assert receipt_dict is not None
        receipt = DurableJobReceipt.from_dict(receipt_dict)

        # Full fidelity assertions after restart
        assert receipt.job_id == record.job_id
        assert receipt.summary.total_count == 10
        assert receipt.summary.succeeded_count == 8
        assert receipt.summary.failed_count == 2
        assert len(receipt.items) == 10
        assert receipt.items[0].attempt == 1
        assert receipt.items[0].item_status == ItemStatus.SUCCEEDED.value
        assert receipt.items[8].error.code == "MISSING_MANDATORY_ADDRESS"
        assert receipt.items[9].error.code == "GEOCODING_UPSTREAM_TIMEOUT"

        # Also query via API with auth headers
        app = create_app(job_queue=reopened_bundle.job_queue, audit_log=reopened_bundle.audit_log, persistence=reopened_bundle)
        client = TestClient(app)
        api_receipt = client.get(
            f"/platform/jobs/{record.job_id}/receipt",
            headers=_auth_headers(tenant_id),
        )
        assert api_receipt.status_code == 200
        assert api_receipt.json()["status"] == "PARTIAL"
        assert len(api_receipt.json()["items"]) == 10

        # 2. Cancellation Test: Item 1 SUCCEEDED, Item 2 CANCELLED mid-execution (attempt=1), Item 3 CANCELLED before execution (attempt=0)
        cancel_items = [
            {
                "item_id": "row-001",
                "address_raw": "台北市松山區民生東路三段1號",
            },
            {
                "item_id": "row-002",
                "address_raw": "台北市松山區民生東路三段2號",
                "cancelled_mid_execution": True,
            },
            {
                "item_id": "row-003",
                "address_raw": "台北市松山區民生東路三段3號",
                "cancelled_before_execution": True,
            },
        ]
        cancel_req = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={"tenant_id": tenant_id, "items": cancel_items},
            idempotency_key="cancel-test-idemp",
        )
        c_record, _ = reopened_bundle.job_queue.enqueue(cancel_req, correlation_id="corr-cancel")
        worker2 = ODayWorker(
            persistence=reopened_bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )
        assert worker2.run_once() is True

        c_job = reopened_bundle.job_queue.get(c_record.job_id)
        assert c_job is not None
        assert c_job.status == JobStatus.CANCELLED
        assert c_job.delivery_state is None

        c_receipt = DurableJobReceipt.from_dict(c_job.payload["receipt"])
        assert c_receipt.summary.total_count == 3
        assert c_receipt.summary.succeeded_count == 1
        assert c_receipt.summary.failed_count == 0
        assert c_receipt.summary.cancelled_count == 2

        # Item 1: SUCCEEDED, attempt=1
        assert c_receipt.items[0].item_status == ItemStatus.SUCCEEDED.value
        assert c_receipt.items[0].attempt == 1

        # Item 2: CANCELLED mid-execution, attempt=1
        assert c_receipt.items[1].item_status == ItemStatus.CANCELLED.value
        assert c_receipt.items[1].attempt == 1
        assert c_receipt.items[1].error.code == "CANCELLED_MID_EXECUTION"

        # Item 3: CANCELLED before execution, attempt=0, last_attempt_at=None
        assert c_receipt.items[2].item_status == ItemStatus.CANCELLED.value
        assert c_receipt.items[2].attempt == 0
        assert c_receipt.items[2].error.code == "CANCELLED_BEFORE_EXECUTION"
        assert c_receipt.items[2].last_attempt_at is None
    finally:
        reopened_bundle.engine.close()


def test_4_mid_batch_interruption_and_resumption(db_path: str) -> None:
    """Mid-batch process crash checkpoints progress; restart does not redo completed items."""
    from unittest.mock import patch

    bundle = _durable_bundle(db_path)
    tenant_id = "tenant-tw-01"
    crash_job, _ = bundle.job_queue.enqueue(
        JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": tenant_id,
                "items": [
                    {"item_id": "row-a", "address_raw": "台北市大安區新生南路一段1號"},
                    {"item_id": "row-b", "address_raw": "台北市大安區新生南路一段2號"},
                ],
            },
            idempotency_key="idemp-crash-resume",
        ),
        correlation_id="corr-crash",
    )

    invocations: list[str] = []

    def interrupting_executor(item, tenant, p):
        invocations.append(item["item_id"])
        if item["item_id"] == "row-b":
            raise KeyboardInterrupt("Simulated process exit on second item")
        return "intake-a-ok", None

    worker = ODayWorker(persistence=bundle, registry=build_default_registry())
    with patch(
        "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
        side_effect=interrupting_executor,
    ):
        try:
            worker.run_once()
        except KeyboardInterrupt:
            pass

    # Durable checkpoint check: row-a was completed and saved in job payload
    persisted = bundle.job_queue.get(crash_job.job_id)
    assert persisted is not None
    saved_receipt = persisted.payload.get("receipt")
    assert saved_receipt is not None
    assert saved_receipt["items"][0]["item_id"] == "row-a"
    assert saved_receipt["items"][0]["item_status"] == ItemStatus.SUCCEEDED.value
    bundle.engine.close()

    # Restart worker on fresh bundle
    reopened = _durable_bundle(db_path)
    try:
        resume_invocations: list[str] = []

        def resumed_executor(item, tenant, p):
            resume_invocations.append(item["item_id"])
            return "intake-b-ok", None

        worker2 = ODayWorker(persistence=reopened, registry=build_default_registry())
        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=resumed_executor,
        ):
            # Lease expired or reclaimed
            resumed_job = reopened.job_queue.get(crash_job.job_id)
            reopened.job_queue.update_status(
                resumed_job.job_id,
                JobStatus.QUEUED,
                payload=resumed_job.payload,
                delivery_state=None,
            )
            assert worker2.run_once() is True

        # Assert row-a was NOT re-invoked
        assert resume_invocations == ["row-b"]
        final_job = reopened.job_queue.get(crash_job.job_id)
        assert final_job.status == JobStatus.SUCCEEDED
        assert final_job.payload["receipt"]["summary"]["succeeded_count"] == 2
    finally:
        reopened.engine.close()


def test_4_live_operator_cancellation_during_execution(db_path: str) -> None:
    """When operator cancels job in DB, unstarted items are marked CANCELLED with attempt=0."""
    from unittest.mock import patch

    bundle = _durable_bundle(db_path)
    try:
        tenant_id = "tenant-tw-01"
        cancel_job, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": tenant_id,
                    "items": [
                        {"item_id": "row-1", "address_raw": "台北市大安區新生南路一段1號"},
                        {"item_id": "row-2", "address_raw": "台北市大安區新生南路一段2號"},
                        {"item_id": "row-3", "address_raw": "台北市大安區新生南路一段3號"},
                    ],
                },
                idempotency_key="idemp-live-cancel",
            ),
            correlation_id="corr-live-cancel",
        )

        executed_items: list[str] = []

        def cancelling_executor(item, tenant, p):
            executed_items.append(item["item_id"])
            if item["item_id"] == "row-1":
                # Operator cancels job concurrently in queue
                bundle.job_queue.update_status(
                    cancel_job.job_id,
                    JobStatus.CANCELLED,
                )
            return f"intake-{item['item_id']}", None

        worker = ODayWorker(persistence=bundle, registry=build_default_registry())
        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=cancelling_executor,
        ):
            worker.run_once()

        assert executed_items == ["row-1"]
        persisted = bundle.job_queue.get(cancel_job.job_id)
        assert persisted.status == JobStatus.CANCELLED
        receipt = persisted.payload["receipt"]
        assert receipt["items"][0]["item_status"] == ItemStatus.SUCCEEDED.value
        assert receipt["items"][0]["attempt"] == 1
        assert receipt["items"][1]["item_status"] == ItemStatus.CANCELLED.value
        assert receipt["items"][1]["attempt"] == 0
        assert receipt["items"][2]["item_status"] == ItemStatus.CANCELLED.value
        assert receipt["items"][2]["attempt"] == 0
    finally:
        bundle.engine.close()


# ==============================================================================
# TEST 5: Duplicate Delivery & Out-of-Order Result Idempotency
# ==============================================================================
def test_5_duplicate_delivery_and_out_of_order(db_path: str) -> None:
    """Test 5: Fencing triple (job_id, item_id, attempt), duplicate messages, and stale result discard."""
    bundle = _durable_bundle(db_path)
    try:
        tenant_id = "tenant-tw-01"

        # 5.2: Duplicate enqueue with same idempotency_key
        initial_items = [
            {"item_id": "item-X", "address_raw": "台北市大安區新生南路一段1號"},
            {"item_id": "item-Y", "address_raw": "台北市大安區新生南路一段2號"},
            {"item_id": "item-Z", "address_raw": "台北市大安區新生南路一段3號", "cancelled_before_execution": True},
        ]
        req1 = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={"tenant_id": tenant_id, "items": initial_items},
            idempotency_key="idemp-batch-replay-01",
        )
        rec1, created1 = bundle.job_queue.enqueue(req1, correlation_id="corr-replay-1")
        assert created1 is True

        req2 = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={"tenant_id": tenant_id, "items": initial_items},
            idempotency_key="idemp-batch-replay-01",
        )
        rec2, created2 = bundle.job_queue.enqueue(req2, correlation_id="corr-replay-2")
        assert created2 is False
        assert rec2.job_id == rec1.job_id

        # 5.1 & 5.3 & 5.4: Message level replay & stale result fencing
        # Initialize items
        items: list[ItemReceipt] = [
            ItemReceipt(
                item_id="item-X",
                item_status=ItemStatus.SUCCEEDED.value,
                attempt=2,
                result_ref="intake-X2",
                last_attempt_at="2026-09-08T16:15:00Z",
            ),
            ItemReceipt(
                item_id="item-Y",
                item_status=ItemStatus.SUCCEEDED.value,
                attempt=2,
                result_ref="intake-Y2",
                last_attempt_at="2026-09-08T16:15:00Z",
            ),
            ItemReceipt(
                item_id="item-Z",
                item_status=ItemStatus.CANCELLED.value,
                attempt=0,
                error=ItemError(
                    code="CANCELLED_BEFORE_EXECUTION",
                    message="Job cancelled before execution",
                    retryable=True,
                ),
                last_attempt_at=None,
            ),
        ]

        # 5.1: Duplicate delivery of same attempt (item-X, attempt=2)
        duplicate_msg = ItemReceipt(
            item_id="item-X",
            item_status=ItemStatus.SUCCEEDED.value,
            attempt=2,
            result_ref="intake-X2",
            last_attempt_at="2026-09-08T16:20:00Z",  # later timestamp
        )
        items, applied = apply_item_result(items, duplicate_msg)
        assert applied is False
        # attempt unchanged, timestamp unchanged
        item_x = next(it for it in items if it.item_id == "item-X")
        assert item_x.attempt == 2
        assert item_x.last_attempt_at == "2026-09-08T16:15:00Z"

        # 5.3: Out-of-order stale result (attempt=1 failure arriving for item-Y when attempt=2 already SUCCEEDED)
        stale_msg = ItemReceipt(
            item_id="item-Y",
            item_status=ItemStatus.FAILED.value,
            attempt=1,
            result_ref=None,
            error=ItemError(
                code="GEOCODING_UPSTREAM_TIMEOUT",
                message="Timeout on attempt 1",
                retryable=True,
            ),
            last_attempt_at="2026-09-08T16:10:00Z",
        )
        items, applied = apply_item_result(items, stale_msg)
        assert applied is False
        item_y = next(it for it in items if it.item_id == "item-Y")
        # Must NOT roll back to FAILED, must NOT null out result_ref, must NOT populate error
        assert item_y.item_status == ItemStatus.SUCCEEDED.value
        assert item_y.result_ref == "intake-Y2"
        assert item_y.error is None
        assert item_y.attempt == 2

        # 5.4: Stale result for cancelled unstarted item-Z (attempt=0)
        stale_cancel_res = ItemReceipt(
            item_id="item-Z",
            item_status=ItemStatus.SUCCEEDED.value,
            attempt=1,
            result_ref="intake-Z1",
        )
        items, applied = apply_item_result(items, stale_cancel_res)
        assert applied is False
        item_z = next(it for it in items if it.item_id == "item-Z")
        assert item_z.item_status == ItemStatus.CANCELLED.value
        assert item_z.attempt == 0

        # 5.5: Pure-function aggregate derivation and consistency
        status_before, summary_before = derive_batch_status_and_summary(items)
        assert status_before == JobStatus.CANCELLED  # item-Z is cancelled
        assert summary_before.total_count == 3
        assert summary_before.succeeded_count == 2
        assert summary_before.failed_count == 0
        assert summary_before.cancelled_count == 1

        # Reordering items does not alter summary or aggregate status
        reversed_items = list(reversed(items))
        status_after, summary_after = derive_batch_status_and_summary(reversed_items)
        assert status_after == status_before
        assert summary_after == summary_before

    finally:
        bundle.engine.close()


# ==============================================================================
# TEST 6: Auth, Tenant Isolation, and Concurrency Guards
# ==============================================================================
def test_6_auth_and_tenant_isolation_guards(db_path: str) -> None:
    """Negative tests for auth requirement, tenant isolation, role RBAC, and invalid retry states."""
    bundle = _durable_bundle(db_path)
    try:
        tenant_a = "tenant-alpha"
        tenant_b = "tenant-beta"
        headers_a = _auth_headers(tenant_a, role="expansion_user", subject="user-a")
        headers_b = _auth_headers(tenant_b, role="expansion_user", subject="user-b")
        headers_unauth_role = _auth_headers(tenant_a, role="auditor", subject="user-auditor")

        app = create_app(job_queue=bundle.job_queue, audit_log=bundle.audit_log, persistence=bundle)
        client = TestClient(app)

        # 1. Enqueue without auth -> 401
        res = client.post(
            "/jobs",
            json={"job_type": BATCH_LISTING_INTAKE_JOB_TYPE, "payload": {"items": [{"item_id": "r1", "address_raw": "Addr 1"}]}},
        )
        assert res.status_code == 401

        # 2. Enqueue with unauthorized role (auditor cannot create listings) -> 403
        res = client.post(
            "/jobs",
            json={"job_type": BATCH_LISTING_INTAKE_JOB_TYPE, "payload": {"items": [{"item_id": "r1", "address_raw": "Addr 1"}]}},
            headers=headers_unauth_role,
        )
        assert res.status_code == 403

        # 3. Enqueue with tenant mismatch -> 403
        res = client.post(
            "/jobs",
            json={"job_type": BATCH_LISTING_INTAKE_JOB_TYPE, "payload": {"tenant_id": tenant_b, "items": [{"item_id": "r1", "address_raw": "Addr 1"}]}},
            headers=headers_a,
        )
        assert res.status_code == 403

        # 4. Enqueue successfully as tenant_a
        res = client.post(
            "/jobs",
            json={"job_type": BATCH_LISTING_INTAKE_JOB_TYPE, "payload": {"tenant_id": tenant_a, "items": [{"item_id": "r1", "address_raw": "Addr 1"}]}},
            headers=headers_a,
        )
        assert res.status_code == 202
        job_id = res.json()["job_id"]

        # 5. Read job without auth -> 401
        assert client.get(f"/jobs/{job_id}").status_code == 401
        assert client.get(f"/platform/jobs/{job_id}").status_code == 401
        assert client.get(f"/platform/jobs/{job_id}/receipt").status_code == 401
        assert client.post(f"/platform/jobs/{job_id}/retry", json={"retry_scope": "FAILED_ONLY"}).status_code == 401

        # 6. Read job from different tenant (tenant_b) -> 404 (isolated)
        assert client.get(f"/jobs/{job_id}", headers=headers_b).status_code == 404
        assert client.get(f"/platform/jobs/{job_id}", headers=headers_b).status_code == 404
        assert client.get(f"/platform/jobs/{job_id}/receipt", headers=headers_b).status_code == 404
        assert client.post(f"/platform/jobs/{job_id}/retry", json={"retry_scope": "FAILED_ONLY"}, headers=headers_b).status_code == 404

        # 7. Retry while job is QUEUED or RUNNING -> 409 Conflict
        res_retry_queued = client.post(f"/platform/jobs/{job_id}/retry", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
        assert res_retry_queued.status_code == 409

        # Lease to RUNNING
        leased = bundle.job_queue.lease(60)
        assert leased is not None
        assert leased.status == JobStatus.RUNNING
        res_retry_running = client.post(f"/platform/jobs/{job_id}/retry", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
        assert res_retry_running.status_code == 409

        # 8. Complete as SUCCEEDED -> Retry rejected with 400
        bundle.job_queue.update_status(job_id, JobStatus.SUCCEEDED)
        res_retry_succeeded = client.post(f"/platform/jobs/{job_id}/retry", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
        assert res_retry_succeeded.status_code == 400

        # 9. Set to PARTIAL with 0 retryable items (e.g. permanent error only) -> Retry rejected with 400
        bundle.job_queue.update_status(
            job_id,
            JobStatus.PARTIAL,
            payload={
                "tenant_id": tenant_a,
                "receipt": {
                    "items": [
                        {
                            "item_id": "r1",
                            "item_status": "FAILED",
                            "attempt": 1,
                            "error": {"code": "PERMANENT_ERROR", "message": "cannot retry", "retryable": False},
                        }
                    ]
                },
            },
        )
        res_retry_zero = client.post(f"/platform/jobs/{job_id}/retry", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
        assert res_retry_zero.status_code == 400
        assert "zero retryable" in res_retry_zero.json()["detail"]["message"].lower()

    finally:
        bundle.engine.close()
