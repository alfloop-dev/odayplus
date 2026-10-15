"""Owned SQLite controls for native Intervention related reads and Today producer.

The original 153 author/lifecycle controls remain byte-for-byte unchanged.
These controls use their explicitly local Principal adapter, never production auth.
"""

import hashlib
import pickle
from dataclasses import replace
from types import SimpleNamespace

import pytest

from modules.intervention.application.approval_contract import InterventionOwnerIntegrityError
from modules.opsboard.application.operator_live_repository import OperatorLiveRepository
from shared.infrastructure.persistence.repositories import (
    DurableForecastOpsRepository,
    DurableStoreRepository,
    DurableTransactionRepository,
)
from tests.contract.test_intervention_approval_authority import (
    CREATOR,
    FOREIGN,
    STORE,
    TENANT,
    add_foreign_case,
    create,
    headers,
    lifecycle_transport,
    record_v2_fact,
)
from tests.contract.test_intervention_approval_authority import (
    owned as inherited_owned,
)

owned = inherited_owned


def native_snapshot(engine, workflow):
    """Business state excludes independent, permitted security access audits."""
    return {
        "documents": [
            (row["doc_id"], row["group_key"], hashlib.sha256(row["data"]).hexdigest())
            for row in engine.query(
                "SELECT doc_id,group_key,data FROM durable_documents WHERE collection=? ORDER BY doc_id",
                (workflow.repository._C,),
            )
        ],
        "mirror": [tuple(row) for row in engine.query("SELECT * FROM interventions ORDER BY intervention_id")],
        "receipts": [tuple(row) for row in engine.query("SELECT * FROM durable_jobs ORDER BY job_id")],
        "lifecycle": [
            event.to_dict() for event in workflow.audit_log.list_events()
            if event.event_type == "intervention.lifecycle.v1"
        ],
    }


def proposed(client, iid, tenant=TENANT):
    for suffix, payload in [
        ("eligibility", {"eligible": True}),
        ("action", {"action_spec": {"price_change_pct": -5, "rollback_plan": "restore"}}),
    ]:
        lifecycle_body, lifecycle_headers = lifecycle_transport(client, iid, suffix,
            {"actor": "untrusted-body", **payload}, actor=CREATOR, tenant=tenant)
        result = client.post(
            f"/api/v1/interventions/{iid}/{suffix}",
            headers=lifecycle_headers,
            json=lifecycle_body,
        )
        assert result.status_code == 200, result.text
    return iid


def foreign_index(owned):
    engine, client, workflow, _ = owned
    foreign, foreign_store = add_foreign_case(client, engine)
    proposed(client, foreign, FOREIGN)
    local = proposed(client, create(client))
    client._lifecycle_foreign_check = lifecycle_transport(client, local, "conflict-check",
        {"actor": "untrusted-body", "allow_overlap": False}, key="v3-foreign-peer-check")
    engine.execute(
        "UPDATE durable_documents SET group_key=? WHERE collection=? AND doc_id=?",
        (STORE, workflow.repository._C, foreign),
    )
    assert engine.query_one(
        "SELECT store_id FROM interventions WHERE intervention_id=?", (foreign,)
    )["store_id"] == foreign_store
    return local, foreign, foreign_store


def observe_foreign(monkeypatch, foreign):
    native = pickle.loads
    decoded = []

    def observed(blob, *args, **kwargs):
        result = native(blob, *args, **kwargs)
        if getattr(result, "intervention_id", None) == foreign:
            decoded.append(foreign)
        return result

    monkeypatch.setattr(pickle, "loads", observed)
    return decoded


def today_producer(engine, workflow, queue):
    """Actual native producer with real canonical store and domain repositories.

    Unrelated optional repositories are absent and honestly reported unavailable;
    Intervention reads and projections are the unchanged production methods.
    """
    return OperatorLiveRepository(SimpleNamespace(
        mode="sqlite",
        engine=engine,
        store_repository=DurableStoreRepository(engine),
        transaction_repository=DurableTransactionRepository(engine),
        intervention_repository=workflow.repository,
        forecastops_repository=DurableForecastOpsRepository(workflow.repository._store),
        audit_log=workflow.audit_log,
        job_queue=queue,
    ))


