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
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from apps.worker.oday_worker.handlers import (
    BATCH_LISTING_INTAKE_JOB_TYPE,
    _default_batch_listing_item_executor,
    batch_listing_intake_id,
    build_batch_listing_intake_service,
    build_default_registry,
)
from apps.worker.oday_worker.main import ODayWorker
from modules.opsboard.application.network_listings import InMemoryAssistedIntakeRepository
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

        def test_1_executor(item, tenant, p, **ctx):
            if item.get("simulate_timeout") or item.get("item_id") == "row-010":
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Geocoding service timed out after 3500ms",
                    retryable=True,
                    details={"timeout_ms": 3500},
                )
            return orig_executor(item, tenant, p, **ctx)

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
            item: dict[str, Any], tenant: str, p: Any, **ctx: Any
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
                f"/jobs/{record.job_id}/retries",
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
            item: dict[str, Any], tenant: str, p: Any, **ctx: Any
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
                f"/jobs/{record.job_id}/retries",
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

            # 4. Read from API GET /jobs/{job_id}
            app = create_app(job_queue=bundle.job_queue, audit_log=bundle.audit_log, persistence=bundle)
            client = TestClient(app)
            api_resp = client.get(
                f"/jobs/{reg_job.job_id}",
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

        def test_4_executor(item, tenant, p, **ctx):
            if item.get("simulate_timeout") or item.get("item_id") == "row-010":
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Geocoding service timed out after 3500ms",
                    retryable=True,
                )
            return orig_executor(item, tenant, p, **ctx)

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

        # Also query via API with auth headers. The durable receipt rides in the
        # job record, so the versioned job read is the receipt read; there is no
        # second endpoint shadowing the assisted-intake router's own
        # ``/jobs/{job_id}/receipt``.
        app = create_app(job_queue=reopened_bundle.job_queue, audit_log=reopened_bundle.audit_log, persistence=reopened_bundle)
        client = TestClient(app)
        api_job = client.get(
            f"/jobs/{record.job_id}",
            headers=_auth_headers(tenant_id),
        )
        assert api_job.status_code == 200
        api_receipt = api_job.json()["payload"]["receipt"]
        assert api_receipt["status"] == "PARTIAL"
        assert len(api_receipt["items"]) == 10

        # 2. Cancellation settled from the persisted job row, not from a flag in
        # the payload. The batch is first driven into the state a crash really
        # leaves behind -- row-001 finished, row-002's attempt-start checkpoint
        # landed but its result never did, row-003 never ran -- and only then is
        # the operator cancellation recorded. Re-entering the handler must settle
        # that state, keeping attempt 0 for the member that never ran and
        # attempt >= 1 for the one that had already started.
        cancel_items = [
            {"item_id": "row-001", "address_raw": "台北市松山區民生東路三段1號"},
            {"item_id": "row-002", "address_raw": "台北市松山區民生東路三段2號"},
            {"item_id": "row-003", "address_raw": "台北市松山區民生東路三段3號"},
        ]
        cancel_req = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={"tenant_id": tenant_id, "items": cancel_items},
            idempotency_key="cancel-test-idemp",
        )
        c_record, _ = reopened_bundle.job_queue.enqueue(cancel_req, correlation_id="corr-cancel")

        def aborting_executor(item, tenant, p, **ctx):
            if item["item_id"] == "row-002":
                raise KeyboardInterrupt("Simulated process exit while row-002 was in flight")
            return orig_executor(item, tenant, p, **ctx)

        worker2 = ODayWorker(
            persistence=reopened_bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )
        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=aborting_executor,
        ):
            with pytest.raises(KeyboardInterrupt):
                worker2.run_once()

        in_flight = reopened_bundle.job_queue.get(c_record.job_id)
        in_flight_items = in_flight.payload["receipt"]["items"]
        assert in_flight_items[0]["item_status"] == ItemStatus.SUCCEEDED.value
        assert in_flight_items[1]["item_status"] == ItemStatus.PENDING.value
        assert in_flight_items[1]["attempt"] == 1
        assert in_flight_items[2]["attempt"] == 0

        # The operator cancels the job for real, on the job row.
        reopened_bundle.job_queue.update_status(c_record.job_id, JobStatus.CANCELLED)

        # Worker restart attempts to run but worker.run_once() returns False (cancelled job is not claimed)
        worker_restart = ODayWorker(
            persistence=reopened_bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )
        assert worker_restart.run_once() is False

        c_job = reopened_bundle.job_queue.get(c_record.job_id)
        assert c_job is not None
        assert c_job.status == JobStatus.CANCELLED
        assert c_job.delivery_state is None

        c_receipt = DurableJobReceipt.from_dict(c_job.payload["receipt"])
        assert c_receipt.summary.total_count == 3
        assert c_receipt.summary.succeeded_count == 1
        assert c_receipt.summary.failed_count == 0
        assert c_receipt.summary.cancelled_count == 2

        # Item 1: finished before the cancellation, so it stays SUCCEEDED
        assert c_receipt.items[0].item_status == ItemStatus.SUCCEEDED.value
        assert c_receipt.items[0].attempt == 1
        assert c_receipt.items[0].result_ref is not None

        # Item 2: its attempt had started, so the cancellation keeps attempt >= 1
        assert c_receipt.items[1].item_status == ItemStatus.CANCELLED.value
        assert c_receipt.items[1].attempt == 1
        assert c_receipt.items[1].error.code == "CANCELLED_MID_EXECUTION"
        assert c_receipt.items[1].last_attempt_at is not None

        # Item 3: never executed, so attempt stays 0 and no attempt is recorded
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

    def interrupting_executor(item, tenant, p, **ctx):
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

        def resumed_executor(item, tenant, p, **ctx):
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

        def cancelling_executor(item, tenant, p, **ctx):
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

        # 5.2: Duplicate enqueue with same idempotency_key before execution
        initial_items = [
            {"item_id": "item-X", "address_raw": "台北市大安區新生南路一段1號"},
            {"item_id": "item-Y", "address_raw": "台北市大安區新生南路一段2號"},
            {"item_id": "item-Z", "address_raw": "台北市大安區新生南路一段3號"},
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

        # Run attempt 1 where X succeeds, Y fails (retryable timeout), Z fails (retryable timeout)
        calls_by_item: dict[str, int] = {}
        original_executor = _default_batch_listing_item_executor

        def executor_pass1(raw_item, *args, **kwargs):
            iid = raw_item.get("item_id")
            calls_by_item[iid] = calls_by_item.get(iid, 0) + 1
            if iid in ("item-Y", "item-Z"):
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Timeout on attempt 1",
                    retryable=True,
                )
            return original_executor(raw_item, *args, **kwargs)

        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=executor_pass1,
        ):
            worker = ODayWorker(
                persistence=bundle,
                registry=build_default_registry(),
                heartbeat_interval_seconds=60.0,
            )
            assert worker.run_once() is True

        job_p1 = bundle.job_queue.get(rec1.job_id)
        assert job_p1.status == JobStatus.PARTIAL
        receipt_p1 = DurableJobReceipt.from_dict(job_p1.payload["receipt"])
        assert receipt_p1.summary.succeeded_count == 1
        assert receipt_p1.summary.failed_count == 2
        assert calls_by_item == {"item-X": 1, "item-Y": 1, "item-Z": 1}

        # 5.1 & Scoped Retry: Retry FAILED_ONLY where Y succeeds on attempt 2, Z fails on attempt 2
        app = create_app(job_queue=bundle.job_queue, audit_log=bundle.audit_log, persistence=bundle)
        client = TestClient(app)
        headers = _auth_headers(tenant_id, role="expansion_user", subject="retry-user")

        def executor_pass2(raw_item, *args, **kwargs):
            iid = raw_item.get("item_id")
            calls_by_item[iid] = calls_by_item.get(iid, 0) + 1
            if iid == "item-Z":
                return None, ItemError(
                    code="GEOCODING_UPSTREAM_TIMEOUT",
                    message="Timeout on attempt 2",
                    retryable=True,
                )
            return original_executor(raw_item, *args, **kwargs)

        # POST /jobs/{job_id}/retries
        resp_retry = client.post(
            f"/api/v1/jobs/{rec1.job_id}/retries",
            headers=headers,
            json={"retry_scope": "FAILED_ONLY", "expected_version": job_p1.version},
        )
        assert resp_retry.status_code == 202

        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=executor_pass2,
        ):
            assert worker.run_once() is True

        job_p2 = bundle.job_queue.get(rec1.job_id)
        assert job_p2.status == JobStatus.PARTIAL
        receipt_p2 = DurableJobReceipt.from_dict(job_p2.payload["receipt"])
        assert receipt_p2.summary.succeeded_count == 2
        assert receipt_p2.summary.failed_count == 1
        # Item-X (succeeded in pass 1) was skipped with 0 calls in pass 2;
        # Item-Y and Item-Z were called exactly once more in pass 2 (total 2 each)
        assert calls_by_item == {"item-X": 1, "item-Y": 2, "item-Z": 2}

        # 5.1 Duplicate delivery of same attempt (item-Y, attempt=2)
        item_y_v2 = next(it for it in receipt_p2.items if it.item_id == "item-Y")
        assert item_y_v2.attempt == 2
        assert item_y_v2.item_status == ItemStatus.SUCCEEDED.value
        assert item_y_v2.result_ref is not None
        y2_last_attempt_at = item_y_v2.last_attempt_at

        duplicate_y2_msg = ItemReceipt(
            item_id="item-Y",
            item_status=ItemStatus.SUCCEEDED.value,
            attempt=2,
            result_ref=item_y_v2.result_ref,
            last_attempt_at="2099-01-01T00:00:00Z",
        )
        current_receipt_items = list(receipt_p2.items)
        items_after_dup, applied_dup = apply_item_result(current_receipt_items, duplicate_y2_msg)
        assert applied_dup is False
        item_y_after_dup = next(it for it in items_after_dup if it.item_id == "item-Y")
        assert item_y_after_dup.attempt == 2
        assert item_y_after_dup.last_attempt_at == y2_last_attempt_at
        assert item_y_after_dup.result_ref == item_y_v2.result_ref

        # 5.2 Duplicate enqueue on completed/partial job does not reset items or summary
        req_dup = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={"tenant_id": tenant_id, "items": initial_items},
            idempotency_key="idemp-batch-replay-01",
        )
        rec_dup, created_dup = bundle.job_queue.enqueue(req_dup, correlation_id="corr-replay-dup")
        assert created_dup is False
        persisted_after_dup_enqueue = bundle.job_queue.get(rec1.job_id)
        assert persisted_after_dup_enqueue.status == JobStatus.PARTIAL
        receipt_dup_enqueue = DurableJobReceipt.from_dict(persisted_after_dup_enqueue.payload["receipt"])
        assert receipt_dup_enqueue.summary.succeeded_count == 2
        assert receipt_dup_enqueue.summary.failed_count == 1

        # 5.3: Out-of-order stale result: item-Y attempt=2 succeeded, then attempt=1 failure arrives
        stale_y1 = ItemReceipt(
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
        items_after_stale, applied_stale = apply_item_result(items_after_dup, stale_y1)
        assert applied_stale is False
        item_y_final = next(it for it in items_after_stale if it.item_id == "item-Y")
        assert item_y_final.item_status == ItemStatus.SUCCEEDED.value
        assert item_y_final.result_ref == item_y_v2.result_ref
        assert item_y_final.error is None
        assert item_y_final.attempt == 2
        # Downstream executor calls for item-Y remained 2 (0 calls for duplicate or stale delivery)
        assert calls_by_item["item-Y"] == 2

        # 5.4: Stale result for cancelled unstarted item (attempt=0)
        req_cancel = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": tenant_id,
                "items": [{"item_id": "item-W", "address_raw": "台北市大安區新生南路一段4號"}],
            },
            idempotency_key="idemp-batch-cancel-unstarted",
        )
        rec_cancel, _ = bundle.job_queue.enqueue(req_cancel, correlation_id="corr-cancel-unstarted")
        bundle.job_queue.update_status(rec_cancel.job_id, JobStatus.CANCELLED)
        job_cancelled = bundle.job_queue.get(rec_cancel.job_id)
        assert job_cancelled.status == JobStatus.CANCELLED
        receipt_cancelled = DurableJobReceipt.from_dict(job_cancelled.payload["receipt"])
        item_w = receipt_cancelled.items[0]
        assert item_w.item_id == "item-W"
        assert item_w.item_status == ItemStatus.CANCELLED.value
        assert item_w.attempt == 0

        stale_w_result = ItemReceipt(
            item_id="item-W",
            item_status=ItemStatus.SUCCEEDED.value,
            attempt=1,
            result_ref="intake-W1",
            last_attempt_at="2026-09-08T16:20:00Z",
        )
        items_w_after, applied_w = apply_item_result(list(receipt_cancelled.items), stale_w_result)
        assert applied_w is False
        item_w_after = items_w_after[0]
        assert item_w_after.item_status == ItemStatus.CANCELLED.value
        assert item_w_after.attempt == 0
        assert item_w_after.result_ref is None

        # 5.5: Post-Restart Aggregate Consistency & Arrival Ordering Parity
        bundle.engine.close()
        reloaded_bundle = _durable_bundle(db_path)
        persisted_job = reloaded_bundle.job_queue.get(rec1.job_id)
        assert persisted_job is not None
        assert persisted_job.status == JobStatus.PARTIAL
        reloaded_receipt = DurableJobReceipt.from_dict(persisted_job.payload["receipt"])
        assert reloaded_receipt.summary.succeeded_count == 2
        assert reloaded_receipt.summary.failed_count == 1
        derived_status, derived_summary = derive_batch_status_and_summary(reloaded_receipt.items)
        assert persisted_job.status == derived_status
        assert reloaded_receipt.summary.to_dict() == derived_summary.to_dict()

        # Arrival ordering permutations: applying messages in different orders yields identical aggregate

        result_messages = [
            ItemReceipt(
                item_id="item-X",
                item_status=ItemStatus.SUCCEEDED.value,
                attempt=1,
                result_ref="intake-X",
                last_attempt_at="2026-09-08T16:01:00Z",
            ),
            ItemReceipt(
                item_id="item-Y",
                item_status=ItemStatus.FAILED.value,
                attempt=1,
                error=ItemError(code="TIMEOUT", message="t1", retryable=True),
                last_attempt_at="2026-09-08T16:02:00Z",
            ),
            ItemReceipt(
                item_id="item-Y",
                item_status=ItemStatus.SUCCEEDED.value,
                attempt=2,
                result_ref="intake-Y",
                last_attempt_at="2026-09-08T16:05:00Z",
            ),
            ItemReceipt(
                item_id="item-Z",
                item_status=ItemStatus.FAILED.value,
                attempt=1,
                error=ItemError(code="TIMEOUT", message="t1", retryable=True),
                last_attempt_at="2026-09-08T16:03:00Z",
            ),
            ItemReceipt(
                item_id="item-Z",
                item_status=ItemStatus.FAILED.value,
                attempt=2,
                error=ItemError(code="TIMEOUT", message="t2", retryable=True),
                last_attempt_at="2026-09-08T16:06:00Z",
            ),
            duplicate_y2_msg,
            stale_y1,
        ]

        base_pending = [
            ItemReceipt(item_id="item-X", item_status=ItemStatus.PENDING.value, attempt=0),
            ItemReceipt(item_id="item-Y", item_status=ItemStatus.PENDING.value, attempt=0),
            ItemReceipt(item_id="item-Z", item_status=ItemStatus.PENDING.value, attempt=0),
        ]

        expected_status, expected_summary = JobStatus.PARTIAL, {
            "total_count": 3,
            "succeeded_count": 2,
            "failed_count": 1,
            "cancelled_count": 0,
            "pending_count": 0,
        }

        # Check multiple arrival orderings (forward, reversed, and sample permutations)
        for order in [
            result_messages,
            list(reversed(result_messages)),
            [result_messages[i] for i in [2, 0, 4, 1, 3, 5, 6]],
            [result_messages[i] for i in [6, 5, 4, 3, 2, 1, 0]],
        ]:
            curr = list(base_pending)
            for msg in order:
                curr, _ = apply_item_result(curr, msg)
            p_status, p_summary = derive_batch_status_and_summary(curr)
            assert p_status == expected_status
            assert p_summary.to_dict() == expected_summary

        reloaded_bundle.engine.close()
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
        assert client.post(f"/jobs/{job_id}/retries", json={"retry_scope": "FAILED_ONLY"}).status_code == 401

        # 6. Read job from different tenant (tenant_b) -> 404 (isolated)
        assert client.get(f"/jobs/{job_id}", headers=headers_b).status_code == 404
        assert client.post(f"/jobs/{job_id}/retries", json={"retry_scope": "FAILED_ONLY"}, headers=headers_b).status_code == 404

        # 7. Retry while job is QUEUED or RUNNING -> 409 Conflict
        res_retry_queued = client.post(f"/jobs/{job_id}/retries", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
        assert res_retry_queued.status_code == 409

        # Lease to RUNNING
        leased = bundle.job_queue.lease(60)
        assert leased is not None
        assert leased.status == JobStatus.RUNNING
        res_retry_running = client.post(f"/jobs/{job_id}/retries", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
        assert res_retry_running.status_code == 409

        # 8. Complete as SUCCEEDED -> Retry rejected with 400
        bundle.job_queue.update_status(job_id, JobStatus.SUCCEEDED)
        res_retry_succeeded = client.post(f"/jobs/{job_id}/retries", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
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
        res_retry_zero = client.post(f"/jobs/{job_id}/retries", json={"retry_scope": "FAILED_ONLY"}, headers=headers_a)
        assert res_retry_zero.status_code == 400
        assert "zero retryable" in res_retry_zero.json()["detail"]["message"].lower()

    finally:
        bundle.engine.close()


# ==============================================================================
# TEST 7: The business write itself is tenant-scoped and replay-safe
#
# These two are the counterfactuals for the failure modes an earlier revision
# actually had: the item executor took the intake id from the payload, so two
# tenants submitting the same id wrote over each other, and it minted a fresh
# random id per attempt, so a crash between the business write and the receipt
# checkpoint left two records behind for one row. Both drive the default
# registry -- no executor double -- and read the business entity back, not the
# receipt.
# ==============================================================================
def test_7_same_submitted_intake_id_cannot_cross_tenants(db_path: str) -> None:
    """Two tenants submitting the same intake_id get two records, neither overwritten."""
    bundle = _durable_bundle(db_path)
    try:
        shared_row = {
            "item_id": "row-001",
            "intake_id": "IN-COLLIDING-0001",
            "address_raw": "台北市信義區松仁路100號",
        }
        records = {}
        for tenant_id, address in (
            ("tenant-alpha", "台北市信義區松仁路100號"),
            ("tenant-beta", "台北市信義區松仁路200號"),
        ):
            row = {**shared_row, "address_raw": address}
            record, created = bundle.job_queue.enqueue(
                JobRequest(
                    job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                    payload={"tenant_id": tenant_id, "items": [row]},
                    idempotency_key=f"idemp-cross-tenant-{tenant_id}",
                ),
                correlation_id=f"corr-cross-{tenant_id}",
            )
            assert created is True
            worker = ODayWorker(
                persistence=bundle,
                registry=build_default_registry(),
                heartbeat_interval_seconds=60.0,
            )
            assert worker.run_once() is True
            records[tenant_id] = record

        intakes = bundle.operator_intake_repository.list_intakes()
        assert len(intakes) == 2, (
            "one tenant's batch row overwrote the other's business record"
        )

        by_tenant = {str(it["tenantId"]): it for it in intakes}
        assert set(by_tenant) == {"tenant-alpha", "tenant-beta"}
        assert by_tenant["tenant-alpha"]["parsedFields"]["address"]["normalizedValue"] == (
            "台北市信義區松仁路100號"
        )
        assert by_tenant["tenant-beta"]["parsedFields"]["address"]["normalizedValue"] == (
            "台北市信義區松仁路200號"
        )

        # The submitted id is not the stored id: the record is addressed by the
        # tenant + job + item triple, so it is unreachable from the payload.
        for tenant_id, record in records.items():
            expected_id = batch_listing_intake_id(tenant_id, record.job_id, "row-001")
            assert by_tenant[tenant_id]["id"] == expected_id
            assert by_tenant[tenant_id]["id"] != "IN-COLLIDING-0001"
            receipt = DurableJobReceipt.from_dict(
                bundle.job_queue.get(record.job_id).payload["receipt"]
            )
            assert receipt.items[0].result_ref == expected_id
    finally:
        bundle.engine.close()


def test_7_crash_between_business_write_and_receipt_leaves_one_record(db_path: str) -> None:
    """A replay after the business write lands writes the same record, not a second one."""
    from unittest.mock import patch

    from apps.worker.oday_worker.handlers import (
        _default_batch_listing_item_executor as orig_executor,
    )
    bundle = _durable_bundle(db_path)
    tenant_id = "tenant-tw-01"
    record, _ = bundle.job_queue.enqueue(
        JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": tenant_id,
                "items": [
                    {"item_id": "row-crash", "address_raw": "台北市中山區南京東路二段5號"}
                ],
            },
            idempotency_key="idemp-crash-atomicity",
        ),
        correlation_id="corr-crash-atomicity",
    )

    def crash_after_business_write(item, tenant, p, **ctx):
        # The business write lands, then the process dies before the result
        # checkpoint can record that it did.
        orig_executor(item, tenant, p, **ctx)
        raise KeyboardInterrupt("Simulated process exit before the result checkpoint")

    worker = ODayWorker(
        persistence=bundle,
        registry=build_default_registry(),
        heartbeat_interval_seconds=60.0,
    )
    with patch(
        "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
        side_effect=crash_after_business_write,
    ):
        with pytest.raises(KeyboardInterrupt):
            worker.run_once()

    assert len(bundle.operator_intake_repository.list_intakes()) == 1
    crashed = bundle.job_queue.get(record.job_id)
    crashed_item = crashed.payload["receipt"]["items"][0]
    assert crashed_item["item_status"] == ItemStatus.PENDING.value
    assert crashed_item["attempt"] == 1, "the attempt-start checkpoint did not land"
    bundle.engine.close()

    # A fresh process reclaims the job and replays the interrupted member.
    reopened = _durable_bundle(db_path)
    try:
        reclaimed = reopened.job_queue.get(record.job_id)
        reopened.job_queue.update_status(
            reclaimed.job_id,
            JobStatus.QUEUED,
            payload=reclaimed.payload,
            delivery_state=None,
        )
        worker2 = ODayWorker(
            persistence=reopened,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )
        assert worker2.run_once() is True

        settled = reopened.job_queue.get(record.job_id)
        assert settled.status == JobStatus.SUCCEEDED
        receipt = DurableJobReceipt.from_dict(settled.payload["receipt"])
        assert receipt.items[0].item_status == ItemStatus.SUCCEEDED.value
        assert receipt.items[0].attempt == 2

        replayed = reopened.operator_intake_repository.list_intakes()
        assert len(replayed) == 1, (
            "the replayed attempt created a second business record for one row"
        )
        assert replayed[0]["id"] == receipt.items[0].result_ref
        assert replayed[0]["id"] == batch_listing_intake_id(
            tenant_id, record.job_id, "row-crash"
        )
        assert replayed[0]["tenantId"] == tenant_id
    finally:
        reopened.engine.close()


def test_7_row_completeness_decides_stage_without_zero_fill(db_path: str) -> None:
    """Stage comes from the row's own data; a gap is a gap, not a zero."""
    bundle = _durable_bundle(db_path)
    try:
        tenant_id = "tenant-tw-01"
        record, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": tenant_id,
                    "items": [
                        {
                            "item_id": "row-complete",
                            "address_raw": "台北市大安區敦化南路一段8號",
                            "rent_per_month": 88000,
                            "area_ping": 42.5,
                            "floor": "1F",
                        },
                        {
                            # Address only: the operator still has to supply
                            # rent and size before this row can be matched.
                            "item_id": "row-incomplete",
                            "address_raw": "台北市大安區敦化南路一段9號",
                        },
                    ],
                },
                idempotency_key="idemp-stage-derivation",
            ),
            correlation_id="corr-stage-derivation",
        )
        worker = ODayWorker(
            persistence=bundle,
            registry=build_default_registry(),
            heartbeat_interval_seconds=60.0,
        )
        assert worker.run_once() is True

        settled = bundle.job_queue.get(record.job_id)
        assert settled.status == JobStatus.SUCCEEDED
        receipt = DurableJobReceipt.from_dict(settled.payload["receipt"])
        assert [it.item_status for it in receipt.items] == [
            ItemStatus.SUCCEEDED.value,
            ItemStatus.SUCCEEDED.value,
        ]

        by_id = {it["id"]: it for it in bundle.operator_intake_repository.list_intakes()}
        complete = by_id[receipt.items[0].result_ref]
        incomplete = by_id[receipt.items[1].result_ref]

        # A complete row runs the existing matcher and lands ready for review.
        assert complete["stage"] == "READY"
        assert complete["matchResult"]["outcome"] == "NEW"
        assert complete["contentFingerprint"]
        assert complete["parsedFields"]["rent"]["normalizedValue"] == 88000
        assert complete["parsedFields"]["areaPing"]["normalizedValue"] == 42.5
        assert "missingRequiredFields" not in complete

        # An incomplete row waits for assisted entry. The missing fields are
        # absent and named, not defaulted to 0, which would have presented the
        # row as a fully imported listing.
        assert incomplete["stage"] == "AWAITING_ASSISTED_ENTRY"
        assert incomplete["matchResult"] is None
        assert sorted(incomplete["missingRequiredFields"]) == ["areaPing", "rent"]
        assert set(incomplete["parsedFields"]) == {"address"}

        # Neither row invents a source URL or a retrieval snapshot.
        for intake in (complete, incomplete):
            assert intake["originalUrl"] is None
            assert intake["canonicalUrl"] is None
            assert intake["rawSnapshot"] is None
            assert intake["intakeMethod"] == "BATCH_ASSISTED_ENTRY"
            assert intake["tenantId"] == tenant_id
    finally:
        bundle.engine.close()


