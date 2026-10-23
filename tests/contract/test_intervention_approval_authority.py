"""Owned SQLite/native API controls for Intervention approval authority.

Principals use the existing local authentication test adapter. These controls
do not exercise production cookies, PostgreSQL, services or human approval.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes.interventions import create_interventions_router
from modules.intervention.application.workflow import InterventionWorkflow
from modules.intervention.domain.lifecycle import InterventionError, InterventionStatus
from shared.audit.worm import LocalAppendOnlyWormSink
from shared.auth import Role
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.job_queue import DurableJobQueue
from shared.infrastructure.persistence.repositories import (
    DurableBrandRepository,
    DurableInterventionRepository,
    DurableLabelRegistry,
    DurableStoreRepository,
)

TENANT = "00000000-0000-0000-0000-000000000001"
FOREIGN = "00000000-0000-0000-0000-000000000099"
BRAND = "00000000-0000-0000-0000-000000000002"
ADDRESS = "00000000-0000-0000-0000-000000000003"
STORE = "synthetic-intervention-store"
CREATOR = "synthetic-int-creator"
REVIEWER = "synthetic-int-reviewer"


def headers(actor=REVIEWER, tenant=TENANT, **extra):
    return {
        "x-subject-id": actor,
        "x-roles": ",".join((Role.OPERATIONS_MANAGER.value, Role.REGIONAL_SUPERVISOR.value)),
        "x-tenant-id": tenant,
        **extra,
    }


def app_for(engine, *, sink=None):
    from apps.api.oday_api.security.dependencies import reset_bound_persistence

    reset_bound_persistence()
    repository = DurableInterventionRepository(SqliteDocumentStore(engine))
    audit = DurableAuditLog(engine, worm_sink=sink)
    queue = DurableJobQueue(engine)
    workflow = InterventionWorkflow(repository=repository, audit_log=audit)
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
        return await call_next(request)

    app.include_router(
        create_interventions_router(
            workflow=workflow,
            label_registry=DurableLabelRegistry(SqliteDocumentStore(engine)),
            job_queue=queue,
            store_repository=DurableStoreRepository(engine),
            brand_repository=DurableBrandRepository(engine),
            require_durable_commands=True,
            require_object_scope=True,
        ),
        prefix="/api/v1",
    )
    return TestClient(app, raise_server_exceptions=False), workflow, queue


def seed_store(engine):
    engine.execute(
        "INSERT INTO tenants (tenant_id, tenant_name, status) VALUES (?, ?, ?)",
        (TENANT, "Synthetic A", "active"),
    )
    engine.execute(
        "INSERT INTO tenants (tenant_id, tenant_name, status) VALUES (?, ?, ?)",
        (FOREIGN, "Synthetic B", "active"),
    )
    engine.execute(
        "INSERT INTO brands (brand_id, tenant_id, brand_code, brand_name) VALUES (?, ?, ?, ?)",
        (BRAND, TENANT, "INT", "Synthetic"),
    )
    engine.execute(
        "INSERT INTO address_locations (address_id, raw_address) VALUES (?, ?)",
        (ADDRESS, "Synthetic address"),
    )
    engine.execute(
        "INSERT INTO stores (store_id, tenant_id, brand_id, store_name, address_id, region_code) VALUES (?, ?, ?, ?, ?, ?)",
        (STORE, TENANT, BRAND, "Synthetic intervention", ADDRESS, "synthetic-north"),
    )


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv("ODP_PRODUCT_MODE", "local")
    from apps.api.oday_api.security import dependencies

    original_principal = dependencies.principal_from_headers

    def test_principal(request_headers, **kwargs):
        principal = original_principal(request_headers, **kwargs)
        if request_headers.get("x-test-modules"):
            principal = replace(
                principal,
                scope=replace(
                    principal.scope, modules=frozenset({request_headers["x-test-modules"]})
                ),
            )
        return principal

    monkeypatch.setattr(dependencies, "principal_from_headers", test_principal)
    engine = SqliteEngine(tmp_path / "owned-intervention.sqlite")
    seed_store(engine)
    client, workflow, queue = app_for(engine)
    yield engine, client, workflow, queue
    client.close()
    engine.close()


def create(client, actor=CREATOR):
    start = datetime.now(UTC)
    response = client.post(
        "/api/v1/interventions",
        headers=headers(actor, **{"Idempotency-Key": str(uuid4())}),
        json={
            "store_id": STORE,
            "kind": "PRICE_CHANGE",
            "trigger_ref": "synthetic-alert",
            "expected_outcome": "synthetic margin review",
            "planned_start": start.isoformat(),
            "planned_end": (start + timedelta(days=7)).isoformat(),
            "created_by": "forged-body-creator",
            "action_spec": {"price_change_pct": -5, "rollback_plan": "restore"},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["created_by"] == actor
    return response.json()["intervention_id"]


def lifecycle_transport(client, iid, route, payload, actor=REVIEWER, tenant=TENANT, key=None):
    """Declared strict-caller fixture: get actual context once, before faults/replay."""
    response = client.get(f"/api/v1/interventions/{iid}/command-context",
        headers=headers(actor, tenant), params={"command": route})
    assert response.status_code == 200, response.text
    context = response.json()
    body = {**payload, "expected_version": context["version"], "command_context": context,
        "expectedActorSubjectId": actor, "expectedTenantId": tenant}
    hdr = headers(actor, tenant, **{"Idempotency-Key": key or str(uuid4()),
        "If-Match": f'W/"{context["version"]}"', "If-Actor-Match": actor,
        "x-correlation-id": str(uuid4())})
    return body, hdr


def pending(client, actor=CREATOR):
    iid = create(client, actor)
    for route, payload in [
        ("eligibility", {"eligible": True, "reasons": ["synthetic eligibility"]}),
        ("action", {"action_spec": {"price_change_pct": -5, "rollback_plan": "restore"}}),
        ("conflict-check", {"allow_overlap": False}),
        ("submit", {}),
    ]:
        lifecycle_body, lifecycle_headers = lifecycle_transport(client, iid, route,
            {"actor": "forged-body-actor", **payload}, actor=actor)
        response = client.post(
            f"/api/v1/interventions/{iid}/{route}",
            headers=lifecycle_headers,
            json=lifecycle_body,
        )
        assert response.status_code == 200, response.text
        assert response.json()["history"][-1]["actor"] == actor
    return iid


def read(client, iid, actor=REVIEWER, **extra):
    response = client.get(f"/api/v1/interventions/{iid}", headers=headers(actor, **extra))
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers["etag"] == f'W/"{result["version"]}"'
    return result


def command(client, iid, actor=REVIEWER, *, key=None):
    snapshot = read(client, iid, actor)
    return {
        "action": "APPROVE",
        "actor": "forged-body-reviewer",
        "reason": "synthetic justified approval",
        "expected_version": snapshot["version"],
        "expectedActorSubjectId": actor,
        "expectedTenantId": TENANT,
        "review_context": snapshot["review_context"],
    }, headers(
        actor,
        **{
            "If-Match": f'W/"{snapshot["version"]}"',
            "If-Actor-Match": actor,
            "Idempotency-Key": key or str(uuid4()),
        },
    )


def post(client, iid, body, hdr):
    return client.post(f"/api/v1/interventions/{iid}/approve", json=body, headers=hdr)


def business_state(engine, workflow, iid):
    return (
        workflow.get(iid).to_dict(),
        tuple(
            tuple(row)
            for row in engine.query(
                "SELECT intervention_id, status, approved_action_json FROM interventions ORDER BY intervention_id"
            )
        ),
        tuple(
            tuple(row)
            for row in engine.query(
                "SELECT job_id, status, payload_json FROM durable_jobs ORDER BY job_id"
            )
        ),
        tuple(
            event.to_dict()
            for event in workflow.audit_log.list_events()
            if event.event_type == "intervention.lifecycle.v1"
        ),
    )


def test_verified_actor_durable_approval_replay_and_execution(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid, key="synthetic-replay")
    approved = post(client, iid, body, hdr)
    assert approved.status_code == 200, approved.text
    value = approved.json()
    assert value["approval"]["actor_id"] == REVIEWER
    assert value["approval"]["approved"] is True
    assert value["approval"]["decision_reason"] == body["reason"]
    assert value["approval"]["approved_at"] and value["approval"]["policy_version"]
    assert value["version"] == body["expected_version"] + 1
    assert value["audit_event_id"] and value["correlation_id"]
    state = business_state(engine, workflow, iid)
    replay = post(client, iid, body, hdr)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**value, "idempotent_replay": True}
    assert business_state(engine, workflow, iid) == state
    execution_body, execution_headers = lifecycle_transport(client, iid, "execute",
        {"executor": "forged-executor"})
    execution = client.post(
        f"/api/v1/interventions/{iid}/execute",
        headers=execution_headers,
        json=execution_body,
    )
    assert execution.status_code == 200, execution.text
    assert execution.json()["status"] == "OBSERVING"
    assert execution.json()["execution"]["executor"] == REVIEWER
    assert execution.json()["observation_window"] is not None
    restored = DurableInterventionRepository(SqliteDocumentStore(engine)).get(iid)
    assert restored.approval.actor_id == REVIEWER
    assert workflow.audit_log.verify_chain().ok


def test_creator_cannot_approve_with_forged_actor_but_can_reject(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid, CREATOR)
    state = business_state(engine, workflow, iid)
    denied = post(client, iid, body, hdr)
    assert denied.status_code == 403, denied.text
    assert business_state(engine, workflow, iid) == state
    body["action"] = "REJECT"
    body["reason"] = "synthetic creator withdrawal"
    allowed = post(client, iid, body, hdr)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["approval"]["actor_id"] == CREATOR
    assert allowed.json()["approval"]["approved"] is False


@pytest.mark.parametrize(
    "case, expected",
    [
        ("version_missing", 428),
        ("version_bool", 422),
        ("version_string", 422),
        ("version_float", 422),
        ("etag_missing", 428),
        ("etag_invalid", 422),
        ("etag_mismatch", 409),
        ("actor_header_missing", 409),
        ("actor_header_other", 409),
        ("actor_body_other", 409),
        ("tenant_body_other", 409),
        ("context_missing", 428),
        ("context_actor", 409),
        ("context_tenant", 409),
        ("context_store", 409),
        ("context_id", 409),
        ("context_version", 409),
        ("context_policy", 409),
        ("context_digest", 409),
        ("context_extra", 422),
        ("reason_blank", 422),
        ("action_invalid", 422),
        ("key_missing", 428),
    ],
)
def test_original_read_preconditions_reject_without_business_writes(owned, case, expected):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    state = business_state(engine, workflow, iid)
    if case == "version_missing":
        body.pop("expected_version")
    elif case == "version_bool":
        body["expected_version"] = True
    elif case == "version_string":
        body["expected_version"] = str(body["expected_version"])
    elif case == "version_float":
        body["expected_version"] = float(body["expected_version"])
    elif case == "etag_missing":
        hdr.pop("If-Match")
    elif case == "etag_invalid":
        hdr["If-Match"] = str(body["expected_version"])
    elif case == "etag_mismatch":
        hdr["If-Match"] = 'W/"999"'
    elif case == "actor_header_missing":
        hdr.pop("If-Actor-Match")
    elif case == "actor_header_other":
        hdr["If-Actor-Match"] = CREATOR
    elif case == "actor_body_other":
        body["expectedActorSubjectId"] = CREATOR
    elif case == "tenant_body_other":
        body["expectedTenantId"] = FOREIGN
    elif case == "context_missing":
        body.pop("review_context")
    elif case == "context_extra":
        body["review_context"]["forged"] = True
    elif case.startswith("context_"):
        field = {
            "actor": "actor_subject_id",
            "tenant": "tenant_id",
            "store": "store_id",
            "id": "intervention_id",
            "version": "version",
            "policy": "policy_version",
            "digest": "snapshot_sha256",
        }[case.removeprefix("context_")]
        body["review_context"][field] = (
            999 if field == "version" else "0" * 64 if field == "snapshot_sha256" else "forged"
        )
    elif case == "reason_blank":
        body["reason"] = "   "
    elif case == "action_invalid":
        body["action"] = "EXECUTE"
    elif case == "key_missing":
        hdr.pop("Idempotency-Key")
    response = post(client, iid, body, hdr)
    assert response.status_code == expected, response.text
    assert business_state(engine, workflow, iid) == state


@pytest.mark.parametrize(
    "restriction",
    [
        "tenant",
        "tenant_missing",
        "store",
        "brand",
        "region",
        "module",
        "clearance",
        "assigned_area",
        "heat_zone",
    ],
)
def test_trusted_object_scope_before_reads_mutation_and_replay(owned, restriction):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid, key="synthetic-scope-replay")
    assert post(client, iid, body, hdr).status_code == 200
    state = business_state(engine, workflow, iid)
    if restriction == "tenant":
        hdr["x-tenant-id"] = FOREIGN
    elif restriction == "tenant_missing":
        hdr.pop("x-tenant-id")
    else:
        key = {
            "store": "x-store-ids",
            "brand": "x-brand-ids",
            "region": "x-region-ids",
            "module": "x-test-modules",
            "clearance": "x-clearance",
            "assigned_area": "x-assigned-area-ids",
            "heat_zone": "x-heat-zone-ids",
        }[restriction]
        hdr[key] = "public" if restriction == "clearance" else "synthetic-outside-scope"
    for path in (f"/{iid}", f"/{iid}/label", f"?store_id={STORE}"):
        response = client.get("/api/v1/interventions" + path, headers=hdr)
        assert response.status_code == 403, (restriction, path, response.text)
    assert post(client, iid, body, hdr).status_code == 403
    assert business_state(engine, workflow, iid) == state


def test_collection_filters_canonical_tenant_and_store_before_case_reads(owned):
    _, client, _, _ = owned
    iid = pending(client)
    assert (
        client.get("/api/v1/interventions", headers=headers()).json()["items"][0]["intervention_id"]
        == iid
    )
    assert client.get("/api/v1/interventions", headers=headers(tenant=FOREIGN)).json() == {
        "items": [],
        "count": 0,
    }
    assert client.get(
        "/api/v1/interventions", headers=headers(**{"x-store-ids": "outside"})
    ).json() == {"items": [], "count": 0}


def test_store_ownership_change_rejects_old_success_replay(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid, key="synthetic-ownership-change")
    assert post(client, iid, body, hdr).status_code == 200
    engine.execute("UPDATE stores SET tenant_id = ? WHERE store_id = ?", (FOREIGN, STORE))
    state = business_state(engine, workflow, iid)
    denied = post(client, iid, body, hdr)
    assert denied.status_code == 403, denied.text
    assert business_state(engine, workflow, iid) == state


def test_actor_change_cannot_replay_other_subject_response(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid, key="synthetic-actor-change")
    assert post(client, iid, body, hdr).status_code == 200
    state = business_state(engine, workflow, iid)
    actor = "synthetic-int-other-reviewer"
    hdr.update(headers(actor), **{"If-Actor-Match": actor})
    body["expectedActorSubjectId"] = actor
    body["review_context"]["actor_subject_id"] = actor
    denied = post(client, iid, body, hdr)
    assert denied.status_code == 409, denied.text
    assert business_state(engine, workflow, iid) == state


def test_stale_original_read_cannot_be_refreshed_implicitly(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    assignment_body, assignment_headers = lifecycle_transport(client, iid, "assign",
        {"assignee": REVIEWER, "actor": "forged", "expected_version": body["expected_version"]})
    assignment = client.post(
        f"/api/v1/interventions/{iid}/assign",
        headers=assignment_headers,
        json=assignment_body,
    )
    assert assignment.status_code == 200, assignment.text
    state = business_state(engine, workflow, iid)
    assert post(client, iid, body, hdr).status_code == 409
    assert business_state(engine, workflow, iid) == state


def test_relational_status_disagreement_cannot_be_overwritten_by_approval(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    engine.execute(
        "UPDATE interventions SET status = ? WHERE intervention_id = ?", ("approved", iid)
    )
    state = business_state(engine, workflow, iid)
    response = post(client, iid, body, hdr)
    assert response.status_code == 409, response.text
    assert business_state(engine, workflow, iid) == state


def test_legacy_body_creator_is_not_upgraded_by_old_lifecycle_audit(owned):
    engine, client, workflow, _ = owned
    start = datetime.now(UTC)
    case = workflow.open_case(
        store_id=STORE,
        kind="PRICE_CHANGE",
        trigger_ref="synthetic-old",
        expected_outcome="synthetic old case",
        planned_start=start,
        planned_end=start + timedelta(days=7),
        created_by=CREATOR,
        correlation_id="synthetic-old-body-correlation",
    )
    workflow.check_eligibility(case.intervention_id, eligible=True, actor=CREATOR)
    workflow.propose_action(case.intervention_id, action_spec={}, actor=CREATOR)
    workflow.check_conflict(case.intervention_id, actor=CREATOR)
    workflow.submit_for_approval(case.intervention_id, actor=CREATOR)
    snapshot = read(client, case.intervention_id)
    assert snapshot["creator_verification"] == "unverified_legacy_record"
    body, hdr = command(client, case.intervention_id)
    state = business_state(engine, workflow, case.intervention_id)
    response = post(client, case.intervention_id, body, hdr)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "CREATOR_VERIFICATION_REQUIRED"
    assert business_state(engine, workflow, case.intervention_id) == state


@pytest.mark.parametrize("failure", ["audit", "receipt", "relational"])
def test_decision_audit_receipt_and_mirror_roll_back_together(owned, monkeypatch, failure):
    engine, client, workflow, queue = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    state = business_state(engine, workflow, iid)
    if failure == "audit":
        original = workflow.audit_log.record

        def fail(event):
            if event.event_type == "intervention.lifecycle.v1" and event.action == "approve":
                raise RuntimeError("synthetic audit fault after domain CAS")
            return original(event)

        monkeypatch.setattr(workflow.audit_log, "record", fail)
    elif failure == "receipt":
        original = queue.update_status

        def fail(*args, **kwargs):
            payload = kwargs.get("payload", {})
            if (
                payload.get("command_scope") == f"interventions:{iid}:decision"
                and payload.get("response") is not None
            ):
                raise RuntimeError("synthetic final receipt fault")
            return original(*args, **kwargs)

        monkeypatch.setattr(queue, "update_status", fail)
    else:
        monkeypatch.setattr(
            workflow.repository,
            "_sync_sql",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("synthetic SQL mirror fault")
            ),
        )
    result = post(client, iid, body, hdr)
    assert result.status_code == 503, result.text
    assert business_state(engine, workflow, iid) == state


def test_owned_worm_delivery_intent_is_atomic_and_not_sent_inline(owned, tmp_path, monkeypatch):
    engine, old_client, _, _ = owned
    sink = LocalAppendOnlyWormSink(tmp_path / "owned-worm")
    client, workflow, queue = app_for(engine, sink=sink)
    try:
        iid = pending(client)
        body, hdr = command(client, iid)
        state = business_state(engine, workflow, iid)
        delivery_before = engine.query_one(
            "SELECT COUNT(*) AS count FROM durable_documents WHERE collection LIKE ?",
            ("%audit.durable_delivery%",),
        )["count"]
        original = queue.update_status

        def fail(*args, **kwargs):
            if kwargs.get("payload", {}).get("command_scope") == f"interventions:{iid}:decision":
                raise RuntimeError("synthetic receipt fault after outbox enqueue")
            return original(*args, **kwargs)

        monkeypatch.setattr(queue, "update_status", fail)
        result = post(client, iid, body, hdr)
        assert result.status_code == 503, result.text
        assert business_state(engine, workflow, iid) == state
        assert (
            engine.query_one(
                "SELECT COUNT(*) AS count FROM durable_documents WHERE collection LIKE ?",
                ("%audit.durable_delivery%",),
            )["count"]
            == delivery_before
        )
        # Independent security audit records may be written outside the command;
        # the rolled-back business approval never reaches the external sink.
        assert not any(
            '"action":"approve"' in p.read_text()
            and '"event_type":"intervention.lifecycle.v1"' in p.read_text()
            for p in (tmp_path / "owned-worm").rglob("*.json")
        )
    finally:
        client.close()


def test_equal_version_two_independent_sqlite_writers_only_one_approval(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    second_engine = SqliteEngine(engine.path)
    second_client, second_workflow, _ = app_for(second_engine)
    try:
        one = command(client, iid, key="synthetic-writer-one")
        two = command(second_client, iid, key="synthetic-writer-two")
        assert one[0]["expected_version"] == two[0]["expected_version"]
        barrier = Barrier(2)

        def execute(args):
            target, command_args = args
            barrier.wait(timeout=5)
            return post(target, iid, *command_args)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, [(client, one), (second_client, two)]))
        assert sorted(result.status_code for result in results) == [200, 409], [
            r.text for r in results
        ]
        assert workflow.get(iid).version == one[0]["expected_version"] + 1
        assert second_workflow.get(iid).approval.actor_id == REVIEWER
        assert (
            len(
                [
                    e
                    for e in workflow.audit_log.list_events()
                    if e.event_type == "intervention.lifecycle.v1" and e.action == "approve"
                ]
            )
            == 1
        )
        with pytest.raises(InterventionError, match="stale update"):
            second_workflow.repository.save_expected(
                workflow.get(iid),
                expected_version=one[0]["expected_version"],
                expected_status=InterventionStatus.PENDING_APPROVAL,
            )
    finally:
        second_client.close()
        second_engine.close()


def test_missing_canonical_store_and_mixed_engines_fail_closed(owned, tmp_path):
    engine, client, workflow, _ = owned
    iid = pending(client)
    other = SqliteEngine(tmp_path / "owned-other.sqlite")
    try:
        app = FastAPI()

        @app.middleware("http")
        async def correlation(request, call_next):
            request.state.correlation_id = str(uuid4())
            return await call_next(request)

        app.include_router(
            create_interventions_router(
                workflow=workflow,
                job_queue=DurableJobQueue(other),
                store_repository=DurableStoreRepository(engine),
                brand_repository=DurableBrandRepository(engine),
                require_durable_commands=True,
            ),
            prefix="/api/v1",
        )
        body, hdr = command(client, iid)
        state = business_state(engine, workflow, iid)
        with TestClient(app, raise_server_exceptions=False) as mixed:
            response = post(mixed, iid, body, hdr)
            assert response.status_code == 503, response.text
        assert business_state(engine, workflow, iid) == state
        assert other.query_one("SELECT COUNT(*) AS count FROM durable_jobs")["count"] == 0
        missing = FastAPI()
        missing.middleware("http")(correlation)
        missing.include_router(
            create_interventions_router(
                workflow=workflow, job_queue=DurableJobQueue(engine), require_durable_commands=True
            ),
            prefix="/api/v1",
        )
        with TestClient(missing, raise_server_exceptions=False) as target:
            assert target.get(f"/api/v1/interventions/{iid}", headers=headers()).status_code == 503
    finally:
        other.close()


def test_native_workflow_segregation_conflict_and_exact_save(owned):
    _, client, workflow, _ = owned
    iid = pending(client)
    current = workflow.get(iid)
    with pytest.raises(InterventionError, match="creator cannot approve"):
        workflow.approve(iid, actor=CREATOR, reason="synthetic", expected_version=current.version)
    with pytest.raises(InterventionError, match="stale update"):
        workflow.approve(
            iid, actor=REVIEWER, reason="synthetic", expected_version=current.version - 1
        )
    blocked = replace(
        current,
        conflict=replace(current.conflict, has_conflict=True, resolved=False),
        version=current.version + 1,
    )
    workflow.repository.save(blocked)
    with pytest.raises(InterventionError, match="unresolved conflict"):
        workflow.approve(iid, actor=REVIEWER, reason="synthetic", expected_version=blocked.version)
    assert workflow.get(iid).status is InterventionStatus.PENDING_APPROVAL


def test_franchisee_read_requires_own_store_and_never_grants_approval(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    hdr["x-roles"] = Role.FRANCHISEE.value
    state = business_state(engine, workflow, iid)
    assert client.get(f"/api/v1/interventions/{iid}", headers=hdr).status_code == 403
    hdr["x-store-ids"] = STORE
    assert client.get(f"/api/v1/interventions/{iid}", headers=hdr).status_code == 200
    assert post(client, iid, body, hdr).status_code == 403
    assert business_state(engine, workflow, iid) == state


def test_inconsistent_canonical_brand_tenant_fails_closed(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    engine.execute("UPDATE brands SET tenant_id = ? WHERE brand_id = ?", (FOREIGN, BRAND))
    state = business_state(engine, workflow, iid)
    assert client.get(f"/api/v1/interventions/{iid}", headers=hdr).status_code == 503
    assert post(client, iid, body, hdr).status_code == 503
    assert business_state(engine, workflow, iid) == state


def test_direct_two_writer_strict_save_expected_rejects_equal_version(owned):
    from modules.intervention.domain.lifecycle import ApprovalRecord

    engine, client, workflow, _ = owned
    iid = pending(client)
    original = workflow.get(iid)
    second_engine = SqliteEngine(engine.path)
    second_repo = DurableInterventionRepository(SqliteDocumentStore(second_engine))
    barrier = Barrier(2)

    def candidate(actor, approved):
        return original.with_transition(
            to_status=InterventionStatus.APPROVED if approved else InterventionStatus.REJECTED,
            actor=actor,
            action="approve" if approved else "reject",
            reason="synthetic storage CAS",
            approval=ApprovalRecord(
                approved=approved,
                actor_id=actor,
                decision_reason="synthetic storage CAS",
                approved_at=datetime.now(UTC),
                policy_version=original.policy_version,
            ),
        )

    def save(args):
        repo, value = args
        barrier.wait(timeout=5)
        try:
            return repo.save_expected(
                value,
                expected_version=original.version,
                expected_status=InterventionStatus.PENDING_APPROVAL,
            )
        except InterventionError as exc:
            return exc

    try:
        values = [candidate(REVIEWER, True), candidate("synthetic-other-reviewer", False)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(
                pool.map(save, [(workflow.repository, values[0]), (second_repo, values[1])])
            )
        winners = [value for value in outcomes if not isinstance(value, InterventionError)]
        failures = [value for value in outcomes if isinstance(value, InterventionError)]
        assert len(winners) == len(failures) == 1
        assert "stale update" in str(failures[0])
        persisted = second_repo.get(iid)
        assert persisted == winners[0]
        assert persisted.version == original.version + 1
        import json

        mirror = engine.query_one(
            "SELECT status, approved_action_json FROM interventions WHERE intervention_id = ?",
            (iid,),
        )
        assert mirror["status"] == persisted.status.value.lower()
        assert json.loads(mirror["approved_action_json"])["actor_id"] == persisted.approval.actor_id
    finally:
        second_engine.close()


def test_production_mode_cannot_select_legacy_none_version_http_path(owned, monkeypatch):
    from apps.api.oday_api.security import dependencies
    from shared.auth import Principal, Scope

    engine, client, workflow, queue = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    body.pop("expected_version")
    monkeypatch.setenv("ODP_PRODUCT_MODE", "production")
    # Explicit synthetic credential adapter; this control checks route mode,
    # not production authentication, and creates no real account or grant.
    monkeypatch.setattr(
        dependencies,
        "principal_from_headers",
        lambda *args, **kwargs: Principal(
            subject_id=REVIEWER,
            roles=frozenset({Role.OPERATIONS_MANAGER}),
            scope=Scope(tenant_id=TENANT),
        ),
    )
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = str(uuid4())
        return await call_next(request)

    app.include_router(
        create_interventions_router(
            workflow=workflow,
            job_queue=queue,
            store_repository=DurableStoreRepository(engine),
            brand_repository=DurableBrandRepository(engine),
            require_durable_commands=False,
            require_object_scope=False,
        ),
        prefix="/api/v1",
    )
    state = business_state(engine, workflow, iid)
    with TestClient(app, raise_server_exceptions=False) as production:
        response = post(production, iid, body, hdr)
        assert response.status_code == 428, response.text
    assert business_state(engine, workflow, iid) == state


def test_read_scope_cannot_use_a_different_canonical_store_engine(owned, tmp_path):
    engine, client, workflow, queue = owned
    iid = pending(client)
    engine.execute("UPDATE stores SET tenant_id = ? WHERE store_id = ?", (FOREIGN, STORE))
    engine.execute("UPDATE brands SET tenant_id = ? WHERE brand_id = ?", (FOREIGN, BRAND))
    other = SqliteEngine(tmp_path / "owned-false-scope.sqlite")
    seed_store(other)
    app = FastAPI()

    @app.middleware("http")
    async def correlation(request, call_next):
        request.state.correlation_id = str(uuid4())
        return await call_next(request)

    app.include_router(
        create_interventions_router(
            workflow=workflow,
            job_queue=queue,
            store_repository=DurableStoreRepository(other),
            brand_repository=DurableBrandRepository(other),
            require_durable_commands=True,
        ),
        prefix="/api/v1",
    )
    state = business_state(engine, workflow, iid)
    try:
        with TestClient(app, raise_server_exceptions=False) as mixed:
            for path in (f"/api/v1/interventions/{iid}", "/api/v1/interventions"):
                response = mixed.get(path, headers=headers())
                assert response.status_code == 503, response.text
        assert business_state(engine, workflow, iid) == state
    finally:
        other.close()


def test_label_lookup_verifies_persisted_intervention_and_store_identity(owned):
    from modules.intervention.domain.lifecycle import EvidenceLevel, LabelRecord

    engine, client, workflow, _ = owned
    iid = pending(client)
    now = datetime.now(UTC)
    label = LabelRecord(
        intervention_id=iid,
        store_id=STORE,
        treatment_type="PRICE_CHANGE",
        outcome_window_start=now,
        outcome_window_end=now + timedelta(days=21),
        label_maturity_time=now + timedelta(days=28),
        is_mature=False,
        evidence_level=EvidenceLevel.L0_ANECDOTAL,
        incremental_revenue=0.0,
        incremental_gross_margin=0.0,
        iromi=None,
        measurement_method="synthetic fixture",
        can_claim_effect=False,
        can_claim_causal=False,
    )
    registry = DurableLabelRegistry(SqliteDocumentStore(engine))
    registry(label)
    response = client.get(f"/api/v1/interventions/{iid}/label", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json() == label.to_dict()
    registry(replace(label, store_id="synthetic-foreign-store"))
    state = business_state(engine, workflow, iid)
    response = client.get(f"/api/v1/interventions/{iid}/label", headers=headers())
    assert response.status_code == 503, response.text
    assert business_state(engine, workflow, iid) == state


def test_foreign_scope_never_reads_intervention_business_aggregate(owned, monkeypatch):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid, key="synthetic-unread-foreign")
    assert post(client, iid, body, hdr).status_code == 200
    state = business_state(engine, workflow, iid)
    original_get = workflow.repository.get
    reads = []

    def tracked_get(*args, **kwargs):
        reads.append(args[0])
        return original_get(*args, **kwargs)

    monkeypatch.setattr(workflow.repository, "get", tracked_get)
    hdr["x-tenant-id"] = FOREIGN
    assert client.get(f"/api/v1/interventions/{iid}", headers=hdr).status_code == 403
    assert post(client, iid, body, hdr).status_code == 403
    assert reads == []
    monkeypatch.setattr(workflow.repository, "get", original_get)
    assert business_state(engine, workflow, iid) == state


def test_postgresql_owner_metadata_rejects_malformed_id_before_any_query():
    from types import SimpleNamespace

    queries = []
    engine = SimpleNamespace(dialect="postgresql", query_one=lambda *args: queries.append(args))
    repository = DurableInterventionRepository.__new__(DurableInterventionRepository)
    repository._store = SimpleNamespace(engine=engine)
    assert repository.store_id_for_scope("synthetic-not-a-uuid", for_update=True) is None
    assert queries == []


@pytest.mark.parametrize("malformed", ["float", "bool"])
def test_v2_stored_float_or_bool_never_gain_fresh_approval(owned, malformed):
    from shared.api.idempotency import request_fingerprint

    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid)
    current = workflow.get(iid)
    corrupt = replace(current, version=float(current.version) if malformed == "float" else True)
    workflow.repository._store.put(workflow.repository._C, iid, corrupt, group_key=current.store_id)
    # Hostile caller can know the exact bad stored digest, but cannot turn its
    # bool/float value into a valid persisted native version by JSON coercion.
    expected = int(corrupt.version)
    body["expected_version"] = expected
    body["review_context"].update(
        version=expected, snapshot_sha256=request_fingerprint(corrupt.to_dict())
    )
    hdr["If-Match"] = f'W/"{expected}"'
    state = business_state(engine, workflow, iid)
    result = post(client, iid, body, hdr)
    after = business_state(engine, workflow, iid)
    record_v2_fact(
        "stored-" + malformed,
        {
            "httpStatus": result.status_code,
            "responseStatus": result.json().get("status"),
            "responseVersion": result.json().get("version"),
            "domainChanged": after != state,
            "storedVersionType": type(corrupt.version).__name__,
        },
    )
    assert result.status_code == 409, result.text
    assert after == state


def add_foreign_case(client, engine):
    brand = "00000000-0000-0000-0000-000000000098"
    store = "synthetic-v2-foreign-store"
    engine.execute(
        "INSERT INTO brands (brand_id,tenant_id,brand_code,brand_name) VALUES (?,?,?,?)",
        (brand, FOREIGN, "INT-FOREIGN", "Synthetic foreign"),
    )
    engine.execute(
        "INSERT INTO stores (store_id,tenant_id,brand_id,store_name,address_id) VALUES (?,?,?,?,?)",
        (store, FOREIGN, brand, "Synthetic foreign", ADDRESS),
    )
    now = datetime.now(UTC)
    response = client.post(
        "/api/v1/interventions",
        headers=headers(CREATOR, FOREIGN, **{"Idempotency-Key": str(uuid4())}),
        json={
            "store_id": store,
            "kind": "PRICE_CHANGE",
            "expected_outcome": "synthetic foreign outcome",
            "planned_start": now.isoformat(),
            "planned_end": (now + timedelta(days=7)).isoformat(),
            "created_by": "forged foreign creator",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["intervention_id"], store


def record_v2_fact(name, value):
    import json
    import os
    from pathlib import Path

    directory = os.environ.get("INTERVENTION_CONTROL_EVIDENCE_DIRECTORY")
    if directory:
        path = Path(directory) / (name + ".json")
        assert not path.exists()
        path.write_text(
            json.dumps(
                {
                    "kind": "author actual owned SQLite/native API v2 gap control",
                    "productionAuthentication": False,
                    "PG": 0,
                    "privateDSNReads": 0,
                    **value,
                },
                indent=2,
            )
            + "\n"
        )
        path.chmod(0o444)


def test_v2_foreign_group_index_rejected_before_any_unpickle(owned, monkeypatch):
    import pickle

    engine, client, workflow, _ = owned
    iid, foreign_store = add_foreign_case(client, engine)
    engine.execute(
        "UPDATE durable_documents SET group_key=? WHERE collection=? AND doc_id=?",
        (STORE, workflow.repository._C, iid),
    )
    assert (
        engine.query_one("SELECT store_id FROM interventions WHERE intervention_id=?", (iid,))[
            "store_id"
        ]
        == foreign_store
    )
    native_load = pickle.loads
    unpickled = []

    def observed(blob, *args, **kwargs):
        value = native_load(blob, *args, **kwargs)
        unpickled.append(getattr(value, "intervention_id", None))
        return value

    monkeypatch.setattr(pickle, "loads", observed)
    response = client.get("/api/v1/interventions", headers=headers())
    items = response.json().get("items", [])
    record_v2_fact(
        "foreign-group-index",
        {
            "httpStatus": response.status_code,
            "foreignCaseExported": any(item.get("intervention_id") == iid for item in items),
            "unpickledInterventionIds": unpickled,
            "relationalOwnerPreserved": True,
        },
    )
    assert response.status_code == 503, response.text
    assert unpickled == []
    assert not items


@pytest.mark.parametrize("value", [5.0, True, "5", None, 0, -1])
def test_v2_invalid_stored_version_cannot_issue_read_context(owned, value):
    engine, client, workflow, _ = owned
    iid = pending(client)
    current = workflow.get(iid)
    workflow.repository._store.put(
        workflow.repository._C, iid, replace(current, version=value), group_key=current.store_id
    )
    state = business_state(engine, workflow, iid)
    result = client.get(f"/api/v1/interventions/{iid}", headers=headers())
    assert result.status_code == 409, result.text
    assert "review_context" not in result.json()
    assert business_state(engine, workflow, iid) == state


@pytest.mark.parametrize("repository_kind", ["durable", "memory"])
@pytest.mark.parametrize("value", [6.0, True, "6", None, 0, -1])
def test_v2_strict_cas_rejects_invalid_actual_next_version(owned, repository_kind, value):
    from modules.intervention.infrastructure.repositories import InMemoryInterventionRepository

    engine, client, workflow, _ = owned
    iid = pending(client)
    current = workflow.get(iid)
    repo = workflow.repository
    if repository_kind == "memory":
        repo = InMemoryInterventionRepository()
        repo.save(current)
    candidate = current.with_transition(
        to_status=InterventionStatus.APPROVED,
        actor=REVIEWER,
        action="approve",
        reason="synthetic storage guard",
    )
    candidate = replace(candidate, version=value)
    state = business_state(engine, workflow, iid)
    with pytest.raises(InterventionError, match="positive built-in integer"):
        repo.save_expected(
            candidate,
            expected_version=current.version,
            expected_status=InterventionStatus.PENDING_APPROVAL,
        )
    assert repo.get(iid) == current
    assert business_state(engine, workflow, iid) == state


@pytest.mark.parametrize("value", [5.0, True])
def test_v2_direct_strict_workflow_rejects_invalid_actual_current_version(owned, value):
    engine, client, workflow, _ = owned
    iid = pending(client)
    current = workflow.get(iid)
    workflow.repository._store.put(
        workflow.repository._C, iid, replace(current, version=value), group_key=current.store_id
    )
    state = business_state(engine, workflow, iid)
    with pytest.raises(InterventionError, match="positive built-in integer"):
        workflow.approve(iid, actor=REVIEWER, reason="synthetic", expected_version=int(value))
    assert business_state(engine, workflow, iid) == state


def test_v2_original_receipt_replay_preserves_authority_without_repairing_bad_version(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    body, hdr = command(client, iid, key="synthetic-v2-original-receipt")
    first = post(client, iid, body, hdr)
    assert first.status_code == 200, first.text
    approved = workflow.get(iid)
    workflow.repository._store.put(
        workflow.repository._C,
        iid,
        replace(approved, version=float(approved.version)),
        group_key=approved.store_id,
    )
    state = business_state(engine, workflow, iid)
    replay = post(client, iid, body, hdr)
    assert replay.status_code == 200, replay.text
    assert replay.json() == {**first.json(), "idempotent_replay": True}
    assert business_state(engine, workflow, iid) == state
    fresh_headers = {**hdr, "Idempotency-Key": "synthetic-v2-fresh-after-corruption"}
    fresh = post(client, iid, body, fresh_headers)
    assert fresh.status_code == 409, fresh.text
    assert business_state(engine, workflow, iid) == state


def storage_snapshot(engine, workflow):
    return (
        tuple(
            tuple(row)
            for row in engine.query(
                "SELECT doc_id,group_key,data FROM durable_documents WHERE collection=? ORDER BY doc_id",
                (workflow.repository._C,),
            )
        ),
        tuple(
            tuple(row)
            for row in engine.query(
                "SELECT intervention_id,store_id,status,approved_action_json FROM interventions ORDER BY intervention_id"
            )
        ),
        tuple(
            tuple(row)
            for row in engine.query(
                "SELECT job_id,status,payload_json FROM durable_jobs ORDER BY job_id"
            )
        ),
        tuple(
            event.to_dict()
            for event in workflow.audit_log.list_events()
            if event.event_type == "intervention.lifecycle.v1"
        ),
    )


@pytest.mark.parametrize("fault", ["wrong_group", "missing_relation", "missing_document"])
def test_v2_owner_index_mismatches_fail_before_payload_reads_without_repair(
    owned, monkeypatch, fault
):
    import pickle

    engine, client, workflow, _ = owned
    iid = pending(client)
    if fault == "wrong_group":
        engine.execute(
            "UPDATE durable_documents SET group_key=? WHERE collection=? AND doc_id=?",
            ("synthetic-other-store", workflow.repository._C, iid),
        )
    elif fault == "missing_relation":
        engine.execute("DELETE FROM interventions WHERE intervention_id=?", (iid,))
    else:
        engine.execute(
            "DELETE FROM durable_documents WHERE collection=? AND doc_id=?",
            (workflow.repository._C, iid),
        )
    state = storage_snapshot(engine, workflow)
    native_load = pickle.loads
    unpickled = []

    def observed(blob, *args, **kwargs):
        unpickled.append(True)
        return native_load(blob, *args, **kwargs)

    monkeypatch.setattr(pickle, "loads", observed)
    response = client.get("/api/v1/interventions", headers=headers())
    assert response.status_code == 503, response.text
    assert unpickled == []
    assert "items" not in response.json()
    assert storage_snapshot(engine, workflow) == state


@pytest.mark.parametrize("fault", ["aggregate_store", "aggregate_id"])
def test_v2_authorized_metadata_requires_matching_decoded_aggregate(owned, fault):
    engine, client, workflow, _ = owned
    iid = pending(client)
    current = workflow.get(iid)
    corrupt = (
        replace(current, store_id="synthetic-foreign-store")
        if fault == "aggregate_store"
        else replace(current, intervention_id=str(uuid4()))
    )
    workflow.repository._store.put(workflow.repository._C, iid, corrupt, group_key=current.store_id)
    state = storage_snapshot(engine, workflow)
    response = client.get("/api/v1/interventions", headers=headers())
    assert response.status_code == 503, response.text
    assert "items" not in response.json()
    assert storage_snapshot(engine, workflow) == state


def test_v2_legitimate_relational_list_filters_remain_available(owned):
    _, client, _, _ = owned
    iid = pending(client)
    correct = client.get(
        "/api/v1/interventions",
        headers=headers(),
        params={"store_id": STORE, "kind": "PRICE_CHANGE", "status": "PENDING_APPROVAL"},
    )
    assert correct.status_code == 200, correct.text
    assert [item["intervention_id"] for item in correct.json()["items"]] == [iid]
    filtered = client.get(
        "/api/v1/interventions", headers=headers(), params={"assigned_to": "synthetic-other"}
    )
    assert filtered.status_code == 200 and filtered.json() == {"items": [], "count": 0}


@pytest.mark.parametrize(
    "query",
    [
        {"status": "synthetic-invalid"},
        {"kind": "synthetic-invalid"},
        {"status": "synthetic-invalid", "assigned_to": "synthetic-none"},
    ],
)
def test_v2_empty_relational_list_preserves_invalid_filter_validation(owned, query):
    _, client, _, _ = owned
    response = client.get("/api/v1/interventions", headers=headers(), params=query)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("value", [5.0, True, "5", None])
def test_v2_optional_version_http_command_cannot_write_invalid_current_version(owned, value):
    engine, client, workflow, _ = owned
    iid = pending(client)
    lifecycle_body, lifecycle_headers = lifecycle_transport(client, iid, "assign",
        {"actor": "forged", "assignee": REVIEWER, "expected_version": None})
    current = workflow.get(iid)
    workflow.repository._store.put(
        workflow.repository._C, iid, replace(current, version=value), group_key=current.store_id
    )
    state = business_state(engine, workflow, iid)
    response = client.post(
        f"/api/v1/interventions/{iid}/assign",
        headers=lifecycle_headers,
        json=lifecycle_body,
    )
    assert response.status_code == 409, response.text
    assert business_state(engine, workflow, iid) == state


def test_v2_invalid_next_version_audit_fence_rolls_back_generic_command(owned, monkeypatch):
    from modules.intervention.domain.lifecycle import Intervention

    engine, client, workflow, _ = owned
    iid = pending(client)
    lifecycle_body, lifecycle_headers = lifecycle_transport(client, iid, "assign",
        {"actor": "forged", "assignee": REVIEWER, "expected_version": None})
    state = business_state(engine, workflow, iid)
    original = Intervention.with_transition

    def bad_next_version(self, **kwargs):
        updated = original(self, **kwargs)
        return (
            replace(updated, version=float(updated.version))
            if kwargs.get("action") == "assign"
            else updated
        )

    monkeypatch.setattr(Intervention, "with_transition", bad_next_version)
    response = client.post(
        f"/api/v1/interventions/{iid}/assign",
        headers=lifecycle_headers,
        json=lifecycle_body,
    )
    assert response.status_code == 409, response.text
    assert business_state(engine, workflow, iid) == state


def test_v2_generic_command_rejects_decoded_owner_mismatch_without_repair(owned):
    engine, client, workflow, _ = owned
    iid = pending(client)
    lifecycle_body, lifecycle_headers = lifecycle_transport(client, iid, "assign",
        {"actor": "forged", "assignee": REVIEWER, "expected_version": None})
    current = workflow.get(iid)
    workflow.repository._store.put(
        workflow.repository._C,
        iid,
        replace(current, store_id="synthetic-foreign-store"),
        group_key=current.store_id,
    )
    state = storage_snapshot(engine, workflow)
    response = client.post(
        f"/api/v1/interventions/{iid}/assign",
        headers=lifecycle_headers,
        json=lifecycle_body,
    )
    assert response.status_code == 409, response.text
    assert storage_snapshot(engine, workflow) == state
