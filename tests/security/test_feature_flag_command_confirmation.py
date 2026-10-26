"""Explicit command declarations and real audit atomicity; no human signoff claim."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.api.oday_api.security import dependencies
from shared.api.errors import install_error_handlers
from shared.auth.feature_flags import FeatureFlag
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.feature_flags import (
    DurableFeatureFlagRegistry,
    UnavailableFeatureFlagRegistry,
)
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from tests.security.test_feature_flag_admin_authorization import (
    BASE,
    KEY,
    _headers,
    _test_app,
)
from tests.security.test_feature_flag_admin_authorization import (
    authorized_flags as authorized_flags,
)

PROOF = {"reason": "  Verified operational reason  ", "riskAcknowledged": True}


def _mounted_client(owner, audit):
    app = _test_app(owner, audit, boundary=dependencies.default_boundary())
    install_error_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


def _durable_case(tmp_path):
    engine = SqliteEngine(tmp_path / "synthetic-confirmation.sqlite")
    try:
        owner = DurableFeatureFlagRegistry(SqliteDocumentStore(engine))
        owner.initialize_defaults()
        audit = DurableAuditLog(engine)
        owner.execute("register", KEY, actor="synthetic-fixture-owner", correlation_id="fixture",
                      audit_log=audit, flag=FeatureFlag(key=KEY, owner="synthetic-owner", high_risk=True))
        queue = DurableJobQueue(engine, flags=owner)
        client = _mounted_client(owner, audit)
        return SimpleNamespace(client=client, registry=owner, audit=audit, engine=engine, queue=queue)
    except BaseException:
        engine.close()
        raise


@pytest.fixture(params=["memory", "sqlite"])
def confirmation_case(request, authorized_flags, tmp_path):
    if request.param == "memory":
        context = SimpleNamespace(
            client=_mounted_client(authorized_flags.registry, authorized_flags.audit),
            registry=authorized_flags.registry, audit=authorized_flags.audit, engine=None,
        )
    else:
        context = _durable_case(tmp_path)
    try:
        yield context
    finally:
        if context.engine is not None:
            context.engine.close()


@pytest.fixture
def durable_confirmation_case(authorized_flags, tmp_path):
    context = _durable_case(tmp_path)
    try:
        yield context
    finally:
        context.engine.close()


def _state(context):
    return (
        context.registry.all(),
        context.registry.read_versioned()[1] if context.engine is not None else None,
        [event.to_dict() for event in context.audit.list_events()
         if event.event_type.startswith("feature_flag.")],
        [dict(row) for row in context.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")]
        if context.engine is not None else None,
    )


@pytest.mark.parametrize("operation,body", [
    ("approve", {}),
    ("approve", {"reason": "ninechars", "riskAcknowledged": True}),
    ("approve", {"reason": "A valid approval reason"}),
    ("approve", {"reason": "A valid approval reason", "riskAcknowledged": "true"}),
    ("approve", {"reason": 1234567890, "riskAcknowledged": True}),
    ("enable", {"reason": "  ", "riskAcknowledged": True}),
    ("disable", {"reason": "Operational stop", "riskAcknowledged": False}),
    ("disable", {"reason": "Operational stop", "riskAcknowledged": 1}),
    ("register", {"reason": "  ", "riskAcknowledged": True}),
])
def test_direct_command_invalid_proof_cannot_mutate_flag_audit_or_queue(
    confirmation_case, operation, body,
):
    context = confirmation_case
    path = BASE if operation == "register" else BASE + "/" + KEY + "/" + operation
    if operation == "register":
        body = {"key": "unit.new-confirmation", "owner": "synthetic-owner", **body}
    before = _state(context)
    response = context.client.post(path, json=body, headers=_headers("admin-a"))
    assert response.status_code == 422
    assert _state(context) == before


def test_confirmed_commands_keep_two_principals_and_actual_trimmed_audit(confirmation_case):
    context = confirmation_case
    path = BASE + "/" + KEY
    for _ in range(2):
        response = context.client.post(path + "/approve", json=PROOF, headers=_headers("admin-a"))
        assert response.status_code == 200
        assert response.json()["flag"]["approved_by"] == ["admin-a"]
    denied = context.client.post(path + "/enable", json=PROOF, headers=_headers("admin-a"))
    assert denied.status_code == 403
    assert not context.registry.get(KEY).enabled
    assert context.client.post(path + "/approve", json=PROOF, headers=_headers("admin-b")).status_code == 200
    enabled = context.client.post(path + "/enable", json=PROOF, headers=_headers("admin-a"))
    assert enabled.status_code == 200
    assert context.registry.get(KEY).enabled
    if context.engine is not None:
        assert enabled.json()["audit"]["delivery_status"] == "not_configured"
        assert enabled.json()["audit"]["event_id"]
    stopped = context.client.post(path + "/disable", json=PROOF, headers=_headers("admin-b"))
    assert stopped.status_code == 200
    assert not context.registry.get(KEY).enabled
    assert context.registry.get(KEY).approved_by == frozenset()
    events = [event for event in context.audit.list_events()
              if event.event_type.startswith("feature_flag.") and event.resource == "feature_flag:" + KEY
              and event.correlation_id == "unit-request"]
    assert [(event.action, event.actor) for event in events] == [
        ("approve", "admin-a"), ("approve", "admin-a"), ("approve", "admin-b"),
        ("enable", "admin-a"), ("disable", "admin-b"),
    ]
    assert all(event.metadata["reason"] == PROOF["reason"].strip() for event in events)
    assert all(event.metadata["riskAcknowledged"] is True for event in events)


def test_other_commands_accept_nonempty_reason_without_inventing_approval_minimum(confirmation_case):
    context = confirmation_case
    key = "unit.low-risk-confirmation"
    proof = {"reason": " x ", "riskAcknowledged": True}
    created = context.client.post(BASE, json={"key": key, "owner": "synthetic-owner", **proof},
                                  headers=_headers("admin-a"))
    assert created.status_code == 200
    assert context.client.post(BASE + "/" + key + "/enable", json=proof,
                               headers=_headers("admin-a")).status_code == 200
    assert context.client.post(BASE + "/" + key + "/disable", json=proof,
                               headers=_headers("admin-a")).status_code == 200
    assert not context.registry.get(key).enabled
    events = [e for e in context.audit.list_events() if e.resource == "feature_flag:" + key
              and e.event_type.startswith("feature_flag.")]
    assert len(events) == 3
    assert all(e.metadata["reason"] == "x" and e.metadata["riskAcknowledged"] is True for e in events)


def test_confirmation_does_not_grant_auth_or_approval_identity(confirmation_case):
    context = confirmation_case
    path = BASE + "/" + KEY + "/approve"
    before = _state(context)
    assert context.client.post(path, json=PROOF).status_code == 401
    assert context.client.post(path, json=PROOF, headers=_headers("business-user")).status_code == 403
    forged = context.client.post(path, json={**PROOF, "approver": "admin-b"}, headers=_headers("admin-a"))
    assert forged.status_code == 403
    assert _state(context) == before


def test_proof_and_actual_audit_roll_back_together(durable_confirmation_case, monkeypatch):
    context = durable_confirmation_case
    before = _state(context)
    original = context.audit.record
    def reject_flag_audit(event):
        if event.event_type == "feature_flag.approve":
            raise RuntimeError("synthetic actual audit failure")
        return original(event)
    monkeypatch.setattr(context.audit, "record", reject_flag_audit)
    response = context.client.post(BASE + "/" + KEY + "/approve", json=PROOF, headers=_headers("admin-a"))
    assert response.status_code == 500
    assert _state(context) == before


@pytest.mark.parametrize("field", ["reason", "riskAcknowledged"])
@pytest.mark.parametrize("raw", ["1e309", "NaN", "Infinity", "-Infinity"])
def test_mounted_error_handler_nonfinite_proof_is_safe_422_without_mutation(
    confirmation_case, field, raw,
):
    context = confirmation_case
    body = ("{\"reason\":" + raw + ",\"riskAcknowledged\":true}" if field == "reason"
            else "{\"reason\":\"Verified operational reason\",\"riskAcknowledged\":" + raw + "}")
    before = _state(context)
    response = context.client.post(
        BASE + "/" + KEY + "/approve", content=body,
        headers={**_headers("admin-a"), "content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()
    assert not any(token in response.text for token in ("NaN", "Infinity", "1e309"))
    assert _state(context) == before


@pytest.mark.parametrize("raw", ["NaN", "1e309", "[NaN]"])
def test_mounted_error_handler_nonobject_proof_is_safe_422(confirmation_case, raw):
    context = confirmation_case
    before = _state(context)
    response = context.client.post(
        BASE + "/" + KEY + "/approve", content=raw,
        headers={**_headers("admin-a"), "content-type": "application/json"},
    )
    assert response.status_code == 422
    assert not any(token in response.text for token in ("NaN", "Infinity", "1e309"))
    assert _state(context) == before


def test_raw_proof_does_not_override_auth_RBAC_or_real_owner_unavailability(confirmation_case):
    context = confirmation_case
    path = BASE + "/" + KEY + "/approve"
    body = '{"reason":NaN,"riskAcknowledged":true}'
    before = _state(context)
    assert context.client.post(path, content=body,
        headers={"content-type": "application/json"}).status_code == 401
    assert context.client.post(path, content=body,
        headers={**_headers("business-user"), "content-type": "application/json"}).status_code == 403
    unavailable = _mounted_client(UnavailableFeatureFlagRegistry(), context.audit)
    response = unavailable.post(path, content=body,
        headers={**_headers("admin-a"), "content-type": "application/json"})
    assert response.status_code == 503
    assert "FEATURE_FLAG_STORE_UNAVAILABLE" in response.text
    assert _state(context) == before



def test_declared_schema_requires_the_actual_four_command_proof_bodies(confirmation_case):
    schema = confirmation_case.client.app.openapi()
    paths = [BASE, *(BASE + "/{key}/" + command for command in ("approve", "enable", "disable"))]
    for path in paths:
        body = schema["paths"][path]["post"]["requestBody"]
        assert body["required"] is True
        body_schema = body["content"]["application/json"]["schema"]
        model = schema["components"]["schemas"][body_schema["$ref"].rsplit("/", 1)[1]]
        assert {"reason", "riskAcknowledged"}.issubset(model["required"])
        assert model["properties"]["reason"]["type"] == "string"
        assert model["properties"]["riskAcknowledged"]["type"] == "boolean"


@pytest.mark.parametrize("operation,raw", [
    ("approve", '{"riskAcknowledged":true,"unrelated":NaN}'),
    ("enable", '{"reason":null,"riskAcknowledged":true,"unrelated":1e309}'),
    ("disable", '{"reason":"Operational stop","riskAcknowledged":null,"unrelated":NaN}'),
    ("register", '{"key":"unit.missing-proof","owner":"synthetic-owner","reason":"Register reason","unrelated":1e309}'),
])
def test_missing_or_null_proof_does_not_echo_unrelated_nonfinite_raw_JSON(
    confirmation_case, operation, raw,
):
    context = confirmation_case
    before = _state(context)
    path = BASE if operation == "register" else BASE + "/" + KEY + "/" + operation
    response = context.client.post(path, content=raw,
        headers={**_headers("admin-a"), "content-type": "application/json"})
    assert response.status_code == 422
    assert response.json()
    assert not any(token in response.text for token in ("NaN", "Infinity", "1e309", "unrelated"))
    assert _state(context) == before