# ==============================================================================
# REVIEW FINDINGS REGRESSION TESTS (F1 - F5)
# ==============================================================================


def test_review_finding_1_heartbeat_version_coordination(db_path: str) -> None:
    """F1: Heartbeat version coordinates with handler checkpoints on long-running items."""
    import time
    from types import SimpleNamespace
    from unittest.mock import patch

    from shared.jobs.queue import InMemoryJobQueue

    queue = InMemoryJobQueue()
    bundle = SimpleNamespace(
        job_queue=queue,
        operator_intake_repository=InMemoryAssistedIntakeRepository(),
    )
    record, _ = queue.enqueue(
        JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": "tenant-review",
                "items": [{"item_id": "one", "address_raw": "台北市大安區1號"}],
            },
        ),
        correlation_id="review-heartbeat",
    )
    worker = ODayWorker(
        persistence=bundle,
        registry=build_default_registry(),
        heartbeat_interval_seconds=0.01,
    )
    failures = []
    heartbeat_calls = []
    original_heartbeat = queue.heartbeat

    def observed_heartbeat(job_id, expected_version, fence_token):
        latest = queue.get(job_id)
        heartbeat_calls.append(
            {
                "expected_version": expected_version,
                "actual_version": latest.version,
                "expected_fence": fence_token,
                "actual_fence": latest.fence_token,
            }
        )
        return original_heartbeat(job_id, expected_version, fence_token)

    def slow_executor(*args, **kwargs):
        time.sleep(0.05)
        return "INTAKE-REVIEW", None

    with patch.object(queue, "heartbeat", side_effect=observed_heartbeat), patch.object(
        worker, "_record_stale_worker", side_effect=lambda job, exc: failures.append(str(exc))
    ), patch(
        "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
        side_effect=slow_executor,
    ):
        assert worker.run_once() is True

    assert len(failures) == 0
    assert len(heartbeat_calls) >= 1
    assert queue.get(record.job_id).status == JobStatus.SUCCEEDED


