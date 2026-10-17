"""Real native case entry on explicitly synthetic matching evidence and actors."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest

from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
from shared.domain.events import EventContractValidator, validate_event
from shared.infrastructure.persistence.identity_cases_sql import (
    CanonicalMatchCaseRepository,
    MatchCandidateEvidence,
)
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph

pytestmark = pytest.mark.requires_live_env


def _parent(case, *, state="MATCHING"):
    _, ids, _, db = case
    intake_id, correlation = str(uuid4()), str(uuid4())
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO intake.intakes (intake_id,tenant_id,submitter_subject_id,intake_method,processing_state,"
            "correlation_id,version) VALUES (%s,%s,%s,'MANUAL',%s,%s,17)",
            (intake_id, ids["tenant"], ids["actor"], state, correlation),
        )
    return intake_id


def _command(case, **overrides):
    _, ids, _, _ = case
    source = MatchCandidateEvidence(ids["source"], 7, Decimal("0.7500"),
        {"observation": "Explicit synthetic matching evidence"}, ids["listing"], 13)
    target = MatchCandidateEvidence(ids["target"], 11, Decimal("0.6250"),
        {"observation": "Explicit alternative physical identity"})
    return {
        "expected_intake_version": 17, "actor_id": ids["actor"], "confidence": Decimal("0.7500"),
        # Reverse lexical input order makes accidental rank-by-lock-order fail.
        "candidates": tuple(sorted((source, target), key=lambda item: item.property_id, reverse=True)),
        "supporting_signals": [{"kind": "SYNTHETIC_REPORTED_EVIDENCE", "description": "Matching mechanism only"}],
        "contradictory_signals": [], "reason": "Explicit synthetic matching review context",
        **overrides,
    }


def _snapshot(case):
    graph, ids, _, _ = case
    with graph.transaction(ids["tenant"]):
        tables = ("intake.intakes", "intake.intake_stage_transitions", "identity.match_cases",
                  "identity.match_candidates", "audit.audit_events", "workflow.outbox_events")
        result = {table: sorted(graph.engine.query("SELECT * FROM " + table + " WHERE tenant_id=?", (ids["tenant"],)),
                               key=lambda row: json.dumps(row, sort_keys=True)) for table in tables}
        result["delivery"] = graph.engine.query("SELECT * FROM durable_jobs ORDER BY job_id")
        return result


def test_case_creation_preserves_explicit_intake_candidates_versions_rank_and_events(canonical_graph):
    from modules.listing.application.intake_audit import CanonicalIntakeAuditSink
    from shared.audit.canonical_delivery import CanonicalAuditDelivery
    from shared.audit.canonical_postgresql import CanonicalPostgresAuditLog
    from shared.infrastructure.persistence.identity_graph_sql import (
        CanonicalIdentityGraphRepository,
    )
    from shared.infrastructure.persistence.job_queue import DurableJobQueue
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    graph, ids, _, db = canonical_graph
    intake_id = _parent(canonical_graph)
    command = _command(canonical_graph)
    repo = CanonicalMatchCaseRepository(graph)
    receipt = repo.create_review_case(ids["tenant"], intake_id, **command)
    assert receipt["status"] == "PENDING_REVIEW" and receipt["version"] == 2 and receipt["intake_version"] == 18
    context = repo.get_for_intake(ids["tenant"], intake_id)
    assert context["match_case"]["match_case_id"] == receipt["match_case_id"]
    assert context["intake"]["intake_id"] == intake_id and context["intake"]["processing_state"] == "NEEDS_REVIEW"
    assert context["match_case"]["supporting_signals"] == command["supporting_signals"]
    assert [row["property_id"] for row in context["candidates"]] == [row.property_id for row in command["candidates"]]
    for row in context["candidates"]:
        original = next(c for c in command["candidates"] if c.property_id == row["property_id"])
        assert row["evidence"]["property_version"] == original.property_version
        if original.listing_id:
            assert row["evidence"]["listing_version"] == 13
    with db.connect() as conn:
        transition = conn.execute("SELECT from_state,to_state,expected_version,resulting_version,match_case_id FROM intake.intake_stage_transitions WHERE intake_id=%s", (intake_id,)).fetchone()
        assert transition[:4] == ("MATCHING", "NEEDS_REVIEW", 17, 18) and str(transition[4]) == receipt["match_case_id"]
        assert conn.execute("SELECT count(*) FROM audit.audit_events").fetchone()[0] == 3
        rows = conn.execute("SELECT payload FROM workflow.outbox_events").fetchall()
        assert len(rows) == 2 and all(validate_event(row[0]) == [] for row in rows)
        assert {row[0]["event_type"] for row in rows} == {"intake.state_changed", "match.review_required"}
        catalog = EventContractValidator().catalog
        assert all(row[0]["producer"] == catalog[row[0]["event_type"]]["producer"] for row in rows)
        assert conn.execute("SELECT count(*) FROM identity.match_decisions WHERE match_case_id=%s", (receipt["match_case_id"],)).fetchone()[0] == 0
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok
    other_engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        queue = DurableJobQueue(other_engine)
        writer = CanonicalPostgresAuditLog(other_engine)
        other = CanonicalIdentityGraphRepository(other_engine, queue,
            CanonicalIntakeAuditSink(writer, graph.audit_sink.retention,
                CanonicalAuditDelivery(writer, queue, graph.audit_sink.delivery.sink)))
        assert CanonicalMatchCaseRepository(other).get_for_intake(ids["tenant"], intake_id) == context
    finally:
        other_engine.close()


def test_select_case_never_falls_back_to_another_intake_or_tenant(canonical_graph):
    graph, ids, _, _ = canonical_graph
    repo = CanonicalMatchCaseRepository(graph)
    missing_case_intake = _parent(canonical_graph)
    with pytest.raises(IdentityNotFoundError, match="MATCH_CASE_NOT_FOUND"):
        repo.get_for_intake(ids["tenant"], missing_case_intake)
    with pytest.raises(IdentityNotFoundError):
        repo.get_for_intake(str(uuid4()), ids["intake"])
    assert repo.get_for_intake(ids["tenant"], ids["intake"])["match_case"]["match_case_id"] == ids["case"]


@pytest.mark.parametrize("mutation,error", [
    ("UPDATE intake.intakes SET version=version+1", "INTAKE_VERSION_CONFLICT"),
    ("UPDATE intake.intakes SET legal_hold=true", "LEGAL_HOLD_CONFLICT"),
    ("UPDATE identity.properties SET version=version+1", "PROPERTY_VERSION_CONFLICT"),
    ("UPDATE expansion.listings SET version=version+1", "LISTING_VERSION_CONFLICT"),
    ("UPDATE expansion.listings SET legal_hold=true", "LEGAL_HOLD_CONFLICT"),
    ("UPDATE intake.intakes SET processing_state='CANCELLED'", "INTAKE_NOT_AWAITING_IDENTITY_REVIEW"),
])
def test_case_entry_rejects_changed_native_authority_atomically(canonical_graph, mutation, error):
    graph, ids, _, db = canonical_graph
    intake_id = _parent(canonical_graph)
    with db.connect() as conn:
        conn.execute(mutation)
    before = _snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match=error):
        CanonicalMatchCaseRepository(graph).create_review_case(ids["tenant"], intake_id, **_command(canonical_graph))
    assert _snapshot(canonical_graph) == before


def test_existing_case_is_selected_without_overwriting_its_evidence(canonical_graph):
    graph, ids, _, _ = canonical_graph
    intake_id = _parent(canonical_graph, state="NEEDS_REVIEW")
    repo = CanonicalMatchCaseRepository(graph)
    created = repo.create_review_case(ids["tenant"], intake_id, **_command(canonical_graph))
    assert created["intake_version"] == 17
    before = _snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="MATCH_CASE_ALREADY_EXISTS"):
        repo.create_review_case(ids["tenant"], intake_id, **_command(canonical_graph))
    assert repo.get_for_intake(ids["tenant"], intake_id)["match_case"]["match_case_id"] == created["match_case_id"]
    assert _snapshot(canonical_graph) == before


def test_case_audit_failure_rolls_back_intake_case_candidates_transitions_and_delivery(canonical_graph, monkeypatch):
    from shared.audit.errors import CanonicalAuditUnavailable

    graph, ids, _, _ = canonical_graph
    intake_id = _parent(canonical_graph)
    persist = graph.audit_sink.persist
    before = _snapshot(canonical_graph)
    calls = 0

    def fail_last(audit, scope):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise CanonicalAuditUnavailable("Synthetic third audit failure")
        return persist(audit, scope)

    monkeypatch.setattr(graph.audit_sink, "persist", fail_last)
    with pytest.raises(CanonicalAuditUnavailable, match="third audit"):
        CanonicalMatchCaseRepository(graph).create_review_case(ids["tenant"], intake_id, **_command(canonical_graph))
    assert calls == 3 and _snapshot(canonical_graph) == before


@pytest.fixture
def case_api(canonical_graph, monkeypatch):
    from types import MethodType, SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.api.app.routes import canonical_match_cases
    from apps.api.app.routes.listings import create_assisted_intake_router
    from shared.audit import InMemoryAuditLog
    from shared.auth import Principal, Role, Scope
    from shared.infrastructure.persistence import PostgresDocumentStore
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.factory import PersistenceBundle

    graph, ids, _, _ = canonical_graph
    store = DurableAssistedIntakeStore(PostgresDocumentStore(graph.engine))
    store.configure_canonical_audit(graph.audit_sink)
    bundle = SimpleNamespace(engine=graph.engine, job_queue=graph.queue, assisted_intake_store=store)
    for name in ("canonical_identity_graph", "canonical_match_cases"):
        setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
    # Explicit synthetic authentication result. Production uses the existing
    # password/JWT boundary, rather than accepting a persona as a grant.
    caller = {"principal": Principal(ids["actor"], frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=ids["tenant"]))}
    monkeypatch.setattr(canonical_match_cases, "principal_from_headers", lambda _: caller["principal"])
    app = FastAPI()
    app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
    app.include_router(create_assisted_intake_router(store), prefix="/api/v1")
    with TestClient(app) as client:
        yield client, caller, store


def _http_command(case):
    command = _command(case)
    body = {k: command[k] for k in ("supporting_signals", "contradictory_signals", "reason")}
    body.update(confidence=str(command["confidence"]), risk_acknowledged=True,
        candidates=[{"property_id": c.property_id, "property_version": c.property_version,
                     "listing_id": c.listing_id, "listing_version": c.listing_version,
                     "confidence": str(c.confidence), "evidence": c.evidence} for c in command["candidates"]])
    return body


def test_default_router_case_entry_and_lost_response_replay_are_one_native_commit(canonical_graph, case_api):
    from shared.infrastructure.persistence import PostgresDocumentStore
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    graph, ids, _, db = canonical_graph
    client, caller, store = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    headers = {"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-native-case-001"}
    body = _http_command(canonical_graph)
    assert client.get(path).status_code == 404
    created = client.post(path, headers=headers, json=body)
    assert created.status_code == 201, created.text
    assert created.headers["etag"] == 'W/"2"'
    before = _snapshot(canonical_graph)
    replay = client.post(path, headers=headers, json=body)
    assert replay.status_code == 201 and replay.json() == created.json()
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert _snapshot(canonical_graph) == before
    selected = client.get(path)
    assert selected.status_code == 200 and selected.json()["match_case_id"] == created.json()["match_case_id"]
    assert "evidence" not in selected.text and "supporting_signals" not in selected.text
    assert client.post(path, headers=headers, json={**body, "reason": "Changed intent"}).status_code == 409
    assert client.post(path, headers={**headers, "If-Match": 'W/"18"'}, json=body).status_code == 409
    assert _snapshot(canonical_graph) == before
    # A new store/connection reads the receipt, independent of this API cache.
    reopened_engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        reopened = DurableAssistedIntakeStore(PostgresDocumentStore(reopened_engine))
        reopened.refresh(ids["tenant"])
        assert len(reopened.replays) == 1
        assert next(iter(reopened.replays.values()))[1] == created.json()
    finally:
        reopened_engine.close()


@pytest.mark.parametrize("revocation", ["role", "tenant", "parent_scope", "candidate_scope", "anonymous"])
def test_case_http_replay_reauthorizes_native_parent_and_candidates(canonical_graph, case_api, revocation):
    from dataclasses import replace

    from shared.auth import Role, Scope

    graph, ids, _, db = canonical_graph
    client, caller, _ = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    headers = {"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-native-case-002"}
    body = _http_command(canonical_graph)
    assert client.post(path, headers=headers, json=body).status_code == 201
    principal = caller["principal"]
    if revocation == "role":
        caller["principal"] = replace(principal, roles=frozenset({Role.OPERATIONS_MANAGER}))
    elif revocation == "tenant":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=str(uuid4())))
    elif revocation == "anonymous":
        caller["principal"] = replace(principal, authenticated=False)
    else:
        region = str(uuid4())
        caller["principal"] = replace(principal, scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({region})))
        if revocation == "candidate_scope":
            with db.connect() as conn:
                conn.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, intake_id))
    before = _snapshot(canonical_graph)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code in {401, 403, 404}, response.text
    assert client.get(path).status_code in {401, 403, 404}
    assert _snapshot(canonical_graph) == before


def test_case_http_receipt_failure_rolls_back_all_native_and_receipt_writes(canonical_graph, case_api, monkeypatch):
    from shared.infrastructure.persistence.assisted_listing_intake import (
        AssistedIntakePersistenceConflict,
    )

    _, ids, _, _ = canonical_graph
    client, _, store = case_api
    intake_id = _parent(canonical_graph)
    before = _snapshot(canonical_graph)
    flush = store.flush

    def unavailable():
        flush()
        raise AssistedIntakePersistenceConflict("Synthetic receipt write failure")

    monkeypatch.setattr(store, "flush", unavailable)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    response = client.post(path, json=_http_command(canonical_graph),
        headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-native-case-003"})
    assert response.status_code == 409, response.text
    assert _snapshot(canonical_graph) == before
    store.refresh(ids["tenant"])
    assert store.replays == {}


def test_existing_listing_only_candidate_selects_actual_case_without_invented_versions(canonical_graph, case_api):
    _, ids, _, db = canonical_graph
    client, _, _ = case_api
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO identity.match_candidates (tenant_id,match_case_id,listing_id,rank,confidence,evidence) "
            "VALUES (%s,%s,%s,1,0.75,'[]'::jsonb)", (ids["tenant"], ids["case"], ids["listing"]),
        )
    before = _snapshot(canonical_graph)
    selected = client.get(f'/api/v1/intakes/{ids["intake"]}/match-case')
    assert selected.status_code == 200, selected.text
    candidate = selected.json()["candidates"][0]
    assert candidate["listing_id"] == ids["listing"] and candidate["property_id"] is None
    assert candidate["observed_property_version"] is None and candidate["observed_listing_version"] is None
    assert _snapshot(canonical_graph) == before
