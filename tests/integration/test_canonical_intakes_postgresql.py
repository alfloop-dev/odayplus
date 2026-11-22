"""Native submissions, using an owned PostgreSQL and synthetic principals only."""

import gc
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import MethodType, SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.app.routes import canonical_intakes
from apps.api.app.routes.listings import create_assisted_intake_router
from shared.audit import InMemoryAuditLog
from shared.audit.errors import CanonicalAuditUnavailable
from shared.auth import Principal, Role, Scope
from shared.domain.events import validate_event
from shared.infrastructure.persistence import PostgresDocumentStore
from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
from shared.infrastructure.persistence.factory import PersistenceBundle
from tests.intake_workflow_fixture import SyntheticIntakeWorkflowPolicy
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def native_api(canonical_graph, monkeypatch):
    graph, ids, _, _ = canonical_graph
    store = DurableAssistedIntakeStore(PostgresDocumentStore(graph.engine))
    store.configure_canonical_audit(graph.audit_sink)
    bundle = SimpleNamespace(engine=graph.engine, job_queue=graph.queue, assisted_intake_store=store,
        assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(), feature_flag_registry=graph.queue._flags)
    for name in ("canonical_identity_graph", "canonical_intakes"):
        setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
    caller = {"principal": Principal(ids["actor"], frozenset({Role.EXPANSION_USER}), Scope(tenant_id=ids["tenant"]))}
    monkeypatch.setattr(canonical_intakes, "principal_from_headers", lambda _: caller["principal"])
    app = FastAPI()
    app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
    app.include_router(create_assisted_intake_router(store), prefix="/api/v1")
    with TestClient(app) as client:
        # Release cyclic garbage from test application setup before exercising
        # bounded commands. GC stays enabled throughout the actual commands.
        gc.collect()
        yield client, caller, store


def command(**overrides):
    return {"intake_method": "MANUAL", "scope": {}, "reason": "Explicit synthetic investigation request", **overrides}


def test_native_gets_use_verified_tenant_and_fresh_original_flag_owner(canonical_graph, native_api):
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    from tests.integration.test_assisted_listing_identity import independent_canonical_graph
    from tests.integration.test_canonical_identity_cases_postgresql import _parent

    graph, ids, _, database = canonical_graph
    client, _, _ = native_api
    intake_id = _parent(canonical_graph, state="SUBMITTED")
    path = "/api/v1/canonical-intakes/" + intake_id
    assert client.get(path).status_code == 200
    page = client.get("/api/v1/canonical-intakes?tenant_id=" + str(uuid4()),
                      headers={"X-Tenant-Id": str(uuid4())})
    assert page.status_code == 200
    assert all(row["tenant_id"] == ids["tenant"] for row in page.json()["items"])
    before = {k: v for k, v in snapshot(canonical_graph).items() if k not in {"documents", "delivery"}}
    # A separately opened real SQL owner revokes the read eligibility. The
    # original app must not retain a RAM/default grant or return an empty list.
    with independent_canonical_graph(graph, database) as reopened:
        reopened.queue._flags.execute("disable", "assisted_intake_v1_read", actor="synthetic-independent-admin",
            correlation_id="synthetic-stop-native-read", audit_log=DurableAuditLog(reopened.engine))
    for url in (path, "/api/v1/canonical-intakes"):
        response = client.get(url)
        assert response.status_code == 403 and response.json() == {"detail": "FEATURE_FLAG_DISABLED"}
    denied = [event for event in client.app.state.audit_log.list_events()
              if event.metadata.get("policy_id") == "intake_feature_flag"]
    assert len(denied) == 2 and all(event.metadata["tenant_id"] == ids["tenant"] for event in denied)
    after = {k: v for k, v in snapshot(canonical_graph).items() if k not in {"documents", "delivery"}}
    assert after == before


@pytest.mark.parametrize("damage", ["missing-owner", "memory-owner", "wrong-engine", "corrupt-record", "missing-enrollment"])
def test_native_read_rollout_distinguishes_unavailable_owner_from_disabled_enrollment(
    canonical_graph, native_api, monkeypatch, tmp_path, damage,
):
    from shared.auth.feature_flags import FeatureFlagRegistry
    from shared.infrastructure.persistence.document_store import SqliteDocumentStore
    from shared.infrastructure.persistence.engine import SqliteEngine
    from shared.infrastructure.persistence.feature_flags import (
        COLLECTION,
        DOCUMENT,
        DurableFeatureFlagRegistry,
    )
    from tests.intake_workflow_fixture import seed_synthetic_intake_read_flag
    from tests.integration.test_canonical_identity_cases_postgresql import _parent

    graph, ids, _, _ = canonical_graph
    client, _, _ = native_api
    intake_id = _parent(canonical_graph, state="SUBMITTED")
    owner = graph.queue._flags
    bundle = client.app.state.persistence_bundle
    foreign = None
    try:
        if damage == "missing-owner":
            monkeypatch.delattr(bundle, "feature_flag_registry")
        elif damage == "memory-owner":
            monkeypatch.setattr(bundle, "feature_flag_registry", FeatureFlagRegistry({f.key: f for f in owner.all()}))
        elif damage == "wrong-engine":
            foreign = SqliteEngine(tmp_path / "foreign-synthetic-flags.sqlite")
            other = DurableFeatureFlagRegistry(SqliteDocumentStore(foreign))
            other.initialize_defaults()
            seed_synthetic_intake_read_flag(other, ids["tenant"])
            monkeypatch.setattr(bundle, "feature_flag_registry", other)
        else:
            raw, version = owner.store.get_versioned(COLLECTION, DOCUMENT)
            if damage == "corrupt-record":
                raw["flags"]["assisted_intake_v1_read"]["bindings"] = None
            else:
                raw["flags"].pop("assisted_intake_v1_read")
            assert owner.store.compare_and_put(COLLECTION, DOCUMENT, raw, expected_version=version) is not None
        for url in ("/api/v1/canonical-intakes/" + intake_id, "/api/v1/canonical-intakes"):
            response = client.get(url)
            assert response.status_code == (403 if damage == "missing-enrollment" else 503), response.text
            assert response.json()["detail"] == (
                "FEATURE_FLAG_DISABLED" if damage == "missing-enrollment" else "FEATURE_FLAG_STORE_UNAVAILABLE")
    finally:
        if foreign is not None:
            foreign.close()


def test_read_flag_does_not_substitute_for_or_accidentally_gate_the_original_submit_policy(canonical_graph, native_api):
    from shared.infrastructure.persistence.audit_log import DurableAuditLog

    graph, _, _, _ = canonical_graph
    client, _, _ = native_api
    graph.queue._flags.execute("disable", "assisted_intake_v1_read", actor="synthetic-admin",
        correlation_id="synthetic-read-stop", audit_log=DurableAuditLog(graph.engine))
    result = client.post("/api/v1/canonical-intakes", json=command(),
                         headers={"Idempotency-Key": "synthetic-submit-with-explicit-workflow-read-disabled"})
    assert result.status_code == 201 and result.json()["processing_state"] == "SUBMITTED", result.text
    assert client.get("/api/v1/canonical-intakes/" + result.json()["intake_id"]).status_code == 403


def snapshot(case):
    graph, ids, _, _ = case
    with graph.transaction(ids["tenant"]):
        tables = ("intake.intakes", "intake.intake_stage_transitions", "identity.match_cases", "expansion.listings",
                  "workflow.jobs", "audit.audit_events", "workflow.outbox_events",
                  "workflow.assignments", "workflow.assignment_transitions", "workflow.sla_instances",
                  "workflow.sla_transitions", "workflow.sla_pause_intervals")
        rows = {table: sorted(graph.engine.query("SELECT * FROM " + table + " WHERE tenant_id=?", (ids["tenant"],)),
                              key=lambda row: json.dumps(row, sort_keys=True)) for table in tables}
        rows["documents"] = graph.engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")
        rows["delivery"] = graph.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")
        return rows


@pytest.mark.parametrize("method", ["MANUAL", "URL"])
def test_native_submission_is_atomic_durable_and_never_claims_processing(canonical_graph, native_api, method):
    from tests.integration.test_assisted_listing_identity import independent_canonical_graph

    graph, ids, _, db = canonical_graph
    client, _, store = native_api
    body = command(intake_method=method)
    if method == "URL":
        body["original_url"] = "https://synthetic.example/Unnormalised?private=synthetic-value"
    headers = {"Idempotency-Key": "synthetic-native-submission-001"}
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", headers=headers, json=body)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["processing_state"] == "SUBMITTED" and receipt["version"] == 1
    assert response.headers["etag"] == 'W/"1"' and response.headers["Idempotency-Replayed"] == "false"
    assert "job_id" not in receipt and "match_case_id" not in receipt
    path = "/api/v1/canonical-intakes/" + receipt["intake_id"]
    selected = client.get(path)
    assert selected.status_code == 200 and selected.json()["submitter_subject_id"] == ids["actor"]
    assert selected.json()["processing_state"] == "SUBMITTED"
    assert "original_url" not in selected.text and "synthetic-value" not in selected.text and body["reason"] not in selected.text
    after = snapshot(canonical_graph)
    assert len(after["intake.intakes"]) == len(before["intake.intakes"]) + 1
    for table in ("identity.match_cases", "expansion.listings", "workflow.jobs"):
        assert after[table] == before[table]
    assert len(after["intake.intake_stage_transitions"]) == len(before["intake.intake_stage_transitions"]) + 1
    native = next(row for row in after["intake.intakes"] if row["intake_id"] == receipt["intake_id"])
    assert native["original_url"] == body.get("original_url")
    assert all(native[key] is None for key in ("canonical_url", "canonical_url_sha256", "source_id", "source_policy_state"))
    transition = next(row for row in after["intake.intake_stage_transitions"] if row["transition_id"] == receipt["transition_id"])
    assert transition["from_state"] is None and transition["to_state"] == "SUBMITTED"
    assert transition["actor_subject_id"] == ids["actor"] and transition["resulting_version"] == 1
    event_row = next(row for row in after["workflow.outbox_events"] if row["event_id"] == receipt["outbox_event_id"])
    event = event_row["payload"]
    event = json.loads(event) if isinstance(event, str) else event
    assert validate_event(event) == [] and event["event_type"] == "intake.submitted"
    assert event_row["causation_id"] == event["causation_id"]
    sensitive = event_row["sensitive_fields"]
    assert (json.loads(sensitive) if isinstance(sensitive, str) else sensitive) == event["sensitive_fields"]
    assert "original_url" not in event["payload"]
    assignment, = [r for r in after["workflow.assignments"] if r["intake_id"] == receipt["intake_id"]]
    sla, = [r for r in after["workflow.sla_instances"] if r["intake_id"] == receipt["intake_id"]]
    assert assignment["status"] == "UNASSIGNED" and assignment["version"] == 1
    assert assignment["owner_subject_id"] is None and assignment["queue_key"] == "synthetic-review-queue"
    assert sla["assignment_id"] == assignment["assignment_id"] and sla["version"] == 1
    started = datetime.fromisoformat(native["submitted_at"])
    assert datetime.fromisoformat(sla["started_at"]) == started
    for key, hours in (("due_soon_at", 3), ("due_at", 4), ("breach_at", 5)):
        assert datetime.fromisoformat(sla[key]) == started + timedelta(hours=hours)
    assert sla["calendar_version"] == "synthetic-calendar-v1"
    workflow = selected.json()["workflow"]
    assert workflow["assignment_id"] == assignment["assignment_id"]
    assert workflow["sla_instance_id"] == sla["sla_instance_id"]
    assert workflow["assignment_version"] == workflow["sla_version"] == 1
    assert workflow["assignment_status"] == "UNASSIGNED" and workflow["sla_state"] == "ON_TRACK"
    assert workflow["owner_subject_id"] is None and workflow["owner_role"] is None
    assert workflow["queue_key"] == assignment["queue_key"]
    assert workflow["calendar_version"] == sla["calendar_version"]
    assert workflow["policy_version"] == sla["policy_version"]
    for table in ("workflow.assignment_transitions", "workflow.sla_transitions"):
        bound = [r for r in after[table] if r["correlation_id"] == receipt["correlation_id"]]
        assert len(bound) == 1 and bound[0]["sequence_no"] == bound[0]["resulting_version"] == 1
    created_audits = [r for r in after["audit.audit_events"] if r["correlation_id"] == receipt["correlation_id"]]
    assert sorted(r["action"] for r in created_audits) == ["assignment.create", "intake.create", "sla.create"]
    created_events = [r for r in after["workflow.outbox_events"] if r["correlation_id"] == receipt["correlation_id"]]
    assert sorted(r["event_type"] for r in created_events) == ["intake.submitted", "sla.state_changed"]
    assert not {"operator.assisted_intakes", "assisted_intake.assignments", "assisted_intake.slas"}.intersection(
        r["collection"] for r in after["documents"])
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    replay = client.post("/api/v1/canonical-intakes", headers=headers, json=body)
    assert replay.status_code == 201 and replay.json() == receipt and replay.headers["Idempotency-Replayed"] == "true"
    assert snapshot(canonical_graph) == after
    assert client.post("/api/v1/canonical-intakes", headers=headers, json=command(reason="Different synthetic intent")).status_code == 409
    assert snapshot(canonical_graph) == after
    with independent_canonical_graph(graph, db) as reopened_graph:
        reopened = DurableAssistedIntakeStore(PostgresDocumentStore(reopened_graph.engine))
        reopened.configure_canonical_audit(reopened_graph.audit_sink)
        bundle = SimpleNamespace(engine=reopened_graph.engine, job_queue=reopened_graph.queue,
            assisted_intake_store=reopened, assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(),
            feature_flag_registry=reopened_graph.queue._flags)
        for name in ("canonical_identity_graph", "canonical_intakes"):
            setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
        app = FastAPI()
        app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
        app.include_router(create_assisted_intake_router(reopened), prefix="/api/v1")
        with TestClient(app) as restarted:
            restarted_detail = restarted.get(path)
            assert restarted_detail.status_code == 200, restarted_detail.text
            assert restarted_detail.json()["workflow"] == workflow
            replayed = restarted.post("/api/v1/canonical-intakes", json=body, headers=headers)
            assert replayed.status_code == 201 and replayed.json() == receipt
            assert replayed.headers["Idempotency-Replayed"] == "true"
        reopened.refresh(ids["tenant"])
        assert next(iter(reopened.replays.values()))[1] == receipt
        assert reopened_graph.engine.query_one("SELECT version, processing_state FROM intake.intakes WHERE intake_id=?",
                                         (receipt["intake_id"],)) == {"version": 1, "processing_state": "SUBMITTED"}
        assert snapshot(canonical_graph) == after
    store.refresh(ids["tenant"])


@pytest.mark.parametrize("mutation", ["audit", "outbox", "receipt", "assignment", "sla", "workflow_event"])
def test_failure_rolls_back_intake_transition_audit_outbox_and_receipt(canonical_graph, native_api, monkeypatch, mutation):
    graph, _, _, _ = canonical_graph
    client, _, store = native_api
    before = snapshot(canonical_graph)

    def fail(*args, **kwargs):
        raise CanonicalAuditUnavailable("Explicit synthetic transaction failure")

    if mutation == "audit":
        monkeypatch.setattr(graph.audit_sink, "persist", fail)
    elif mutation == "outbox":
        monkeypatch.setattr(type(graph), "_outbox", fail)
    elif mutation in {"assignment", "sla"}:
        execute = graph.engine.execute
        table = "workflow.assignments" if mutation == "assignment" else "workflow.sla_instances"

        def fail_workflow(sql, *args, **kwargs):
            if sql.startswith("INSERT INTO " + table):
                fail()
            return execute(sql, *args, **kwargs)

        monkeypatch.setattr(graph.engine, "execute", fail_workflow)
    elif mutation == "workflow_event":
        outbox = type(graph)._outbox

        def fail_workflow_event(owner, event):
            if event.event_type == "sla.state_changed":
                fail()
            return outbox(owner, event)

        monkeypatch.setattr(type(graph), "_outbox", fail_workflow_event)
    else:
        monkeypatch.setattr(store, "flush", fail)
    response = client.post("/api/v1/canonical-intakes", json=command(), headers={"Idempotency-Key": "synthetic-native-rollback-001"})
    assert response.status_code == 503, response.text
    assert snapshot(canonical_graph) == before