def test_review_finding_2_forged_receipt_sanitization(db_path: str) -> None:
    """F2: API sanitizes forged client receipt and default worker executes real business writes."""
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app

    bundle = _durable_bundle(db_path)
    try:
        app = create_app(
            job_queue=bundle.job_queue,
            audit_log=bundle.audit_log,
            persistence=bundle,
        )
        headers = _auth_headers("review-tenant", role="expansion_user", subject="rev-user")
        with TestClient(app) as client:
            payload = {
                "items": [{"item_id": "a", "address_raw": "台北市信義區忠孝東路五段1號"}],
                "receipt": {
                    "items": [
                        {
                            "item_id": "a",
                            "item_status": "SUCCEEDED",
                            "attempt": 99,
                            "result_ref": "nonexistent-intake",
                            "error": None,
                        }
                    ]
                },
            }
            res = client.post(
                "/api/v1/jobs",
                headers=headers,
                json={"job_type": "batch-listing-intake", "payload": payload},
            )
            assert res.status_code == 202
            job_id = res.json()["job_id"]

            worker = ODayWorker(persistence=bundle, heartbeat_interval_seconds=60.0)
            assert worker.run_once() is True

            result = client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()
            intakes = bundle.operator_intake_repository.list_intakes()

            assert len(intakes) == 1
            assert result["status"] == "succeeded"
            assert result["payload"]["receipt"]["summary"]["succeeded_count"] == 1
            assert result["payload"]["receipt"]["items"][0]["attempt"] == 1
            assert result["payload"]["receipt"]["items"][0]["result_ref"] == intakes[0]["id"]
    finally:
        bundle.engine.close()


