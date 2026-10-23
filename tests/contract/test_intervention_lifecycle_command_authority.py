"""Native owned SQLite lifecycle transport controls; local test Principals only."""

import hashlib
import json
import pickle
from dataclasses import replace

import pytest

from tests.contract.test_intervention_approval_authority import (
    FOREIGN,
    REVIEWER,
    STORE,
    command,
    create,
    headers,
    lifecycle_transport,
    post,
)
from tests.contract.test_intervention_approval_authority import (
    owned as inherited_owned,
)

owned = inherited_owned

COMMANDS = {
    "assign": {"actor": "forged", "assignee": REVIEWER},
    "unassign": {"actor": "forged"},
    "eligibility": {"actor": "forged", "eligible": True},
    "action": {"actor": "forged", "action_spec": {"price_change_pct": -4}},
    "conflict-check": {"actor": "forged", "allow_overlap": False},
    "submit": {"actor": "forged"},
    "execute": {"executor": "forged", "executed_at": "2025-01-01T00:00:00+00:00"},
    "outcomes": {
        "actor": "forged",
        "incremental_revenue": 100,
        "incremental_gross_margin": 45,
        "ad_spend": 10,
        "has_control_group": True,
        "pretrend_status": "PASS",
    },
    "evaluate": {"actor": "forged", "now": "2025-04-01T00:00:00+00:00"},
    "close": {"actor": "forged", "disposition": "KEEP", "reason": "reviewed native result"},
    "adjust": {"actor": "forged", "reason": "native replacement"},
    "stop": {"actor": "forged", "reason": "native stop"},
    "rollback": {"actor": "forged", "reason": "native rollback"},
}


def send(client, iid, cmd, body, hdr):
    return client.post(f"/api/v1/interventions/{iid}/{cmd}", json=body, headers=hdr)


def invoke(client, iid, cmd, extra=None):
    body, hdr = lifecycle_transport(client, iid, cmd, {**COMMANDS[cmd], **(extra or {})})
    result = send(client, iid, cmd, body, hdr)
    assert result.status_code == 200, result.text
    return result.json()


def ready(owned, target):
    _, client, _, _ = owned
    iid = create(client)
    if target in {"assign", "unassign", "eligibility"}:
        return iid
    invoke(client, iid, "eligibility")
    if target == "action":
        return iid
    invoke(client, iid, "action")
    if target == "conflict-check":
        return iid
    invoke(client, iid, "conflict-check")
    if target == "submit":
        return iid
    invoke(client, iid, "submit")
    body, hdr = command(client, iid)
    response = post(client, iid, body, hdr)
    assert response.status_code == 200, response.text
    if target in {"execute", "adjust", "stop"}:
        return iid
    invoke(client, iid, "execute")
    if target in {"outcomes", "rollback"}:
        return iid
    invoke(client, iid, "outcomes")
    if target == "evaluate":
        return iid
    invoke(client, iid, "evaluate")
    return iid


