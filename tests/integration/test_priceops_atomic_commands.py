"""Real SQL failure, process-death, outbox and ownership-race proofs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from apps.worker.oday_worker.main import ODayWorker
from shared.audit.durable_delivery import COLLECTION, JOB_TYPE, DurableAuditDelivery
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.domain.models import AddressLocation, Brand, Store, Tenant
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.repositories import (
    DurableAddressLocationRepository,
    DurableBrandRepository,
    DurableStoreRepository,
    DurableTenantRepository,
)
from shared.jobs.queue import JobStatus, NonRetryableJobError
from tests.integration.test_priceops_command_authority import proposal
from tests.integration.test_priceops_durable_commands import database as priceops_database
from tests.integration.test_priceops_durable_commands import http_context, seed

database = priceops_database

TENANT = "11111111-1111-4111-8111-111111111111"
BRAND = "22222222-2222-4222-8222-222222222222"
STORE = "33333333-3333-4333-8333-333333333333"
ADDRESS = "44444444-4444-4444-8444-444444444444"
OTHER_BRAND = "55555555-5555-4555-8555-555555555555"


def effects(engine):
    return {
        "documents": [(r["collection"], r["doc_id"], bytes(r["data"])) for r in engine.query(
            "SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")],
        "jobs": [dict(r) for r in engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
        "audits": [dict(r) for r in engine.query(
            "SELECT * FROM durable_audit_events WHERE event_type LIKE ? ORDER BY event_id", ("priceops.%",))],
    }


def business_objects(root):
    return [p for p in root.rglob("*.json")
            if json.loads(p.read_text())["payload"]["event_type"].startswith("priceops.")]


def business_events(audit):
    return [e for e in audit.list_events() if e.event_type.startswith("priceops.")]


@pytest.mark.parametrize("failure", ["domain", "audit", "receipt", "outbox"])
def test_failed_creation_rolls_back_every_effect_and_same_key_can_retry(database, tmp_path, monkeypatch, failure):
    open_engine, _ = database
    engine = open_engine()
    try:
        client, repository, audit = http_context(engine)
        sink = LocalAppendOnlyWormSink(tmp_path / "synthetic-worm")
        audit._worm_sink = sink
        before = effects(engine)
        with monkeypatch.context() as patch:
            if failure == "domain":
                original = repository.insert_plan
                def fail(plan):
                    original(plan)
                    raise RuntimeError("synthetic domain failure after insert")
                patch.setattr(repository, "insert_plan", fail)
            elif failure == "audit":
                original = audit.record
                def fail(event):
                    original(event)
                    if event.event_type == "priceops.plan_created.v1":
                        raise RuntimeError("synthetic audit failure after append")
                patch.setattr(audit, "record", fail)
            elif failure == "receipt":
                original = DurableJobQueue.update_status
                def fail(self, job_id, status, **kwargs):
                    result = original(self, job_id, status, **kwargs)
                    if status == JobStatus.SUCCEEDED:
                        raise RuntimeError("synthetic receipt failure after update")
                    return result
                patch.setattr(DurableJobQueue, "update_status", fail)
            else:
                original = DurableJobQueue.enqueue
                def fail(self, request, **kwargs):
                    result = original(self, request, **kwargs)
                    if request.job_type == JOB_TYPE:
                        raise RuntimeError("synthetic outbox failure after enqueue")
                    return result
                patch.setattr(DurableJobQueue, "enqueue", fail)
            client.raise_server_exceptions = False
            client._transport.raise_server_exceptions = False
            failed = client.post("/priceops/plans", json=proposal("atomic-plan"),
                                 headers={"Idempotency-Key": "atomic-create"})
            assert failed.status_code >= 500
        assert effects(engine) == before
        assert business_objects(tmp_path / "synthetic-worm") == []
        retried = client.post("/priceops/plans", json=proposal("atomic-plan"),
                              headers={"Idempotency-Key": "atomic-create"})
        assert retried.status_code == 201, retried.text
        replay = client.post("/priceops/plans", json=proposal("atomic-plan"),
                             headers={"Idempotency-Key": "atomic-create"})
        assert replay.status_code == 201 and replay.json()["idempotent_replay"] is True
        assert len(repository.list_plans()) == 1
        assert len(business_events(audit)) == 1 and audit.verify_chain().ok
        assert len(engine.query("SELECT job_id FROM durable_jobs")) == 2
    finally:
        engine.close()


def test_process_death_after_business_and_audit_append_leaves_no_partial_command(database):
    open_engine, env = database
    engine = open_engine()
    client, _, _ = http_context(engine)
    before = effects(engine)
    engine.close()
    code = """