def test_v3_foreign_conflict_peer_refused_before_decode_and_writes(owned, monkeypatch):
    engine, client, workflow, _ = owned
    local, foreign, _ = foreign_index(owned)
    before = native_snapshot(engine, workflow)
    decoded = observe_foreign(monkeypatch, foreign)
    response = client.post(
        f"/api/v1/interventions/{local}/conflict-check",
        headers=client._lifecycle_foreign_check[1],
        json=client._lifecycle_foreign_check[0],
    )
    after = native_snapshot(engine, workflow)
    record_v2_fact("v3-conflict-foreign-peer", {
        "httpStatus": response.status_code,
        "foreignBusinessDecodeCount": len(decoded),
        "businessChanged": after != before,
        "foreignIdExported": foreign in response.text,
        "securityAccessAuditsExcludedFromBusinessState": True,
    })
    assert response.status_code == 503, response.text
    assert decoded == []
    assert after == before


def test_v3_foreign_today_native_producer_refused_before_decode_and_projection(owned, monkeypatch):
    engine, _, workflow, queue = owned
    _, foreign, _ = foreign_index(owned)
    before = native_snapshot(engine, workflow)
    decoded = observe_foreign(monkeypatch, foreign)
    state = today_producer(engine, workflow, queue).load_state(tenant_id=TENANT)
    exported = any(item["id"] == foreign for item in state["workQueue"])
    section = state["_meta"]["sections"]["interventions"]
    record_v2_fact("v3-today-foreign-peer", {
        "producer": "OperatorLiveRepository.load_state",
        "foreignBusinessDecodeCount": len(decoded),
        "foreignWorkQueueRowExported": exported,
        "interventionSection": section,
        "businessChanged": native_snapshot(engine, workflow) != before,
        "productionHTTPOrCookieExercised": False,
    })
    assert not exported
    assert decoded == []
    assert section["state"] == "degraded" and section["recordCount"] == 0
    assert native_snapshot(engine, workflow) == before


def test_v3_common_durable_store_reader_refuses_foreign_index_before_decode(owned, monkeypatch):
    _, _, workflow, _ = owned
    _, foreign, _ = foreign_index(owned)
    decoded = observe_foreign(monkeypatch, foreign)
    records = []
    refused = False
    try:
        records = workflow.repository.list_by_store(STORE)
    except InterventionOwnerIntegrityError:
        refused = True
    record_v2_fact("v3-default-reader-foreign-peer", {
        "refused": refused,
        "foreignBusinessDecodeCount": len(decoded),
        "foreignRecordReturned": any(item.intervention_id == foreign for item in records),
    })
    assert refused
    assert decoded == []
    assert records == []


@pytest.mark.parametrize("fault", ["missing_relation", "missing_document", "wrong_group"])
def test_v3_related_metadata_corruption_refuses_before_any_decode(owned, monkeypatch, fault):
    engine, client, workflow, _ = owned
    local = proposed(client, create(client))
    peer = proposed(client, create(client))
    lifecycle_body, lifecycle_headers = lifecycle_transport(client, local, "conflict-check", {"actor": "body"})
    if fault == "missing_relation":
        engine.execute("DELETE FROM interventions WHERE intervention_id=?", (peer,))
    elif fault == "missing_document":
        engine.execute("DELETE FROM durable_documents WHERE collection=? AND doc_id=?", (workflow.repository._C, peer))
    else:
        engine.execute("UPDATE durable_documents SET group_key=? WHERE collection=? AND doc_id=?", ("wrong-store", workflow.repository._C, peer))
    before = native_snapshot(engine, workflow)
    decoded = []
    native = pickle.loads

    def observed(blob, *args, **kwargs):
        decoded.append(True)
        return native(blob, *args, **kwargs)

    monkeypatch.setattr(pickle, "loads", observed)
    with pytest.raises(InterventionOwnerIntegrityError):
        workflow.repository.list_by_store(STORE)
    assert decoded == []
    result = client.post(f"/api/v1/interventions/{local}/conflict-check",
        headers=lifecycle_headers, json=lifecycle_body)
    assert result.status_code == 503, result.text
    assert native_snapshot(engine, workflow) == before