def test_native_submission_without_workflow_policy_fails_closed(canonical_graph, native_api):
    client, _, _ = native_api
    client.app.state.persistence_bundle.assisted_intake_workflow_policy = None
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=command(), headers={
        "Idempotency-Key": "synthetic-native-no-workflow-001",
    })
    assert response.status_code == 503, response.text
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("damage", ["missing_checkpoint", "assignment_due", "calendar"])
def test_native_workflow_detail_refuses_partial_or_inconsistent_recorded_facts(canonical_graph, native_api, damage):
    graph, ids, _, _ = canonical_graph
    client, _, _ = native_api
    created = client.post("/api/v1/canonical-intakes", json=command(), headers={
        "Idempotency-Key": "synthetic-native-damaged-workflow-001",
    })
    assert created.status_code == 201, created.text
    iid = created.json()["intake_id"]
    statements = {
        "missing_checkpoint": "UPDATE workflow.sla_instances SET due_soon_at=NULL WHERE tenant_id=? AND intake_id=?",
        "assignment_due": "UPDATE workflow.assignments SET due_at=due_at+INTERVAL '1 hour' WHERE tenant_id=? AND intake_id=?",
        "calendar": "UPDATE workflow.sla_instances SET calendar_version='' WHERE tenant_id=? AND intake_id=?",
    }
    with graph.transaction(ids["tenant"]):
        graph.engine.execute(statements[damage], (ids["tenant"], iid))
    before = snapshot(canonical_graph)
    response = client.get("/api/v1/canonical-intakes/" + iid)
    assert response.status_code == 503, response.text
    assert snapshot(canonical_graph) == before


def test_native_workflow_history_prevents_deleting_its_sla(canonical_graph, native_api):
    from psycopg import errors

    graph, ids, _, _ = canonical_graph
    client, _, _ = native_api
    created = client.post("/api/v1/canonical-intakes", json=command(), headers={
        "Idempotency-Key": "synthetic-native-protected-sla-001",
    })
    assert created.status_code == 201, created.text
    iid = created.json()["intake_id"]
    before = snapshot(canonical_graph)
    with pytest.raises(errors.ForeignKeyViolation) as error, graph.transaction(ids["tenant"]):
        graph.engine.execute("DELETE FROM workflow.sla_instances WHERE tenant_id=? AND intake_id=?", (ids["tenant"], iid))
    assert error.value.diag.constraint_name == "fk_sla_transition_tenant"
    assert snapshot(canonical_graph) == before
    assert client.get("/api/v1/canonical-intakes/" + iid).status_code == 200


def test_historical_workflow_without_recorded_binding_keeps_detail_readable_and_unknown(canonical_graph, native_api):
    graph, ids, _, _ = canonical_graph
    aid = str(uuid4())
    with graph.transaction(ids["tenant"]):
        graph.engine.execute("INSERT INTO workflow.assignments (assignment_id,tenant_id,intake_id,status,queue_key,due_at) "
            "VALUES (?,?,?,'UNASSIGNED','synthetic-historical-queue','2026-10-13T04:00:00Z')",
            (aid, ids["tenant"], ids["intake"]))
        graph.engine.execute("INSERT INTO workflow.sla_instances (tenant_id,intake_id,policy_version,state,started_at,due_at) "
            "VALUES (?,?,'synthetic-historical-policy','ON_TRACK','2026-10-13T00:00:00Z','2026-10-13T04:00:00Z')",
            (ids["tenant"], ids["intake"]))
    before = snapshot(canonical_graph)
    client, _, _ = native_api
    response = client.get("/api/v1/canonical-intakes/" + ids["intake"])
    assert response.status_code == 200, response.text
    assert response.json()["workflow"] is None
    assert "synthetic-historical-policy" not in response.text
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("revocation", ["role", "tenant", "scope", "ownership", "anonymous"])
def test_get_and_lost_response_replay_recheck_current_authority(canonical_graph, native_api, revocation):
    graph, ids, _, _ = canonical_graph
    client, caller, _ = native_api
    headers = {"Idempotency-Key": "synthetic-native-revalidation-001"}
    created = client.post("/api/v1/canonical-intakes", json=command(), headers=headers)
    assert created.status_code == 201, created.text
    intake_id = created.json()["intake_id"]
    principal = caller["principal"]
    if revocation == "role":
        caller["principal"] = replace(principal, roles=frozenset({Role.PLATFORM_ADMIN}))
    elif revocation == "tenant":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=str(uuid4())))
    elif revocation == "scope":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({str(uuid4())})))
    elif revocation == "ownership":
        with graph.transaction(ids["tenant"]):
            graph.engine.execute("UPDATE intake.intakes SET submitter_subject_id=? WHERE intake_id=?", (str(uuid4()), intake_id))
    else:
        caller["principal"] = replace(principal, authenticated=False)
    before = snapshot(canonical_graph)
    selected = client.get("/api/v1/canonical-intakes/" + intake_id)
    assert selected.status_code in {401, 403, 404}, selected.text
    replay = client.post("/api/v1/canonical-intakes", json=command(), headers=headers)
    # A different tenant has a different idempotency namespace, so its new
    # submission is valid and cannot reveal the original tenant's receipt.
    if revocation == "tenant":
        assert replay.status_code == 201 and replay.json()["intake_id"] != intake_id
    else:
        assert replay.status_code in {401, 403}, replay.text
        assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("patch", [
    {"intake_method": "URL"}, {"intake_method": "URL", "original_url": "file:///private"},
    {"intake_method": "URL", "original_url": "https://user:password@synthetic.example/"},
    {"original_url": "https://synthetic.example/"}, {"reason": "   "},
    {"submitter_subject_id": str(uuid4())}, {"scope": {"tenant_id": str(uuid4())}},
])
def test_unvalidated_or_impersonated_submissions_create_nothing(canonical_graph, native_api, patch):
    client, _, _ = native_api
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=command(**patch), headers={"Idempotency-Key": "synthetic-native-validation-001"})
    assert response.status_code == 422, response.text
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("role", [Role.PLATFORM_ADMIN, Role.AUDITOR, Role.OPERATOR_VIEWER, Role.OPERATIONS_MANAGER])
def test_display_persona_cannot_grant_manual_submission(canonical_graph, native_api, role):
    client, caller, _ = native_api
    caller["principal"] = replace(caller["principal"], roles=frozenset({role}))
    before = snapshot(canonical_graph)
    response = client.post("/api/v1/canonical-intakes", json=command(), headers={
        "Idempotency-Key": "synthetic-native-denied-create-001", "x-operator-role": "expansion-manager",
    })
    assert response.status_code == 403, response.text
    assert snapshot(canonical_graph) == before


def test_submitted_intake_cannot_bypass_processing_to_create_a_match_case(canonical_graph, native_api):
    from modules.listing.domain.identity_graph import IdentityConflictError
    from shared.infrastructure.persistence.identity_cases_sql import CanonicalMatchCaseRepository
    from tests.integration.test_canonical_identity_cases_postgresql import _command

    graph, ids, _, _ = canonical_graph
    client, _, _ = native_api
    created = client.post("/api/v1/canonical-intakes", json=command(), headers={
        "Idempotency-Key": "synthetic-native-no-state-bypass-001",
    })
    assert created.status_code == 201, created.text
    before = snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="INTAKE_NOT_AWAITING_IDENTITY_REVIEW"):
        CanonicalMatchCaseRepository(graph).create_case(ids["tenant"], created.json()["intake_id"],
            **_command(canonical_graph, expected_intake_version=1, actor_id=ids["reviewer"]))
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("method", ["URL", "MANUAL"])
def test_exact_receipt_read_restores_original_submission_without_repeating_command(canonical_graph, native_api, method):
    client, caller, _ = native_api
    body = command(intake_method=method)
    if method == "URL":
        body["original_url"] = "https://synthetic.example/receipt-private?source=synthetic"
    tenant = caller["principal"].tenant_id
    actor = caller["principal"].subject_id
    headers = {"Idempotency-Key": "synthetic-exact-submission-receipt-001", "If-Tenant-Match": tenant, "If-Actor-Match": actor}
    created = client.post("/api/v1/canonical-intakes", json=body, headers=headers)
    assert created.status_code == 201, created.text
    original = created.json()
    before = snapshot(canonical_graph)
    for expected, status in [(str(uuid4()), 409), ("malformed-tenant", 422)]:
        denied = client.get("/api/v1/canonical-intakes/submissions/receipt", headers={**headers, "If-Tenant-Match": expected})
        assert denied.status_code == status and "receipt" not in denied.json(), denied.text
        assert snapshot(canonical_graph) == before
    for expected, status in [(str(uuid4()), 409), ("malformed-actor", 422)]:
        denied = client.get("/api/v1/canonical-intakes/submissions/receipt", headers={**headers, "If-Actor-Match": expected})
        assert denied.status_code == status and "receipt" not in denied.json(), denied.text
        assert snapshot(canonical_graph) == before
    for header, value in [("If-Actor-Match", actor), ("If-Tenant-Match", tenant)]:
        duplicate = client.get("/api/v1/canonical-intakes/submissions/receipt",
            headers=[("Idempotency-Key", headers["Idempotency-Key"]), (header, value), (header, value)])
        assert duplicate.status_code == 422 and snapshot(canonical_graph) == before
    principal = caller["principal"]
    caller["principal"] = replace(principal, scope=replace(principal.scope, tenant_id=str(uuid4())))
    changed = client.get("/api/v1/canonical-intakes/submissions/receipt", headers=headers)
    assert changed.status_code == 409 and changed.json() == {"detail": "CANONICAL_TENANT_CONTEXT_CHANGED"}
    caller["principal"] = principal
    rejected = client.post("/api/v1/canonical-intakes", json=body,
        headers={**headers, "Idempotency-Key": headers["Idempotency-Key"] + ":foreign", "If-Tenant-Match": str(uuid4())})
    assert rejected.status_code == 409 and snapshot(canonical_graph) == before
    response = client.get("/api/v1/canonical-intakes/submissions/receipt", headers=headers)
    assert response.status_code == 200, response.text
    observed = response.json()
    assert observed["observation"] == "RECEIPT_COMMITTED"
    assert observed["original_http_status"] == 201 and observed["receipt"] == original
    assert datetime.fromisoformat(observed["observed_at"]).utcoffset() is not None
    assert response.headers["cache-control"] == "no-store"
    assert "original_url" not in response.text and body["reason"] not in response.text
    assert snapshot(canonical_graph) == before
    # A manager's current read grant does not make a different actor's key his.
    caller["principal"] = replace(caller["principal"], subject_id=str(uuid4()), roles=frozenset({Role.SITE_REVIEWER}))
    absent = client.get("/api/v1/canonical-intakes/submissions/receipt", headers={"Idempotency-Key": headers["Idempotency-Key"]})
    assert absent.status_code == 200, absent.text
    assert absent.json()["observation"] == "NOT_OBSERVED" and "receipt" not in absent.json()
    assert snapshot(canonical_graph) == before


def test_exact_receipt_refuses_actor_aba_even_when_both_actors_submitted_the_same_key(canonical_graph, native_api):
    _, ids, _, _ = canonical_graph
    client, caller, _ = native_api
    actor_a = caller["principal"]
    actor_b = replace(actor_a, subject_id=ids["reviewer"])
    key = "synthetic-actor-aba-exact-receipt-001"
    headers = {"Idempotency-Key": key, "If-Tenant-Match": actor_a.tenant_id}
    receipts = []
    # Use the original fixture's synthetic authenticated-principal seam, with
    # two actual HTTP commands and durable receipts, never inserted terminal rows.
    for principal in (actor_a, actor_b):
        caller["principal"] = principal
        response = client.post("/api/v1/canonical-intakes", json=command(), headers=headers)
        assert response.status_code == 201, response.text
        receipts.append(response.json())
    assert receipts[0]["intake_id"] != receipts[1]["intake_id"]
    before = snapshot(canonical_graph)
    denied = client.get("/api/v1/canonical-intakes/submissions/receipt",
        headers={**headers, "If-Actor-Match": actor_a.subject_id})
    assert denied.status_code == 409 and denied.json() == {"detail": "CANONICAL_ACTOR_CONTEXT_CHANGED"}
    caller["principal"] = actor_a
    restored = client.get("/api/v1/canonical-intakes/submissions/receipt",
        headers={**headers, "If-Actor-Match": actor_a.subject_id})
    assert restored.status_code == 200 and restored.json()["receipt"] == receipts[0]
    caller["principal"] = actor_b
    other = client.get("/api/v1/canonical-intakes/submissions/receipt",
        headers={**headers, "If-Actor-Match": actor_b.subject_id})
    assert other.status_code == 200 and other.json()["receipt"] == receipts[1]
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("damage", ["null-payload", "tenant", "key", "extra", "status", "model", "digest"])
def test_exact_receipt_read_reports_corrupt_original_record_as_unavailable(canonical_graph, native_api, damage):
    graph, ids, _, _ = canonical_graph
    client, _, store = native_api
    key = "synthetic-corrupt-exact-receipt-001"
    created = client.post("/api/v1/canonical-intakes", json=command(), headers={"Idempotency-Key": key})
    assert created.status_code == 201, created.text
    namespace = f'{ids["tenant"]}:{ids["actor"]}:submitCanonicalIntake:{key}'
    physical = f'{ids["tenant"]}:{namespace}'
    with graph.transaction(ids["tenant"]):
        row = store._store.get("assisted_intake.idempotency_replays", physical)
        if damage == "null-payload":
            row = None
        elif damage in {"tenant", "key", "extra"}:
            row[{"tenant": "_tenant_id", "key": "_state_key", "extra": "undeclared"}[damage]] = "synthetic-mismatched-wrapper"
        else:
            digest, body, status = row["value"]
            row["value"] = ("invalid-digest" if damage == "digest" else digest,
                {**body, "intake_id": "synthetic-not-a-native-UUID"} if damage == "model" else body,
                200 if damage == "status" else status)
        store._store.put("assisted_intake.idempotency_replays", physical, row)
    before = snapshot(canonical_graph)
    response = client.get("/api/v1/canonical-intakes/submissions/receipt", headers={"Idempotency-Key": key})
    assert response.status_code == 503, response.text
    assert "receipt" not in response.json() and "NOT_OBSERVED" not in response.text
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("revocation", ["role", "scope", "anonymous", "read-disabled", "missing-flag-owner"])
def test_exact_receipt_read_rechecks_current_view_and_original_rollout(canonical_graph, native_api, monkeypatch, revocation):
    from shared.infrastructure.persistence.audit_log import DurableAuditLog
    graph, ids, _, _ = canonical_graph
    client, caller, _ = native_api
    key = "synthetic-exact-receipt-current-view-001"
    assert client.post("/api/v1/canonical-intakes", json=command(), headers={"Idempotency-Key": key}).status_code == 201
    current = caller["principal"]
    if revocation == "role":
        caller["principal"] = replace(current, roles=frozenset({Role.PLATFORM_ADMIN}))
    elif revocation == "scope":
        caller["principal"] = replace(current, scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({str(uuid4())})))
    elif revocation == "anonymous":
        caller["principal"] = replace(current, authenticated=False)
    elif revocation == "read-disabled":
        graph.queue._flags.execute("disable", "assisted_intake_v1_read", actor="synthetic-admin",
            correlation_id="synthetic-receipt-current-read", audit_log=DurableAuditLog(graph.engine))
    else:
        monkeypatch.setattr(client.app.state.persistence_bundle, "feature_flag_registry", None)
    before = snapshot(canonical_graph)
    for lookup in (key, "synthetic-exact-receipt-unobserved-001"):
        response = client.get("/api/v1/canonical-intakes/submissions/receipt", headers={"Idempotency-Key": lookup})
        if revocation == "scope" and lookup != key:
            # This authorized collection query has no object whose scope can
            # be checked. It must not borrow a different key's resource.
            assert response.status_code == 200, response.text
            assert response.json()["observation"] == "NOT_OBSERVED" and "receipt" not in response.json()
        else:
            assert response.status_code == (503 if revocation == "missing-flag-owner" else 401 if revocation == "anonymous" else 403), response.text
            assert "receipt" not in response.json() and "NOT_OBSERVED" not in response.text
    assert snapshot(canonical_graph) == before