def storage(engine, workflow):
    return {
        "docs": [
            (r["collection"], r["doc_id"], r["group_key"], hashlib.sha256(r["data"]).hexdigest())
            for r in engine.query(
                "SELECT collection,doc_id,group_key,data FROM durable_documents ORDER BY collection,doc_id"
            )
            if "audit.durable_delivery" not in r["collection"]
        ],
        "mirrors": [
            tuple(r) for r in engine.query("SELECT * FROM interventions ORDER BY intervention_id")
        ],
        "jobs": [
            tuple(r)
            for r in engine.query(
                "SELECT * FROM durable_jobs WHERE job_type != 'durable-audit-delivery' ORDER BY job_id"
            )
        ],
        "audit": [
            e.to_dict()
            for e in workflow.audit_log.list_events()
            if e.event_type == "intervention.lifecycle.v1"
        ],
        "delivery": [
            (r["doc_id"], hashlib.sha256(r["data"]).hexdigest())
            for r in engine.query(
                "SELECT doc_id,data FROM durable_documents WHERE collection LIKE '%audit.durable_delivery%' ORDER BY doc_id"
            )
            if getattr(pickle.loads(r["data"]).get("event"), "event_type", None)
            == "intervention.lifecycle.v1"
        ],
        "deliveryJobs": [
            tuple(r)
            for r in engine.query(
                "SELECT * FROM durable_jobs WHERE job_type='durable-audit-delivery' ORDER BY job_id"
            )
            if json.loads(r["payload_json"]).get("audit_event_id")
            in {
                e.event_id
                for e in workflow.audit_log.list_events()
                if e.event_type == "intervention.lifecycle.v1"
            }
        ],
    }


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_thirteen_native_command_paths_have_real_history_receipt_and_exact_replay(owned, cmd):
    engine, client, workflow, _ = owned
    iid = ready(owned, cmd)
    body, hdr = lifecycle_transport(client, iid, cmd, COMMANDS[cmd])
    first = send(client, iid, cmd, body, hdr)
    assert first.status_code == 200, first.text
    value = first.json()
    expected_delta = 2 if cmd in {"execute", "evaluate"} else 1
    assert value["version"] == body["expected_version"] + expected_delta
    saved = workflow.get(iid)
    assert saved.version == value["version"]
    assert len(saved.history[-expected_delta:]) == expected_delta
    assert all(
        t.actor == REVIEWER and t.correlation_id == hdr["x-correlation-id"]
        for t in saved.history[-expected_delta:]
    )
    assert value["command"] == cmd and value["previous_version"] == body["expected_version"]
    assert value["lifecycle_audit_event_id"] and value["correlation_id"] == hdr["x-correlation-id"]
    before = storage(engine, workflow)
    replay = send(client, iid, cmd, body, hdr)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**value, "idempotent_replay": True}
    assert storage(engine, workflow) == before


@pytest.mark.parametrize("cmd", list(COMMANDS))
@pytest.mark.parametrize(
    "fault,expected",
    [
        ("missing", 428),
        ("bool", 422),
        ("float", 422),
        ("string", 422),
        ("null", 422),
        ("zero", 422),
        ("negative", 422),
        ("context_missing", 428),
        ("snapshot", 409),
        ("key", 428),
        ("cid", 428),
    ],
)
def test_each_command_strict_precondition_failure_has_zero_effects(owned, cmd, fault, expected):
    engine, client, workflow, _ = owned
    iid = ready(owned, cmd)
    body, hdr = lifecycle_transport(client, iid, cmd, COMMANDS[cmd])
    if fault == "missing":
        body.pop("expected_version")
    elif fault in {"bool", "float", "string", "null", "zero", "negative"}:
        body["expected_version"] = {
            "bool": True,
            "float": float(body["expected_version"]),
            "string": str(body["expected_version"]),
            "null": None,
            "zero": 0,
            "negative": -1,
        }[fault]
    elif fault == "context_missing":
        body.pop("command_context")
    elif fault == "snapshot":
        body["command_context"]["snapshot_sha256"] = "0" * 64
    elif fault == "key":
        hdr.pop("Idempotency-Key")
    elif fault == "cid":
        hdr.pop("x-correlation-id")
    before = storage(engine, workflow)
    response = send(client, iid, cmd, body, hdr)
    assert response.status_code == expected, response.text
    assert storage(engine, workflow) == before


