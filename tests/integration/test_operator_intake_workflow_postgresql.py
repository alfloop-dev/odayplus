"""Prove the production operator factory writes one durable V1 workflow owner."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes import listings
from apps.api.app.routes.operator import create_operator_router
from modules.opsboard.application import network_listings
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from shared.auth import Role
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from tests.intake_registry_fixture import (
    AT,
    MANAGER,
    OWNER,
    TENANT,
    _audit_retention_row,
    _calendar_row,
    _workflow_row,
)
from tests.integration.test_intake_registry_calendar_postgresql import (
    _bundle,
    _insert_policy,
    _seed_identities,
    _upgrade,
)

pytestmark = pytest.mark.requires_live_env
BASE = "/api/v1/operator/network-listings"


def _headers(actor=MANAGER, tenant=TENANT, key="synthetic-operator-submit"):
    return {
        "x-subject-id": actor, "x-tenant-id": tenant,
        "x-roles": "site_reviewer,expansion_user" if actor == MANAGER else "expansion_user",
        "x-operator-role": "expansion-manager" if actor == MANAGER else "expansion-staff",
        "x-correlation-id": str(uuid4()), "Idempotency-Key": key,
    }


def _client(bundle, *, workflow=True, policy=True):
    app = FastAPI()
    app.state.job_queue = bundle.job_queue
    app.include_router(create_operator_router(
        audit_log=bundle.audit_log, document_store=bundle.assisted_intake_store._store,
        listing_repository_for_tenant=bundle.listing_repository_for_tenant,
        sitescore_repository_for_tenant=lambda tenant: bundle._scoped_repository("sitescore_repository", tenant),
        sitescore_decision_repository_for_tenant=bundle.sitescore_decision_store_for_tenant,
        avm_repository_for_tenant=lambda tenant: bundle._scoped_repository("avm_repository", tenant),
        netplan_repository_for_tenant=lambda tenant: bundle._scoped_repository("netplan_repository", tenant),
        priceops_repository_for_tenant=lambda tenant: bundle._scoped_repository("priceops_repository", tenant),
        intake_workflow_store=bundle.assisted_intake_store if workflow else None,
        intake_workflow_policy=bundle.assisted_intake_workflow_policy if policy else None,
        live_repository=OperatorLiveRepository(bundle), require_live_data=True,
        persistence_mode="postgresql", provider_mode="live",
    ), prefix="/api/v1")
    app.include_router(listings.create_assisted_intake_router(
        bundle.assisted_intake_store, workflow_policy=bundle.assisted_intake_workflow_policy,
    ), prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def bridge_database(intake_blank_db, monkeypatch):
    db = intake_blank_db
    _upgrade(db, "head")
    apply_upgrade_to_database(db.url())
    policy = _workflow_row()
    parameters = deepcopy(policy.parameters)
    parameters["assignee_role_bindings"]["site-reviewer"] = Role.SITE_REVIEWER.value
    with db.connect() as conn:
        _seed_identities(conn)
        _insert_policy(conn, replace(policy, parameters=parameters))
        _insert_policy(conn, _calendar_row())
        _insert_policy(conn, _audit_retention_row())

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AT.astimezone(tz or UTC)

    monkeypatch.setattr(network_listings, "datetime", Clock)
    monkeypatch.setattr(listings, "datetime", Clock)
    return db


def _snapshot(bundle):
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    return deepcopy(tuple(getattr(store, field) for field in (
        "intakes", "assignments", "slas", "workflow_audits", "workflow_outbox", "replays",
    )))


def _synthetic_approved_policy(monkeypatch):
    # An isolated policy double exercises transaction/queue wiring only. It is
    # not an admitted live source, and neither fixture gate nor network is used.
    from modules.external_data.application import assisted_intake

    policy = replace(assisted_intake.resolve_source_policy("https://rent.591.com.tw/test"),
                     policy="APPROVED_RETRIEVAL", may_retrieve=True, quarantines=False)
    monkeypatch.setattr(assisted_intake, "resolve_source_policy", lambda _url: policy)

    def no_snapshot(_self):
        raise AssertionError("Submission/retry must never retrieve synchronously")

    monkeypatch.setattr(network_listings.NetworkListingService, "_get_snapshot_service", no_snapshot)


def _claim_intake_job(bundle, job_id):
    # Canonical audit delivery has higher queue priority. Claim those local
    # synthetic rows without executing any delivery, then take this intake job.
    for _ in range(10):
        job = bundle.job_queue.claim_next(worker_id="isolated-intake-test")
        assert job is not None
        if job.job_id == job_id:
            return job
        assert job.job_type == "canonical-audit-delivery"
    raise AssertionError("Intake job was not claimable")


def test_production_operator_submit_reads_and_transfers_same_canonical_workflow(bridge_database, monkeypatch):
    db = bridge_database
    bundle = _bundle(db, monkeypatch)
    try:
        client = _client(bundle)
        body = {"url": "https://rent.591.com.tw/bridge-real", "actorName": "spoof@example.invalid"}
        headers = _headers()
        submitted = client.post(f"{BASE}/intake/submit", json=body, headers=headers)
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        iid = record["id"]
        assert str(UUID(iid)) == iid
        assert record["submittedBy"] == MANAGER
        assert record["submittedAt"] == "2026-10-09T08:30:00Z"
        assert record["lastObservedAt"] is None
        assert record["assignmentStatus"] == "UNASSIGNED" and record["ownerSubjectId"] is None
        assert record["assignmentVersion"] == record["slaVersion"] == 1
        aid, sid = record["assignmentId"], record["slaInstanceId"]
        before = _snapshot(bundle)
        replay = client.post(f"{BASE}/intake/submit", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == record
        assert _snapshot(bundle) == before
        assert bundle.operator_intake_repository_for_tenant(TENANT).list_intakes() == []
        assert len(before[0]) == len(before[1]) == len(before[2]) == 1
        assert before[0][iid]["submitted_by"] == MANAGER
        assert before[1][aid]["intake_id"] == before[2][sid]["intake_id"] == iid
        assert record["dueAt"] == before[2][sid]["due_at"]
        assert "spoof@example.invalid" not in str(record)
        assignment = client.put(f"/api/v1/intakes/{iid}/assignment", json={
            "owner_subject_id": OWNER, "owner_role": "expansion-staff",
            "due_at": record["dueAt"], "reason": "Synthetic bridge assignment",
        }, headers={**_headers(key="synthetic-bridge-assign"), "If-Match": 'W/"1"'})
        assert assignment.status_code == 200, assignment.text
        av = assignment.json()["version"]
        transferred = client.post(f"/api/v1/assignments/{aid}/actions/transfer", json={
            "target_owner_subject_id": MANAGER, "target_owner_role": "site-reviewer",
            "handoff_note": "Synthetic verified handoff",
            "due_at": record["dueAt"], "reason": "Synthetic bridge transfer",
        }, headers={**_headers(OWNER, key="synthetic-bridge-transfer"), "If-Match": f'W/"{av}"'})
        assert transferred.status_code == 200, transferred.text
        final_version = transferred.json()["version"]
    finally:
        bundle.engine.close()
    bundle = _bundle(db, monkeypatch)
    try:
        client = _client(bundle)
        detail = client.get(f"{BASE}/intake/{iid}", headers=_headers())
        assert detail.status_code == 200, detail.text
        record = detail.json()
        assert record["assignmentId"] == aid and record["slaInstanceId"] == sid
        assert record["assignmentVersion"] == final_version
        assert record["ownerSubjectId"] == MANAGER
        page = client.get(f"{BASE}/intake", headers=_headers())
        assert page.status_code == 200, page.text
        rows = page.json()["items"]
        assert len(rows) == 1
        for key in ("id", "submittedBy", "submittedAt", "updatedAt", "lastObservedAt", "ownerSubjectId",
                    "assignmentId", "assignmentVersion", "assignmentStatus", "slaInstanceId", "slaVersion", "dueAt"):
            assert rows[0][key] == record[key]
        other = client.get(f"{BASE}/intake/{iid}", headers=_headers(tenant=str(uuid4())))
        assert other.status_code == 404, other.text
        assert "original_url" not in record and "operator_presentation" not in record
    finally:
        bundle.engine.close()


@pytest.mark.parametrize("failure", ["policy", "outbox", "replay"])
def test_operator_submission_rolls_back_workflow_replay_and_queue(bridge_database, monkeypatch, failure):
    _synthetic_approved_policy(monkeypatch)
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle, policy=failure != "policy")
        before = _snapshot(bundle)
        enqueued = []
        original_enqueue = bundle.job_queue.enqueue

        def enqueue(*args, **kwargs):
            result = original_enqueue(*args, **kwargs)
            if result[0].job_type == "assisted-listing-intake":
                enqueued.append(result[0].job_id)
            return result

        monkeypatch.setattr(bundle.job_queue, "enqueue", enqueue)
        with bridge_database.connect() as conn:
            jobs_before = conn.execute("SELECT count(*) FROM odp_runtime.durable_jobs").fetchone()[0]
        if failure != "policy":
            original = bundle.assisted_intake_store._store.put

            def fail(collection, key, value, **kwargs):
                if ((failure == "outbox" and collection == "assisted_intake.workflow_outbox")
                        or (failure == "replay" and collection.startswith("operator.idempotency_cache"))):
                    raise RuntimeError("synthetic bridge persistence failure")
                return original(collection, key, value, **kwargs)

            monkeypatch.setattr(bundle.assisted_intake_store._store, "put", fail)
        body = {"url": "https://www.synthetic.example/detail-999.html"}
        if failure == "policy":
            response = client.post(f"{BASE}/intake/submit", json=body, headers=_headers())
            assert response.status_code == 503, response.text
        else:
            with pytest.raises(RuntimeError, match="synthetic bridge persistence failure"):
                client.post(f"{BASE}/intake/submit", json=body, headers=_headers())
        assert _snapshot(bundle) == before
        assert len(enqueued) == 1
        assert bundle.operator_intake_repository_for_tenant(TENANT).list_intakes() == []
        assert bundle.operator_intake_repository_for_tenant(TENANT).list_idempotency_records() == []
        with bridge_database.connect() as conn:
            assert conn.execute("SELECT count(*) FROM odp_runtime.durable_jobs").fetchone()[0] == jobs_before
    finally:
        bundle.engine.close()


def test_linked_job_retry_after_restart_is_atomic_and_idempotent(bridge_database, monkeypatch):
    from shared.jobs.queue import JobStatus

    _synthetic_approved_policy(monkeypatch)
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://example.invalid/approved-double"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        iid, job_id = record["id"], record["jobId"]
        assert bundle.job_queue.get(job_id).payload["intake_id"] == iid
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        with store.transaction():
            store.intakes[iid]["state"] = "FAILED"
            store.intakes[iid]["version"] += 1
            store.flush()
        bundle.job_queue.update_status(job_id, JobStatus.FAILED)
    finally:
        bundle.engine.close()
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        headers = _headers(key="synthetic-linked-retry")
        response = client.post(f"{BASE}/intake/{iid}/retry", json={}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 3 and response.json()["stage"] == "SUBMITTED"
        assert response.json()["jobId"] == job_id
        job_after = bundle.job_queue.get(job_id)
        assert job_after.status == JobStatus.QUEUED
        after = _snapshot(bundle)
        replay = client.post(f"{BASE}/intake/{iid}/retry", json={}, headers=headers)
        assert replay.status_code == 200 and replay.json() == response.json()
        assert _snapshot(bundle) == after
        assert bundle.job_queue.get(job_id).version == job_after.version
    finally:
        bundle.engine.close()


def test_operator_workflow_store_unavailable_returns_503_on_reads(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle, workflow=False)
        for path in ("/intake", f"/intake/{uuid4()}"):
            response = client.get(BASE + path, headers=_headers())
            assert response.status_code == 503, response.text
            assert response.json()["detail"] == "INTAKE_WORKFLOW_STORE_UNAVAILABLE"
    finally:
        bundle.engine.close()


def test_production_worker_failure_is_visible_on_same_canonical_intake(bridge_database, monkeypatch):
    from types import SimpleNamespace

    from apps.worker.assisted_listing_intake.worker import handle_assisted_listing_intake
    from modules.external_data.application import source_snapshots
    from shared.jobs.queue import NonRetryableJobError

    _synthetic_approved_policy(monkeypatch)
    # Constructor double only: the real security gate must still reject the
    # worker's legacy fixture transport, without network or object-store use.
    monkeypatch.setattr(source_snapshots, "build_source_snapshot_service", lambda *_: SimpleNamespace(
        check_source_policy=lambda *_args: "APPROVED_RETRIEVAL",
    ))
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://example.invalid/no-live-adapter"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        job = _claim_intake_job(bundle, record["jobId"])
        assert job is not None and job.job_id == record["jobId"]
        # Synthetic authenticated submission above; production retrieval gate
        # below. Production HTTP authentication is deliberately not weakened.
        with monkeypatch.context() as production:
            production.setenv("ODP_PRODUCT_MODE", "production")
            production.setenv("EXTERNAL_PROVIDER_MODE", "live")
            with pytest.raises(NonRetryableJobError):
                handle_assisted_listing_intake(job, bundle)
        detail = client.get(f"{BASE}/intake/{record['id']}", headers=_headers())
        assert detail.status_code == 200, detail.text
        result = detail.json()
        assert result["stage"] == "FAILED" and result["version"] == record["version"] + 1
        assert result["failure"]["code"] == "ODP-INTAKE-SYNTHETIC-FIXTURE-DISABLED"
        assert result["failure"]["retryable"] is False
        assert result["lastObservedAt"] is None and result["snapshotId"] is None
        assert result["assignmentId"] == record["assignmentId"]
        assert result["slaInstanceId"] == record["slaInstanceId"]
    finally:
        bundle.engine.close()


def test_concurrent_submission_replays_one_workflow(bridge_database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    bundle = _bundle(bridge_database, monkeypatch)
    replica = _bundle(bridge_database, monkeypatch)
    try:
        clients = [_client(bundle), _client(replica)]
        barrier = Barrier(2)
        headers = _headers()

        def submit(index):
            barrier.wait(timeout=10)
            return clients[index].post(f"{BASE}/intake/submit", json={"url": "https://rent.591.com.tw/concurrent"},
                                       headers=headers)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        assert all(result.status_code == 200 for result in results), [r.text for r in results]
        assert results[0].json() == results[1].json()
        state = _snapshot(bundle)
        assert len(state[0]) == len(state[1]) == len(state[2]) == 1
        assert len(bundle.operator_intake_repository_for_tenant(TENANT).list_idempotency_records()) == 1
    finally:
        bundle.engine.close()
        replica.engine.close()


def test_operator_correction_and_decision_versions_survive_replay(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://rent.591.com.tw/edits"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        initial = submitted.json()
        iid = initial["id"]
        correction_body = {"fields": {"floor": "2"}, "reason": "Synthetic correction",
                           "riskSummary": "Synthetic non-identity edit", "riskAcknowledged": True}
        correction_headers = _headers(key="synthetic-bridge-correction")
        corrected = client.post(f"{BASE}/intake/{iid}/correct", json=correction_body, headers=correction_headers)
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["version"] == 2
        replay = client.post(f"{BASE}/intake/{iid}/correct", json=correction_body, headers=correction_headers)
        assert replay.status_code == 200 and replay.json() == corrected.json()
        decision_body = {"action": "reject", "reason": "Synthetic rejected source evidence",
                         "riskSummary": "Synthetic decision", "riskAcknowledged": True}
        # A distinct reviewer preserves the existing segregation-of-duty rule.
        from tests.intake_registry_fixture import APPROVER

        with bridge_database.connect() as conn:
            for role in (Role.SITE_REVIEWER, Role.EXPANSION_USER):
                conn.execute("INSERT INTO identity.account_roles(account_id,role,granted_by) VALUES (%s,%s,%s)",
                             (APPROVER, role.value, "isolated-synthetic-test"))
        decision_headers = {**_headers(APPROVER, key="synthetic-bridge-decision"),
                            "x-roles": "site_reviewer,expansion_user", "x-operator-role": "expansion-manager"}
        decided = client.post(f"{BASE}/intake/{iid}/decide", json=decision_body, headers=decision_headers)
        assert decided.status_code == 200, decided.text
        assert decided.json()["version"] == 3 and decided.json()["stage"] == "FAILED"
        replay = client.post(f"{BASE}/intake/{iid}/decide", json=decision_body, headers=decision_headers)
        assert replay.status_code == 200 and replay.json() == decided.json()
        for key in ("assignmentId", "slaInstanceId"):
            assert decided.json()[key] == initial[key]
        assert _snapshot(bundle)[0][iid]["version"] == 3
    finally:
        bundle.engine.close()


def test_parent_authorization_lock_serializes_v1_owner_change(bridge_database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from shared.infrastructure.persistence.operator_intake_workflow import (
        OperatorIntakeWorkflowRepository,
    )

    bundle = _bundle(bridge_database, monkeypatch)
    replica = _bundle(bridge_database, monkeypatch)
    try:
        submitted = _client(bundle).post(f"{BASE}/intake/submit", json={"url": "https://rent.591.com.tw/locked"},
                                         headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        iid = record["id"]
        repo = OperatorIntakeWorkflowRepository(bundle.assisted_intake_store,
            bundle.operator_intake_repository_for_tenant(TENANT), TENANT, bundle.assisted_intake_workflow_policy)
        reached_parent = Event()
        original_lock = replica.assisted_intake_store._read_locked

        def observed_lock(collection, key):
            if collection == "operator.assisted_intakes" and key == f"{TENANT}:{iid}":
                reached_parent.set()
            return original_lock(collection, key)

        monkeypatch.setattr(replica.assisted_intake_store, "_read_locked", observed_lock)
        client = _client(replica)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with repo.transaction(intake_id=iid):
                future = pool.submit(client.put, f"/api/v1/intakes/{iid}/assignment", json={
                    "owner_subject_id": OWNER, "owner_role": "expansion-staff",
                    "due_at": record["dueAt"], "reason": "Synthetic serialized ownership change",
                }, headers={**_headers(key="synthetic-locked-assignment"), "If-Match": 'W/"1"'})
                assert reached_parent.wait(timeout=5)
                assert not future.done(), "V1 writer committed across the authorization row lock"
                assert repo.get_intake(iid)["ownerSubjectId"] is None
            response = future.result(timeout=5)
        assert response.status_code == 200, response.text
        assert repo.get_intake(iid)["ownerSubjectId"] == OWNER
    finally:
        bundle.engine.close()
        replica.engine.close()


def test_stale_worker_cannot_publish_after_job_fence_changes(bridge_database, monkeypatch):
    from types import SimpleNamespace

    from apps.worker.assisted_listing_intake.worker import handle_assisted_listing_intake
    from modules.external_data.application import assisted_intake, source_snapshots
    from modules.external_data.security import assisted_listing_retrieval
    from shared.jobs.queue import JobFenceRejectedError

    _synthetic_approved_policy(monkeypatch)
    snapshot = SimpleNamespace(create_snapshot=lambda **_: "synthetic-snapshot")
    monkeypatch.setattr(source_snapshots, "build_source_snapshot_service", lambda *_: snapshot)
    monkeypatch.setattr(assisted_listing_retrieval, "RetrievalSecurityGate", lambda **_: SimpleNamespace(
        fetch=lambda *_args, **_kwargs: SimpleNamespace(ok=True, body=b'{}'),
    ))
    monkeypatch.setattr(assisted_intake, "parse_snapshot", lambda _: {
        key: {"normalizedValue": value, "correctedValue": None}
        for key, value in {"address": "Synthetic address", "rent": 10, "areaPing": 12}.items()
    })
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = _client(bundle)
        submitted = client.post(f"{BASE}/intake/submit", json={"url": "https://example.invalid/fence-double"},
                                headers=_headers())
        assert submitted.status_code == 200, submitted.text
        record = submitted.json()
        job = _claim_intake_job(bundle, record["jobId"])
        before = _snapshot(bundle)

        def match(**_):
            # Represent another claimant acquiring the lease while MATCHING
            # was computing. The Intake version itself has not changed.
            bundle.engine.execute("UPDATE durable_jobs SET fence_token=fence_token+1 WHERE job_id=?", (job.job_id,))
            return assisted_intake.MatchResult(
                outcome="NEW", confidence=1.0, target_listing_id=None,
                signals=(), summary="Synthetic new listing after lease change",
            )

        monkeypatch.setattr(assisted_intake, "match_listing", match)
        with pytest.raises(JobFenceRejectedError):
            handle_assisted_listing_intake(job, bundle)
        assert _snapshot(bundle) == before
        detail = client.get(f"{BASE}/intake/{record['id']}", headers=_headers()).json()
        assert detail["stage"] == "SUBMITTED" and detail["lastObservedAt"] is None
    finally:
        bundle.engine.close()