def test_exact_receipt_read_is_durable_without_any_legacy_refresh_or_business_retry(canonical_graph, native_api, monkeypatch):
    from tests.integration.test_assisted_listing_identity import independent_canonical_graph
    graph, _, _, database = canonical_graph
    client, _, _ = native_api
    key = "synthetic-direct-durable-exact-receipt-001"
    response = client.post("/api/v1/canonical-intakes", json=command(), headers={"Idempotency-Key": key})
    assert response.status_code == 201
    before = snapshot(canonical_graph)
    # A separately owned SQL engine, store and real router have no old RAM
    # receipt snapshot. They must read the same committed original result.
    with independent_canonical_graph(graph, database) as reopened:
        fresh = DurableAssistedIntakeStore(PostgresDocumentStore(reopened.engine))
        fresh.configure_canonical_audit(reopened.audit_sink)
        bundle = SimpleNamespace(engine=reopened.engine, job_queue=reopened.queue, assisted_intake_store=fresh,
            assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(), feature_flag_registry=reopened.queue._flags)
        for name in ("canonical_identity_graph", "canonical_intakes"):
            setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
        app = FastAPI()
        app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
        app.include_router(canonical_intakes.create_canonical_intakes_router(fresh), prefix="/api/v1")
        def no_legacy(*args, **kwargs):
            raise AssertionError("Read-only receipt lookup must not refresh or flush legacy command state")
        monkeypatch.setattr(fresh, "refresh", no_legacy)
        monkeypatch.setattr(fresh, "flush", no_legacy)
        with TestClient(app) as restarted:
            observed = restarted.get("/api/v1/canonical-intakes/submissions/receipt", headers={"Idempotency-Key": key})
            assert observed.status_code == 200, observed.text
            assert observed.json()["receipt"] == response.json()
    assert snapshot(canonical_graph) == before


def test_native_processing_history_reads_real_submission_without_raw_reason_or_repeating_work(canonical_graph, native_api):
    client, _, _ = native_api
    submitted = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-native-history-original-submit-001"})
    assert submitted.status_code == 201, submitted.text
    receipt = submitted.json()
    before = snapshot(canonical_graph)
    response = client.get(f'/api/v1/canonical-intakes/{receipt["intake_id"]}/processing-history',
        params={"intake_version": receipt["version"]})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["intake_id"] == receipt["intake_id"] and value["intake_version"] == receipt["version"]
    assert value["next_cursor"] is None and value["previous_cursor"] is None
    assert len(value["items"]) == 1
    item = value["items"][0]
    assert item["transition_id"] == receipt["transition_id"]
    assert item["from_state"] is None and item["to_state"] == "SUBMITTED"
    assert item["resulting_version"] == receipt["version"]
    assert item["correlation_id"] == receipt["correlation_id"]
    assert "idempotency_key" not in response.text and "reason_text" not in response.text
    assert command()["reason"] not in response.text
    assert response.headers["cache-control"] == "no-store"
    embedded = client.get(f'/api/v1/canonical-intakes/{receipt["intake_id"]}', params={"include_history": "true"})
    assert embedded.status_code == 200 and embedded.json()["processing_history"] == value, embedded.text
    assert snapshot(canonical_graph) == before


def test_native_detail_source_urls_keep_original_masking_and_never_return_legacy_credentials(canonical_graph, native_api):
    from dataclasses import replace

    from shared.auth import DataClassification

    _, _, _, db = canonical_graph
    client, caller, _ = native_api
    original = "https://synthetic.example/source-history?private=synthetic-private-value"
    submitted = client.post("/api/v1/canonical-intakes", json=command(intake_method="URL", original_url=original),
        headers={"Idempotency-Key": "synthetic-native-source-masking-submit-001"})
    assert submitted.status_code == 201, submitted.text
    path = '/api/v1/canonical-intakes/' + submitted.json()["intake_id"]
    basic = client.get(path)
    assert basic.status_code == 200 and basic.json()["source_info"] is None
    assert "synthetic-private-value" not in basic.text
    caller["principal"] = replace(caller["principal"],
        scope=replace(caller["principal"].scope, clearance=DataClassification.PUBLIC))
    selected = client.get(path, params={"include_source": "true"})
    assert selected.status_code == 200, selected.text
    fields = selected.json()["source_info"]["url_fields"]
    assert {v["field_path"] for v in fields} == {"originalUrl", "canonicalUrl"}
    assert all(v["masked"] and v["effective"] is None for v in fields)
    assert "synthetic-private-value" not in selected.text
    caller["principal"] = replace(caller["principal"],
        scope=replace(caller["principal"].scope, clearance=DataClassification.CONFIDENTIAL))
    selected = client.get(path, params={"include_source": "true"})
    assert selected.status_code == 200, selected.text
    fields = {v["field_path"]: v for v in selected.json()["source_info"]["url_fields"]}
    assert fields["originalUrl"]["effective"] == original and not fields["originalUrl"]["masked"]
    with db.connect() as connection:
        # Synthetic preexisting malformed evidence; no current submission or
        # source grant is fabricated by this read-model test.
        connection.execute("UPDATE intake.intakes SET original_url=%s WHERE intake_id=%s",
            ("https://synthetic-user:synthetic-password@synthetic.example/private", submitted.json()["intake_id"]))
    selected = client.get(path, params={"include_source": "true"})
    assert selected.status_code == 200, selected.text
    field = selected.json()["source_info"]["url_fields"][0]
    assert field["field_path"] == "originalUrl" and field["masked"] and field["effective"] is None
    assert "synthetic-password" not in selected.text


@pytest.mark.parametrize("column", ["original_url", "canonical_url"])
@pytest.mark.parametrize("legacy_url,private_marker", [
    ("https://synthetic.example/a/sk-12345678ABCD", "sk-12345678ABCD"),
    ("https://synthetic.example/a?note=Cookie:synthetic-private-value", "synthetic-private-value"),
    ("https://synthetic.example/a?access_token=synthetic-private-value", "synthetic-private-value"),
    ("https://synthetic.example/a#access_token=synthetic-private-value", "synthetic-private-value"),
])
def test_source_projection_never_reveals_historical_credentials_at_highest_clearance(canonical_graph, native_api, column, legacy_url, private_marker):
    from dataclasses import replace

    from shared.auth import DataClassification

    _, _, _, db = canonical_graph
    client, caller, _ = native_api
    submitted = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-historical-url-redaction-001"})
    assert submitted.status_code == 201, submitted.text
    intake_id = submitted.json()["intake_id"]
    # These are retained malformed rows, never admitted credentials or a source grant.
    with db.connect() as connection:
        connection.execute("UPDATE intake.intakes SET " + column + "=%s WHERE intake_id=%s", (legacy_url, intake_id))
    caller["principal"] = replace(caller["principal"],
        scope=replace(caller["principal"].scope, clearance=DataClassification.HIGHLY_RESTRICTED))
    before = snapshot(canonical_graph)
    selected = client.get(f"/api/v1/canonical-intakes/{intake_id}", params={"include_source": "true"})
    assert selected.status_code == 200, selected.text
    fields = {v["field_path"]: v for v in selected.json()["source_info"]["url_fields"]}
    value = fields["originalUrl" if column == "original_url" else "canonicalUrl"]
    assert value["masked"] and value["effective"] is None
    assert private_marker not in selected.text
    assert snapshot(canonical_graph) == before


def _retained_history_fixture(case, api, count):
    """Read-model rows only; this does not prove any service execution or grant."""
    graph, ids, _, db = case
    client, _, _ = api
    response = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-retained-history-original-submit-001"})
    assert response.status_code == 201, response.text
    receipt = response.json()
    previous = "SUBMITTED"
    with db.connect() as connection:
        for sequence in range(2, count + 1):
            target = "CHECKING_IDENTITY" if sequence == 2 else "CHECKING_SOURCE_POLICY" if sequence % 2 else "AWAITING_ASSISTED_ENTRY"
            connection.execute("INSERT INTO intake.intake_stage_transitions (transition_id,tenant_id,intake_id,sequence_no,"
                "from_state,to_state,service_principal,permission,reason_code,reason_text,idempotency_key,expected_version,"
                "resulting_version,correlation_id,occurred_at) SELECT %s,tenant_id,intake_id,%s,%s,%s,"
                "'synthetic-read-history-fixture','intake.history.synthetic_read_fixture','SYNTHETIC_RETAINED_HISTORY',"
                "'PRIVATE_SYNTHETIC_HISTORY_REASON','PRIVATE_SYNTHETIC_COMMAND_KEY',%s,%s,correlation_id,"
                "submitted_at+%s*interval '1 microsecond' FROM intake.intakes WHERE intake_id=%s",
                (str(uuid4()), sequence, previous, target, sequence - 1, sequence, sequence, receipt["intake_id"]))
            previous = target
        connection.execute("UPDATE intake.intakes SET version=%s,processing_state=%s,"
            "last_transition_at=submitted_at+%s*interval '1 microsecond' WHERE intake_id=%s",
            (count, previous, count, receipt["intake_id"]))
    return receipt["intake_id"], count, ids


def test_native_history_pages_real_sql_records_forward_and_backward_without_inventing_stages(canonical_graph, native_api):
    client, _, _ = native_api
    intake_id, current_version, _ = _retained_history_fixture(canonical_graph, native_api, 56)
    path = f"/api/v1/canonical-intakes/{intake_id}/processing-history"
    before = snapshot(canonical_graph)
    first = client.get(path, params={"intake_version": current_version})
    assert first.status_code == 200, first.text
    first = first.json()
    middle = client.get(path, params={"intake_version": current_version, "cursor": first["next_cursor"]})
    assert middle.status_code == 200, middle.text
    middle = middle.json()
    last = client.get(path, params={"intake_version": current_version, "cursor": middle["next_cursor"]})
    assert last.status_code == 200, last.text
    last = last.json()
    assert [len(page["items"]) for page in (first, middle, last)] == [25, 25, 6]
    assert [item["sequence_no"] for page in (first, middle, last) for item in page["items"]] == list(range(1, 57))
    assert all(page["through_sequence"] == 56 and page["intake_version"] == 56 for page in (first, middle, last))
    assert first["previous_cursor"] is None and last["next_cursor"] is None
    earlier = client.get(path, params={"intake_version": current_version, "cursor": last["previous_cursor"]})
    assert earlier.status_code == 200 and earlier.json() == middle, earlier.text
    beginning = client.get(path, params={"intake_version": current_version, "cursor": middle["previous_cursor"]})
    assert beginning.status_code == 200 and beginning.json() == first, beginning.text
    assert first["items"][0]["actor_subject_id"] is not None and first["items"][0]["service_principal"] is None
    assert first["items"][1]["actor_subject_id"] is None and first["items"][1]["service_principal"] == "synthetic-read-history-fixture"
    assert all("PRIVATE_SYNTHETIC" not in json.dumps(page) and "reason_text" not in json.dumps(page)
        and "idempotency_key" not in json.dumps(page) for page in (first, middle, last))
    assert snapshot(canonical_graph) == before


def test_native_history_rejects_foreign_or_stale_cursor_before_mixing_snapshot_versions(canonical_graph, native_api):
    import base64

    _, _, _, db = canonical_graph
    client, _, _ = native_api
    intake_id, current_version, _ = _retained_history_fixture(canonical_graph, native_api, 30)
    path = f"/api/v1/canonical-intakes/{intake_id}/processing-history"
    first = client.get(path, params={"intake_version": current_version})
    assert first.status_code == 200, first.text
    cursor = first.json()["next_cursor"]
    decoded = json.loads(base64.urlsafe_b64decode(cursor))
    before = snapshot(canonical_graph)
    for patch, status in [({"tenant_id": str(uuid4())}, 422), ({"intake_id": str(uuid4())}, 422),
        ({"intake_version": current_version + 1}, 409), ({"through_sequence": 999}, 422),
        ({"position": True}, 422), ({"position": 31}, 422), ({"direction": "invented"}, 422),
        ({"position": 1, "direction": "backward"}, 422), ({"new_actor": "admin"}, 422)]:
        changed = base64.urlsafe_b64encode(json.dumps({**decoded, **patch}).encode()).decode()
        response = client.get(path, params={"intake_version": current_version, "cursor": changed})
        assert response.status_code == status, (patch, response.text)
        assert "items" not in response.json()
    assert snapshot(canonical_graph) == before
    with db.connect() as connection:
        connection.execute("UPDATE intake.intakes SET version=version+1 WHERE intake_id=%s", (intake_id,))
    before = snapshot(canonical_graph)
    stale = client.get(path, params={"intake_version": current_version, "cursor": cursor})
    assert stale.status_code == 409 and stale.json()["detail"] == "CANONICAL_INTAKE_VERSION_CHANGED"
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("denial", ["anonymous", "role", "scope", "flag-disabled", "flag-owner"])
def test_native_history_rechecks_original_current_authority_before_even_decoding_cursor(canonical_graph, native_api, monkeypatch, denial):
    from dataclasses import replace

    from shared.infrastructure.persistence.audit_log import DurableAuditLog

    graph, _, _, _ = canonical_graph
    client, caller, _ = native_api
    submitted = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-history-current-authority-001"})
    assert submitted.status_code == 201, submitted.text
    principal = caller["principal"]
    if denial == "anonymous":
        caller["principal"] = replace(principal, authenticated=False)
    elif denial == "role":
        caller["principal"] = replace(principal, roles=frozenset({Role.PLATFORM_ADMIN}))
    elif denial == "scope":
        caller["principal"] = replace(principal, scope=replace(principal.scope, region_ids=frozenset({str(uuid4())})))
    elif denial == "flag-disabled":
        graph.queue._flags.execute("disable", "assisted_intake_v1_read", actor="synthetic-admin",
            correlation_id="synthetic-history-stop-read", audit_log=DurableAuditLog(graph.engine))
    else:
        monkeypatch.setattr(client.app.state.persistence_bundle, "feature_flag_registry", None)
    before = snapshot(canonical_graph)
    response = client.get(f'/api/v1/canonical-intakes/{submitted.json()["intake_id"]}/processing-history',
        params={"intake_version": submitted.json()["version"], "cursor": "invalid-but-authorization-must-run-first"})
    expected = 401 if denial == "anonymous" else 503 if denial == "flag-owner" else 403
    assert response.status_code == expected, response.text
    assert "items" not in response.json()
    assert snapshot(canonical_graph) == before


@pytest.mark.parametrize("damage", ["ambiguous-actor", "negative-sequence", "future-version", "contradictory-state"])
def test_native_history_does_not_turn_corrupt_original_rows_into_fabricated_progress(canonical_graph, native_api, damage):
    _, _, _, db = canonical_graph
    client, _, _ = native_api
    submitted = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-corrupt-original-history-001"})
    assert submitted.status_code == 201
    sql = {"ambiguous-actor": "service_principal='synthetic-other-service'", "negative-sequence": "sequence_no=0",
        "future-version": "resulting_version=99", "contradictory-state": "to_state='READY'"}[damage]
    with db.connect() as connection:
        connection.execute("UPDATE intake.intake_stage_transitions SET " + sql + " WHERE transition_id=%s", (submitted.json()["transition_id"],))
    before = snapshot(canonical_graph)
    response = client.get(f'/api/v1/canonical-intakes/{submitted.json()["intake_id"]}/processing-history',
        params={"intake_version": submitted.json()["version"]})
    assert response.status_code == 503, response.text
    assert "items" not in response.json() and "SUBMITTED" not in response.text
    assert snapshot(canonical_graph) == before