def test_review_finding_3_cancellation_races(db_path: str) -> None:
    """F3: Cancellation races on terminal write and attempt start are safely settled with derive parity."""
    # Sub-case A1: All items succeeded before cancel arrives -> derive parity yields SUCCEEDED
    bundle_a = _durable_bundle(db_path)
    try:
        queue_a = bundle_a.job_queue
        rec_a, _ = queue_a.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": "tenant-race-a",
                    "items": [{"item_id": "item-1", "address_raw": "台北市大安區1號"}],
                },
            ),
            correlation_id="race-terminal",
        )
        orig_update_a = queue_a.update_status
        cancelled_a = False

        def racing_terminal_update(job_id, status, *args, **kwargs):
            nonlocal cancelled_a
            receipt = (kwargs.get("payload") or {}).get("receipt", {})
            if not cancelled_a and status == JobStatus.SUCCEEDED and receipt:
                cancelled_a = True
                orig_update_a(job_id, JobStatus.CANCELLED)
            return orig_update_a(job_id, status, *args, **kwargs)

        with patch.object(queue_a, "update_status", side_effect=racing_terminal_update):
            ODayWorker(persistence=bundle_a, heartbeat_interval_seconds=60.0).run_once()

        final_a = queue_a.get(rec_a.job_id)
        assert cancelled_a is True
        derived_a, summary_a = derive_batch_status_and_summary(final_a.payload["receipt"]["items"])
        assert final_a.status == JobStatus.SUCCEEDED
        assert final_a.payload["receipt"]["status"] == "SUCCEEDED"
        assert derived_a == JobStatus.SUCCEEDED
        assert summary_a.succeeded_count == 1
        assert summary_a.cancelled_count == 0
    finally:
        bundle_a.engine.close()

    # Sub-case A2: Multi-item batch where item-1 succeeded but cancel arrives before item-2 starts -> CANCELLED
    bundle_a2 = _durable_bundle(db_path + ".a2.sqlite3")
    try:
        queue_a2 = bundle_a2.job_queue
        rec_a2, _ = queue_a2.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": "tenant-race-a2",
                    "items": [
                        {"item_id": "item-1", "address_raw": "台北市大安區1號"},
                        {"item_id": "item-2", "address_raw": "台北市大安區2號"},
                    ],
                },
            ),
            correlation_id="race-mid-cancel",
        )
        orig_update_a2 = queue_a2.update_status
        cancelled_a2 = False

        def racing_mid_update(job_id, status, *args, **kwargs):
            nonlocal cancelled_a2
            receipt = (kwargs.get("payload") or {}).get("receipt", {})
            items = receipt.get("items", [])
            # Cancel right after item-1 result lands (item-1 is SUCCEEDED, item-2 is PENDING)
            if not cancelled_a2 and any(it.get("item_id") == "item-1" and it.get("item_status") == "SUCCEEDED" for it in items):
                cancelled_a2 = True
                orig_update_a2(job_id, JobStatus.CANCELLED)
            return orig_update_a2(job_id, status, *args, **kwargs)

        with patch.object(queue_a2, "update_status", side_effect=racing_mid_update):
            ODayWorker(persistence=bundle_a2, heartbeat_interval_seconds=60.0).run_once()

        final_a2 = queue_a2.get(rec_a2.job_id)
        assert cancelled_a2 is True
        derived_a2, summary_a2 = derive_batch_status_and_summary(final_a2.payload["receipt"]["items"])
        assert final_a2.status == JobStatus.CANCELLED
        assert final_a2.payload["receipt"]["status"] == "CANCELLED"
        assert derived_a2 == JobStatus.CANCELLED
        assert summary_a2.succeeded_count == 1
        assert summary_a2.cancelled_count == 1
    finally:
        bundle_a2.engine.close()

    # Sub-case B: Attempt start race (cancellation before attempt checkpoint lands)
    bundle_b = _durable_bundle(db_path + ".b.sqlite3")
    try:
        queue_b = bundle_b.job_queue
        rec_b, _ = queue_b.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": "tenant-race-b",
                    "items": [{"item_id": "item-1", "address_raw": "台北市大安區2號"}],
                },
            ),
            correlation_id="race-attempt",
        )
        orig_update_b = queue_b.update_status
        cancelled_b = False
        executor_calls = []

        def racing_attempt_update(job_id, status, *args, **kwargs):
            nonlocal cancelled_b
            receipt = (kwargs.get("payload") or {}).get("receipt", {})
            if not cancelled_b and status == JobStatus.RUNNING and receipt:
                cancelled_b = True
                orig_update_b(job_id, JobStatus.CANCELLED)
            return orig_update_b(job_id, status, *args, **kwargs)

        def mock_executor(item, *args, **kwargs):
            executor_calls.append(item["item_id"])
            return "dummy-ref", None

        with patch.object(queue_b, "update_status", side_effect=racing_attempt_update), patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=mock_executor,
        ):
            ODayWorker(persistence=bundle_b, heartbeat_interval_seconds=60.0).run_once()

        final_b = queue_b.get(rec_b.job_id)
        assert cancelled_b is True
        assert executor_calls == []
        assert final_b.status == JobStatus.CANCELLED
        item_b0 = final_b.payload["receipt"]["items"][0]
        assert item_b0["attempt"] == 0
        assert item_b0["error"]["code"] == "CANCELLED_BEFORE_EXECUTION"
    finally:
        bundle_b.engine.close()