import os
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.postgresql import PostgresEngine
from tests.integration.test_priceops_durable_commands import http_context
from tests.integration.test_priceops_command_authority import proposal
engine = SqliteEngine(os.environ['PRICEOPS_TEST_PATH']) if os.environ['PRICEOPS_TEST_BACKEND']=='sqlite' else PostgresEngine(os.environ['PRICEOPS_TEST_DATABASE_URL'], validate_schema=False)
client,repo,audit = http_context(engine)
original = audit.record
def die(event):
    result = original(event)
    if event.event_type == 'priceops.plan_created.v1': os._exit(73)
    return result
audit.record = die
client.post('/priceops/plans',json=proposal('crashed-plan'),headers={'Idempotency-Key':'crashed-create'})
raise RuntimeError('crash was not exercised')
"""
    result = subprocess.run([sys.executable, "-c", code], env={**os.environ, **env},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr
    reopened = open_engine()
    try:
        assert effects(reopened) == before
        client, _, _ = http_context(reopened)
        assert client.post("/priceops/plans", json=proposal("crashed-plan"),
                           headers={"Idempotency-Key": "crashed-create"}).status_code == 201
    finally:
        reopened.close()


@pytest.mark.parametrize("command", ["simulate", "simulate-scenario", "decision-writeback", "approve"])
def test_failed_final_receipt_rolls_back_transition_aggregate_and_audit(database, monkeypatch, command):
    open_engine, _ = database
    engine = open_engine()
    try:
        client, _, audit = http_context(engine)
        if command == "simulate":
            assert client.post("/priceops/plans", json=proposal("durable-plan")).status_code == 201
        else:
            scenario, _, _ = seed(client)
        headers = {"Idempotency-Key": "failed-transition"}
        body = {"reason": "authored transition"}
        if command == "simulate-scenario":
            body = {"candidate_prices": {"new-item": 80}}
        elif command == "decision-writeback":
            body.update(actor="forged", decision="scenario_selected", selected_scenario_id=scenario["scenario_id"])
        elif command == "approve":
            assert client.post("/priceops/plans/durable-plan/submit", json=body).status_code == 200
            body.update(actor_id="forged", decision="approved")
            headers["x-subject-id"] = "independent-reviewer"
        before = effects(engine)
        with monkeypatch.context() as patch:
            original = DurableJobQueue.update_status
            def fail(self, job_id, status, **kwargs):
                result = original(self, job_id, status, **kwargs)
                if status == JobStatus.SUCCEEDED:
                    raise RuntimeError("synthetic final receipt failure")
                return result
            patch.setattr(DurableJobQueue, "update_status", fail)
            failed = client.post(f"/priceops/plans/durable-plan/{command}", json=body, headers=headers)
            assert failed.status_code == 503, failed.text
        assert effects(engine) == before and audit.verify_chain().ok
        retried = client.post(f"/priceops/plans/durable-plan/{command}", json=body, headers=headers)
        assert retried.status_code == 200, retried.text
    finally:
        engine.close()


def test_optimizer_batch_failure_on_second_plan_rolls_back_first_plan(database, monkeypatch):
    open_engine, _ = database
    engine = open_engine()
    try:
        client, repository, _ = http_context(engine)
        before = effects(engine)
        calls = 0
        original = repository.insert_plan
        with monkeypatch.context() as patch:
            def fail(plan):
                nonlocal calls
                calls += 1
                result = original(plan)
                if calls == 2:
                    raise RuntimeError("synthetic second-plan failure")
                return result
            patch.setattr(repository, "insert_plan", fail)
            client._transport.raise_server_exceptions = False
            body = {"plans": [proposal("batch-first"), proposal("batch-second")]}
            failed = client.post("/priceops/optimizer-jobs", json=body,
                                 headers={"Idempotency-Key": "atomic-batch"})
            assert failed.status_code == 500, failed.text
        assert effects(engine) == before
        retried = client.post("/priceops/optimizer-jobs", json=body,
                              headers={"Idempotency-Key": "atomic-batch"})
        assert retried.status_code == 202, retried.text
        assert {plan.plan_id for plan in repository.list_plans()} == {"batch-first", "batch-second"}
    finally:
        engine.close()


@pytest.mark.parametrize("failure", ["outage", "lost_response"])
def test_real_worker_recovers_committed_outbox_without_repeating_command(database, tmp_path, monkeypatch, failure):
    open_engine, _ = database
    engine = open_engine()
    try:
        client, repository, audit = http_context(engine)
        sink = LocalAppendOnlyWormSink(tmp_path / "synthetic-worm")
        audit._worm_sink = sink
        response = client.post("/priceops/plans", json=proposal("worker-plan"),
                               headers={"Idempotency-Key": "worker-create"})
        assert response.status_code == 201
        assert business_objects(tmp_path / "synthetic-worm") == []
        queue = DurableJobQueue(engine)
        job = queue.get(engine.query_one("SELECT job_id FROM durable_jobs WHERE job_type = ?", (JOB_TYPE,))["job_id"])
        bundle = SimpleNamespace(engine=engine, job_queue=queue, audit_log=audit)
        worker = ODayWorker(persistence=bundle, worker_id="synthetic-durable-audit", audit_only=True)
        original = sink.write_audit_event
        with monkeypatch.context() as patch:
            def fail(event):
                if failure == "lost_response":
                    original(event)
                raise RuntimeError("synthetic WORM " + failure)
            patch.setattr(sink, "write_audit_event", fail)
            assert worker.run_once() is True
        assert queue.get(job.job_id).status != JobStatus.SUCCEEDED
        assert worker.run_once() is True
        assert queue.get(job.job_id).status == JobStatus.SUCCEEDED
        assert worker.run_once() is False
        event = business_events(audit)[0]
        assert sink.lookup_audit_event(event) is not None
        row = DurableAuditDelivery(engine, "tenant-a", queue, sink).store.get(COLLECTION, event.event_id)
        assert row["status"] == "delivered"
        assert len(repository.list_plans()) == 1 and len(business_events(audit)) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("field", ["tenant_id", "sink_id", "event_hash"])
def test_forged_outbox_job_is_denied_before_external_write(database, tmp_path, field):
    open_engine, _ = database
    engine = open_engine()
    try:
        client, _, audit = http_context(engine)
        sink = LocalAppendOnlyWormSink(tmp_path / "synthetic-worm")
        audit._worm_sink = sink
        assert client.post("/priceops/plans", json=proposal("forgery-plan")).status_code == 201
        queue = DurableJobQueue(engine)
        job = queue.get(engine.query_one("SELECT job_id FROM durable_jobs WHERE job_type = ?", (JOB_TYPE,))["job_id"])
        forged = replace(job, payload={**job.payload, field: "forged"})
        with pytest.raises(NonRetryableJobError):
            DurableAuditDelivery(engine, "tenant-a", queue, sink).deliver(forged)
        assert business_objects(tmp_path / "synthetic-worm") == []
    finally:
        engine.close()


@pytest.fixture
def canonical_case(database, request):
    open_engine, env = database
    if env["PRICEOPS_TEST_BACKEND"] == "postgresql":
        from tests.integration.test_postgresql_persistence import _provision_canonical_schema
        _provision_canonical_schema(request.getfixturevalue("intake_blank_db"))
    engine = open_engine()
    DurableTenantRepository(engine).save_tenant(Tenant(tenant_id=TENANT, tenant_name="Synthetic atomic tenant"))
    brands, stores = DurableBrandRepository(engine), DurableStoreRepository(engine)
    for identity, code in ((BRAND, "original"), (OTHER_BRAND, "transferred")):
        brands.save_brand(Brand(brand_id=identity, tenant_id=TENANT, brand_code=code, brand_name=code))
    DurableAddressLocationRepository(engine).save_address(AddressLocation(
        address_id=ADDRESS, tenant_id=TENANT, raw_address="Synthetic", normalized_address="Synthetic"))
    stores.save_store(Store(store_id=STORE, tenant_id=TENANT, brand_id=BRAND,
                           address_id=ADDRESS, store_name="Synthetic store", region_code="north"))
    client, repository, audit = http_context(engine, store_repository=stores, brand_repository=brands)
    client.headers.update({"x-tenant-id": TENANT})
    body = proposal("durable-plan", store_id=STORE, brand_id=BRAND)
    body["tenant_id"] = TENANT
    seed(client, plan_proposal=body)
    assert client.post("/priceops/plans/durable-plan/submit", json={"reason": "independent review"}).status_code == 200
    try:
        yield client, repository, audit, stores, open_engine
    finally:
        engine.close()


def test_transfer_between_dependency_and_command_is_rechecked(canonical_case, monkeypatch):
    client, repository, audit, stores, _ = canonical_case
    original = stores.get_store
    calls = 0
    def transfer_after_read(identity):
        nonlocal calls
        store = original(identity)
        calls += 1
        if calls == 1:
            stores.save_store(replace(store, brand_id=OTHER_BRAND))
        return store
    monkeypatch.setattr(stores, "get_store", transfer_after_read)
    response = client.post("/priceops/plans/durable-plan/approve",
                           headers={"x-subject-id": "independent-reviewer"},
                           json={"actor_id": "forged", "decision": "approved", "reason": "authored independent decision"})
    assert response.status_code == 404
    assert repository.list_approvals("durable-plan") == []
    assert not any(e.event_type == "priceops.approved.v1" for e in audit.list_events())


def test_transfer_waits_for_approval_transaction_and_old_replay_is_denied(canonical_case, monkeypatch):
    client, repository, audit, stores, open_engine = canonical_case
    approval_written, release_approval, transfer_started = Event(), Event(), Event()
    original = repository.save_approval
    def pause_after_write(record):
        result = original(record)
        approval_written.set()
        assert release_approval.wait(10)
        return result
    monkeypatch.setattr(repository, "save_approval", pause_after_write)
    body = {"actor_id": "forged", "decision": "approved", "reason": "authored independent decision"}
    headers = {"x-subject-id": "independent-reviewer", "Idempotency-Key": "locked-approval"}
    before_store = stores.get_store(STORE)
    other = open_engine()
    try:
        def transfer():
            transfer_started.set()
            DurableStoreRepository(other).save_store(replace(before_store, brand_id=OTHER_BRAND))
        with ThreadPoolExecutor(max_workers=2) as pool:
            approval = pool.submit(client.post, "/priceops/plans/durable-plan/approve", json=body, headers=headers)
            assert approval_written.wait(10)
            movement = pool.submit(transfer)
            assert transfer_started.wait(10)
            try:
                with pytest.raises(TimeoutError):
                    movement.result(timeout=0.15)
            finally:
                release_approval.set()
            response = approval.result(timeout=10)
            assert response.status_code == 200, response.text
            movement.result(timeout=10)
        assert stores.get_store(STORE).brand_id == OTHER_BRAND
        assert client.post("/priceops/plans/durable-plan/approve", json=body, headers=headers).status_code == 404
        assert len(repository.list_approvals("durable-plan")) == 1
        assert len([e for e in audit.list_events() if e.event_type == "priceops.approved.v1"]) == 1
    finally:
        release_approval.set()
        other.close()


@pytest.mark.parametrize("shared_engine", [False, True])
def test_eight_requests_replay_one_approval_and_verify_full_audit_chain(canonical_case, shared_engine):
    shared_client, repository, audit, _, open_engine = canonical_case
    body = {"actor_id": "forged", "decision": "approved", "reason": "authored independent decision"}
    headers = {"x-subject-id": "independent-reviewer", "x-tenant-id": TENANT,
               "Idempotency-Key": "eight-connection-approval"}
    def approve(index):
        if shared_engine:
            response = shared_client.post("/priceops/plans/durable-plan/approve", json=body, headers=headers)
            assert response.status_code == 200, response.text
            return response.json()
        engine = open_engine()
        try:
            client, _, _ = http_context(engine, store_repository=DurableStoreRepository(engine),
                                        brand_repository=DurableBrandRepository(engine))
            response = client.post("/priceops/plans/durable-plan/approve", json=body, headers=headers)
            assert response.status_code == 200, response.text
            return response.json()
        finally:
            engine.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(approve, range(8)))
    assert sum(not response["idempotent_replay"] for response in responses) == 1
    assert len({response["audit_event_id"] for response in responses}) == 1
    assert len(repository.list_approvals("durable-plan")) == 1
    assert len([e for e in audit.list_events() if e.event_type == "priceops.approved.v1"]) == 1
    assert audit.verify_chain().ok