def _retained_processing_evidence(case, api, count=1):
    """Synthetic read-model rows only; no parser execution, grant or verification."""
    _, ids, _, db = case
    client, _, _ = api
    submitted = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-metadata-read-model-" + uuid4().hex})
    assert submitted.status_code == 201, submitted.text
    receipt = submitted.json()
    release = str(uuid4())
    snapshots, runs = [], []
    with db.connect() as connection:
        connection.execute("UPDATE intake.intakes SET source_id='synthetic-feed' WHERE intake_id=%s", (receipt["intake_id"],))
        connection.execute("INSERT INTO intake.parser_releases (parser_release_id,source_id,package_name,semantic_version,"
            "input_schema_version,output_schema_version,artifact_uri,artifact_sha256,test_corpus_version,validation_status) "
            "VALUES (%s,'synthetic-feed','synthetic.metadata.reader',%s,'synthetic-input/1','synthetic-output/1',"
            "'gs://PRIVATE_NATIVE_ARTIFACT/never-return','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',"
            "'synthetic-corpus/1','DRAFT')", (release, '1.0.0+' + uuid4().hex))
        for index in range(count):
            snapshot_id, run_id = str(uuid4()), str(uuid4())
            snapshots.append(snapshot_id)
            runs.append(run_id)
            connection.execute("INSERT INTO intake.source_snapshots (source_snapshot_id,tenant_id,intake_id,source_id,"
                "original_url,raw_object_uri,content_sha256,media_type,byte_length,captured_at,observed_at,stored_at,"
                "capture_method,retention_class,encryption_key_ref,object_generation) "
                "SELECT %s,tenant_id,intake_id,'synthetic-feed','https://PRIVATE_NATIVE_URL/?token=PRIVATE',"
                "'gs://PRIVATE_NATIVE_OBJECT/never-read',%s,'application/json',4,"
                "submitted_at+%s*interval '1 microsecond',submitted_at+%s*interval '1 microsecond',"
                "submitted_at+%s*interval '1 microsecond','MANUAL_ENTRY','STANDARD','PRIVATE_NATIVE_KMS',%s "
                "FROM intake.intakes WHERE intake_id=%s",
                (snapshot_id, uuid4().hex * 2, index, index, index,
                 None if index == 0 else 9_223_372_036_854_775_798, receipt["intake_id"]))
            connection.execute("INSERT INTO intake.parser_runs (parser_run_id,tenant_id,intake_id,source_snapshot_id,"
                "parser_release_id,status,parsed_payload,normalized_payload,validation_errors,correlation_id,created_at) "
                "SELECT %s,tenant_id,intake_id,%s,%s,'PARTIAL','{\"private\":\"PRIVATE_NATIVE_PARSED\"}',"
                "'{\"private\":\"PRIVATE_NATIVE_NORMALIZED\"}','[\"PRIVATE_NATIVE_VALIDATION_ERROR\"]',correlation_id,"
                "submitted_at+%s*interval '1 microsecond' FROM intake.intakes WHERE intake_id=%s",
                (run_id, snapshot_id, release, index, receipt["intake_id"]))
    return receipt, snapshots, runs, release


@pytest.mark.parametrize("kind,column,value", [
    ("snapshot", "retention_class", "https://u:PRIVATE@public.example/"),
    ("parser", "semantic_version", "https://u:PRIVATE@public.example/"),
    ("parser", "output_schema_version", "https://public.example/?access_token=PRIVATE"),
    ("snapshot", "media_type", "https://public.example/#route?token=PRIVATE"),
    ("snapshot", "retention_class", "v1 (https://u:PRIVATE@public.example/)"),
    ("parser", "package_name", "access_token=PRIVATE"),
])
def test_retained_processing_metadata_cannot_expose_url_or_query_credentials(canonical_graph, native_api, kind, column, value):
    _, _, _, db = canonical_graph
    client, _, _ = native_api
    receipt, snapshots, _, release = _retained_processing_evidence(canonical_graph, native_api)
    table, key, reference = ("intake.source_snapshots", "source_snapshot_id", snapshots[0]) if kind == "snapshot" else (
        "intake.parser_releases", "parser_release_id", release)
    with db.connect() as connection:
        connection.execute("UPDATE " + table + " SET " + column + "=%s WHERE " + key + "=%s", (value, reference))
    before = snapshot(canonical_graph)
    response = client.get(f'/api/v1/canonical-intakes/{receipt["intake_id"]}', params={"include_evidence": "true"})
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "CANONICAL_INTAKE_EVIDENCE_UNAVAILABLE"}
    assert "PRIVATE" not in response.text and snapshot(canonical_graph) == before


def _processing_evidence_state(case):
    graph, ids, _, _ = case
    saved = snapshot(case)
    with graph.transaction(ids["tenant"]):
        for table, key in (("intake.source_snapshots", "source_snapshot_id"), ("intake.parser_runs", "parser_run_id")):
            saved[table] = graph.engine.query("SELECT * FROM " + table + " WHERE tenant_id=? ORDER BY " + key, (ids["tenant"],))
    return saved


def test_native_processing_evidence_is_opt_in_and_empty_is_an_actual_saved_read(canonical_graph, native_api):
    client, _, _ = native_api
    submitted = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-empty-saved-metadata-001"})
    assert submitted.status_code == 201
    path = '/api/v1/canonical-intakes/' + submitted.json()["intake_id"]
    before = _processing_evidence_state(canonical_graph)
    basic = client.get(path)
    assert basic.status_code == 200 and basic.json()["processing_evidence"] is None
    selected = client.get(path, params={"include_evidence": "true"})
    assert selected.status_code == 200, selected.text
    page = selected.json()["processing_evidence"]
    assert page["items"] == [] and page["next_cursor"] is None and page["previous_cursor"] is None
    assert datetime.fromisoformat(page["through_at"]) <= datetime.fromisoformat(page["observed_at"])
    assert _processing_evidence_state(canonical_graph) == before