@pytest.mark.parametrize("fault", ["owner", "identity", "float", "bool", "string", "null", "zero", "negative"])
def test_v3_authorized_metadata_rejects_malformed_related_aggregate(owned, fault):
    engine, client, workflow, _ = owned
    local = proposed(client, create(client))
    peer = proposed(client, create(client))
    lifecycle_body, lifecycle_headers = lifecycle_transport(client, local, "conflict-check", {"actor": "body"})
    old = workflow.get(peer)
    changes = {"owner": {"store_id": "foreign-business-store"}, "identity": {"intervention_id": "wrong-business-id"},
        "float": {"version": float(old.version)}, "bool": {"version": True}, "string": {"version": str(old.version)},
        "null": {"version": None}, "zero": {"version": 0}, "negative": {"version": -1}}[fault]
    workflow.repository._store.put(workflow.repository._C, peer, replace(old, **changes), group_key=STORE)
    before = native_snapshot(engine, workflow)
    result = client.post(f"/api/v1/interventions/{local}/conflict-check",
        headers=lifecycle_headers, json=lifecycle_body)
    assert result.status_code == 503, result.text
    assert native_snapshot(engine, workflow) == before


def test_v3_joined_payload_read_rechecks_authority_after_metadata_preflight(owned, monkeypatch):
    engine, client, workflow, _ = owned
    _, rotated_owner = add_foreign_case(client, engine)
    peer = proposed(client, create(client))
    original = workflow.repository.list_scope_ids_by_store
    decoded = observe_foreign(monkeypatch, peer)

    def rotated(store_id):
        ids = original(store_id)
        engine.execute("UPDATE interventions SET store_id=? WHERE intervention_id=?", (rotated_owner, peer))
        return ids

    monkeypatch.setattr(workflow.repository, "list_scope_ids_by_store", rotated)
    with pytest.raises(InterventionOwnerIntegrityError):
        workflow.repository.list_by_store(STORE)
    assert decoded == []


def test_v3_native_related_and_today_positive_paths_keep_conflict_semantics(owned, monkeypatch):
    engine, client, workflow, queue = owned
    local = proposed(client, create(client))
    peer = proposed(client, create(client))

    def forbidden_raw_group(*args, **kwargs):
        raise AssertionError("native related reader must never call raw list_by_group")

    monkeypatch.setattr(workflow.repository._store, "list_by_group", forbidden_raw_group)
    lifecycle_body, lifecycle_headers = lifecycle_transport(client, local, "conflict-check",
        {"actor": "body", "allow_overlap": False})
    result = client.post(f"/api/v1/interventions/{local}/conflict-check",
        headers=lifecycle_headers, json=lifecycle_body)
    assert result.status_code == 200, result.text
    assert result.json()["conflict"]["conflicting_ids"] == [peer]
    assert result.json()["conflict"]["blocks_approval"] is True
    submit_body, submit_headers = lifecycle_transport(client, local, "submit", {"actor": "body"})
    denied = client.post(f"/api/v1/interventions/{local}/submit",
        headers=submit_headers, json=submit_body)
    assert denied.status_code == 422, denied.text
    state = today_producer(engine, workflow, queue).load_state(tenant_id=TENANT)
    assert {item["id"] for item in state["workQueue"]} == {local, peer}
    assert state["_meta"]["sections"]["interventions"]["state"] == "available"


def test_v3_all_store_collection_retains_early_foreign_index_refusal(owned, monkeypatch):
    _, client, _, _ = owned
    _, foreign, _ = foreign_index(owned)
    decoded = observe_foreign(monkeypatch, foreign)
    result = client.get("/api/v1/interventions", headers=headers())
    assert result.status_code == 503, result.text
    assert decoded == []


def test_v3_current_scope_replay_does_not_reenter_corrupt_related_reader(owned, monkeypatch):
    engine, client, workflow, _ = owned
    local = proposed(client, create(client))
    key = "v3-original-related-command"
    body, hdr = lifecycle_transport(client, local, "conflict-check",
        {"actor": "body", "allow_overlap": False}, key=key)
    first = client.post(f"/api/v1/interventions/{local}/conflict-check", headers=hdr, json=body)
    assert first.status_code == 200, first.text
    foreign, _ = add_foreign_case(client, engine)
    engine.execute("UPDATE durable_documents SET group_key=? WHERE collection=? AND doc_id=?", (STORE, workflow.repository._C, foreign))
    before = native_snapshot(engine, workflow)
    decoded = observe_foreign(monkeypatch, foreign)
    replay = client.post(f"/api/v1/interventions/{local}/conflict-check", headers=hdr, json=body)
    assert replay.status_code == 200
    assert replay.json() == {**first.json(), "idempotent_replay": True}
    assert native_snapshot(engine, workflow) == before and decoded == []
    denied = client.post(f"/api/v1/interventions/{local}/conflict-check",
        headers={**hdr, "x-tenant-id": FOREIGN}, json=body)
    assert denied.status_code == 403
    assert native_snapshot(engine, workflow) == before and decoded == []
