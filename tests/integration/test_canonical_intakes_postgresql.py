"""Native submissions, using an owned PostgreSQL and synthetic principals only."""

import gc
import json
from dataclasses import replace
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
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph

pytestmark = pytest.mark.requires_live_env


@pytest.fixture
def native_api(canonical_graph, monkeypatch):
    graph, ids, _, _ = canonical_graph
    store = DurableAssistedIntakeStore(PostgresDocumentStore(graph.engine))
    store.configure_canonical_audit(graph.audit_sink)
    bundle = SimpleNamespace(engine=graph.engine, job_queue=graph.queue, assisted_intake_store=store)
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


def snapshot(case):
    graph, ids, _, _ = case
    with graph.transaction(ids["tenant"]):
        tables = ("intake.intakes", "intake.intake_stage_transitions", "identity.match_cases", "expansion.listings",
                  "workflow.jobs", "audit.audit_events", "workflow.outbox_events")
        rows = {table: sorted(graph.engine.query("SELECT * FROM " + table + " WHERE tenant_id=?", (ids["tenant"],)),
                              key=lambda row: json.dumps(row, sort_keys=True)) for table in tables}
        rows["documents"] = graph.engine.query("SELECT collection, doc_id, data FROM durable_documents ORDER BY collection, doc_id")
        rows["delivery"] = graph.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")
        return rows


@pytest.mark.parametrize("method", ["MANUAL", "URL"])
def test_native_submission_is_atomic_durable_and_never_claims_processing(canonical_graph, native_api, method):
    from shared.infrastructure.persistence.postgresql import PostgresEngine

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
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    replay = client.post("/api/v1/canonical-intakes", headers=headers, json=body)
    assert replay.status_code == 201 and replay.json() == receipt and replay.headers["Idempotency-Replayed"] == "true"
    assert snapshot(canonical_graph) == after
    assert client.post("/api/v1/canonical-intakes", headers=headers, json=command(reason="Different synthetic intent")).status_code == 409
    assert snapshot(canonical_graph) == after
    reopened_engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        reopened = DurableAssistedIntakeStore(PostgresDocumentStore(reopened_engine))
        reopened.refresh(ids["tenant"])
        assert next(iter(reopened.replays.values()))[1] == receipt
        assert reopened_engine.query_one("SELECT version, processing_state FROM intake.intakes WHERE intake_id=?",
                                         (receipt["intake_id"],)) == {"version": 1, "processing_state": "SUBMITTED"}
    finally:
        reopened_engine.close()
    store.refresh(ids["tenant"])


@pytest.mark.parametrize("mutation", ["audit", "outbox", "receipt"])
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
    else:
        monkeypatch.setattr(store, "flush", fail)
    response = client.post("/api/v1/canonical-intakes", json=command(), headers={"Idempotency-Key": "synthetic-native-rollback-001"})
    assert response.status_code == 503, response.text
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