def test_processing_evidence_merges_real_sql_rows_in_both_directions_and_reads_no_payload(canonical_graph, native_api, monkeypatch):
    from modules.external_data.application.source_snapshots import SourceSnapshotService
    from modules.listing.application.native_intake_parser_runs import (
        CanonicalNativeParserRunRepository,
    )
    from shared.infrastructure.persistence.assisted_listing_intake import (
        NativeIntakeSnapshotMetadataRepository,
    )

    graph, _, _, _ = canonical_graph
    client, _, _ = native_api
    receipt, snapshots, runs, _ = _retained_processing_evidence(canonical_graph, native_api, 31)
    before = _processing_evidence_state(canonical_graph)
    def forbidden(*args, **kwargs):
        raise AssertionError("Metadata GET must not use body/execution/replay readers or SELECT-star owners")
    monkeypatch.setattr(CanonicalNativeParserRunRepository, "get", forbidden)
    monkeypatch.setattr(NativeIntakeSnapshotMetadataRepository, "required_for_intake", forbidden)
    monkeypatch.setattr(SourceSnapshotService, "get_snapshot", forbidden)
    query, fetched = graph.engine.query, []
    def observe(sql, params=()):
        rows = query(sql, params)
        if "FROM intake.source_snapshots" in sql or "FROM intake.parser_runs" in sql:
            fetched.extend(rows)
        return rows
    monkeypatch.setattr(graph.engine, "query", observe)
    path = f'/api/v1/canonical-intakes/{receipt["intake_id"]}/processing-evidence'
    pages = []
    cursor = None
    for _ in range(3):
        response = client.get(path, params={"intake_version": receipt["version"], **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        page = response.json()
        pages.append(page)
        cursor = page["next_cursor"]
    assert cursor is None and [len(p["items"]) for p in pages] == [25, 25, 12]
    items = [item for page in pages for item in page["items"]]
    assert [(item["kind"], item["metadata"].get("parser_run_id") or item["metadata"]["source_snapshot_id"]) for item in items] == [
        item for pair in zip([("SNAPSHOT", v) for v in snapshots], [("PARSER_RUN", v) for v in runs], strict=True) for item in pair]
    assert all(p["through_at"] == pages[0]["through_at"] for p in pages)
    assert items[0]["metadata"]["object_generation"] is None
    assert items[2]["metadata"]["object_generation"] == "9223372036854775798"
    assert all("PRIVATE_NATIVE" not in json.dumps(p) and "VERIFIED" not in json.dumps(p) for p in pages)
    for expected, cursor in ((pages[1], pages[2]["previous_cursor"]), (pages[0], pages[1]["previous_cursor"])):
        response = client.get(path, params={"intake_version": receipt["version"], "cursor": cursor})
        assert response.status_code == 200, response.text
        observed = response.json()
        assert {k: v for k, v in observed.items() if k != "observed_at"} == {k: v for k, v in expected.items() if k != "observed_at"}
        assert datetime.fromisoformat(observed["observed_at"]) >= datetime.fromisoformat(expected["observed_at"])
    assert fetched and all(not ({"raw_object_uri", "redacted_object_uri", "encryption_key_ref", "legal_hold_id",
        "parsed_payload", "normalized_payload", "validation_errors", "field_confidence"} & row.keys()) for row in fetched)
    # Undo observation before the broad read-state comparison; no query is substituted.
    monkeypatch.setattr(graph.engine, "query", query)
    assert _processing_evidence_state(canonical_graph) == before


@pytest.mark.parametrize("denial", ["anonymous", "role", "tenant", "scope", "ownership", "flag-disabled", "flag-owner"])
def test_processing_evidence_rechecks_current_original_authority_before_cursor_or_metadata(canonical_graph, native_api, monkeypatch, denial):
    from shared.infrastructure.persistence.audit_log import DurableAuditLog

    graph, ids, _, _ = canonical_graph
    client, caller, _ = native_api
    receipt, _, _, _ = _retained_processing_evidence(canonical_graph, native_api)
    p = caller["principal"]
    if denial == "anonymous":
        caller["principal"] = replace(p, authenticated=False)
    elif denial == "role":
        caller["principal"] = replace(p, roles=frozenset({Role.PLATFORM_ADMIN}))
    elif denial == "tenant":
        caller["principal"] = replace(p, scope=Scope(tenant_id=str(uuid4())))
    elif denial == "scope":
        caller["principal"] = replace(p, scope=replace(p.scope, region_ids=frozenset({str(uuid4())})))
    elif denial == "ownership":
        caller["principal"] = replace(p, subject_id=str(uuid4()))
    elif denial == "flag-disabled":
        graph.queue._flags.execute("disable", "assisted_intake_v1_read", actor="synthetic-admin",
            correlation_id="synthetic-stop-native-metadata", audit_log=DurableAuditLog(graph.engine))
    else:
        monkeypatch.setattr(client.app.state.persistence_bundle, "feature_flag_registry", None)
    before = _processing_evidence_state(canonical_graph)
    path = f'/api/v1/canonical-intakes/{receipt["intake_id"]}'
    expected = {"anonymous": 401, "tenant": 404, "flag-owner": 503}.get(denial, 403)
    for url, params in ((path, {"include_evidence": "true"}), (path + "/processing-evidence",
            {"intake_version": receipt["version"], "cursor": "must-not-decode-before-authority"})):
        response = client.get(url, params=params, headers={"X-Operator-Role": "expansion-manager"})
        assert response.status_code == expected, response.text
        assert "items" not in response.json() and "PRIVATE_NATIVE" not in response.text
    assert _processing_evidence_state(canonical_graph) == before


def test_processing_evidence_cursor_binds_exact_parent_position_version_and_time_bound(canonical_graph, native_api):
    import base64

    _, _, _, db = canonical_graph
    client, _, _ = native_api
    receipt, snapshots, _, _ = _retained_processing_evidence(canonical_graph, native_api, 14)
    other, foreign_snapshots, foreign_runs, _ = _retained_processing_evidence(canonical_graph, native_api)
    with db.connect() as connection:
        stamp = connection.execute("SELECT stored_at FROM intake.source_snapshots WHERE source_snapshot_id=%s", (snapshots[0],)).fetchone()[0]
        connection.execute("UPDATE intake.source_snapshots SET stored_at=%s WHERE source_snapshot_id=%s", (stamp, foreign_snapshots[0]))
        connection.execute("UPDATE intake.parser_runs SET created_at=%s WHERE parser_run_id=%s", (stamp, foreign_runs[0]))
    path = f'/api/v1/canonical-intakes/{receipt["intake_id"]}/processing-evidence'
    first = client.get(path, params={"intake_version": receipt["version"]})
    assert first.status_code == 200, first.text
    encoded = first.json()["next_cursor"]
    decoded = json.loads(base64.urlsafe_b64decode(encoded))
    before = _processing_evidence_state(canonical_graph)
    future = (datetime.fromisoformat(first.json()["observed_at"]) + timedelta(hours=1)).isoformat()
    for change, expected in [({"tenant_id": str(uuid4())}, 422), ({"intake_id": other["intake_id"]}, 422),
        ({"intake_version": receipt["version"] + 1}, 409), ({"through_at": future}, 422),
        ({"position_at": future}, 422), ({"position_kind": "invented"}, 422), ({"direction": "invented"}, 422),
        ({"position_id": str(uuid4())}, 422), ({"position_at": stamp.isoformat(), "position_kind": "SNAPSHOT", "position_id": foreign_snapshots[0]}, 422),
        ({"position_at": stamp.isoformat(), "position_kind": "PARSER_RUN", "position_id": foreign_runs[0]}, 422),
        ({"position_kind": "PARSER_RUN", "position_id": "00000000-0000-0000-0000-000000000000"}, 422),
        ({"actor_override": "manager"}, 422)]:
        cursor = base64.urlsafe_b64encode(json.dumps({**decoded, **change}).encode()).decode()
        response = client.get(path, params={"intake_version": receipt["version"], "cursor": cursor})
        assert response.status_code == expected, (change, response.text)
    for page_size in (0, 26, "true"):
        assert client.get(path, params={"intake_version": receipt["version"], "page_size": page_size}).status_code == 422
    assert _processing_evidence_state(canonical_graph) == before
    with db.connect() as connection:
        connection.execute("UPDATE intake.intakes SET version=version+1 WHERE intake_id=%s", (receipt["intake_id"],))
    before = _processing_evidence_state(canonical_graph)
    stale = client.get(path, params={"intake_version": receipt["version"], "cursor": encoded})
    assert stale.status_code == 409 and stale.json()["detail"] == "CANONICAL_INTAKE_VERSION_CHANGED"
    assert _processing_evidence_state(canonical_graph) == before


@pytest.mark.parametrize("damage", ["snapshot-version", "generation", "hash", "oversize-media", "oversize-release", "foreign-snapshot", "release-source"])
def test_processing_evidence_preserves_corrupt_or_unknown_lineage_as_unavailable(canonical_graph, native_api, damage):
    _, ids, _, db = canonical_graph
    client, _, _ = native_api
    receipt, snapshots, runs, release = _retained_processing_evidence(canonical_graph, native_api)
    with db.connect() as connection:
        if damage == "snapshot-version":
            connection.execute("UPDATE intake.source_snapshots SET version=0 WHERE source_snapshot_id=%s", (snapshots[0],))
        elif damage == "generation":
            connection.execute("UPDATE intake.source_snapshots SET object_generation=-1 WHERE source_snapshot_id=%s", (snapshots[0],))
        elif damage == "hash":
            connection.execute("UPDATE intake.source_snapshots SET content_sha256=%s WHERE source_snapshot_id=%s", ("z" * 64, snapshots[0]))
        elif damage == "oversize-media":
            connection.execute("UPDATE intake.source_snapshots SET media_type=%s WHERE source_snapshot_id=%s", ("a" * 5000, snapshots[0]))
        elif damage == "oversize-release":
            connection.execute("UPDATE intake.parser_releases SET package_name=%s WHERE parser_release_id=%s", ("a" * 5000, release))
        elif damage == "release-source":
            connection.execute("INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,retrieval_mode,policy_owner_subject_id) "
                "VALUES ('synthetic-other-metadata-source','Synthetic only','1','ASSISTED_ENTRY_ONLY',%s)", (ids["actor"],))
            connection.execute("UPDATE intake.parser_releases SET source_id='synthetic-other-metadata-source' WHERE parser_release_id=%s", (release,))
    if damage == "foreign-snapshot":
        _, foreign_snapshots, _, _ = _retained_processing_evidence(canonical_graph, native_api)
        with db.connect() as connection:
            connection.execute("UPDATE intake.parser_runs SET source_snapshot_id=%s WHERE parser_run_id=%s", (foreign_snapshots[0], runs[0]))
    before = _processing_evidence_state(canonical_graph)
    response = client.get(f'/api/v1/canonical-intakes/{receipt["intake_id"]}', params={"include_evidence": "true"})
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "CANONICAL_INTAKE_EVIDENCE_UNAVAILABLE"}
    assert _processing_evidence_state(canonical_graph) == before


def test_processing_evidence_keeps_safe_mime_profile_urls_and_observes_current_run_version(canonical_graph, native_api):
    _, _, _, db = canonical_graph
    client, _, _ = native_api
    receipt, snapshots, runs, _ = _retained_processing_evidence(canonical_graph, native_api)
    mime = 'application/ld+json; profile="https://schema.org/"'
    with db.connect() as connection:
        connection.execute("UPDATE intake.source_snapshots SET media_type=%s WHERE source_snapshot_id=%s", (mime, snapshots[0]))
    path = f'/api/v1/canonical-intakes/{receipt["intake_id"]}'
    first = client.get(path, params={"include_evidence": "true"})
    assert first.status_code == 200, first.text
    first = first.json()["processing_evidence"]
    assert first["items"][0]["metadata"]["media_type"] == mime
    with db.connect() as connection:
        connection.execute("UPDATE intake.parser_runs SET version=version+1 WHERE parser_run_id=%s", (runs[0],))
    second = client.get(path, params={"include_evidence": "true"})
    assert second.status_code == 200, second.text
    second = second.json()["processing_evidence"]
    assert second["intake_version"] == first["intake_version"]
    assert second["items"][1]["metadata"]["version"] == first["items"][1]["metadata"]["version"] + 1
    assert datetime.fromisoformat(second["observed_at"]) >= datetime.fromisoformat(first["observed_at"])


def _native_workflow_actors(ids, monkeypatch, caller):
    """Synthetic manager/assignee/recipient principals for the original V1 routes."""
    from apps.api.oday_api.security import dependencies

    monkeypatch.setattr(dependencies, "principal_from_headers", lambda _: caller["principal"])
    people = {name: str(uuid4()) for name in ("manager", "assignee", "recipient")}
    roles = {"manager": Role.SITE_REVIEWER, "assignee": Role.EXPANSION_USER, "recipient": Role.EXPANSION_USER}

    def act(name):
        subject = ids["actor"] if name == "submitter" else people[name]
        role = Role.EXPANSION_USER if name == "submitter" else roles[name]
        caller["principal"] = Principal(subject, frozenset({role}), Scope(tenant_id=ids["tenant"]))
        return subject

    return people, act


def _native_detail(client, intake_id):
    detail = client.get("/api/v1/canonical-intakes/" + intake_id)
    assert detail.status_code == 200, detail.text
    return detail.json()


def _workflow_rows(case, intake_id):
    graph, ids, _, _ = case
    with graph.transaction(ids["tenant"]):
        query = graph.engine.query
        assignment, = query("SELECT * FROM workflow.assignments WHERE tenant_id=? AND intake_id=?", (ids["tenant"], intake_id))
        sla, = query("SELECT * FROM workflow.sla_instances WHERE tenant_id=? AND intake_id=?", (ids["tenant"], intake_id))
        return {"assignment": assignment, "sla": sla,
            "assignment_transitions": query("SELECT * FROM workflow.assignment_transitions WHERE tenant_id=? "
                "AND assignment_id=? ORDER BY sequence_no", (ids["tenant"], assignment["assignment_id"])),
            "sla_transitions": query("SELECT * FROM workflow.sla_transitions WHERE tenant_id=? "
                "AND sla_instance_id=? ORDER BY sequence_no", (ids["tenant"], sla["sla_instance_id"])),
            "pauses": query("SELECT * FROM workflow.sla_pause_intervals WHERE tenant_id=? AND sla_instance_id=? "
                "ORDER BY paused_at", (ids["tenant"], sla["sla_instance_id"])),
            "intake": query("SELECT version,processing_state FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                (ids["tenant"], intake_id))[0]}


def _legacy_business_documents(case):
    rows = snapshot(case)["documents"]
    return [row for row in rows if row["collection"] in {
        "operator.assisted_intakes", "assisted_intake.assignments", "assisted_intake.slas",
        "assisted_intake.workflow_audits", "assisted_intake.workflow_outbox"}]


def test_native_assignment_and_sla_commands_persist_original_sql_rows_across_restart(
        canonical_graph, native_api, monkeypatch):
    from tests.integration.test_assisted_listing_identity import independent_canonical_graph

    graph, ids, _, db = canonical_graph
    client, caller, _ = native_api
    people, act = _native_workflow_actors(ids, monkeypatch, caller)
    act("submitter")
    receipt = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-native-workflow-submit-001"}).json()
    intake_id = receipt["intake_id"]
    workflow = _native_detail(client, intake_id)["workflow"]
    assignment_id, sla_id = workflow["assignment_id"], workflow["sla_instance_id"]

    act("manager")
    assign_body = {"owner_subject_id": people["assignee"], "owner_role": "expansion_user",
        "due_at": workflow["due_at"], "reason": "Synthetic native assignment"}
    assign_headers = {"Idempotency-Key": "synthetic-native-assign-0001", "If-Match": 'W/"1"'}
    assigned = client.put(f"/api/v1/intakes/{intake_id}/assignment", json=assign_body, headers=assign_headers)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "ASSIGNED" and assigned.json()["version"] == 2
    assert assigned.json()["owner_subject_id"] == people["assignee"]
    # Same key/body returns the stored outcome; another body is refused.
    replayed = client.put(f"/api/v1/intakes/{intake_id}/assignment", json=assign_body, headers=assign_headers)
    assert replayed.status_code == 200 and replayed.json() == assigned.json()
    assert client.put(f"/api/v1/intakes/{intake_id}/assignment", headers=assign_headers,
        json={**assign_body, "reason": "Another synthetic intent"}).status_code == 409
    observed = client.get(f"/api/v1/intakes/{intake_id}/assignment/receipt",
        headers={"Idempotency-Key": assign_headers["Idempotency-Key"]})
    assert observed.status_code == 200, observed.text
    assert observed.json()["observation"] == "RECEIPT_COMMITTED" and observed.json()["receipt"] == assigned.json()
    assert observed.headers["Cache-Control"] == "no-store"
    unseen = client.get(f"/api/v1/intakes/{intake_id}/assignment/receipt",
        headers={"Idempotency-Key": "synthetic-native-assign-unseen"})
    assert unseen.status_code == 200 and unseen.json()["observation"] == "NOT_OBSERVED"

    act("assignee")
    claim_headers = {"Idempotency-Key": "synthetic-native-claim-00001", "If-Match": 'W/"2"'}
    claimed = client.post(f"/api/v1/assignments/{assignment_id}/actions/claim",
        json={"reason": "Synthetic native claim"}, headers=claim_headers)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["status"] == "CLAIMED" and claimed.json()["version"] == 3

    act("manager")
    sla_version = _workflow_rows(canonical_graph, intake_id)["sla"]["version"]
    resumed_due = []
    for round_no in (1, 2):
        expected = (datetime.fromisoformat(graph.engine.query_one("SELECT clock_timestamp() AS at")["at"])
                    + timedelta(hours=round_no)).isoformat()
        paused = client.post(f"/api/v1/sla-instances/{sla_id}/actions/pause",
            json={"reason": "Synthetic approved hold", "expected_resume_at": expected},
            headers={"Idempotency-Key": f"synthetic-native-pause-{round_no:05d}", "If-Match": f'W/"{sla_version}"'})
        assert paused.status_code == 200, paused.text
        assert paused.json()["state"] == "PAUSED" and paused.json()["active_pause_interval_id"]
        sla_version = paused.json()["version"]
        resumed = client.post(f"/api/v1/sla-instances/{sla_id}/actions/resume",
            json={"reason": "Synthetic hold released"},
            headers={"Idempotency-Key": f"synthetic-native-resume-{round_no:04d}", "If-Match": f'W/"{sla_version}"'})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["state"] == "ON_TRACK" and resumed.json()["active_pause_interval_id"] is None
        sla_version = resumed.json()["version"]
        resumed_due.append(resumed.json()["due_at"])
        pause_receipt = client.get(f"/api/v1/sla-instances/{sla_id}/actions/pause/receipt",
            headers={"Idempotency-Key": f"synthetic-native-pause-{round_no:05d}"})
        assert pause_receipt.json()["receipt"] == paused.json()
    rows = _workflow_rows(canonical_graph, intake_id)
    assert len(rows["pauses"]) == 2 and all(p["resumed_at"] is not None for p in rows["pauses"])
    for pause in rows["pauses"]:
        # Each interval keeps its own recorded expectation, distinct from its actual resume.
        assert pause["expected_resume_at"] != pause["resumed_at"]
        assert pause["approved_by"] != pause["created_by"] and pause["version"] == 2
    assert rows["sla"]["paused_at"] is None and rows["sla"]["resume_at"] is None
    assert [t["permission"] for t in rows["sla_transitions"]] == [
        "sla.create", "sla.schedule_resolved", "sla.pause", "sla.resume", "sla.pause", "sla.resume"]
    assert [t["resulting_version"] for t in rows["sla_transitions"]] == list(range(1, 7))
    # Resume moved the deadline; the bound Assignment follows in the same unit.
    assert datetime.fromisoformat(rows["assignment"]["due_at"]) == datetime.fromisoformat(resumed_due[-1].replace("Z", "+00:00"))
    assert rows["assignment"]["version"] == 5
    assert rows["intake"] == {"version": 1, "processing_state": "SUBMITTED"}

    act("assignee")
    transferred = client.post(f"/api/v1/assignments/{assignment_id}/actions/transfer",
        json={"target_owner_subject_id": people["recipient"], "target_owner_role": "expansion_user",
              "reason": "Synthetic native transfer", "handoff_note": "Synthetic handoff context"},
        headers={"Idempotency-Key": "synthetic-native-transfer-001", "If-Match": 'W/"5"'})
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["status"] == "TRANSFERRED" and transferred.json()["owner_subject_id"] == people["recipient"]

    act("recipient")
    accepted = client.post(f"/api/v1/assignments/{assignment_id}/actions/claim",
        json={"reason": "Synthetic recipient claim"},
        headers={"Idempotency-Key": "synthetic-native-claim-00002", "If-Match": 'W/"6"'})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "CLAIMED" and accepted.json()["version"] == 8
    before_complete = snapshot(canonical_graph)
    incomplete = client.post(f"/api/v1/assignments/{assignment_id}/actions/complete",
        json={"reason": "Synthetic completion attempt"},
        headers={"Idempotency-Key": "synthetic-native-complete-01", "If-Match": 'W/"8"'})
    # The original evaluator needs the Intake's committed terminal work.
    assert incomplete.status_code == 409 and "WORK_INCOMPLETE" in incomplete.text
    assert snapshot(canonical_graph) == before_complete
    complete_probe = client.get(f"/api/v1/assignments/{assignment_id}/actions/complete/receipt",
        headers={"Idempotency-Key": "synthetic-native-complete-01"})
    assert complete_probe.status_code == 200 and complete_probe.json()["observation"] == "NOT_OBSERVED"

    rows = _workflow_rows(canonical_graph, intake_id)
    assert [(t["from_status"], t["to_status"], t["permission"]) for t in rows["assignment_transitions"]] == [
        (None, "UNASSIGNED", "assignment.create"), ("UNASSIGNED", "ASSIGNED", "assignment.assign"),
        ("ASSIGNED", "CLAIMED", "assignment.claim"), ("CLAIMED", "CLAIMED", "sla.deadline_adjusted"),
        ("CLAIMED", "CLAIMED", "sla.deadline_adjusted"), ("CLAIMED", "TRANSFERRED", "assignment.transfer"),
        ("TRANSFERRED", "ASSIGNED", "assignment.accept"), ("ASSIGNED", "CLAIMED", "assignment.claim")]
    assert [t["resulting_version"] for t in rows["assignment_transitions"]] == list(range(1, 9))
    assert rows["assignment"]["previous_owner_subject_id"] == people["assignee"]
    assert rows["assignment"]["assigned_by"] == people["manager"]
    after = snapshot(canonical_graph)
    actions = sorted(r["action"] for r in after["audit.audit_events"]
                     if r["resource_type"] in {"assignment", "sla_instance"})
    assert actions.count("assignment.claim") == 2 and actions.count("sla.pause") == 2
    events = [json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
              for r in after["workflow.outbox_events"]]
    assert all(validate_event(event) == [] for event in events)
    assert {"assignment.assigned", "assignment.claimed", "assignment.transferred"} <= {e["event_type"] for e in events}
    assert _legacy_business_documents(canonical_graph) == []
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok

    act("manager")
    detail = _native_detail(client, intake_id)["workflow"]
    with independent_canonical_graph(graph, db) as reopened_graph:
        reopened = DurableAssistedIntakeStore(PostgresDocumentStore(reopened_graph.engine))
        reopened.configure_canonical_audit(reopened_graph.audit_sink)
        bundle = SimpleNamespace(engine=reopened_graph.engine, job_queue=reopened_graph.queue,
            assisted_intake_store=reopened, assisted_intake_workflow_policy=SyntheticIntakeWorkflowPolicy(),
            feature_flag_registry=reopened_graph.queue._flags)
        for name in ("canonical_identity_graph", "canonical_intakes"):
            setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
        app = FastAPI()
        app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
        app.include_router(create_assisted_intake_router(reopened), prefix="/api/v1")
        with TestClient(app) as restarted:
            assert _native_detail(restarted, intake_id)["workflow"] == detail
            assert detail["assignment_version"] == 8 and detail["assignment_status"] == "CLAIMED"
            assert detail["owner_subject_id"] == people["recipient"] and detail["sla_version"] == 7
            act("assignee")
            again = restarted.post(f"/api/v1/assignments/{assignment_id}/actions/claim",
                json={"reason": "Synthetic native claim"}, headers=claim_headers)
            assert again.status_code == 200 and again.json() == claimed.json()
            observed = restarted.get(f"/api/v1/assignments/{assignment_id}/actions/claim/receipt",
                headers={"Idempotency-Key": claim_headers["Idempotency-Key"]})
            assert observed.json()["receipt"] == claimed.json()
    assert snapshot(canonical_graph) == after


def test_native_workflow_commands_refuse_stale_foreign_and_unauthorized_without_writes(
        canonical_graph, native_api, monkeypatch):
    graph, ids, _, _ = canonical_graph
    client, caller, _ = native_api
    people, act = _native_workflow_actors(ids, monkeypatch, caller)
    act("submitter")
    intake_id = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-native-negative-submit"}).json()["intake_id"]
    workflow = _native_detail(client, intake_id)["workflow"]
    body = {"owner_subject_id": people["assignee"], "owner_role": "expansion_user",
            "due_at": workflow["due_at"], "reason": "Synthetic native assignment"}
    before = snapshot(canonical_graph)
    # Staff cannot assign; stale Intake version is a conflict; captured
    # principal fences refuse before any storage.
    assert client.put(f"/api/v1/intakes/{intake_id}/assignment", json=body,
        headers={"Idempotency-Key": "synthetic-native-neg-assign-1", "If-Match": 'W/"1"'}).status_code == 403
    act("manager")
    assert client.put(f"/api/v1/intakes/{intake_id}/assignment", json=body,
        headers={"Idempotency-Key": "synthetic-native-neg-assign-2", "If-Match": 'W/"7"'}).status_code == 409
    assert client.put(f"/api/v1/intakes/{intake_id}/assignment", json=body,
        headers={"Idempotency-Key": "synthetic-native-neg-assign-3", "If-Match": 'W/"1"',
                 "If-Actor-Match": str(uuid4())}).status_code in {403, 409}
    # Unclaimed SLA pause by a manager with a non-approved reason is denied.
    assert client.post(f"/api/v1/sla-instances/{workflow['sla_instance_id']}/actions/pause",
        json={"reason": "Not an approved hold", "expected_resume_at": "2099-01-01T00:00:00Z"},
        headers={"Idempotency-Key": "synthetic-native-neg-pause-1", "If-Match": 'W/"1"'}).status_code == 409
    # Another tenant cannot see the native resource at all.
    caller["principal"] = Principal(people["manager"], frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=str(uuid4())))
    assert client.post(f"/api/v1/assignments/{workflow['assignment_id']}/actions/claim",
        json={"reason": "Synthetic foreign claim"},
        headers={"Idempotency-Key": "synthetic-native-neg-claim-1", "If-Match": 'W/"1"'}).status_code == 404
    assert snapshot(canonical_graph) == before
    assert _legacy_business_documents(canonical_graph) == []


@pytest.mark.parametrize("mutation", ["audit", "outbox", "receipt", "transition"])
def test_native_workflow_failure_rolls_back_rows_audit_outbox_and_receipt_then_same_key_succeeds(
        canonical_graph, native_api, monkeypatch, mutation):
    graph, ids, _, _ = canonical_graph
    client, caller, store = native_api
    people, act = _native_workflow_actors(ids, monkeypatch, caller)
    act("submitter")
    intake_id = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-native-rollback-sub"}).json()["intake_id"]
    workflow = _native_detail(client, intake_id)["workflow"]
    act("manager")
    body = {"owner_subject_id": people["assignee"], "owner_role": "expansion_user",
            "due_at": workflow["due_at"], "reason": "Synthetic native assignment"}
    headers = {"Idempotency-Key": "synthetic-native-rollback-assign", "If-Match": 'W/"1"'}
    before = snapshot(canonical_graph)
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise CanonicalAuditUnavailable("Explicit synthetic transaction failure")

        if mutation == "audit":
            patch.setattr(graph.audit_sink, "persist", fail)
        elif mutation == "outbox":
            patch.setattr(type(graph), "_outbox", fail)
        elif mutation == "transition":
            execute = graph.engine.execute

            def fail_transition(sql, *args, **kwargs):
                if sql.startswith("INSERT INTO workflow.sla_transitions"):
                    fail()
                return execute(sql, *args, **kwargs)

            patch.setattr(graph.engine, "execute", fail_transition)
        else:
            patch.setattr(store, "flush", fail)
        failed = client.put(f"/api/v1/intakes/{intake_id}/assignment", json=body, headers=headers)
        assert failed.status_code == 503, failed.text
    assert snapshot(canonical_graph) == before
    probe = client.get(f"/api/v1/intakes/{intake_id}/assignment/receipt",
        headers={"Idempotency-Key": headers["Idempotency-Key"]})
    assert probe.status_code == 200 and probe.json()["observation"] == "NOT_OBSERVED"
    retried = client.put(f"/api/v1/intakes/{intake_id}/assignment", json=body, headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "ASSIGNED"


def test_historical_pause_without_recorded_expectation_stays_unavailable(canonical_graph, native_api, monkeypatch):
    graph, ids, _, _ = canonical_graph
    client, caller, _ = native_api
    people, act = _native_workflow_actors(ids, monkeypatch, caller)
    act("submitter")
    intake_id = client.post("/api/v1/canonical-intakes", json=command(),
        headers={"Idempotency-Key": "synthetic-native-history-sub1"}).json()["intake_id"]
    workflow = _native_detail(client, intake_id)["workflow"]
    with graph.transaction(ids["tenant"]):
        # An older closed interval with no recorded expectation (pre-007 row).
        graph.engine.execute("INSERT INTO workflow.sla_pause_intervals (tenant_id,sla_instance_id,paused_at,resumed_at,"
            "reason,approved_by,created_by) VALUES (?,?,?,?,?,?,?)",
            (ids["tenant"], workflow["sla_instance_id"], workflow["started_at"],
             (datetime.fromisoformat(workflow["started_at"]) + timedelta(minutes=5)).isoformat(),
             "Synthetic approved hold", str(uuid4()), str(uuid4())))
    before = snapshot(canonical_graph)
    act("manager")
    response = client.post(f"/api/v1/sla-instances/{workflow['sla_instance_id']}/actions/pause",
        json={"reason": "Synthetic approved hold", "expected_resume_at": "2099-01-01T00:00:00Z"},
        headers={"Idempotency-Key": "synthetic-native-history-pause", "If-Match": 'W/"1"'})
    assert response.status_code == 503 and "INTAKE_WORKFLOW_POLICY_UNAVAILABLE" in response.text
    assert snapshot(canonical_graph) == before


def _native_completion_env(db, tmp_path, monkeypatch, *, completion_requirements):
    """Real SQL registry/identities/audit/identity-graph fixture for native completion."""
    import hashlib
    from contextlib import contextmanager
    from copy import deepcopy
    from pathlib import Path

    from apps.api.app.routes import canonical_identity_decisions, canonical_match_cases
    from apps.api.oday_api.security import dependencies
    from modules.listing.application.identity_delivery import (
        IDENTITY_POLICY_ENV,
        IDENTITY_SIGNING_KEY_ENV,
    )
    from modules.listing.application.intake_audit import (
        CanonicalIntakeAuditSink,
        RegistryIntakeAuditRetention,
    )
    from modules.listing.application.intake_registry_calendar import RegistryIntakeWorkflowPolicy
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.audit.worm import LocalAppendOnlyWormSink
    from shared.identity import SqlIdentityStore
    from shared.infrastructure.persistence.decision_policy import SqlDecisionPolicyRepository
    from shared.infrastructure.persistence.feature_flags import DurableFeatureFlagRegistry
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine
    from tests.intake_registry_fixture import (
        APPROVER,
        MANAGER,
        OWNER,
        TENANT,
        _audit_retention_row,
        _calendar_row,
        _workflow_row,
    )
    from tests.intake_workflow_fixture import seed_synthetic_intake_read_flag
    from tests.integration.test_intake_registry_calendar_postgresql import (
        _insert_policy,
        _seed_identities,
    )

    reviewer2 = "00000000-0000-0000-0000-000000000109"
    ids = {
        "tenant": TENANT,
        "approver": APPROVER,
        "manager": MANAGER,
        "owner": OWNER,
        "reviewer": reviewer2,
        "source": str(uuid4()),
        "target": str(uuid4()),
        "listing": str(uuid4()),
        "edge": str(uuid4()),
    }
    monkeypatch.setenv(IDENTITY_SIGNING_KEY_ENV, "Explicit synthetic native completion signing key material")
    monkeypatch.setenv(IDENTITY_POLICY_ENV, json.dumps({
        "policy_id": "synthetic-native-completion-identity", "version": 1,
        "max_attempts": 2, "deadline_seconds": 120, "backoff_seconds": [1],
    }))
    root = Path(__file__).resolve().parents[2]
    row = _workflow_row()
    parameters = deepcopy(row.parameters)
    parameters["assignee_role_bindings"]["site-reviewer"] = Role.SITE_REVIEWER.value
    parameters["completion_requirements"] = completion_requirements
    with db.connect() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS core")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS core.tenants ("
            "tenant_id UUID PRIMARY KEY, tenant_name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active')"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workflow.decisions ("
            "decision_id UUID PRIMARY KEY, policy_version_id VARCHAR(100))"
        )
        for migration in (
            "infra/db/migrations/000011_identity_schema.sql",
            "infra/db/migrations/000014_decision_policy_registry.sql",
            "infra/db/migrations/000029_intake_policy_calendar_kinds.sql",
            "infra/db/migrations/000030_intake_audit_retention_kind.sql",
            "infra/db/migrations/000034_native_intake_auth_wait.sql",
        ):
            conn.execute((root / migration).read_text())
        _seed_identities(conn)
        conn.execute(
            "INSERT INTO identity.accounts(account_id,tenant_id,username,email,status,created_by) "
            "VALUES (%s,%s,%s,%s,'active','isolated-synthetic-test')",
            (reviewer2, TENANT, reviewer2, reviewer2 + "@example.invalid"),
        )
        conn.execute(
            "INSERT INTO identity.account_roles(account_id,role,granted_by) "
            "VALUES (%s,%s,'isolated-synthetic-test')",
            (reviewer2, Role.SITE_REVIEWER.value),
        )
        conn.execute("INSERT INTO identity.account_scopes(account_id) VALUES (%s)", (reviewer2,))
        _insert_policy(conn, replace(row, parameters=parameters))
        _insert_policy(conn, _calendar_row())
        _insert_policy(conn, _audit_retention_row())
        conn.execute(
            "INSERT INTO intake.source_registry (source_id,display_name,canonicalization_rule_version,"
            "retrieval_mode,policy_owner_subject_id) VALUES ('synthetic-feed','Synthetic only','1',"
            "'ASSISTED_ENTRY_ONLY',%s)",
            (APPROVER,),
        )
        for key, version in (("source", 7), ("target", 11)):
            conn.execute(
                "INSERT INTO identity.properties (property_id,tenant_id,normalized_address,address_fingerprint,version) "
                "VALUES (%s,%s,%s,%s,%s)",
                (ids[key], TENANT, key, hashlib.sha256(key.encode()).hexdigest(), version),
            )
        conn.execute(
            "INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,"
            "lifecycle_state,version,created_at) VALUES (%s,%s,%s,'synthetic-feed','source-17','ACTIVE',13,"
            "clock_timestamp()-interval '2 days')",
            (ids["listing"], TENANT, ids["source"]),
        )
        conn.execute(
            "INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,listing_id,"
            "property_id,match_strategy,confidence,edge_version,effective_from) "
            "VALUES (%s,%s,'synthetic-feed','source-17',%s,%s,'HUMAN',0.75,5,clock_timestamp()-interval '1 day')",
            (ids["edge"], TENANT, ids["listing"], ids["source"]),
        )

    caller = {}
    roles = {
        MANAGER: Role.SITE_REVIEWER,
        reviewer2: Role.SITE_REVIEWER,
        OWNER: Role.EXPANSION_USER,
        APPROVER: Role.DATA_OWNER,
    }

    def act(actor_id):
        caller["principal"] = Principal(actor_id, frozenset({roles[actor_id]}), Scope(tenant_id=TENANT))

    for module in (canonical_intakes, canonical_match_cases, canonical_identity_decisions, dependencies):
        monkeypatch.setattr(module, "principal_from_headers", lambda _: caller["principal"])

    worm_dir = tmp_path / "synthetic-native-completion-worm"

    @contextmanager
    def open_client(*, seed_flag=False):
        engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
        try:
            if seed_flag:
                engine.apply_runtime_migration()
            doc_store = PostgresDocumentStore(engine)
            flags = DurableFeatureFlagRegistry(doc_store)
            if seed_flag:
                flags.initialize_defaults()
                seed_synthetic_intake_read_flag(flags, TENANT)
            queue = DurableJobQueue(engine, flags=flags)
            identities = SqlIdentityStore(connection_factory=engine.pooled_connection)
            policies = SqlDecisionPolicyRepository(engine)
            workflow_policy = RegistryIntakeWorkflowPolicy(policies, identities)
            writer = CanonicalPostgresAuditLog(engine)
            audit_sink = CanonicalIntakeAuditSink(
                writer,
                RegistryIntakeAuditRetention(policies, identities),
                CanonicalAuditDelivery(writer, queue, LocalAppendOnlyWormSink(worm_dir)),
            )
            store = DurableAssistedIntakeStore(doc_store)
            store.configure_canonical_audit(audit_sink)
            bundle = SimpleNamespace(
                engine=engine,
                job_queue=queue,
                assisted_intake_store=store,
                assisted_intake_workflow_policy=workflow_policy,
                feature_flag_registry=flags,
            )
            for name in (
                "canonical_identity_graph",
                "canonical_intakes",
                "canonical_match_cases",
                "canonical_identity_decisions",
            ):
                setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
            app = FastAPI()
            app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
            app.include_router(
                create_assisted_intake_router(store, workflow_policy=workflow_policy),
                prefix="/api/v1",
            )
            with TestClient(app) as client:
                yield client, bundle
        finally:
            engine.close()

    def state_snapshot(bundle):
        graph = bundle.canonical_identity_graph()
        with graph.transaction(TENANT):
            tables = (
                "intake.intakes", "identity.match_cases", "identity.match_decisions",
                "intake.human_corrections", "expansion.promotion_decisions", "workflow.jobs",
                "workflow.assignments", "workflow.assignment_transitions",
                "workflow.sla_instances", "workflow.sla_transitions",
                "audit.audit_events", "workflow.outbox_events",
            )
            rows = {
                table: sorted(
                    graph.engine.query("SELECT * FROM " + table + " WHERE tenant_id=?", (TENANT,)),
                    key=lambda item: json.dumps(item, sort_keys=True),
                )
                for table in tables
            }
            rows["documents"] = graph.engine.query(
                "SELECT collection,doc_id,data FROM durable_documents ORDER BY collection,doc_id"
            )
            return rows

    def start_claimed(client, *, key_prefix):
        act(OWNER)
        created = client.post(
            "/api/v1/canonical-intakes",
            json=command(),
            headers={"Idempotency-Key": f"{key_prefix}-submit"},
        )
        assert created.status_code == 201, created.text
        intake_id = created.json()["intake_id"]
        workflow = _native_detail(client, intake_id)["workflow"]
        assignment_id, sla_id = workflow["assignment_id"], workflow["sla_instance_id"]
        act(MANAGER)
        assigned = client.put(
            f"/api/v1/intakes/{intake_id}/assignment",
            json={
                "owner_subject_id": OWNER, "owner_role": "expansion-staff",
                "due_at": workflow["due_at"], "reason": "Synthetic SQL native assignment",
            },
            headers={"Idempotency-Key": f"{key_prefix}-assign", "If-Match": 'W/"1"'},
        )
        assert assigned.status_code == 200, assigned.text
        act(OWNER)
        claimed = client.post(
            f"/api/v1/assignments/{assignment_id}/actions/claim",
            json={"reason": "Synthetic SQL native claim"},
            headers={
                "Idempotency-Key": f"{key_prefix}-claim",
                "If-Match": f'W/"{assigned.json()["version"]}"',
            },
        )
        assert claimed.status_code == 200, claimed.text
        return intake_id, assignment_id, sla_id, claimed.json()["version"]

    def propose_merge(client, intake_id, *, key_prefix):
        with db.connect() as conn:
            conn.execute(
                "UPDATE intake.intakes SET processing_state='NEEDS_REVIEW',version=2 WHERE intake_id=%s",
                (intake_id,),
            )
        act(MANAGER)
        case_resp = client.post(
            f"/api/v1/intakes/{intake_id}/match-case",
            json={
                "outcome": "POSSIBLE_MATCH", "confidence": "0.75",
                "supporting_signals": [{"signal": "address_near_match"}], "contradictory_signals": [],
                "reason": "Synthetic native match case for identity review completion test",
                "risk_acknowledged": True,
                "candidates": [
                    {
                        "property_id": ids["source"], "property_version": 7,
                        "listing_id": ids["listing"], "listing_version": 13,
                        "confidence": "0.75", "evidence": {"observation": "Synthetic source"},
                    },
                    {
                        "property_id": ids["target"], "property_version": 11,
                        "listing_id": None, "listing_version": None,
                        "confidence": "0.60", "evidence": {"observation": "Synthetic target"},
                    },
                ],
            },
            headers={"Idempotency-Key": f"{key_prefix}-case", "If-Match": 'W/"2"'},
        )
        assert case_resp.status_code == 201, case_resp.text
        case_id = case_resp.json()["match_case_id"]
        ctx = client.get(
            f"/api/v1/match-cases/{case_id}/identity-context",
            params={"include_listing_input": "false"},
        )
        assert ctx.status_code == 200, ctx.text
        view = ctx.json()
        plan = {
            "source_property_ids": [ids["source"]],
            "target_property_id": ids["target"],
            "expected_property_versions": {r["property_id"]: r["version"] for r in view["properties"]},
            "expected_listing_versions": {r["listing_id"]: r["version"] for r in view["listings"]},
            "expected_edge_versions": {
                r["edge_id"]: r["edge_version"]
                for r in view["source_identity_edges"] if r["property_id"] == ids["source"]
            },
            "candidate_reassignment_plan": [],
        }
        proposal = client.post(
            f"/api/v1/match-cases/{case_id}/identity-decisions",
            json={
                "decision_type": "MERGE", "plan": plan, "risk_acknowledged": True,
                "reason": "Synthetic native merge proposal awaiting independent review",
            },
            headers={
                "Idempotency-Key": f"{key_prefix}-proposal",
                "If-Match": f'W/"{case_resp.json()["version"]}"',
            },
        )
        assert proposal.status_code == 201, proposal.text
        return case_id, proposal.json()

    return ids, act, open_client, state_snapshot, start_claimed, propose_merge


def test_native_completion_succeeds_after_real_identity_review_rejection_reopens_and_replays(
    intake_db, tmp_path, monkeypatch
):
    db = intake_db
    ids, act, open_client, state_snapshot, start_claimed, propose_merge = _native_completion_env(
        db, tmp_path, monkeypatch,
        completion_requirements=[{
            "kind": "decision", "allowed_states": ["REJECTED"],
            "minimum_count": 1, "require_audit": True,
        }],
    )
    complete_key = "synthetic-native-complete-rejected-001"
    with open_client(seed_flag=True) as (client, bundle):
        intake_id, aid, sid, version = start_claimed(client, key_prefix="synthetic-rej")
        _, proposal = propose_merge(client, intake_id, key_prefix="synthetic-rej")

        # Pending decision cannot satisfy REJECTED requirement.
        act(ids["owner"])
        before_pending = state_snapshot(bundle)
        denied = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Attempt completion before review rejection"},
            headers={"Idempotency-Key": "synthetic-rej-early-complete", "If-Match": f'W/"{version}"'},
        )
        assert denied.status_code == 409 and "WORK_INCOMPLETE" in denied.text
        assert state_snapshot(bundle) == before_pending

        # Real independent reviewer rejects the identity decision through the HTTP API.
        act(ids["reviewer"])
        rejected = client.post(
            f'/api/v1/canonical-identity-decisions/{proposal["decision_id"]}/actions/review',
            json={
                "decision": "REJECT",
                "expected_case_version": proposal["match_case_version"],
                "reason": "Independent reviewer rejected the proposed merge",
                "risk_acknowledged": False,
            },
            headers={
                "Idempotency-Key": "synthetic-rej-review-001",
                "If-Match": f'W/"{proposal["version"]}"',
            },
        )
        assert rejected.status_code == 200, rejected.text
        review_receipt = rejected.json()
        assert review_receipt["status"] == "REJECTED"

        # Original complete HTTP action now succeeds with the exact bound rejection audit.
        act(ids["owner"])
        completed = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Complete native assignment after verified rejection"},
            headers={"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'},
        )
        assert completed.status_code == 200, completed.text
        receipt = completed.json()
        assert receipt["status"] == "COMPLETED" and receipt["version"] == version + 1

        after = state_snapshot(bundle)
        assignment_row, = [r for r in after["workflow.assignments"] if r["assignment_id"] == aid]
        sla_row, = [r for r in after["workflow.sla_instances"] if r["sla_instance_id"] == sid]
        assert assignment_row["status"] == "COMPLETED" and sla_row["state"] == "COMPLETED"
        assert sla_row["completed_at"] is not None
        complete_audit = next(
            r for r in after["audit.audit_events"] if r["audit_event_id"] == receipt["audit_event_id"]
        )
        after_val = (
            json.loads(complete_audit["after_value"])
            if isinstance(complete_audit["after_value"], str)
            else complete_audit["after_value"]
        )
        assert after_val["completion_evidence"] == [{
            "resource_type": "decision",
            "resource_id": proposal["decision_id"],
            "version": review_receipt["version"],
            "state": "REJECTED",
            "audit_event_id": review_receipt["audit_event_id"],
        }]
        events = [
            json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"]
            for r in after["workflow.outbox_events"]
        ]
        assert all(validate_event(event) == [] for event in events)
        assert {"assignment.completed", "sla.state_changed"} <= {e["event_type"] for e in events}
        assert not [
            r for r in after["documents"]
            if r["collection"] in {
                "operator.assisted_intakes", "assisted_intake.assignments", "assisted_intake.slas",
                "assisted_intake.workflow_audits", "assisted_intake.workflow_outbox",
            }
        ]
        assert bundle.canonical_identity_graph().audit_sink.writer.verify_chain(ids["tenant"]).ok

    # Reopen a fresh engine/store: completed state and same-key replay survive restart,
    # and current SQL actor/policy authority is still enforced on replay.
    with open_client() as (restarted, fresh_bundle):
        act(ids["manager"])
        workflow = _native_detail(restarted, intake_id)["workflow"]
        assert workflow["assignment_status"] == "COMPLETED" and workflow["sla_state"] == "COMPLETED"
        act(ids["owner"])
        before_replay = state_snapshot(fresh_bundle)
        replay = restarted.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Complete native assignment after verified rejection"},
            headers={"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'},
        )
        assert replay.status_code == 200 and replay.json() == receipt
        assert state_snapshot(fresh_bundle) == before_replay
        observed = restarted.get(
            f"/api/v1/assignments/{aid}/actions/complete/receipt",
            headers={"Idempotency-Key": complete_key},
        )
        assert observed.status_code == 200 and observed.json()["receipt"] == receipt
        with db.connect() as conn:
            conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["owner"],))
        revoked_actor = restarted.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Complete native assignment after verified rejection"},
            headers={"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'},
        )
        assert revoked_actor.status_code == 403, revoked_actor.text
        with db.connect() as conn:
            conn.execute("UPDATE identity.accounts SET status='active' WHERE account_id=%s", (ids["owner"],))
            conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["approver"],))
        revoked_policy = restarted.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Complete native assignment after verified rejection"},
            headers={"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'},
        )
        assert revoked_policy.status_code == 503, revoked_policy.text
        assert state_snapshot(fresh_bundle) == before_replay


