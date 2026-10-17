"""Current submitter authority, global cross-tenant quota and atomic dual leases."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from modules.external_data.application.native_intake_process import NativeIORejected
from modules.listing.application.native_intake_requester_authority import (
    current_native_retrieval_submitter,
)
from modules.listing.application.native_intake_retrieval_leases import (
    NativeRetrievalCompositeLeases,
)
from modules.listing.application.native_intake_retrieval_policy import (
    JOB_TYPE,
    POLICY_ENV,
    SIGNING_KEY_ENV,
    STORAGE_ENV,
    NativeIntakeRetrievalAuthenticator,
    NativeIntakeRetrievalConflict,
    native_intake_retrieval_policy,
)
from modules.listing.application.native_intake_retrieval_quota import (
    NativeIntakeRetrievalQuota,
    NativeRetrievalQuotaExceeded,
)
from shared.auth import Role
from shared.jobs.queue import JobFenceRejectedError, JobRequest
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph
from tests.integration.test_native_intake_requester_authority_postgresql import (
    authority as authority,
)

pytestmark = pytest.mark.requires_live_env


def test_current_original_staff_submitter_needs_no_new_human_confidential_clearance(authority):
    graph, ids, db, intake = authority
    with db.connect() as connection:
        connection.execute("UPDATE identity.account_roles SET role='expansion_user'")
        connection.execute("UPDATE identity.account_scopes SET clearance='PUBLIC'")
    with graph.transaction(ids["tenant"]):
        principal = current_native_retrieval_submitter(graph.engine, intake, before=lambda: None)
        assert principal.subject_id == intake["submitter_subject_id"] == ids["actor"]
        assert principal.has_role(Role.EXPANSION_USER)


@pytest.mark.parametrize("sql", [
    "UPDATE identity.accounts SET status='disabled'",
    "UPDATE identity.accounts SET tenant_id=gen_random_uuid()",
    "DELETE FROM identity.account_roles",
    "UPDATE identity.account_roles SET role='platform_admin'",
    "DELETE FROM identity.account_scopes",
    "UPDATE identity.account_scopes SET modules='[\"priceops\"]'::jsonb",
    "UPDATE identity.account_scopes SET brand_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET region_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET store_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET assigned_area_ids='[\"foreign\"]'::jsonb",
    "UPDATE identity.account_scopes SET heat_zone_ids='[\"foreign\"]'::jsonb",
])
def test_retrieval_rechecks_actual_original_submitter_current_grants(authority, sql):
    graph, ids, db, intake = authority
    with db.connect() as connection:
        connection.execute("UPDATE identity.account_roles SET role='expansion_user'")
    with graph.transaction(ids["tenant"]):
        assert current_native_retrieval_submitter(graph.engine, intake, before=lambda: None).subject_id == ids["actor"]
    with db.connect() as connection:
        connection.execute(sql)
    with graph.transaction(ids["tenant"]):
        with pytest.raises((PermissionError, HTTPException)):
            current_native_retrieval_submitter(graph.engine, intake, before=lambda: None)


@pytest.fixture
def quota_case(canonical_graph):
    graph, ids, _, db = canonical_graph
    with db.connect() as connection:
        connection.execute((Path(__file__).resolve().parents[2] /
                            "infra/db/migrations/000035_native_intake_retrieval_quota.sql").read_text())
    with graph.transaction(ids["tenant"]):
        source = graph._one("SELECT * FROM intake.source_registry WHERE source_id='synthetic-feed'", ())
    return graph, ids, db, {**source, "requests_per_minute": 2, "concurrent_requests": 2}


def reserve(case, tenant=None, **kwargs):
    graph, ids, _, source = case
    with graph.transaction(tenant or ids["tenant"]):
        return NativeIntakeRetrievalQuota(graph.engine).reserve(source, global_limit=kwargs.get("limit", 2),
            charge_rate=kwargs.get("charge", True), before=lambda: None)


def test_global_concurrency_cannot_be_bypassed_by_another_tenant(quota_case):
    graph, ids, _, source = quota_case
    first = reserve(quota_case)
    second = reserve(quota_case, str(uuid4()))
    assert first.reservation_id != second.reservation_id and first.rate_reservation_id != second.rate_reservation_id
    with pytest.raises(NativeRetrievalQuotaExceeded, match="CONCURRENCY_FULL"):
        reserve(quota_case, str(uuid4()), charge=False)
    with graph.transaction(ids["tenant"]):
        quota = NativeIntakeRetrievalQuota(graph.engine)
        quota.schema(before=lambda: None)
        rows = graph.engine.query("SELECT * FROM intake.native_retrieval_inflight_slots")
        assert len(rows) == 2
        assert all(set(r) == {"reservation_id", "source_id", "admitted_at", "expires_at"} for r in rows)
        assert all(r["source_id"] == source["source_id"] for r in rows)
        quota.release(first, process_reaped=True, before=lambda: None)
    # Releasing concurrency never erases the prior HTTP rate charge.
    with pytest.raises(NativeRetrievalQuotaExceeded, match="SOURCE_RATE_FULL"):
        reserve(quota_case)
    assert reserve(quota_case, charge=False).rate_reservation_id is None


def test_per_source_limit_and_rolling_rate_window_are_independent(quota_case):
    graph, ids, db, source = quota_case
    source["concurrent_requests"] = 1
    first = reserve(quota_case)
    with pytest.raises(NativeRetrievalQuotaExceeded, match="CONCURRENCY_FULL"):
        reserve(quota_case, charge=False)
    with graph.transaction(ids["tenant"]):
        NativeIntakeRetrievalQuota(graph.engine).release(first, process_reaped=True, before=lambda: None)
    second = reserve(quota_case)
    with graph.transaction(ids["tenant"]):
        NativeIntakeRetrievalQuota(graph.engine).release(second, process_reaped=True, before=lambda: None)
    with pytest.raises(NativeRetrievalQuotaExceeded, match="SOURCE_RATE_FULL"):
        reserve(quota_case)
    with db.connect() as connection:
        connection.execute("UPDATE intake.native_retrieval_rate_reservations SET admitted_at=clock_timestamp()-interval '61 seconds',"
                           "expires_at=clock_timestamp()-interval '1 second'")
    assert reserve(quota_case).rate_reservation_id is not None


def test_quota_expiry_outlives_io_and_cleanup_and_unreaped_cannot_release(quota_case):
    graph, ids, _, _ = quota_case
    reservation = reserve(quota_case)
    with graph.transaction(ids["tenant"]):
        slot = graph._one("SELECT * FROM intake.native_retrieval_inflight_slots WHERE reservation_id=?", (reservation.reservation_id,))
        duration = (datetime.fromisoformat(slot["expires_at"]) - datetime.fromisoformat(slot["admitted_at"])).total_seconds()
        assert 11.9 <= duration <= 12.1
        with pytest.raises(NativeIntakeRetrievalConflict, match="REAP_PROOF_REQUIRED"):
            NativeIntakeRetrievalQuota(graph.engine).release(reservation, process_reaped=False, before=lambda: None)
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT reservation_id FROM intake.native_retrieval_inflight_slots WHERE reservation_id=?", (reservation.reservation_id,))


def test_racing_tenants_never_overbook_global_slot(quota_case):
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait(timeout=3)
        try:
            return reserve(quota_case, str(uuid4()), limit=1)
        except Exception as error:
            assert isinstance(error, NativeRetrievalQuotaExceeded) or getattr(error, "sqlstate", None) in {"40001", "40P01"}
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(r is not None for r in results) == 1
    graph, ids, _, _ = quota_case
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT count(*) AS count FROM intake.native_retrieval_inflight_slots")["count"] == 1


@pytest.mark.parametrize("sql", [
    "ALTER TABLE intake.native_retrieval_inflight_slots ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE intake.native_retrieval_inflight_slots ADD COLUMN tenant_id uuid",
    "ALTER TABLE intake.native_retrieval_inflight_slots ADD FOREIGN KEY (reservation_id) REFERENCES workflow.jobs(job_id)",
])
def test_each_new_reservation_rechecks_anonymous_global_schema_after_admission(quota_case, sql):
    _, _, db, _ = quota_case
    first = reserve(quota_case)
    assert first.reservation_id
    with db.connect() as connection:
        # Remove only this fixture's reservation before adding a deliberately
        # forbidden reverse FK, so the mutation itself is valid real SQL.
        if "FOREIGN KEY" in sql:
            connection.execute("DELETE FROM intake.native_retrieval_inflight_slots")
        connection.execute(sql)
    with pytest.raises(NativeIntakeRetrievalConflict, match="GLOBAL_QUOTA_"):
        reserve(quota_case, str(uuid4()), charge=False)


def test_quota_and_workflow_intent_rollback_preserves_both_global_ledgers(quota_case):
    graph, ids, _, source = quota_case
    with pytest.raises(RuntimeError, match="synthetic intent failure"):
        with graph.transaction(ids["tenant"]):
            NativeIntakeRetrievalQuota(graph.engine).reserve(source, global_limit=2, charge_rate=True, before=lambda: None)
            raise RuntimeError("synthetic intent failure")
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT count(*) AS count FROM intake.native_retrieval_inflight_slots")["count"] == 0
        assert graph.engine.query_one("SELECT count(*) AS count FROM intake.native_retrieval_rate_reservations")["count"] == 0


def test_quota_migration_refuses_partial_feature_install_instead_of_repairing_it(quota_case):
    _, _, db, _ = quota_case
    with db.connect() as connection:
        connection.execute("DROP TABLE intake.native_retrieval_inflight_slots")
    with db.connect() as connection:
        with pytest.raises(Exception, match="only partially installed") as problem:
            connection.execute((Path(__file__).resolve().parents[2] /
                                "infra/db/migrations/000035_native_intake_retrieval_quota.sql").read_text())
        assert problem.value.sqlstate == "55000"


@pytest.fixture
def leases_case(quota_case, monkeypatch):
    graph, ids, db, source = quota_case
    monkeypatch.setenv(POLICY_ENV, json.dumps({"policy_id": "synthetic-native-retrieval", "version": 1,
        "tenant_ids": [ids["tenant"]], "max_attempts": 2, "deadline_seconds": 120, "backoff_seconds": [10]}))
    monkeypatch.setenv(STORAGE_ENV, json.dumps({"version": 1, "global_max_concurrency": 2, "storage_bindings": [
        {"tenant_id": ids["tenant"], "bucket": "synthetic-native-snapshots", "kms_key_name":
         "projects/test/locations/asia-east1/keyRings/native/cryptoKeys/snapshots", "residency_mode": "TW_ONLY", "retention_class": "STANDARD"}]}))
    monkeypatch.setenv(SIGNING_KEY_ENV, "test-only-separate-retrieval-key-" * 2)
    policy = native_intake_retrieval_policy(ids["tenant"])
    job_id = str(uuid4())
    token = NativeIntakeRetrievalAuthenticator.sign(job_id, ids["tenant"], 1)
    with graph.transaction(ids["tenant"]):
        graph.queue.enqueue(JobRequest(JOB_TYPE, {"job_id": job_id, "tenant_id": ids["tenant"], "attempt_token": token}, job_id=job_id),
                            correlation_id=str(uuid4()))
    delivery = graph.queue.claim_next(worker_id="synthetic-retrieval-worker")
    assert delivery.job_id == job_id
    payload = {"delivery_generation": 1, "accepted_delivery_fence": delivery.fence_token,
               "accepted_worker": delivery.locked_by, "policy": policy.snapshot()}
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
            "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,lease_expires_at,payload,correlation_id,idempotency_key) "
            "VALUES (?,?,?,'intake',?,'RUNNING','RETRIEVING',1,2,2,clock_timestamp()+interval '120 seconds',?,?::jsonb,?,?)",
            (job_id, ids["tenant"], JOB_TYPE, ids["intake"], delivery.lease_expires_at.isoformat(),
             json.dumps(payload), delivery.correlation_id, job_id))
    controller = NativeRetrievalCompositeLeases(graph, delivery, business_fence=2, policy_sha256=policy.sha256)
    yield graph, ids, db, delivery, controller
    controller.pause()


def test_dual_heartbeat_rolls_back_physical_when_business_update_fails(leases_case, monkeypatch):
    graph, ids, _, delivery, controller = leases_case
    before = graph.queue.get(delivery.job_id)
    execute = graph.engine.execute

    def fail_business(sql, params=()):
        if sql.startswith("UPDATE workflow.jobs SET heartbeat_at"):
            raise RuntimeError("synthetic business write failure")
        return execute(sql, params)

    monkeypatch.setattr(graph.engine, "execute", fail_business)
    with pytest.raises(RuntimeError, match="synthetic business"):
        controller.start()
    assert graph.queue.get(delivery.job_id) == before
    with graph.transaction(ids["tenant"]):
        assert graph.engine.query_one("SELECT version FROM workflow.jobs WHERE job_id=?", (delivery.job_id,))["version"] == 1


def test_stop_join_prevents_heartbeat_inside_serializable_root_and_restart_reads_versions(leases_case, monkeypatch):
    graph, _, _, delivery, controller = leases_case
    monkeypatch.setattr("modules.listing.application.native_intake_retrieval_leases.HEARTBEAT_SECONDS", 0.02)
    controller.start()
    controller.guard()
    with controller.root():
        before = graph.queue.get(delivery.job_id)
        business = graph.engine.query_one("SELECT version FROM workflow.jobs WHERE job_id=?", (delivery.job_id,))["version"]
        graph.engine.query_one("SELECT pg_sleep(0.08)")
        assert graph.queue.get(delivery.job_id) == before
        assert graph.engine.query_one("SELECT version FROM workflow.jobs WHERE job_id=?", (delivery.job_id,))["version"] == business
    with pytest.raises(NativeIORejected, match="DUAL_LEASE_LOST"):
        controller.guard()
    controller.start()
    controller.guard()
    controller.pause()
    after = graph.queue.get(delivery.job_id)
    assert after.version > before.version and after.fence_token == before.fence_token


@pytest.mark.parametrize("sql", [
    "UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp()",
    "UPDATE workflow.jobs SET lease_expires_at=clock_timestamp()-interval '1 second'",
    "UPDATE workflow.jobs SET timeout_at=clock_timestamp()-interval '1 second'",
    "UPDATE workflow.jobs SET fence_token=fence_token+1",
    "UPDATE workflow.jobs SET payload=jsonb_set(payload,'{delivery_generation}','2')",
    "UPDATE odp_runtime.durable_jobs SET locked_by='foreign-worker'",
    "UPDATE odp_runtime.durable_jobs SET fence_token=fence_token+1",
    "UPDATE odp_runtime.durable_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'",
])
def test_current_cancel_expiry_and_fence_cannot_be_resurrected_by_heartbeat(leases_case, sql):
    graph, _, db, delivery, controller = leases_case
    with db.connect() as connection:
        connection.execute(sql)
    before = graph.queue.get(delivery.job_id)
    with pytest.raises(JobFenceRejectedError, match="DUAL_LEASE_NOT_OWNED"):
        controller.start()
    assert graph.queue.get(delivery.job_id) == before


def test_current_config_change_stops_renewal_and_never_grants_io(leases_case, monkeypatch):
    graph, _, _, delivery, controller = leases_case
    controller.start()
    controller.pause()
    policy = json.loads(__import__("os").environ[POLICY_ENV])
    monkeypatch.setenv(POLICY_ENV, json.dumps({**policy, "version": 2}))
    before = graph.queue.get(delivery.job_id)
    with pytest.raises(JobFenceRejectedError, match="DUAL_LEASE_NOT_OWNED"):
        controller.start()
    assert graph.queue.get(delivery.job_id) == before
    with pytest.raises(NativeIORejected):
        controller.guard()


def test_real_background_renewals_advance_both_versions_and_keep_same_actual_lease(leases_case, monkeypatch):
    graph, ids, _, delivery, controller = leases_case
    monkeypatch.setattr("modules.listing.application.native_intake_retrieval_leases.HEARTBEAT_SECONDS", 0.02)
    before = graph.queue.get(delivery.job_id)
    controller.start()
    wait_bound = time.monotonic() + 3
    while time.monotonic() < wait_bound:
        with controller._lock:
            proof = controller._proof
        if proof.physical_version >= before.version + 3:
            break
        time.sleep(0.01)
    controller.guard()
    controller.pause()
    with graph.transaction(ids["tenant"]):
        physical = graph.queue.get(delivery.job_id)
        business = graph._one("SELECT version,lease_expires_at FROM workflow.jobs WHERE tenant_id=? AND job_id=?",
                              (ids["tenant"], delivery.job_id))
    assert physical.version >= before.version + 3
    assert business["version"] == physical.version - 1
    assert datetime.fromisoformat(business["lease_expires_at"]) == physical.lease_expires_at


def test_actual_background_cancellation_stops_io_guard_without_renewing_a_revoked_job(leases_case, monkeypatch):
    graph, _, db, delivery, controller = leases_case
    monkeypatch.setattr("modules.listing.application.native_intake_retrieval_leases.HEARTBEAT_SECONDS", 0.02)
    controller.start()
    with db.connect() as connection:
        connection.execute("UPDATE workflow.jobs SET cancellation_requested_at=clock_timestamp() WHERE job_id=%s", (delivery.job_id,))
    assert controller._stop.wait(timeout=3)
    with pytest.raises(NativeIORejected, match="DUAL_LEASE_LOST"):
        controller.guard()
    controller.pause()
    with graph.transaction(controller.tenant):
        job = graph._one("SELECT status,cancellation_requested_at FROM workflow.jobs WHERE job_id=? AND tenant_id=?",
                         (delivery.job_id, controller.tenant))
    assert job["status"] == "RUNNING" and job["cancellation_requested_at"] is not None
