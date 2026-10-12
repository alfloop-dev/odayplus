"""Scoped HTTP writes and Today readbacks against isolated actual PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules.store_ops import create_operator_store_ops_router
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from modules.opsboard.application.store_ops import (
    DurableStoreOpsRepository,
    InMemoryStoreOpsRepository,
)
from modules.opsboard.application.store_ops_audit_delivery import COLLECTION
from modules.opsboard.application.store_ops_scope import binding_hash
from shared.domain import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import apply_upgrade_to_database
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from tests.integration.test_intake_registry_calendar_postgresql import _bundle, _upgrade

pytestmark = pytest.mark.requires_live_env
BASE = "/api/v1/operator/store-ops"


def _client(bundle):
    app = FastAPI()

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        request.state.correlation_id = request.headers.get(
            "x-correlation-id", "synthetic-store-ops"
        )
        return await call_next(request)

    app.include_router(
        create_operator_store_ops_router(
            repository=bundle.store_ops_repository,
            store_repository=bundle.store_repository,
            delivery_job_queue=bundle.job_queue,
            audit_log=bundle.audit_log,
            require_live_data=True,
        ),
        prefix="/api/v1",
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def case(intake_blank_db, monkeypatch, tmp_path):
    _upgrade(intake_blank_db, "head")
    apply_upgrade_to_database(intake_blank_db.url())
    monkeypatch.setenv("ODP_AUDIT_WORM_SINK_URI", (tmp_path / "synthetic-worm").as_uri())
    bundle = _bundle(intake_blank_db, monkeypatch)
    tenant_a, tenant_b, subject = [str(uuid4()) for _ in range(3)]
    brand_a, brand_b, address = [str(uuid4()) for _ in range(3)]
    store_a, store_hidden, store_foreign = [str(uuid4()) for _ in range(3)]
    issue_a, issue_hidden, issue_foreign = [str(uuid4()) for _ in range(3)]
    bundle.address_location_repository.save_address(
        AddressLocation(address_id=address, raw_address="Synthetic scope test")
    )
    for tenant, brand in ((tenant_a, brand_a), (tenant_b, brand_b)):
        bundle.tenant_repository.save_tenant(
            Tenant(tenant_id=tenant, tenant_name="Synthetic tenant")
        )
        bundle.brand_repository.save_brand(
            Brand(brand_id=brand, tenant_id=tenant, brand_code=brand, brand_name="Synthetic brand")
        )
    for store, tenant, brand, region in (
        (store_a, tenant_a, brand_a, "north"),
        (store_hidden, tenant_a, brand_a, "south"),
        (store_foreign, tenant_b, brand_b, "north"),
    ):
        bundle.store_repository.save_store(
            Store(
                store_id=store,
                tenant_id=tenant,
                brand_id=brand,
                address_id=address,
                region_code=region,
                store_name="Synthetic store",
                store_status="open",
            )
        )

    def issue(key, store):
        return {
            "id": key,
            "title": "Synthetic issue",
            "storeId": store,
            "storeName": "Synthetic store",
            "status": "new",
            "severity": "high",
            "source": "payment",
            "ownerRoleId": "opsLead",
            "ownerName": "",
            "slaDueAt": "2026-01-01T00:00:00Z",
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
            "evidenceIds": [],
            "summary": "Synthetic facts",
        }

    def state(rows):
        return {
            "stores": [
                {
                    "id": row["storeId"],
                    "name": "Synthetic store",
                    "lights": {
                        "demand": "green",
                        "operations": "red",
                        "staffing": "green",
                        "margin": "green",
                    },
                }
                for row in rows
            ],
            "issues": rows,
            "evidence": [],
            "auditEvents": [],
            "nextAuditOrdinal": 1,
        }

    documents = PostgresDocumentStore(bundle.engine)
    repo_a = DurableStoreOpsRepository(TenantScopedDocumentStore(documents, tenant_a))
    repo_b = DurableStoreOpsRepository(TenantScopedDocumentStore(documents, tenant_b))
    repo_a.save_state(state([issue(issue_a, store_a), issue(issue_hidden, store_hidden)]))
    repo_b.save_state(state([issue(issue_foreign, store_foreign)]))
    # Poison the legacy global aggregate; live reads/writes must ignore it.
    global_state = state([issue(str(uuid4()), store_a)])
    global_state["issues"][0]["title"] = "legacy global secret"
    bundle.store_ops_repository.save_state(global_state)
    headers = {
        "x-subject-id": subject,
        "x-tenant-id": tenant_a,
        "x-roles": "operations_manager",
        "x-store-ids": store_a,
        "x-region-ids": "north",
        "x-brand-ids": brand_a,
        "x-operator-role": "ops-lead",
        "Idempotency-Key": "synthetic-transition",
    }
    try:
        yield {
            "db": intake_blank_db,
            "bundle": bundle,
            "repo": repo_a,
            "foreign_repo": repo_b,
            "headers": headers,
            "tenant": tenant_a,
            "subject": subject,
            "store": store_a,
            "hidden_store": store_hidden,
            "issue": issue_a,
            "hidden_issue": issue_hidden,
            "foreign_issue": issue_foreign,
            "global_state": global_state,
            "monkeypatch": monkeypatch,
        }
    finally:
        bundle.engine.close()


def _transition(case, **body):
    return _client(case["bundle"]).post(
        f"{BASE}/issues/{case['issue']}/triage",
        json={"severity": "critical", **body},
        headers=case["headers"],
    )


def test_http_write_scoped_read_today_and_restart_share_the_committed_state(case):
    hidden_before = deepcopy(case["repo"].get_state()["issues"][1])
    response = _transition(case, actorName="forged actor", actorRoleId="auditPm")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["issue"]["status"] == "triaged"
    assert [row["id"] for row in body["issues"]] == [case["issue"]]
    assert body["auditEvent"]["actorName"] == case["subject"]
    assert body["auditEvent"]["actorRoleId"] == "opsLead"
    assert case["repo"].get_state()["issues"][1] == hidden_before
    assert case["bundle"].store_ops_repository.get_state() == case["global_state"]
    read = _client(case["bundle"]).get(f"{BASE}/issues/{case['issue']}", headers=case["headers"])
    assert read.json()["issue"]["severity"] == "critical"
    state = OperatorLiveRepository(case["bundle"]).load_state(
        tenant_id=case["tenant"], store_ids=(case["store"],)
    )
    cards = {card["label"]: card["value"] for card in state["kpis"]}
    assert cards["高風險未指派"] == "1"
    task = next(row for row in state["workQueue"] if row["id"] == case["issue"])
    assert task["status"] == "triaged"
    assert task["target"] == {"workspace": "store", "entityId": case["issue"], "tab": "assign"}
    events = [
        event
        for event in case["bundle"].audit_log.list_events(tenant_id=case["tenant"])
        if event.event_type == "operator.store_ops.issue_transition"
    ]
    assert len(events) == 1
    assert events[0].actor == case["subject"]
    assert events[0].metadata["actor_subject_id"] == case["subject"]
    assert body["auditDelivery"]["status"] == "delivered"
    queued = case["repo"]._store.get(COLLECTION, events[0].event_id)
    assert queued["status"] == "delivered"
    assert queued["receipt"]["object_uri"].startswith("file://")
    reopened = _bundle(case["db"], case["monkeypatch"])
    try:
        after = _client(reopened).get(f"{BASE}/issues/{case['issue']}", headers=case["headers"])
        assert after.status_code == 200
        assert after.json()["issue"]["status"] == "triaged"
    finally:
        reopened.engine.close()


def test_current_scope_denies_foreign_issue_and_preserves_all_business_state(case):
    original, foreign = case["repo"].get_state(), case["foreign_repo"].get_state()
    client = _client(case["bundle"])
    for issue in (case["hidden_issue"], case["foreign_issue"]):
        response = client.post(
            f"{BASE}/issues/{issue}/triage", json={"severity": "critical"}, headers=case["headers"]
        )
        assert response.status_code == 404, response.text
    assert case["repo"].get_state() == original
    assert case["foreign_repo"].get_state() == foreign


def test_replay_is_payload_actor_and_current_scope_bound(case):
    assert _transition(case).status_code == 200
    replay = _transition(case)
    assert replay.status_code == 200
    assert replay.json()["idempotentReplay"] is True
    assert len(case["repo"].get_state()["auditEvents"]) == 1
    assert _transition(case, severity="low").status_code == 409
    actor = {**case["headers"], "x-subject-id": str(uuid4())}
    response = _client(case["bundle"]).post(
        f"{BASE}/issues/{case['issue']}/triage", json={"severity": "critical"}, headers=actor
    )
    assert response.status_code == 409  # actual lifecycle conflict, not another actor's replay
    reduced = {**case["headers"], "x-store-ids": case["hidden_store"], "x-region-ids": "south"}
    response = _client(case["bundle"]).post(
        f"{BASE}/issues/{case['issue']}/triage", json={"severity": "critical"}, headers=reduced
    )
    assert response.status_code == 404


def test_audit_failure_rolls_back_state_and_idempotency(case):
    original = case["repo"].get_state()
    real_record = case["bundle"].audit_log.record

    def fail_business_audit(event):
        if event.event_type == "operator.store_ops.issue_transition":
            raise RuntimeError("synthetic audit failure")
        return real_record(event)

    case["monkeypatch"].setattr(case["bundle"].audit_log, "record", fail_business_audit)
    response = _transition(case)
    assert response.status_code == 503, response.text
    assert case["repo"].get_state() == original
    key = binding_hash(
        [case["subject"], "store_ops.transition", case["headers"]["Idempotency-Key"]]
    )
    assert case["repo"].get_idempotency_result(key) is None
    case["monkeypatch"].setattr(case["bundle"].audit_log, "record", real_record)
    assert _transition(case).status_code == 200


def test_receipt_failure_rolls_back_written_state_and_business_audit(case):
    original = case["repo"].get_state()
    real_save = DurableStoreOpsRepository.save_idempotency_result

    def fail_receipt(self, key, result):
        raise RuntimeError("synthetic receipt failure")

    case["monkeypatch"].setattr(DurableStoreOpsRepository, "save_idempotency_result", fail_receipt)
    response = _transition(case)
    assert response.status_code == 500
    assert case["repo"].get_state() == original
    events = [
        event
        for event in case["bundle"].audit_log.list_events(tenant_id=case["tenant"])
        if event.event_type == "operator.store_ops.issue_transition"
    ]
    assert events == []
    assert case["repo"]._store.list_all(COLLECTION) == []
    assert case["bundle"].engine.query_one(
        "SELECT COUNT(*) AS count FROM durable_jobs WHERE job_type = ?",
        ("store-ops-audit-delivery",),
    )["count"] == 0
    case["monkeypatch"].setattr(DurableStoreOpsRepository, "save_idempotency_result", real_save)
    assert _transition(case).status_code == 200


def test_worm_outage_keeps_committed_state_and_restart_replay_delivers(case):
    sink = case["bundle"].audit_log._worm_sink
    real_write = sink.write_audit_event

    def unavailable(event):
        raise RuntimeError("synthetic external sink outage")

    case["monkeypatch"].setattr(sink, "write_audit_event", unavailable)
    response = _transition(case)
    assert response.status_code == 200
    assert response.json()["auditDelivery"]["status"] == "pending"
    assert response.json()["auditDelivery"]["pending"] == 1
    assert case["repo"].get_state()["issues"][0]["status"] == "triaged"
    case["monkeypatch"].setattr(sink, "write_audit_event", real_write)
    reopened = _bundle(case["db"], case["monkeypatch"])
    try:
        response = _client(reopened).post(
            f"{BASE}/issues/{case['issue']}/triage",
            json={"severity": "critical"},
            headers=case["headers"],
        )
        assert response.status_code == 200, response.text
        assert response.json()["idempotentReplay"] is True
        assert response.json()["auditDelivery"]["status"] == "delivered"
        assert len(case["repo"].get_state()["auditEvents"]) == 1
    finally:
        reopened.engine.close()


def test_interrupted_delivery_recovers_verified_existing_object_without_overwrite(case):
    sink = case["bundle"].audit_log._worm_sink
    real_write = sink.write_audit_event

    def write_then_interrupt(event):
        receipt = real_write(event)
        if event.event_type == "operator.store_ops.issue_transition":
            raise RuntimeError("synthetic interruption after object creation")
        return receipt

    case["monkeypatch"].setattr(sink, "write_audit_event", write_then_interrupt)
    first = _transition(case)
    assert first.status_code == 200
    assert first.json()["auditDelivery"]["pending"] == 1

    def refuse_business_overwrite(event):
        if event.event_type == "operator.store_ops.issue_transition":
            pytest.fail("existing object must not be overwritten")
        return real_write(event)

    case["monkeypatch"].setattr(sink, "write_audit_event", refuse_business_overwrite)
    replay = _transition(case)
    assert replay.status_code == 200
    assert replay.json()["auditDelivery"]["status"] == "delivered"


def test_clearance_change_cannot_replay_an_old_snapshot(case):
    assert _transition(case).status_code == 200
    headers = {**case["headers"], "x-clearance": "INTERNAL"}
    response = _client(case["bundle"]).post(
        f"{BASE}/issues/{case['issue']}/triage", json={"severity": "critical"}, headers=headers
    )
    assert response.status_code == 403, response.text


def test_missing_tenant_materialization_is_not_seeded_from_global_or_fixture(case):
    case["repo"]._store.delete_collection("opsboard.store_ops.state")
    response = _client(case["bundle"]).get(f"{BASE}/summary", headers=case["headers"])
    assert response.status_code == 503
    assert (
        response.json()["detail"]["message"]
        == "Store Ops production state has not been materialized"
    )
    assert case["repo"]._store.get("opsboard.store_ops.state", "default") is None
    assert case["bundle"].store_ops_repository.get_state() == case["global_state"]


def test_materialized_tenant_fixture_is_rejected_before_any_mutation(case):
    fixture = InMemoryStoreOpsRepository().get_state()
    case["repo"]._store.put("opsboard.store_ops.state", "default", fixture)
    response = _transition(case)
    assert response.status_code == 503
    assert "canonical fixture identifiers" in response.json()["detail"]["message"]
    assert case["repo"]._store.get("opsboard.store_ops.state", "default") == fixture


def test_concurrent_scoped_writes_preserve_both_stores_and_audit_ordinals(case):
    first_headers = {**case["headers"], "Idempotency-Key": "first-concurrent"}
    second_headers = {**case["headers"], "x-subject-id": str(uuid4()), "x-store-ids": case["hidden_store"], "x-region-ids": "south", "Idempotency-Key": "second-concurrent"}

    def transition(issue, headers):
        return _client(case["bundle"]).post(f"{BASE}/issues/{issue}/triage", json={"severity": "critical"}, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(transition, case["issue"], first_headers), pool.submit(transition, case["hidden_issue"], second_headers)]
        results = [call.result(timeout=30) for call in calls]
    assert [response.status_code for response in results] == [200, 200], [response.text for response in results]
    state = case["repo"].get_state()
    assert [issue["status"] for issue in state["issues"]] == ["triaged", "triaged"]
    assert {event["id"] for event in state["auditEvents"]} == {"AUD-OPS-1", "AUD-OPS-2"}
    assert state["nextAuditOrdinal"] == 3


def test_replay_refreshes_snapshot_without_reexecuting_the_original_command(case):
    assert _transition(case).status_code == 200
    response = _client(case["bundle"]).post(f"{BASE}/issues/{case['issue']}/escalate", json={}, headers={**case["headers"], "Idempotency-Key": "synthetic-escalate"})
    assert response.status_code == 200
    replay = _transition(case)
    assert replay.status_code == 200
    assert replay.json()["idempotentReplay"] is True
    assert replay.json()["issue"]["status"] == "escalated"
    assert replay.json()["issues"][0]["status"] == "escalated"
    assert len(case["repo"].get_state()["auditEvents"]) == 2


def test_live_attachment_upload_list_download_and_delete_use_scoped_actor(case):
    client = _client(case["bundle"])
    path = f"{BASE}/issues/{case['issue']}/attachments"
    upload = client.post(path, json={"filename": "synthetic.jpg", "contentBase64": "c3ludGhldGlj", "actorName": "forged-user", "actorRoleId": "auditPm"}, headers=case["headers"])
    assert upload.status_code == 200, upload.text
    attachment_id = upload.json()["id"]
    assert upload.json()["uploadedBy"] == case["subject"]
    listed = client.get(path, headers=case["headers"])
    assert listed.status_code == 200, listed.text
    assert listed.json()["count"] == 1
    downloaded = client.get(path + f"/{attachment_id}/download", headers=case["headers"])
    assert downloaded.status_code == 200, downloaded.text
    deleted = client.delete(path + f"/{attachment_id}", headers={**case["headers"], "X-Actor-Role-Id": "auditPm"})
    assert deleted.status_code == 200, deleted.text
    assert client.get(path, headers=case["headers"]).json()["count"] == 0
    events = case["repo"].get_state()["auditEvents"]
    assert len(events) == 3
    assert all(event["actorName"] == case["subject"] and event["actorRoleId"] == "opsLead" for event in events)


def test_worker_recovers_pending_delivery_without_another_http_request(case):
    from apps.worker.oday_worker.main import ODayWorker

    sink = case["bundle"].audit_log._worm_sink
    real_write = sink.write_audit_event

    def fail_business_delivery(event):
        if event.event_type == "operator.store_ops.issue_transition":
            raise RuntimeError("synthetic delivery outage")
        return real_write(event)

    case["monkeypatch"].setattr(sink, "write_audit_event", fail_business_delivery)
    response = _transition(case)
    assert response.status_code == 200, response.text
    assert response.json()["auditDelivery"]["pending"] == 1
    case["monkeypatch"].setattr(sink, "write_audit_event", real_write)
    worker = ODayWorker(persistence=case["bundle"], worker_id="synthetic-audit-worker")
    assert worker.run_once() is True
    deliveries = case["repo"]._store.list_all(COLLECTION)
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "delivered"
    job = case["bundle"].job_queue.get_by_idempotency_key(
        "store-ops-audit:" + case["tenant"] + ":" + deliveries[0]["event"].event_id
    )
    assert job.status.value == "succeeded"
    assert job.attempts == 1
    assert worker.run_once() is False
    assert len(case["repo"].get_state()["auditEvents"]) == 1


def test_delivery_job_enqueue_failure_rolls_back_the_complete_business_transaction(case):
    original = case["repo"].get_state()
    real_enqueue = case["bundle"].job_queue.enqueue

    def fail_enqueue(request, *, correlation_id):
        if request.job_type == "store-ops-audit-delivery":
            raise RuntimeError("synthetic queue persistence failure")
        return real_enqueue(request, correlation_id=correlation_id)

    case["monkeypatch"].setattr(case["bundle"].job_queue, "enqueue", fail_enqueue)
    assert _transition(case).status_code == 503
    assert case["repo"].get_state() == original
    assert case["repo"]._store.list_all(COLLECTION) == []
    assert not any(
        event.event_type == "operator.store_ops.issue_transition"
        for event in case["bundle"].audit_log.list_events(tenant_id=case["tenant"])
    )


def test_worker_outage_retries_without_claiming_delivery_success(case):
    from apps.worker.oday_worker.main import ODayWorker

    sink = case["bundle"].audit_log._worm_sink
    real_write = sink.write_audit_event

    def fail_business_delivery(event):
        if event.event_type == "operator.store_ops.issue_transition":
            raise RuntimeError("synthetic delivery outage")
        return real_write(event)

    case["monkeypatch"].setattr(sink, "write_audit_event", fail_business_delivery)
    assert _transition(case).status_code == 200
    delivery = case["repo"]._store.list_all(COLLECTION)[0]
    key = "store-ops-audit:" + case["tenant"] + ":" + delivery["event"].event_id
    worker = ODayWorker(persistence=case["bundle"], worker_id="synthetic-retry-worker")
    assert worker.run_once() is True
    job = case["bundle"].job_queue.get_by_idempotency_key(key)
    assert job.status.value == "queued"
    assert job.attempts == 1
    assert case["repo"]._store.get(COLLECTION, delivery["event"].event_id)["status"] == "pending"
    case["monkeypatch"].setattr(sink, "write_audit_event", real_write)
    assert worker.run_once() is True
    job = case["bundle"].job_queue.get_by_idempotency_key(key)
    assert job.status.value == "succeeded"
    assert job.attempts == 2
    assert len(case["repo"].get_state()["auditEvents"]) == 1


def test_worker_rejects_a_different_sink_without_an_external_write(case):
    from apps.worker.consumers.store_ops_audit import handle_store_ops_audit_delivery
    from shared.jobs.queue import JobRecord, NonRetryableJobError

    assert _transition(case).status_code == 200
    delivery = case["repo"]._store.list_all(COLLECTION)[0]
    sink = case["bundle"].audit_log._worm_sink

    def forbidden_write(event):
        pytest.fail("A job may not choose another configured WORM target")

    case["monkeypatch"].setattr(sink, "write_audit_event", forbidden_write)
    job = JobRecord(
        job_type="store-ops-audit-delivery",
        payload={"tenant_id": case["tenant"], "audit_event_id": delivery["event"].event_id, "sink_id": "gs://unapproved-target"},
        correlation_id="synthetic-target-denial",
    )
    with pytest.raises(NonRetryableJobError, match="configured sink"):
        handle_store_ops_audit_delivery(job, case["bundle"])