def test_native_completion_succeeds_after_real_executed_identity_decision_and_rejects_raw_status(
    intake_db, tmp_path, monkeypatch
):
    from modules.listing.application.identity_delivery import IdentityJobAdmission
    from modules.listing.application.identity_execution import IdentityExecutionService

    db = intake_db
    ids, act, open_client, state_snapshot, start_claimed, propose_merge = _native_completion_env(
        db, tmp_path, monkeypatch,
        completion_requirements=[{
            "kind": "decision", "allowed_states": ["EXECUTED"], "action": "MERGE",
            "minimum_count": 1, "require_audit": True,
        }],
    )
    with open_client(seed_flag=True) as (client, bundle):
        intake_id, aid, sid, version = start_claimed(client, key_prefix="synthetic-exec")
        _, proposal = propose_merge(client, intake_id, key_prefix="synthetic-exec")

        # Raw status='EXECUTED' without graph.execution_receipt must stay denied.
        with db.connect() as conn:
            conn.execute(
                "UPDATE identity.match_decisions SET status='EXECUTED',executed_at=clock_timestamp() "
                "WHERE match_decision_id=%s",
                (proposal["decision_id"],),
            )
        act(ids["owner"])
        before_raw = state_snapshot(bundle)
        raw_denied = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Raw EXECUTED row without execution receipt must fail"},
            headers={"Idempotency-Key": "synthetic-exec-raw-denied", "If-Match": f'W/"{version}"'},
        )
        assert raw_denied.status_code == 409 and "WORK_INCOMPLETE" in raw_denied.text
        assert state_snapshot(bundle) == before_raw
        # Restore PENDING_REVIEW so the real review + worker execution path runs cleanly.
        with db.connect() as conn:
            conn.execute(
                "UPDATE identity.match_decisions SET status='PENDING_REVIEW',executed_at=NULL "
                "WHERE match_decision_id=%s",
                (proposal["decision_id"],),
            )

        act(ids["reviewer"])
        approved = client.post(
            f'/api/v1/canonical-identity-decisions/{proposal["decision_id"]}/actions/review',
            json={
                "decision": "APPROVE",
                "expected_case_version": proposal["match_case_version"],
                "reason": "Independent reviewer approved native merge execution",
                "risk_acknowledged": True,
            },
            headers={
                "Idempotency-Key": "synthetic-exec-approve-001",
                "If-Match": f'W/"{proposal["version"]}"',
            },
        )
        assert approved.status_code == 200, approved.text
        job_id = approved.json()["job_id"]
        graph = bundle.canonical_identity_graph()
        delivery = None
        for _ in range(30):
            candidate = graph.queue.claim_next(worker_id="synthetic-native-exec-worker")
            assert candidate is not None
            if candidate.job_id == job_id:
                delivery = candidate
                break
            assert candidate.job_type == "canonical-audit-delivery"
            graph.audit_sink.delivery.deliver(candidate)
        assert delivery is not None and delivery.job_id == job_id
        exec_result = IdentityExecutionService(graph).run(delivery)
        IdentityJobAdmission(graph).settle_delivery(delivery)

        # Tampering workflow.jobs or decision version invalidates graph.execution_receipt.
        with db.connect() as conn:
            conn.execute(
                "UPDATE workflow.jobs SET status='FAILED' WHERE job_id=%s", (job_id,)
            )
        act(ids["owner"])
        before_bad_job = state_snapshot(bundle)
        assert client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Tampered execution job must fail"},
            headers={"Idempotency-Key": "synthetic-exec-bad-job", "If-Match": f'W/"{version}"'},
        ).status_code == 409
        assert state_snapshot(bundle) == before_bad_job
        with db.connect() as conn:
            conn.execute(
                "UPDATE workflow.jobs SET status='SUCCEEDED' WHERE job_id=%s", (job_id,)
            )
            conn.execute(
                "UPDATE identity.match_decisions SET version=version+1 WHERE match_decision_id=%s",
                (proposal["decision_id"],),
            )
        before_bad_ver = state_snapshot(bundle)
        assert client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Drifted decision version must fail"},
            headers={"Idempotency-Key": "synthetic-exec-bad-ver", "If-Match": f'W/"{version}"'},
        ).status_code == 409
        assert state_snapshot(bundle) == before_bad_ver
        with db.connect() as conn:
            conn.execute(
                "UPDATE identity.match_decisions SET version=version-1 WHERE match_decision_id=%s",
                (proposal["decision_id"],),
            )

        # Genuine unmodified EXECUTED decision succeeds through graph.execution_receipt.
        complete_key = "synthetic-native-complete-exec-001"
        completed = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Complete after real executed merge decision"},
            headers={"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'},
        )
        assert completed.status_code == 200, completed.text
        receipt = completed.json()
        assert receipt["status"] == "COMPLETED"
        after = state_snapshot(bundle)
        complete_audit = next(
            r for r in after["audit.audit_events"] if r["audit_event_id"] == receipt["audit_event_id"]
        )
        after_val = (
            json.loads(complete_audit["after_value"])
            if isinstance(complete_audit["after_value"], str)
            else complete_audit["after_value"]
        )
        assert after_val["completion_evidence"] == [{
            "resource_type": "decision",
            "resource_id": proposal["decision_id"],
            "version": 5,
            "state": "EXECUTED",
            "audit_event_id": exec_result["audit_event_id"],
        }]
        assert bundle.canonical_identity_graph().audit_sink.writer.verify_chain(ids["tenant"]).ok

    with open_client() as (restarted, _):
        act(ids["owner"])
        replay = restarted.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Complete after real executed merge decision"},
            headers={"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'},
        )
        assert replay.status_code == 200 and replay.json() == receipt