def test_review_finding_4_duplicate_item_id_validation(db_path: str) -> None:
    """F4: API and handler reject duplicate item IDs in batch payload."""
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app

    bundle = _durable_bundle(db_path)
    try:
        app = create_app(
            job_queue=bundle.job_queue,
            audit_log=bundle.audit_log,
            persistence=bundle,
        )
        headers = _auth_headers("review-tenant", role="expansion_user", subject="rev-user")
        with TestClient(app) as client:
            payload = {
                "items": [
                    {"item_id": "same-id", "address_raw": "台北市大安區1號"},
                    {"item_id": "same-id", "address_raw": "台北市大安區2號"},
                ],
            }
            res = client.post(
                "/api/v1/jobs",
                headers=headers,
                json={"job_type": "batch-listing-intake", "payload": payload},
            )
            assert res.status_code == 422
            assert "DUPLICATE_ITEM_ID" in res.text

        # Handler also raises NonRetryableJobError if duplicate IDs bypass API
        dup_req = JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": "review-tenant",
                "items": [
                    {"item_id": "dup", "address_raw": "台北市大安區1號"},
                    {"item_id": "dup", "address_raw": "台北市大安區2號"},
                ],
            },
        )
        rec, _ = bundle.job_queue.enqueue(dup_req, correlation_id="corr-dup")
        worker = ODayWorker(persistence=bundle, heartbeat_interval_seconds=60.0)
        assert worker.run_once() is True
        job_record = bundle.job_queue.get(rec.job_id)
        assert job_record.status == JobStatus.FAILED
    finally:
        bundle.engine.close()


