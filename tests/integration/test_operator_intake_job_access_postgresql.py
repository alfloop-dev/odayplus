"""Canonical authority and transactional retry for the generic single-job API."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.jobs import JobRequest, JobStatus
from tests.intake_registry_fixture import MANAGER, OWNER, TENANT
from tests.integration.test_intake_registry_calendar_postgresql import _bundle
from tests.integration.test_operator_intake_workflow_postgresql import (
    BASE,
    _client,
    _headers,
    _snapshot,
    _synthetic_approved_policy,
    bridge_database,
)

pytestmark = pytest.mark.requires_live_env
# Explicit fixture import makes the isolated database setup available here.
assert bridge_database


def _failed_submission(bundle, monkeypatch):
    _synthetic_approved_policy(monkeypatch)
    response = _client(bundle).post(
        f"{BASE}/intake/submit", json={"url": "https://example.invalid/single-job"},
        headers=_headers(),
    )
    assert response.status_code == 200, response.text
    record = response.json()
    store = bundle.assisted_intake_store
    store.refresh(TENANT)
    with store.transaction():
        store.intakes[record["id"]]["state"] = "FAILED"
        store.intakes[record["id"]]["version"] += 1
        store.flush()
    bundle.job_queue.update_status(record["jobId"], JobStatus.FAILED)
    return record


def test_single_job_read_and_retry_use_canonical_owner_and_complete_scope(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        record = _failed_submission(bundle, monkeypatch)
        job_id, iid = record["jobId"], record["id"]
        client = TestClient(create_app(persistence=bundle))
        before, job_before = _snapshot(bundle), bundle.job_queue.get(job_id).to_dict()
        for headers, expected in (
            (_headers(OWNER), 403),  # Same tenant, unrelated staff.
            ({**_headers(), "x-region-ids": "other-region"}, 403),
            ({**_headers(), "x-assigned-area-ids": "other-area"}, 403),
            ({**_headers(), "x-store-ids": "other-store"}, 403),
            ({**_headers(), "x-heat-zone-ids": "other-zone"}, 403),
            (_headers(tenant=str(uuid4())), 404),
        ):
            for prefix in ("/jobs", "/api/v1/jobs"):
                read = client.get(f"{prefix}/{job_id}", headers=headers)
                assert read.status_code == expected, read.text
                retry = client.post(f"{prefix}/{job_id}/retries", headers=headers)
                assert retry.status_code == expected, retry.text
        assert _snapshot(bundle) == before
        assert bundle.job_queue.get(job_id).to_dict() == job_before
        allowed = client.get(f"/api/v1/jobs/{job_id}", headers=_headers())
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["job_id"] == job_id
        # A payload-supplied owner/scope cannot replace the canonical record.
        job = bundle.job_queue.get(job_id)
        bundle.job_queue.update_status(job_id, JobStatus.FAILED, payload={
            **job.payload, "owner": OWNER, "submitter": OWNER,
            "regionId": "other-region", "assignedAreaId": "other-area",
        })
        assert client.get(f"/api/v1/jobs/{job_id}", headers=_headers(OWNER)).status_code == 403
        assert client.get(f"/api/v1/jobs/{job_id}", headers=_headers()).status_code == 200
        # The actual assigned subject can also read the same canonical job.
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        with store.transaction():
            assignment = store.assignments[store.intakes[iid]["assignment_id"]]
            assignment["owner_subject_id"] = OWNER
            assignment["status"] = "ASSIGNED"
            assignment["version"] += 1
            store.flush()
        assert client.get(f"/api/v1/jobs/{job_id}", headers=_headers(OWNER)).status_code == 200
        # Reverse link is required; a copied payload is not a second authority.
        forged, _ = bundle.job_queue.enqueue(JobRequest(
            job_type="assisted-listing-intake", payload=deepcopy(job.payload),
            idempotency_key="synthetic-forged-single-job",
        ), correlation_id=str(uuid4()))
        for path in (f"/api/v1/jobs/{forged.job_id}", f"/jobs/{forged.job_id}"):
            assert client.get(path, headers=_headers()).status_code == 404
            assert client.post(path + "/retries", headers=_headers()).status_code == 404
        bundle.job_queue.update_status(job_id, JobStatus.FAILED, payload={
            **job.payload, "intake_id": str(uuid4()),
        })
        assert client.get(f"/api/v1/jobs/{job_id}", headers=_headers()).status_code == 404
    finally:
        bundle.engine.close()


def test_single_job_retry_uses_the_operator_transaction_and_actor_bound_replay(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        record = _failed_submission(bundle, monkeypatch)
        iid, job_id = record["id"], record["jobId"]
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        with store.transaction():
            store.intakes[iid]["scope"]["store_id"] = "permitted-store"
            store.intakes[iid]["version"] += 1
            store.flush()
    finally:
        bundle.engine.close()
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        client = TestClient(create_app(persistence=bundle))
        headers = {**_headers(key="synthetic-generic-single-job-retry"), "x-store-ids": "permitted-store"}
        missing_key = {key: value for key, value in headers.items() if key != "Idempotency-Key"}
        assert client.post(f"/api/v1/jobs/{job_id}/retries", headers=missing_key).status_code == 422
        assert client.post(f"/api/v1/jobs/{job_id}/retries", headers=headers,
                           json={"retry_scope": "INVALID"}).status_code == 422
        before = _snapshot(bundle)
        response = client.post(f"/api/v1/jobs/{job_id}/retries", headers=headers,
                               json={"retry_scope": "FAILED_ONLY"})
        assert response.status_code == 202, response.text
        assert response.json() == {
            "job_id": job_id, "status": "queued", "retry_scope": "FAILED_ONLY", "retried_items_count": 1,
        }
        job_after = bundle.job_queue.get(job_id)
        after = _snapshot(bundle)
        assert job_after.status == JobStatus.QUEUED
        assert after[0][iid]["state"] == "SUBMITTED" and after[0][iid]["version"] == 4
        assert after[0][iid]["assignment_id"] == before[0][iid]["assignment_id"]
        assert after[0][iid]["sla_instance_id"] == before[0][iid]["sla_instance_id"]
        assert after[0][iid]["operator_presentation"]["auditEvents"][-1]["actorName"] == MANAGER
        replay = client.post(f"/jobs/{job_id}/retries", headers=headers)
        assert replay.status_code == 202 and replay.json() == response.json()
        assert _snapshot(bundle) == after
        assert bundle.job_queue.get(job_id).to_dict() == job_after.to_dict()
        # The operator endpoint shares the exact persisted replay, not a second command.
        operator_replay = _client(bundle).post(f"{BASE}/intake/{iid}/retry", headers=headers, json={})
        assert operator_replay.status_code == 200, operator_replay.text
        assert operator_replay.json()["version"] == 4
        assert _snapshot(bundle) == after
        assert bundle.job_queue.get(job_id).to_dict() == job_after.to_dict()
        fresh = client.post(f"/api/v1/jobs/{job_id}/retries", headers=_headers(key="new-key"))
        assert fresh.status_code == 409, fresh.text
    finally:
        bundle.engine.close()


def test_single_job_retry_rolls_back_queue_and_intake_when_receipt_write_fails(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        record = _failed_submission(bundle, monkeypatch)
        client = TestClient(create_app(persistence=bundle))
        job_id = record["jobId"]
        before, job_before = _snapshot(bundle), bundle.job_queue.get(job_id).to_dict()
        original = bundle.assisted_intake_store._store.put

        def fail(collection, key, value, **kwargs):
            if collection.startswith("operator.idempotency_cache"):
                raise RuntimeError("synthetic generic retry receipt failure")
            return original(collection, key, value, **kwargs)

        monkeypatch.setattr(bundle.assisted_intake_store._store, "put", fail)
        with pytest.raises(RuntimeError, match="synthetic generic retry receipt failure"):
            client.post(f"/api/v1/jobs/{job_id}/retries", headers=_headers(key="synthetic-rollback"))
        assert _snapshot(bundle) == before
        assert bundle.job_queue.get(job_id).to_dict() == job_before
    finally:
        bundle.engine.close()


def test_single_job_retry_refuses_nonretryable_and_active_linked_jobs(bridge_database, monkeypatch):
    bundle = _bundle(bridge_database, monkeypatch)
    try:
        record = _failed_submission(bundle, monkeypatch)
        client = TestClient(create_app(persistence=bundle))
        job_id, iid = record["jobId"], record["id"]
        for state in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.CANCELLED):
            bundle.job_queue.update_status(job_id, state)
            before, job_before = _snapshot(bundle), bundle.job_queue.get(job_id).to_dict()
            response = client.post(f"/api/v1/jobs/{job_id}/retries", headers=_headers(key=f"reject-{state.value}"))
            assert response.status_code == 409, response.text
            assert _snapshot(bundle) == before
            assert bundle.job_queue.get(job_id).to_dict() == job_before
        bundle.job_queue.update_status(job_id, JobStatus.FAILED)
        store = bundle.assisted_intake_store
        store.refresh(TENANT)
        with store.transaction():
            store.intakes[iid]["operator_presentation"]["failure"] = {
                "code": "PERMANENT_SYNTHETIC_FAILURE", "retryable": False,
            }
            store.intakes[iid]["version"] += 1
            store.flush()
        before, job_before = _snapshot(bundle), bundle.job_queue.get(job_id).to_dict()
        for scope in ("FAILED_ONLY", "FAILED_RETRYABLE_ONLY"):
            response = client.post(f"/api/v1/jobs/{job_id}/retries", headers=_headers(key=scope),
                                   json={"retry_scope": scope})
            assert response.status_code == 409, response.text
            assert _snapshot(bundle) == before
            assert bundle.job_queue.get(job_id).to_dict() == job_before
    finally:
        bundle.engine.close()


def test_generic_and_operator_retry_recheck_fresh_ownership_and_scope_before_write(bridge_database, monkeypatch):
    from modules.opsboard.application.network_listings import NetworkListingService

    bundle = _bundle(bridge_database, monkeypatch)
    try:
        record = _failed_submission(bundle, monkeypatch)
        generic = TestClient(create_app(persistence=bundle))
        operator = _client(bundle)
        iid, job_id = record["id"], record["jobId"]
        store = bundle.assisted_intake_store
        original = NetworkListingService.retry_intake
        revoked = {}

        def revoke_after_route_authorization(service, *args, **kwargs):
            store.refresh(TENANT)
            with store.transaction():
                canonical = store.intakes[iid]
                if revoked["axis"] == "owner":
                    assignment = store.assignments[canonical["assignment_id"]]
                    assignment["owner_subject_id"] = MANAGER
                    assignment["version"] += 1
                else:
                    canonical["scope"]["heat_zone_id"] = "revoked-zone"
                canonical["version"] += 1
                store.flush()
            revoked["snapshot"] = _snapshot(bundle)
            return original(service, *args, **kwargs)

        monkeypatch.setattr(NetworkListingService, "retry_intake", revoke_after_route_authorization)
        for client, path in ((generic, f"/api/v1/jobs/{job_id}/retries"),
                             (operator, f"{BASE}/intake/{iid}/retry")):
            for axis in ("owner", "scope"):
                store.refresh(TENANT)
                with store.transaction():
                    canonical = store.intakes[iid]
                    canonical["scope"]["heat_zone_id"] = "permitted-zone"
                    canonical["version"] += 1
                    assignment = store.assignments[canonical["assignment_id"]]
                    assignment["owner_subject_id"] = OWNER
                    assignment["status"] = "ASSIGNED"
                    assignment["version"] += 1
                    store.flush()
                revoked["axis"] = axis
                job_before = bundle.job_queue.get(job_id).to_dict()
                response = client.post(path, json={}, headers={
                    **_headers(OWNER, key=f"fresh-{axis}"), "x-heat-zone-ids": "permitted-zone",
                })
                assert response.status_code == 403, response.text
                assert _snapshot(bundle) == revoked["snapshot"]
                assert bundle.job_queue.get(job_id).to_dict() == job_before
    finally:
        bundle.engine.close()