@pytest.mark.parametrize(
    "field",
    [
        "expectedActorSubjectId",
        "expectedTenantId",
        "context_actor",
        "context_tenant",
        "context_command",
        "context_id",
        "context_store",
        "context_version",
        "context_policy",
        "context_extra",
        "etag_missing",
        "etag_type",
        "etag_version",
        "actor_header",
    ],
)
def test_complete_context_and_headers_are_bound_to_original_caller(owned, field):
    engine, client, workflow, _ = owned
    iid = ready(owned, "assign")
    body, hdr = lifecycle_transport(client, iid, "assign", COMMANDS["assign"])
    expected = 409
    if field in {"expectedActorSubjectId", "expectedTenantId"}:
        body[field] = "foreign"
    elif field.startswith("context_"):
        key = {
            "actor": "actor_subject_id",
            "tenant": "tenant_id",
            "id": "intervention_id",
            "store": "store_id",
            "version": "version",
            "policy": "policy_version",
            "command": "command",
            "extra": "unexpected",
        }[field[8:]]
        body["command_context"][key] = (
            body["expected_version"] + 1 if key == "version" else "foreign"
        )
        if field == "context_extra":
            expected = 422
    elif field == "etag_missing":
        hdr.pop("If-Match")
        expected = 428
    elif field == "etag_type":
        hdr["If-Match"] = 'W/"1.0"'
        expected = 422
    elif field == "etag_version":
        hdr["If-Match"] = f'W/"{body["expected_version"] + 1}"'
    else:
        hdr["If-Actor-Match"] = "other"
    before = storage(engine, workflow)
    response = send(client, iid, "assign", body, hdr)
    assert response.status_code == expected, response.text
    assert storage(engine, workflow) == before


def test_authorized_original_replay_survives_corrupt_target_but_not_current_owner(
    owned, monkeypatch
):
    engine, client, workflow, _ = owned
    iid = ready(owned, "assign")
    body, hdr = lifecycle_transport(client, iid, "assign", COMMANDS["assign"])
    first = send(client, iid, "assign", body, hdr)
    assert first.status_code == 200, first.text
    workflow.repository._store.put(
        workflow.repository._C, iid, replace(workflow.get(iid), version=True), group_key=STORE
    )
    before = storage(engine, workflow)
    monkeypatch.setattr(
        workflow.repository,
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no replay decode")),
    )
    replay = send(client, iid, "assign", body, hdr)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**first.json(), "idempotent_replay": True}
    assert storage(engine, workflow) == before
    for denial in [
        {"x-tenant-id": FOREIGN},
        {"x-subject-id": "other"},
        {"x-roles": "MARKETING_MANAGER"},
    ]:
        result = send(client, iid, "assign", body, {**hdr, **denial})
        assert result.status_code in {403, 409}, result.text
        assert storage(engine, workflow) == before


@pytest.mark.parametrize("transport", ["cid", "etag", "body", "keybody"])
def test_success_receipt_rejects_original_key_with_changed_transport(owned, transport):
    _, client, _, _ = owned
    iid = ready(owned, "assign")
    body, hdr = lifecycle_transport(client, iid, "assign", COMMANDS["assign"])
    first = send(client, iid, "assign", body, hdr)
    assert first.status_code == 200, first.text
    if transport == "cid":
        hdr["x-correlation-id"] = "different"
    elif transport == "etag":
        hdr["If-Match"] = 'W/"999"'
    elif transport == "body":
        body["actor"] = "changed-forged-body"
    else:
        body["command_context"]["snapshot_sha256"] = "0" * 64
    result = send(client, iid, "assign", body, hdr)
    assert result.status_code == 409, result.text


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_current_foreign_owner_is_denied_before_business_decode_and_reservation(
    owned, monkeypatch, cmd
):
    engine, client, workflow, queue = owned
    iid = ready(owned, cmd)
    body, hdr = lifecycle_transport(client, iid, cmd, COMMANDS[cmd])
    before = storage(engine, workflow)

    def forbidden(*args, **kwargs):
        raise AssertionError("denial must precede decode/reserve")

    monkeypatch.setattr(workflow.repository, "get", forbidden)
    monkeypatch.setattr(queue, "enqueue", forbidden)
    response = send(client, iid, cmd, body, {**hdr, "x-tenant-id": FOREIGN})
    assert response.status_code == 403, response.text
    assert storage(engine, workflow) == before


def test_explicit_missing_key_execute_is_428_without_any_business_effect(owned):
    engine, client, workflow, _ = owned
    iid = ready(owned, "execute")
    before = storage(engine, workflow)
    response = send(client, iid, "execute", {"executor": "forged-executor"}, headers(REVIEWER))
    assert response.status_code == 428, response.text
    assert storage(engine, workflow) == before