def test_native_completion_succeeds_after_real_intake_cancellation_and_denies_tampered_audit(
    intake_db, tmp_path, monkeypatch
):
    from shared.jobs.queue import JobRequest, JobStatus

    db = intake_db
    ids, act, open_client, state_snapshot, start_claimed, _ = _native_completion_env(
        db, tmp_path, monkeypatch,
        completion_requirements=[{
            "kind": "intake", "allowed_states": ["READY", "CANCELLED"],
            "minimum_count": 1, "require_audit": True,
        }],
    )
    with open_client(seed_flag=True) as (client, bundle):
        intake_id, aid, _, version = start_claimed(client, key_prefix="synthetic-cancel")
        # Set up settled authorization-wait precondition so real POST /cancel commits intake.cancelled.v1.
        job_id = str(uuid4())
        graph = bundle.canonical_identity_graph()
        with graph.transaction(ids["tenant"]):
            corr = graph.engine.query_one(
                "SELECT correlation_id FROM intake.intakes WHERE tenant_id=? AND intake_id=?",
                (ids["tenant"], intake_id),
            )["correlation_id"]
            graph.engine.execute(
                "UPDATE intake.intakes SET processing_state='AWAITING_SOURCE_AUTHORIZATION',"
                "source_policy_state='AUTH_REQUIRED',version=2 WHERE tenant_id=? AND intake_id=?",
                (ids["tenant"], intake_id),
            )
            graph.engine.execute(
                "INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,correlation_id,"
                "idempotency_key,version) VALUES (?,?,'native-intake-source-policy','intake',?,"
                "'SUCCEEDED','AWAITING_SOURCE_AUTHORIZATION',1,1,1,clock_timestamp()+interval '1 hour',"
                "'{}'::jsonb,?,?,2)",
                (job_id, ids["tenant"], intake_id, corr, "synthetic-policy:" + intake_id),
            )
            delivery, _ = graph.queue.enqueue(
                JobRequest("native-intake-source-policy", {"intake_id": intake_id},
                           idempotency_key="synthetic-policy:" + intake_id, job_id=job_id),
                correlation_id=corr,
            )
        claimed_job = None
        for _ in range(30):
            candidate = graph.queue.claim_next(worker_id="synthetic-policy-worker")
            assert candidate is not None
            if candidate.job_id == delivery.job_id:
                claimed_job = candidate
                break
            assert candidate.job_type == "canonical-audit-delivery"
            graph.audit_sink.delivery.deliver(candidate)
        assert claimed_job is not None and claimed_job.job_id == delivery.job_id
        graph.queue.update_status(
            job_id, JobStatus.SUCCEEDED,
            expected_version=claimed_job.version, fence_token=claimed_job.fence_token,
        )
        act(ids["owner"])
        cancelled = client.post(
            f"/api/v1/canonical-intakes/{intake_id}/cancel",
            json={"reason": "Cancel settled authorization wait"},
            headers={"Idempotency-Key": "synthetic-real-cancel-001", "If-Match": 'W/"2"'},
        )
        assert cancelled.status_code == 200, cancelled.text
        cancel_receipt = cancelled.json()

        # Corrupting the committed intake audit hash must deny completion fail-closed.
        with db.connect() as conn:
            saved_sha = conn.execute(
                "SELECT event_sha256 FROM audit.audit_events WHERE audit_event_id=%s",
                (cancel_receipt["audit_event_id"],),
            ).fetchone()[0]
            conn.execute(
                "UPDATE audit.audit_events SET event_sha256=repeat('0', 64) WHERE audit_event_id=%s",
                (cancel_receipt["audit_event_id"],),
            )
        before_tamper = state_snapshot(bundle)
        assert client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Corrupt intake audit must be rejected"},
            headers={"Idempotency-Key": "synthetic-cancel-corrupt", "If-Match": f'W/"{version}"'},
        ).status_code == 409
        assert state_snapshot(bundle) == before_tamper
        with db.connect() as conn:
            conn.execute(
                "UPDATE audit.audit_events SET event_sha256=%s WHERE audit_event_id=%s",
                (saved_sha, cancel_receipt["audit_event_id"]),
            )

        complete_key = "synthetic-native-complete-cancel-001"
        completed = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Complete after real native intake cancellation"},
            headers={"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'},
        )
        assert completed.status_code == 200, completed.text
        receipt = completed.json()
        after = state_snapshot(bundle)
        complete_audit = next(
            r for r in after["audit.audit_events"] if r["audit_event_id"] == receipt["audit_event_id"]
        )
        after_val = (
            json.loads(complete_audit["after_value"])
            if isinstance(complete_audit["after_value"], str)
            else complete_audit["after_value"]
        )
        assert after_val["completion_evidence"] == [{
            "resource_type": "intake",
            "resource_id": intake_id,
            "version": cancel_receipt["version"],
            "state": "CANCELLED",
            "audit_event_id": cancel_receipt["audit_event_id"],
        }]
        assert bundle.canonical_identity_graph().audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("tamper", [
    "missing_audit", "wrong_tenant", "wrong_intake", "wrong_resource", "wrong_version",
    "wrong_state", "uncommitted_audit", "corrupt_sha", "unresolved_sibling",
    "revoked_actor", "revoked_approver",
])
def test_native_completion_denies_unbound_tampered_uncommitted_and_revoked_proof(
    intake_db, tmp_path, monkeypatch, tamper
):
    from psycopg.types.json import Jsonb

    db = intake_db
    ids, act, open_client, state_snapshot, start_claimed, propose_merge = _native_completion_env(
        db, tmp_path, monkeypatch,
        completion_requirements=[{
            "kind": "decision", "allowed_states": ["REJECTED"],
            "minimum_count": 1, "require_audit": True,
        }],
    )
    with open_client(seed_flag=True) as (client, bundle):
        intake_id, aid, _, version = start_claimed(client, key_prefix="synthetic-neg")
        case_id, proposal = propose_merge(client, intake_id, key_prefix="synthetic-neg")
        act(ids["reviewer"])
        rejected = client.post(
            f'/api/v1/canonical-identity-decisions/{proposal["decision_id"]}/actions/review',
            json={
                "decision": "REJECT",
                "expected_case_version": proposal["match_case_version"],
                "reason": "Independent reviewer rejected the proposed merge",
                "risk_acknowledged": False,
            },
            headers={
                "Idempotency-Key": "synthetic-neg-review-001",
                "If-Match": f'W/"{proposal["version"]}"',
            },
        )
        assert rejected.status_code == 200, rejected.text
        audit_id = rejected.json()["audit_event_id"]

        if tamper == "wrong_intake":
            other_intake, _, _, _ = start_claimed(client, key_prefix="synthetic-other-intake")
            _, other_prop = propose_merge(client, other_intake, key_prefix="synthetic-other-intake")
            act(ids["reviewer"])
            other_rej = client.post(
                f'/api/v1/canonical-identity-decisions/{other_prop["decision_id"]}/actions/review',
                json={
                    "decision": "REJECT",
                    "expected_case_version": other_prop["match_case_version"],
                    "reason": "Independent rejection on another intake",
                    "risk_acknowledged": False,
                },
                headers={
                    "Idempotency-Key": "synthetic-other-review-001",
                    "If-Match": f'W/"{other_prop["version"]}"',
                },
            )
            assert other_rej.status_code == 200, other_rej.text
            foreign_audit_id = other_rej.json()["audit_event_id"]
            with db.connect() as conn:
                conn.execute(
                    "UPDATE identity.match_decisions SET after_graph="
                    "jsonb_set(after_graph, '{review,audit_event_id}', to_jsonb(%s::text)) "
                    "WHERE match_decision_id=%s",
                    (foreign_audit_id, proposal["decision_id"]),
                )
        elif tamper == "missing_audit":
            with db.connect() as conn:
                conn.execute("DELETE FROM audit.audit_events WHERE audit_event_id=%s", (audit_id,))
        elif tamper == "wrong_tenant":
            with db.connect() as conn:
                conn.execute(
                    "UPDATE audit.audit_events SET tenant_id=%s WHERE audit_event_id=%s",
                    (str(uuid4()), audit_id),
                )
        elif tamper == "wrong_resource":
            with db.connect() as conn:
                intake_audit = conn.execute(
                    "SELECT audit_event_id::text FROM audit.audit_events "
                    "WHERE resource_type='intake' AND resource_id=%s LIMIT 1",
                    (intake_id,),
                ).fetchone()[0]
                conn.execute(
                    "UPDATE identity.match_decisions SET after_graph="
                    "jsonb_set(after_graph, '{review,audit_event_id}', to_jsonb(%s::text)) "
                    "WHERE match_decision_id=%s",
                    (intake_audit, proposal["decision_id"]),
                )
        elif tamper == "wrong_version":
            with db.connect() as conn:
                conn.execute(
                    "UPDATE identity.match_decisions SET version=version+1 WHERE match_decision_id=%s",
                    (proposal["decision_id"],),
                )
        elif tamper == "wrong_state":
            with db.connect() as conn:
                conn.execute(
                    "UPDATE identity.match_decisions SET after_graph="
                    "jsonb_set(after_graph, '{review,audit_event_id}', to_jsonb(%s::text)) "
                    "WHERE match_decision_id=%s",
                    (proposal["audit_event_id"], proposal["decision_id"]),
                )
        elif tamper == "uncommitted_audit":
            with db.connect() as conn:
                conn.execute(
                    "UPDATE identity.match_decisions SET after_graph="
                    "jsonb_set(after_graph, '{review,audit_event_id}', to_jsonb(%s::text)) "
                    "WHERE match_decision_id=%s",
                    (str(uuid4()), proposal["decision_id"]),
                )
        elif tamper == "corrupt_sha":
            with db.connect() as conn:
                conn.execute(
                    "UPDATE audit.audit_events SET event_sha256=repeat('0', 64) WHERE audit_event_id=%s",
                    (audit_id,),
                )
        elif tamper == "unresolved_sibling":
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO identity.match_decisions (match_decision_id,tenant_id,match_case_id,"
                    "decision_type,status,proposer_subject_id,reason,risk_acknowledged,before_graph,after_graph) "
                    "VALUES (%s,%s,%s,'MERGE','PENDING_REVIEW',%s,'Unresolved sibling decision',true,%s,%s)",
                    (str(uuid4()), ids["tenant"], case_id, ids["manager"], Jsonb({}), Jsonb({})),
                )
        elif tamper == "revoked_actor":
            with db.connect() as conn:
                conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["owner"],))
        else:
            with db.connect() as conn:
                conn.execute("UPDATE identity.accounts SET status='disabled' WHERE account_id=%s", (ids["approver"],))

        act(ids["owner"])
        before = state_snapshot(bundle)
        response = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Attempt completion with tampered or revoked proof"},
            headers={"Idempotency-Key": f"synthetic-neg-{tamper}", "If-Match": f'W/"{version}"'},
        )
        expected = 403 if tamper == "revoked_actor" else 503 if tamper == "revoked_approver" else 409
        assert response.status_code == expected, response.text
        if expected == 409:
            assert "WORK_INCOMPLETE" in response.text
        assert state_snapshot(bundle) == before


