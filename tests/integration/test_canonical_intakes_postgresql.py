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
    headers = {"Idempotency-Key": "synthetic-exact-submission-receipt-001"}
    created = client.post("/api/v1/canonical-intakes", json=body, headers=headers)
    assert created.status_code == 201, created.text
    original = created.json()
    before = snapshot(canonical_graph)
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
    absent = client.get("/api/v1/canonical-intakes/submissions/receipt", headers=headers)
    assert absent.status_code == 200, absent.text
    assert absent.json()["observation"] == "NOT_OBSERVED" and "receipt" not in absent.json()
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