def test_review_finding_5_crash_replay_preserves_operator_corrections(db_path: str) -> None:
    """F5: Crash replay preserves operator corrections without destructive upsert."""
    from datetime import UTC, datetime, timedelta

    bundle = _durable_bundle(db_path)
    tenant = "tenant-review"
    original_address = "台北市大安區1號"
    corrected_address = "台北市大安區99號"
    try:
        record, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": tenant,
                    "items": [
                        {
                            "item_id": "one",
                            "address_raw": original_address,
                            "rent_per_month": 10000,
                            "area_ping": 30,
                            "floor": "1F",
                        }
                    ],
                },
            ),
            correlation_id="review-replay-correction",
        )
        worker = ODayWorker(persistence=bundle, heartbeat_interval_seconds=60.0)

        def crash_after_write(*args, **kwargs):
            _default_batch_listing_item_executor(*args, **kwargs)
            raise KeyboardInterrupt("crash after business write")

        with patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            side_effect=crash_after_write,
        ):
            with pytest.raises(KeyboardInterrupt):
                worker.run_once()

        intake_id = batch_listing_intake_id(tenant, record.job_id, "one")
        service = build_batch_listing_intake_service(tenant, bundle)
        corrected = service.correct_intake(
            intake_id=intake_id,
            fields={"address": corrected_address},
            reason="Operator verified the door number",
            risk_summary="Operator checked identity",
            risk_acknowledged=True,
            actor_role_id="expansion_user",
            actor_name="Review operator",
            idempotency_key="review-correction-key",
            correlation_id="review-correction",
        )
        assert corrected["parsedFields"]["address"]["correctedValue"] == corrected_address

        # Expire lease
        bundle.engine.execute(
            "UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), record.job_id),
        )
        bundle.engine.close()

        # Restart worker and replay
        restarted = _durable_bundle(db_path)
        worker2 = ODayWorker(persistence=restarted, heartbeat_interval_seconds=60.0)
        assert worker2.run_once() is True
        replayed = build_batch_listing_intake_service(tenant, restarted).get_intake(intake_id)
        final_job = restarted.job_queue.get(record.job_id)

        assert final_job.status == JobStatus.SUCCEEDED
        assert replayed["parsedFields"]["address"]["correctedValue"] == corrected_address
        assert replayed["parsedFields"]["address"]["correctionReason"] == "Operator verified the door number"
        assert replayed["parsedFields"]["address"]["normalizedValue"] == original_address
        assert any(e["action"] == "intake.correct" for e in replayed["auditEvents"])
        restarted.engine.close()
    finally:
        pass


def test_review_finding_r1_checkpoint_failure_preserves_receipt(db_path: str) -> None:
    """R1: Checkpoint storage failure triggers queue retry with latest persisted receipt preserved."""
    bundle = _durable_bundle(db_path)
    queue = bundle.job_queue
    job, _ = queue.enqueue(
        JobRequest(
            job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
            payload={
                "tenant_id": "review-synthetic",
                "items": [
                    {"item_id": "a", "address_raw": "台北市大安區1號"},
                    {"item_id": "b", "address_raw": "台北市大安區2號"},
                ],
            },
        ),
        correlation_id="review-checkpoint-failure",
    )
    original_update = queue.update_status
    calls: list[str] = []
    checkpoints_before_failure: list[dict[str, Any]] = []
    failed = False

    def update(job_id, status, *args, **kwargs):
        nonlocal failed
        receipt = (kwargs.get("payload") or {}).get("receipt") or {}
        rows = receipt.get("items", [])
        if (
            not failed
            and status == JobStatus.RUNNING
            and any(r["item_id"] == "b" and r["item_status"] == "SUCCEEDED" for r in rows)
        ):
            failed = True
            checkpoints_before_failure.append(queue.get(job_id).payload["receipt"])
            raise OSError("synthetic transient checkpoint write failure")
        return original_update(job_id, status, *args, **kwargs)

    def executor(item, *args, **kwargs):
        calls.append(item["item_id"])
        return _default_batch_listing_item_executor(item, *args, **kwargs)

    with patch.object(queue, "update_status", side_effect=update), patch(
        "apps.worker.oday_worker.handlers._default_batch_listing_item_executor", side_effect=executor
    ):
        assert ODayWorker(persistence=bundle, heartbeat_interval_seconds=60.0).run_once()

    after_retry_queue = queue.get(job.job_id)
    assert checkpoints_before_failure[0]["items"][0]["item_status"] == "SUCCEEDED"
    assert after_retry_queue.status == JobStatus.QUEUED
    assert after_retry_queue.payload.get("receipt") is not None
    assert after_retry_queue.payload["receipt"]["items"][0]["item_status"] == "SUCCEEDED"
    assert after_retry_queue.payload["_retry_count"] == 1
    bundle.engine.close()

    restarted = _durable_bundle(db_path)
    with patch(
        "apps.worker.oday_worker.handlers._default_batch_listing_item_executor", side_effect=executor
    ):
        assert ODayWorker(persistence=restarted, heartbeat_interval_seconds=60.0).run_once()

    final = restarted.job_queue.get(job.job_id)
    assert calls == ["a", "b", "b"]
    assert final.status == JobStatus.SUCCEEDED
    assert final.payload["receipt"]["items"][0]["item_id"] == "a"
    assert final.payload["receipt"]["items"][0]["attempt"] == 1
    assert final.payload["receipt"]["items"][1]["item_id"] == "b"
    assert final.payload["receipt"]["items"][1]["attempt"] == 2
    assert len(restarted.operator_intake_repository.list_intakes()) == 2
    restarted.engine.close()


def test_review_finding_r1_distinct_engine_stale_worker_cannot_overwrite_correction(db_path: str) -> None:
    """R1: Stale lease worker with distinct SQLite connection cannot overwrite committed operator correction."""
    from datetime import UTC, datetime, timedelta

    old_bundle = _durable_bundle(db_path)
    new_bundle = _durable_bundle(db_path)
    tenant = "review-r1-tenant"
    original_address = "台北市大安區原地址1號"
    corrected_address = "台北市大安區更正地址99號"

    try:
        assert old_bundle.engine is not new_bundle.engine
        assert old_bundle.engine.lock is not new_bundle.engine.lock

        record, _ = old_bundle.job_queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": tenant,
                    "items": [
                        {
                            "item_id": "one",
                            "address_raw": original_address,
                            "rent_per_month": 10000,
                            "area_ping": 30,
                            "floor": "1F",
                        }
                    ],
                },
            ),
            correlation_id="review-r1-correlation",
        )
        intake_id = batch_listing_intake_id(tenant, record.job_id, "one")
        old_store = old_bundle.operator_intake_repository._store
        original_put_if_absent = old_store.put_if_absent
        injected = False

        def delayed_old_put_if_absent(collection, doc_id, obj, **kwargs):
            nonlocal injected
            if collection == "operator.assisted_intakes" and doc_id == intake_id and not injected:
                injected = True
                assert new_bundle.operator_intake_repository.get_intake(intake_id) is None
                new_bundle.engine.execute(
                    "UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?",
                    ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), record.job_id),
                )
                assert ODayWorker(
                    persistence=new_bundle,
                    worker_id="review-r1-new-worker",
                    heartbeat_interval_seconds=60.0,
                ).run_once() is True
                assert new_bundle.job_queue.get(record.job_id).status == JobStatus.SUCCEEDED
                build_batch_listing_intake_service(tenant, new_bundle).correct_intake(
                    intake_id=intake_id,
                    fields={"address": corrected_address},
                    reason="Synthetic operator correction",
                    risk_summary="Synthetic identity check",
                    risk_acknowledged=True,
                    actor_role_id="expansion_user",
                    actor_name="synthetic-review-operator",
                    idempotency_key="synthetic-review-r1-correction",
                    correlation_id="synthetic-review-r1-correction",
                )
                corrected = new_bundle.operator_intake_repository.get_intake(intake_id)
                assert corrected["parsedFields"]["address"]["correctedValue"] == corrected_address
            return original_put_if_absent(collection, doc_id, obj, **kwargs)

        with patch.object(old_store, "put_if_absent", side_effect=delayed_old_put_if_absent):
            old_worker_ran = ODayWorker(
                persistence=old_bundle,
                worker_id="review-r1-old-worker",
                heartbeat_interval_seconds=60.0,
            ).run_once()

        final = new_bundle.operator_intake_repository.get_intake(intake_id)
        final_job = new_bundle.job_queue.get(record.job_id)
        assert injected is True
        assert old_worker_ran is True
        assert final["parsedFields"]["address"]["correctedValue"] == corrected_address
        assert any(event["action"] == "intake.correct" for event in final["auditEvents"])
        assert final_job.status == JobStatus.SUCCEEDED
        assert final_job.payload["receipt"]["items"][0]["attempt"] == 2
    finally:
        old_bundle.engine.close()
        new_bundle.engine.close()


