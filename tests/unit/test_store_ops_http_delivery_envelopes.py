"""HTTP response-shape regression with owned SQLite persistence and delivery ports.

Exercises the real router, service, scoped merge, durable repository, SQLite CAS
and audit record/hash logic. The advisory/transaction observer reports the
PostgreSQL dialect only for the existing route contract; it records that one
advisory statement and delegates transactions to the same real SQLite engine.
This is SQLite engineering coverage, not PostgreSQL, native-browser, real-login
or remote-WORM evidence; the native PostgreSQL suite remains required.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.app.routes.operator_modules import store_ops as route_module
from apps.api.oday_api.security import dependencies
from modules.opsboard.application.store_ops import DurableStoreOpsRepository, StoreOpsService
from modules.opsboard.application.store_ops_scope import binding_hash
from shared.audit import InMemoryAuditLog
from shared.auth import Role
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine

TENANT = "unit-envelope-tenant"
SUBJECT = "unit-envelope-subject"
STORE = "unit-envelope-store"
ISSUE = "unit-envelope-issue"
BASE = "/api/v1/operator/store-ops"
DELIVERED = {"status": "delivered", "pending": 0, "deliveredNow": 1}
PENDING = {"status": "pending", "pending": None, "retryable": True}


class SqliteTransactionObservationPort:
    """Record advisory admission while transactions use the owned SQLite engine."""

    dialect = "postgresql"

    def __init__(self, sqlite_engine):
        self.sqlite_engine = sqlite_engine
        self.advisory_calls = 0
        self.active = False

    @contextmanager
    def transaction(self):
        assert not self.active
        self.active = True
        try:
            with self.sqlite_engine.transaction():
                yield
        finally:
            self.active = False

    def execute(self, statement, parameters):
        assert self.active
        assert statement == "SELECT pg_advisory_xact_lock(hashtextextended(?, 0))"
        assert parameters == ("operator.store_ops:" + TENANT,)
        self.advisory_calls += 1


class OwnedFileDocumentPort:
    """Delegate persistence and atomic row versions to the same owned SQLite store."""

    tenant_id = TENANT

    def __init__(self, path, engine):
        assert engine.sqlite_engine.path == path
        self.path, self.engine = path, engine
        self._documents = SqliteDocumentStore(engine.sqlite_engine)

    def get(self, collection, key):
        return self._documents.get(collection, key)

    def put(self, collection, key, value, **kwargs):
        return self._documents.put(collection, key, value, **kwargs)

    def get_versioned(self, collection, key):
        return self._documents.get_versioned(collection, key)

    def compare_and_put(self, collection, key, value, *, expected_version):
        return self._documents.compare_and_put(
            collection, key, value, expected_version=expected_version,
        )


class UnitAuditLog(InMemoryAuditLog):
    def __init__(self, engine):
        super().__init__(worm_sink=SimpleNamespace(sink_id="inert-unit://source-sink"))
        self._engine = engine

    @contextmanager
    def transactional_worm_sink(self, sink):
        before = self._worm_sink
        self._worm_sink = sink
        try:
            yield
        finally:
            self._worm_sink = before


def _state():
    issue = {
        "id": ISSUE, "storeId": STORE, "storeName": "Unit store", "title": "Recorded unit incident",
        "status": "new", "severity": "medium", "source": "payment", "ownerRoleId": "opsLead",
        "ownerName": "", "createdAt": "2026-10-14T09:00:00+00:00",
        "updatedAt": "2026-10-14T09:00:00+00:00", "slaDueAt": "2026-10-16T10:00:00+00:00",
        "evidenceIds": [], "summary": "Explicit unit protocol input",
    }
    hidden = {**deepcopy(issue), "id": "unit-hidden-issue", "storeId": "unit-hidden-store"}
    return {
        "stores": [{"id": STORE, "name": "Unit store", "lights": {}},
                   {"id": "unit-hidden-store", "name": "Hidden unit store", "lights": {}}],
        "issues": [issue, hidden], "evidence": [], "auditEvents": [], "nextAuditOrdinal": 1,
        "attachments": [{"id": "unit-attachment", "issueId": ISSUE, "tenantId": TENANT,
                         "filename": "declared-only.txt", "classification": "general",
                         "sensitivityLevel": "general", "kind": "note"}],
    }


@pytest.fixture
def http_case(tmp_path, monkeypatch):
    path = tmp_path / "owned-unit-documents.sqlite3"
    engine = SqliteTransactionObservationPort(SqliteEngine(path))
    try:
        repo = DurableStoreOpsRepository(OwnedFileDocumentPort(path, engine))
        repo.save_state(_state())
        before_state, before_version = repo.read_versioned_state()
        assert isinstance(before_version, int) and before_version > 0
        stale_state = deepcopy(before_state)
        stale_state["issues"][0]["severity"] = "critical"
        assert repo.compare_and_save_state(
            stale_state, expected_version=before_version - 1,
        ) is False
        assert repo.read_versioned_state() == (before_state, before_version)
        audit = UnitAuditLog(engine)
        delivery_state = SimpleNamespace(fail=False, drains=0, queued=[])

        class DeliveryPort:
            sink_id = "inert-unit://transactional-delivery"

            def __init__(self, *_args):
                pass

            def write_audit_event(self, event):
                delivery_state.queued.append(event)

            def deliver_pending(self):
                assert not engine.active, "Delivery must remain after the business transaction"
                delivery_state.drains += 1
                if delivery_state.fail:
                    raise RuntimeError("Explicit unit delivery observation failure")
                return deepcopy(DELIVERED)

        async def unit_guard(_request: Request):
            # This is an explicit principal admission port, not real authentication.
            return None

        monkeypatch.setattr(dependencies, "require_operator_permission", lambda *_a, **_kw: unit_guard)
        monkeypatch.setattr(route_module, "StoreOpsAuditDelivery", DeliveryPort)
        scope = SimpleNamespace(
            tenant_id=TENANT, brand_ids=frozenset(), region_ids=frozenset(), store_ids=frozenset({STORE}),
            clearance=SimpleNamespace(value="general"), assigned_area_ids=frozenset(),
            heat_zone_ids=frozenset(), modules=frozenset(),
        )
        principal = SimpleNamespace(authenticated=True, subject_id=SUBJECT, scope=scope,
                                    roles=frozenset({Role.OPERATIONS_MANAGER}))
        app = FastAPI()

        @app.middleware("http")
        async def unit_principal(request: Request, call_next):
            request.state.operator_principal = principal
            request.state.operator_role_id = "ops-lead"
            request.state.correlation_id = "unit-http-correlation"
            return await call_next(request)

        stores = SimpleNamespace(list_stores=lambda **_kwargs: [SimpleNamespace(store_id=STORE)])
        app.include_router(route_module.create_operator_store_ops_router(
            repository=repo, store_repository=stores, audit_log=audit, require_live_data=True,
        ), prefix="/api/v1")
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, repo=repo, path=path, engine=engine,
                                  audit=audit, delivery=delivery_state)

    finally:
        engine.sqlite_engine.close()


@pytest.mark.parametrize("post_failure,get_failure", [(False, False), (False, True), (True, True)])
def test_command_envelope_and_reopened_get_preserve_exact_issue_and_delivery_drain(
    http_case, post_failure, get_failure,
):
    case = http_case
    hidden_before = deepcopy(case.repo.get_state()["issues"][1])
    case.delivery.fail = post_failure
    response = case.client.post(
        f"{BASE}/issues/{ISSUE}/triage",
        json={"severity": "high", "decision": "accept", "notes": "Retained submitted note",
              "actorName": "Forged caller", "actorRoleId": "auditPm"},
        headers={"Idempotency-Key": "unit-envelope-command"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auditDelivery"] == (PENDING if post_failure else DELIVERED)
    saved_issue = body["issue"]
    assert "auditDelivery" not in saved_issue
    durable_before_read = case.repo.get_state()
    assert durable_before_read["issues"][0] == saved_issue
    assert durable_before_read["issues"][1] == hidden_before
    record = saved_issue["workflowRecords"][-1]
    assert record["actorSubjectId"] == record["actorName"] == SUBJECT
    assert record["actorEvidence"] == "authenticated_principal" and record["actorRoleId"] == "opsLead"
    events = case.audit.list_events(tenant_id=TENANT)
    assert len(events) == 1 and events[0].actor == SUBJECT
    assert events[0].metadata["reviewRecordHash"] == binding_hash(record)
    assert events[0].metadata["workflowAuditId"] == record["workflowAuditId"]
    assert case.delivery.drains == 1 and len(case.delivery.queued) == 1

    # A fresh document-port/repository instance reads the same owned file.
    reopened = DurableStoreOpsRepository(OwnedFileDocumentPort(case.path, case.engine))
    assert reopened.get_state() == durable_before_read
    case.delivery.fail = get_failure
    read = case.client.get(f"{BASE}/issues/{ISSUE}")
    assert read.status_code == 200, read.text
    assert read.json()["issue"] == saved_issue
    assert "auditDelivery" not in read.json()["issue"]
    assert case.delivery.drains == 2, "GET must still attempt pending delivery with no command audit"
    assert reopened.get_state() == durable_before_read
    assert len(case.audit.list_events()) == 1
    assert case.engine.advisory_calls == 2


def test_raw_attachment_get_is_not_mistaken_for_a_response_envelope(http_case):
    case = http_case
    before = case.repo.get_state()
    expected = StoreOpsService(case.repo).get_attachment(
        issue_id=ISSUE, attachment_id="unit-attachment", tenant_id=TENANT,
        user_roles=[Role.OPERATIONS_MANAGER.value],
    )
    response = case.client.get(f"{BASE}/issues/{ISSUE}/attachments/unit-attachment")
    assert response.status_code == 200, response.text
    assert response.json() == {**expected, "correlation_id": "unit-http-correlation"}
    assert "auditDelivery" not in response.json()
    assert case.delivery.drains == 1
    assert case.repo.get_state() == before and case.audit.list_events() == []