@pytest.mark.parametrize("kind,state", [
    ("correction", "APPLIED"),
    ("promotion", "COMPLETED"),
    ("job", "SUCCEEDED"),
    ("decision", "REVERSED"),
    ("decision", "SUPERSEDED"),
])
def test_native_completion_fails_closed_for_unsupported_or_unaudited_policy_work_kinds(
    intake_db, tmp_path, monkeypatch, kind, state
):
    from psycopg.types.json import Jsonb

    db = intake_db
    ids, act, open_client, state_snapshot, start_claimed, propose_merge = _native_completion_env(
        db, tmp_path, monkeypatch,
        completion_requirements=[{
            "kind": kind, "allowed_states": [state], "minimum_count": 1, "require_audit": True,
        }],
    )
    with open_client(seed_flag=True) as (client, bundle):
        intake_id, aid, _, version = start_claimed(client, key_prefix=f"synthetic-unsup-{kind}")
        if kind == "correction":
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO intake.human_corrections (correction_id,tenant_id,intake_id,field_path,"
                    "field_classification,corrected_value,after_effective_value,reason,proposed_by,"
                    "reviewed_by,status,version) VALUES (%s,%s,%s,'rent','INTERNAL',%s,%s,"
                    "'Synthetic applied correction without per-row audit',%s,%s,'APPLIED',2)",
                    (str(uuid4()), ids["tenant"], intake_id, Jsonb(50000), Jsonb(50000),
                     ids["owner"], ids["manager"]),
                )
        elif kind == "promotion":
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO expansion.promotion_decisions (promotion_decision_id,tenant_id,intake_id,"
                    "listing_id,property_id,target_format_code,status,proposer_subject_id,reviewer_subject_id,"
                    "reason,risk_acknowledged,gate_snapshot,version) VALUES (%s,%s,%s,%s,%s,'STANDARD',"
                    "'COMPLETED',%s,%s,'Synthetic promotion without native audit producer',true,%s,2)",
                    (str(uuid4()), ids["tenant"], intake_id, ids["listing"], ids["source"],
                     ids["owner"], ids["manager"], Jsonb({})),
                )
        elif kind == "job":
            with db.connect() as conn:
                corr = conn.execute(
                    "SELECT correlation_id FROM intake.intakes WHERE intake_id=%s", (intake_id,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO workflow.jobs (job_id,tenant_id,job_type,aggregate_type,aggregate_id,"
                    "status,checkpoint,attempt,max_attempts,fence_token,timeout_at,payload,result,"
                    "correlation_id,idempotency_key,version) VALUES (%s,%s,'native-intake-start','intake',"
                    "%s,'SUCCEEDED','CHECKING_IDENTITY',1,1,1,clock_timestamp()+interval '1 hour',"
                    "%s,%s,%s,%s,2)",
                    (str(uuid4()), ids["tenant"], intake_id, Jsonb({}), Jsonb({}), corr,
                     "synthetic-job:" + intake_id),
                )
        else:
            _, proposal = propose_merge(client, intake_id, key_prefix=f"synthetic-unsup-{state}")
            with db.connect() as conn:
                conn.execute(
                    "UPDATE identity.match_decisions SET status=%s,version=version+1 WHERE match_decision_id=%s",
                    (state, proposal["decision_id"]),
                )

        act(ids["owner"])
        before = state_snapshot(bundle)
        response = client.post(
            f"/api/v1/assignments/{aid}/actions/complete",
            json={"reason": "Unsupported native work kind must stay fail-closed"},
            headers={"Idempotency-Key": f"synthetic-unsup-{kind}-{state}", "If-Match": f'W/"{version}"'},
        )
        assert response.status_code == 409 and "WORK_INCOMPLETE" in response.text
        assert state_snapshot(bundle) == before


@pytest.mark.parametrize("failure", ["audit", "outbox", "receipt", "transition", "backpressure"])
def test_native_completion_rollback_and_backpressure_preserve_atomicity_and_same_key_retry(
    intake_db, tmp_path, monkeypatch, failure
):
    from shared.audit.canonical_delivery import JOB_TYPE as AUDIT_DELIVERY_JOB_TYPE

    db = intake_db
    ids, act, open_client, state_snapshot, start_claimed, propose_merge = _native_completion_env(
        db, tmp_path, monkeypatch,
        completion_requirements=[{
            "kind": "decision", "allowed_states": ["REJECTED"],
            "minimum_count": 1, "require_audit": True,
        }],
    )
    with open_client(seed_flag=True) as (client, bundle):
        intake_id, aid, _, version = start_claimed(client, key_prefix=f"synthetic-rb-{failure}")
        _, proposal = propose_merge(client, intake_id, key_prefix=f"synthetic-rb-{failure}")
        act(ids["reviewer"])
        rejected = client.post(
            f'/api/v1/canonical-identity-decisions/{proposal["decision_id"]}/actions/review',
            json={
                "decision": "REJECT",
                "expected_case_version": proposal["match_case_version"],
                "reason": "Independent reviewer rejected before rollback check",
                "risk_acknowledged": False,
            },
            headers={
                "Idempotency-Key": f"synthetic-rb-review-{failure}",
                "If-Match": f'W/"{proposal["version"]}"',
            },
        )
        assert rejected.status_code == 200, rejected.text

        act(ids["owner"])
        complete_key = f"synthetic-rb-complete-{failure}"
        headers = {"Idempotency-Key": complete_key, "If-Match": f'W/"{version}"'}
        body = {"reason": "Complete after rejection with rollback protection"}
        if failure == "backpressure":
            with bundle.engine.transaction():
                bundle.engine.execute(
                    "UPDATE durable_jobs SET created_at=created_at-interval '10 minutes' "
                    "WHERE job_type=?",
                    (AUDIT_DELIVERY_JOB_TYPE,),
                )
        before = state_snapshot(bundle)

        def fail(*args, **kwargs):
            raise CanonicalAuditUnavailable("Explicit synthetic completion failure")

        with monkeypatch.context() as patch:
            if failure == "audit":
                patch.setattr(bundle.assisted_intake_store._canonical_audit_sink, "persist", fail)
            elif failure == "outbox":
                from shared.infrastructure.persistence.identity_graph_sql import (
                    CanonicalIdentityGraphRepository,
                )

                patch.setattr(CanonicalIdentityGraphRepository, "_outbox", fail)
            elif failure == "receipt":
                patch.setattr(bundle.assisted_intake_store, "flush", fail)
            elif failure == "transition":
                execute = bundle.engine.execute

                def fail_transition(sql, *args, **kwargs):
                    if sql.startswith("INSERT INTO workflow.sla_transitions"):
                        fail()
                    return execute(sql, *args, **kwargs)

                patch.setattr(bundle.engine, "execute", fail_transition)
            failed = client.post(f"/api/v1/assignments/{aid}/actions/complete", json=body, headers=headers)
            assert failed.status_code == 503, failed.text

        assert state_snapshot(bundle) == before
        probe = client.get(
            f"/api/v1/assignments/{aid}/actions/complete/receipt",
            headers={"Idempotency-Key": complete_key},
        )
        assert probe.status_code == 200 and probe.json()["observation"] == "NOT_OBSERVED"

        if failure == "backpressure":
            with bundle.engine.transaction():
                bundle.engine.execute(
                    "UPDATE durable_jobs SET created_at=clock_timestamp() WHERE job_type=?",
                    (AUDIT_DELIVERY_JOB_TYPE,),
                )
        retried = client.post(f"/api/v1/assignments/{aid}/actions/complete", json=body, headers=headers)
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "COMPLETED"
        assert bundle.canonical_identity_graph().audit_sink.writer.verify_chain(ids["tenant"]).ok