def test_review_finding_r2_cancellation_race_distinct_engine(db_path: str) -> None:
    """R2: Cancellation read-modify-write on one connection does not overwrite SUCCEEDED on another."""
    operator_bundle = _durable_bundle(db_path)
    worker_bundle = _durable_bundle(db_path)
    tenant = "synthetic-r2-tenant"
    try:
        record, _ = worker_bundle.job_queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": tenant,
                    "items": [
                        {"item_id": "row-1", "address_raw": "synthetic address 1"},
                        {"item_id": "row-2", "address_raw": "synthetic address 2"},
                    ],
                },
                idempotency_key="synthetic-r2-cancel-race",
            ),
            correlation_id="synthetic-r2-correlation",
        )
        claimed = worker_bundle.job_queue.claim_next(worker_id="synthetic-r2-worker")
        assert claimed.job_id == record.job_id
        pending = [
            ItemReceipt("row-1", ItemStatus.PENDING.value, 1),
            ItemReceipt("row-2", ItemStatus.PENDING.value, 0),
        ]
        _, summary = derive_batch_status_and_summary(pending)
        receipt = DurableJobReceipt(
            job_id=record.job_id,
            job_type=record.job_type,
            tenant_id=tenant,
            status="RUNNING",
            summary=summary,
            items=tuple(pending),
            created_at=record.created_at.isoformat(),
            correlation_id=record.correlation_id,
            idempotency_key=record.idempotency_key,
        )
        payload = dict(claimed.payload)
        payload["receipt"] = receipt.to_dict()
        payload["summary"] = summary.to_dict()
        worker_bundle.job_queue.update_status(
            record.job_id,
            JobStatus.RUNNING,
            payload=payload,
            expected_version=claimed.version,
            fence_token=claimed.fence_token,
        )

        original_query = operator_bundle.engine.query_one
        injected = False

        def pause_after_cancellation_read(sql, params=()):
            nonlocal injected
            row = original_query(sql, params)
            if not injected and "SELECT version, fence_token" in sql:
                injected = True
                latest = worker_bundle.job_queue.get(record.job_id)
                success_items = [
                    ItemReceipt("row-1", ItemStatus.SUCCEEDED.value, 1, result_ref="synthetic-intake-1"),
                    pending[1],
                ]
                _, success_summary = derive_batch_status_and_summary(success_items)
                updated_receipt = dict(latest.payload["receipt"])
                updated_receipt["items"] = [item.to_dict() for item in success_items]
                updated_receipt["summary"] = success_summary.to_dict()
                updated_payload = dict(latest.payload)
                updated_payload["receipt"] = updated_receipt
                updated_payload["summary"] = success_summary.to_dict()
                worker_bundle.job_queue.update_status(
                    record.job_id,
                    JobStatus.RUNNING,
                    payload=updated_payload,
                    expected_version=latest.version,
                    fence_token=latest.fence_token,
                )
            return row

        with patch.object(operator_bundle.engine, "query_one", side_effect=pause_after_cancellation_read):
            operator_bundle.job_queue.update_status(record.job_id, JobStatus.CANCELLED)

        final = worker_bundle.job_queue.get(record.job_id)
        assert injected is True
        assert final.status == JobStatus.CANCELLED
        receipt_final = final.payload["receipt"]
        assert receipt_final["items"][0]["item_id"] == "row-1"
        assert receipt_final["items"][0]["item_status"] == "SUCCEEDED"
        assert receipt_final["items"][0]["result_ref"] == "synthetic-intake-1"
        assert receipt_final["items"][1]["item_id"] == "row-2"
        assert receipt_final["items"][1]["item_status"] == "CANCELLED"
        assert final.payload["summary"]["succeeded_count"] == 1
        assert final.payload["summary"]["cancelled_count"] == 1
    finally:
        operator_bundle.engine.close()
        worker_bundle.engine.close()


def test_review_finding_r3_r4_scope_and_submitter_preservation(db_path: str) -> None:
    """R1 & R2 & R3 & R4: Batch API enforces scope aliases consistency, heat-zone/region/brand/area scope, and preserves submitter & scope for assisted entry."""
    from modules.listing.application.intake_authorization import authorize_intake_action

    bundle = _durable_bundle(db_path)
    tenant_id = "scope-tenant"
    headers_staff = {
        "x-subject-id": "scope-user",
        "x-roles": "expansion_user",
        "x-tenant-id": tenant_id,
        "x-heat-zone-ids": "HZ-A",
        "x-region-ids": "REG-A",
        "x-brand-ids": "BRAND-A",
        "x-assigned-area-ids": "AREA-A",
    }
    headers_other_staff = {
        "x-subject-id": "other-user",
        "x-roles": "expansion_user",
        "x-tenant-id": tenant_id,
        "x-heat-zone-ids": "HZ-A",
        "x-region-ids": "REG-A",
        "x-brand-ids": "BRAND-A",
        "x-assigned-area-ids": "AREA-A",
    }
    headers_wrong_region_staff = {
        "x-subject-id": "scope-user",
        "x-roles": "expansion_user",
        "x-tenant-id": tenant_id,
        "x-heat-zone-ids": "HZ-A",
        "x-region-ids": "REG-B",
        "x-brand-ids": "BRAND-A",
        "x-assigned-area-ids": "AREA-A",
    }

    try:
        app = create_app(job_queue=bundle.job_queue, audit_log=bundle.audit_log, persistence=bundle)
        client = TestClient(app)

        # R1: Enqueue with conflicting scope aliases on same item is rejected with 422 CONFLICTING_SCOPE_ALIAS
        resp_conflict_hz = client.post(
            "/api/v1/jobs",
            headers=headers_staff,
            json={
                "job_type": "batch-listing-intake",
                "payload": {
                    "items": [
                        {"item_id": "c1", "address_raw": "synthetic", "heatZoneId": "HZ-A", "heat_zone_id": "HZ-B"},
                    ]
                },
            },
        )
        assert resp_conflict_hz.status_code == 422
        assert resp_conflict_hz.json().get("detail", {}).get("code") == "CONFLICTING_SCOPE_ALIAS"

        resp_conflict_reg = client.post(
            "/api/v1/jobs",
            headers=headers_staff,
            json={
                "job_type": "batch-listing-intake",
                "payload": {
                    "items": [
                        {"item_id": "c2", "address_raw": "synthetic", "regionId": "REG-A", "region_id": "REG-B"},
                    ]
                },
            },
        )
        assert resp_conflict_reg.status_code == 422
        assert resp_conflict_reg.json().get("detail", {}).get("code") == "CONFLICTING_SCOPE_ALIAS"

        # R3: Enqueue with out-of-scope heat zone (HZ-B) is rejected with 403 SCOPE_DENIED
        resp_denied = client.post(
            "/api/v1/jobs",
            headers=headers_staff,
            json={
                "job_type": "batch-listing-intake",
                "payload": {
                    "items": [
                        {"item_id": "outside", "address_raw": "synthetic outside", "heatZoneId": "HZ-B"},
                        {"item_id": "inside", "address_raw": "synthetic inside", "heatZoneId": "HZ-A"},
                    ]
                },
            },
        )
        assert resp_denied.status_code == 403
        assert resp_denied.json().get("detail") == "SCOPE_DENIED"

        # R2 & R3: Enqueue with authorized multi-axis scope (HZ-A, REG-A, BRAND-A, AREA-A) succeeds
        resp_ok = client.post(
            "/api/v1/jobs",
            headers=headers_staff,
            json={
                "job_type": "batch-listing-intake",
                "payload": {
                    "items": [
                        {
                            "item_id": "item-1",
                            "address_raw": "台北市大安區新生南路一段1號",
                            "heatZoneId": "HZ-A",
                            "regionId": "REG-A",
                            "brandId": "BRAND-A",
                            "assignedAreaId": "AREA-A",
                        },
                    ]
                },
            },
        )
        assert resp_ok.status_code == 202
        job_id = resp_ok.json()["job_id"]

        # Worker processes job and creates intake record
        assert ODayWorker(persistence=bundle, heartbeat_interval_seconds=60.0).run_once() is True
        job = bundle.job_queue.get(job_id)
        assert job.status == JobStatus.SUCCEEDED

        # R4: Submitter principal preserved - creator can view job via API
        resp_get = client.get(f"/api/v1/jobs/{job_id}", headers=headers_staff)
        assert resp_get.status_code == 200

        # R4: Other staff cannot view job (ownership required)
        resp_get_other = client.get(f"/api/v1/jobs/{job_id}", headers=headers_other_staff)
        assert resp_get_other.status_code == 403

        # R2: Intake record contains all authorized scope fields
        intake_id = batch_listing_intake_id(tenant_id, job_id, "item-1")
        intake = bundle.operator_intake_repository.get_intake(intake_id)
        assert intake is not None
        assert intake["submitter"] == "scope-user"
        assert intake["owner"] == "scope-user"
        assert intake["heatZoneId"] == "HZ-A"
        assert intake["regionId"] == "REG-A"
        assert intake["brandId"] == "BRAND-A"
        assert intake["assignedAreaId"] == "AREA-A"

        from apps.api.oday_api.security.dependencies import principal_from_headers
        principal = principal_from_headers(headers_staff)
        # Should not raise SCOPE_DENIED or OWNERSHIP_REQUIRED
        authorize_intake_action(principal, "view", resource=intake)

        # Creator can correct intake
        corrected = build_batch_listing_intake_service(tenant_id, bundle).correct_intake(
            intake_id=intake_id,
            fields={"address": "台北市大安區新生南路一段99號"},
            reason="Corrected by creator",
            risk_summary="Verified",
            risk_acknowledged=True,
            actor_role_id="expansion_user",
            actor_name="scope-user",
            idempotency_key="creator-correct-01",
            correlation_id="creator-correct-01",
        )
        assert corrected["parsedFields"]["address"]["correctedValue"] == "台北市大安區新生南路一段99號"

        # User restricted to REG-B is denied on this intake with 403 SCOPE_DENIED
        principal_wrong_region = principal_from_headers(headers_wrong_region_staff)
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            authorize_intake_action(principal_wrong_region, "view", resource=intake)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "SCOPE_DENIED"
    finally:
        bundle.engine.close()


def test_review_finding_r5_pre_execution_cancellation_canonical_envelope(db_path: str) -> None:
    """R5: Cancellation before first checkpoint preserves canonical job_id, job_type, correlation_id, idempotency_key, created_at."""
    bundle = _durable_bundle(db_path)
    tenant = "synthetic-r5-tenant"
    try:
        record, _ = bundle.job_queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": tenant,
                    "items": [{"item_id": "row-before-start", "address_raw": "synthetic address"}],
                },
                idempotency_key="synthetic-prestart-idempotency",
            ),
            correlation_id="synthetic-prestart-correlation",
        )
        bundle.job_queue.update_status(record.job_id, JobStatus.CANCELLED)
        bundle.engine.close()

        reopened = _durable_bundle(db_path)
        roundtrip = reopened.job_queue.get(record.job_id)
        raw = roundtrip.payload["receipt"]
        parsed = DurableJobReceipt.from_dict(raw)

        assert raw["job_id"] == record.job_id
        assert raw["job_type"] == BATCH_LISTING_INTAKE_JOB_TYPE
        assert raw["correlation_id"] == "synthetic-prestart-correlation"
        assert raw["idempotency_key"] == "synthetic-prestart-idempotency"
        assert raw["created_at"] == record.created_at.isoformat()
        assert raw["status"] == "CANCELLED"

        assert parsed.job_id == record.job_id
        assert parsed.job_type == BATCH_LISTING_INTAKE_JOB_TYPE
        assert parsed.summary.cancelled_count == 1
        assert parsed.summary.total_count == 1
        assert parsed.items[0].attempt == 0
        assert parsed.items[0].item_status == "CANCELLED"

        app = create_app(job_queue=reopened.job_queue, audit_log=reopened.audit_log, persistence=reopened)
        client = TestClient(app)
        api_resp = client.get(f"/api/v1/jobs/{record.job_id}", headers=_auth_headers(tenant))
        assert api_resp.status_code == 200
        api_receipt = api_resp.json()["payload"]["receipt"]
        assert api_receipt["job_id"] == record.job_id
        assert api_receipt["job_type"] == BATCH_LISTING_INTAKE_JOB_TYPE

        reopened.engine.close()
    finally:
        bundle.engine.close()


def test_review_finding_r4_heartbeat_collision_memory_and_durable(db_path: str) -> None:
    """R4: Heartbeat version race between get and checkpoint write succeeds on both in-memory and durable queues."""
    from shared.infrastructure.persistence.factory import _memory_bundle

    for kind in ("memory", "durable"):
        bundle = (
            _memory_bundle()
            if kind == "memory"
            else _durable_bundle(db_path + f".{kind}.sqlite3")
        )
        queue = bundle.job_queue
        record, _ = queue.enqueue(
            JobRequest(
                job_type=BATCH_LISTING_INTAKE_JOB_TYPE,
                payload={
                    "tenant_id": "synthetic-review",
                    "items": [{"item_id": "row-a", "address_raw": "synthetic address"}],
                },
            ),
            correlation_id="synthetic-heartbeat-collision",
        )
        original_update = queue.update_status
        injected = False

        def make_race(target_queue, orig_update):
            def race(job_id, status, *args, **kwargs):
                nonlocal injected
                receipt = (kwargs.get("payload") or {}).get("receipt", {})
                receipt_items = receipt.get("items", [])
                if (
                    not injected
                    and status == JobStatus.RUNNING
                    and receipt_items
                    and receipt_items[0]["item_status"] == "SUCCEEDED"
                ):
                    injected = True
                    before = target_queue.get(job_id)
                    target_queue.heartbeat(job_id, expected_version=before.version, fence_token=before.fence_token)
                return orig_update(job_id, status, *args, **kwargs)

            return race

        with patch.object(queue, "update_status", side_effect=make_race(queue, original_update)), patch(
            "apps.worker.oday_worker.handlers._default_batch_listing_item_executor",
            return_value=("synthetic-intake", None),
        ):
            assert ODayWorker(persistence=bundle, heartbeat_interval_seconds=60.0).run_once() is True

        final = queue.get(record.job_id)
        assert final.status == JobStatus.SUCCEEDED
        assert injected is True
        assert final.payload.get("receipt") is not None
        assert final.payload["receipt"]["items"][0]["item_status"] == "SUCCEEDED"
        if bundle.engine is not None:
            bundle.engine.close()
