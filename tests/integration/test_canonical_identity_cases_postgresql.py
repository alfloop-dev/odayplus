"""Real native case entry on explicitly synthetic matching evidence and actors."""

import hashlib
import json
from decimal import Decimal
from uuid import UUID, uuid4

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
                  "identity.match_candidates", "audit.audit_events", "workflow.outbox_events",
                  "identity.match_decisions", "workflow.jobs", "identity.properties",
                  "identity.source_identity_edges", "expansion.listings", "expansion.listing_revisions")
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
    receipt = repo.create_case(ids["tenant"], intake_id, **command)
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
        CanonicalMatchCaseRepository(graph).create_case(ids["tenant"], intake_id, **_command(canonical_graph))
    assert _snapshot(canonical_graph) == before


def test_case_property_catalog_scopes_before_paging_and_creates_only_through_original_command(canonical_graph, case_api):
    from dataclasses import replace

    from shared.auth import DataClassification, Scope

    _, ids, _, database = canonical_graph
    client, caller, _ = case_api
    intake_id = _parent(canonical_graph)
    region, outside = str(uuid4()), str(uuid4())
    allowed = [str(UUID(int=n)) for n in (100, 200)]
    with database.connect() as connection:
        connection.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, intake_id))
        connection.execute("UPDATE identity.properties SET region_id=%s WHERE tenant_id=%s", (outside, ids["tenant"]))
        for n in (1, 2, 3, 100, 200):
            identity = str(UUID(int=n))
            connection.execute("INSERT INTO identity.properties(property_id,tenant_id,region_id,normalized_address,address_fingerprint) "
                "VALUES(%s,%s,%s,%s,%s)", (identity, ids["tenant"], region if n >= 100 else outside,
                f"Observed synthetic Property {n}", hashlib.sha256(identity.encode()).hexdigest()))
    caller["principal"] = replace(caller["principal"], scope=Scope(tenant_id=ids["tenant"],
        region_ids=frozenset({region}), clearance=DataClassification.CONFIDENTIAL))
    path = f"/api/v1/intakes/{intake_id}/match-case"
    before = _snapshot(canonical_graph)
    first = client.get(path + "/property-options", params={"page_size": 1})
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    assert first.json()["intake_id"] == intake_id and first.json()["intake_version"] == 17
    assert [row["property_id"] for row in first.json()["items"]] == allowed[:1]
    second = client.get(path + "/property-options", params={"page_size": 1, "cursor": first.json()["next_cursor"]})
    assert second.status_code == 200, second.text
    assert [row["property_id"] for row in second.json()["items"]] == allowed[1:]
    assert second.json()["next_cursor"] is None
    assert "confidence" not in first.text and "Property 1\"" not in first.text
    assert _snapshot(canonical_graph) == before
    items = first.json()["items"] + second.json()["items"]
    body = {"outcome": "POSSIBLE_MATCH", "confidence": "0.75", "risk_acknowledged": True,
        "reason": "Explicit human review context from observed scoped Properties",
        "supporting_signals": [{"kind": "HUMAN_REVIEW_CONTEXT", "description": "Explicit synthetic human observation"}],
        "contradictory_signals": [], "candidates": [{"property_id": row["property_id"], "property_version": row["version"],
            "confidence": "0.75", "evidence": {"context_origin": "HUMAN_REVIEW_CONTEXT"}} for row in items]}
    created = client.post(path, json=body, headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-catalog-context-001"})
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "PENDING_REVIEW" and created.json()["intake_version"] == 18
    actual = client.get(path)
    assert actual.status_code == 200 and actual.json()["match_case_id"] == created.json()["match_case_id"]
    assert {row["property_id"] for row in actual.json()["candidates"]} == set(allowed)


@pytest.mark.parametrize("axis,column", [("brand_ids", "brand_id"), ("assigned_area_ids", "assigned_area_id"), ("heat_zone_ids", "heat_zone_id")])
def test_case_property_catalog_never_borrows_parent_axes(canonical_graph, case_api, axis, column):
    from dataclasses import replace

    from shared.auth import Scope

    _, ids, _, database = canonical_graph
    intake_id, value = _parent(canonical_graph), str(uuid4())
    with database.connect() as connection:
        connection.execute(f"UPDATE intake.intakes SET {column}=%s WHERE intake_id=%s", (value, intake_id))
    client, caller, _ = case_api
    caller["principal"] = replace(caller["principal"], scope=Scope(tenant_id=ids["tenant"], **{axis: frozenset({value})}))
    response = client.get(f"/api/v1/intakes/{intake_id}/match-case/property-options")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [] and response.json()["next_cursor"] is None


def test_case_property_catalog_uses_existing_address_mask_and_independent_object_auth(canonical_graph, case_api):
    from dataclasses import replace

    from shared.auth import DataClassification, Role

    client, caller, _ = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case/property-options"
    caller["principal"] = replace(caller["principal"], scope=replace(caller["principal"].scope, clearance=DataClassification.PUBLIC))
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["items"] and all(row["address_masked"] and row["normalized_address"] is None for row in response.json()["items"])
    caller["principal"] = replace(caller["principal"], roles=frozenset({Role.EXPANSION_USER}))
    denied = client.get(path)
    assert denied.status_code == 403 and "items" not in denied.json()


def test_existing_case_is_selected_without_overwriting_its_evidence(canonical_graph):
    graph, ids, _, _ = canonical_graph
    intake_id = _parent(canonical_graph, state="NEEDS_REVIEW")
    repo = CanonicalMatchCaseRepository(graph)
    created = repo.create_case(ids["tenant"], intake_id, **_command(canonical_graph))
    assert created["intake_version"] == 17
    before = _snapshot(canonical_graph)
    with pytest.raises(IdentityConflictError, match="MATCH_CASE_ALREADY_EXISTS"):
        repo.create_case(ids["tenant"], intake_id, **_command(canonical_graph))
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
        CanonicalMatchCaseRepository(graph).create_case(ids["tenant"], intake_id, **_command(canonical_graph))
    assert calls == 3 and _snapshot(canonical_graph) == before


@pytest.fixture
def case_api(canonical_graph, monkeypatch):
    from types import MethodType, SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.api.app.routes import canonical_identity_decisions, canonical_match_cases
    from apps.api.app.routes.listings import create_assisted_intake_router
    from shared.audit import InMemoryAuditLog
    from shared.auth import Principal, Role, Scope
    from shared.infrastructure.persistence import PostgresDocumentStore
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.factory import PersistenceBundle

    graph, ids, _, _ = canonical_graph
    store = DurableAssistedIntakeStore(PostgresDocumentStore(graph.engine))
    store.configure_canonical_audit(graph.audit_sink)
    # Match the real bundle's explicit unavailable policy for these historical
    # graph rows. Detail reads must not invent a submission-policy grant.
    bundle = SimpleNamespace(engine=graph.engine, job_queue=graph.queue,
        assisted_intake_store=store, assisted_intake_workflow_policy=None)
    for name in ("canonical_identity_graph", "canonical_intakes", "canonical_match_cases", "canonical_identity_decisions"):
        setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
    # Explicit synthetic authentication result. Production uses the existing
    # password/JWT boundary, rather than accepting a persona as a grant.
    caller = {"principal": Principal(ids["actor"], frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=ids["tenant"]))}
    monkeypatch.setattr(canonical_match_cases, "principal_from_headers", lambda _: caller["principal"])
    monkeypatch.setattr(canonical_identity_decisions, "principal_from_headers", lambda _: caller["principal"])
    app = FastAPI()
    app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
    app.include_router(create_assisted_intake_router(store), prefix="/api/v1")
    with TestClient(app) as client:
        yield client, caller, store


def _manual_context_command(case, receipt):
    body = _http_command(case)
    body.update(confidence="0.3750", reason="Explicit additional human association, preserving original matcher evidence",
        append_to_case={"match_case_id": receipt["match_case_id"], "version": receipt["version"]},
        candidates=[{"property_id": case[1]["split_target"], "property_version": 3, "confidence": "0.3750",
            "listing_id": None, "listing_version": None, "evidence": {"observation": "Explicit additional actual root"}}])
    return body


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
    explicit_default = client.post(path, headers=headers, json={**body, "outcome": "POSSIBLE_MATCH"})
    assert explicit_default.status_code == 201 and explicit_default.json() == created.json()
    # The pre-extension request shape remains the durable replay identity.
    old_digest = hashlib.sha256(json.dumps({"body": body, "if_match": headers["If-Match"]},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    store.refresh(ids["tenant"])
    assert next(iter(store.replays.values()))[0] == old_digest
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


@pytest.mark.parametrize("revocation", ["role", "tenant", "parent_scope", "candidate_scope", "anonymous", "allowed_region"])
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
        if revocation in {"candidate_scope", "allowed_region"}:
            with db.connect() as conn:
                conn.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, intake_id))
                if revocation == "allowed_region":
                    conn.execute("UPDATE identity.properties SET region_id=%s WHERE tenant_id=%s", (region, ids["tenant"]))
    before = _snapshot(canonical_graph)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code in ({201} if revocation == "allowed_region" else {401, 403, 404}), response.text
    assert client.get(path).status_code in ({200} if revocation == "allowed_region" else {401, 403, 404})
    assert _snapshot(canonical_graph) == before


@pytest.mark.parametrize("target,method", [("intake", "get"), ("intake", "post"),
                                           ("property", "get"), ("property", "post")])
def test_case_access_denial_audit_identifies_the_actual_native_target(canonical_graph, case_api, target, method):
    from dataclasses import replace

    from shared.auth import Role, Scope

    _, ids, _, db = canonical_graph
    client, caller, _ = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    body = _http_command(canonical_graph)
    # This context intentionally contains Properties only. A Listing has its
    # own authorization boundary and cannot lend its scope to a Property.
    for candidate in body["candidates"]:
        candidate["listing_id"] = candidate["listing_version"] = None
    headers = {"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-case-access-audit-001"}
    assert client.post(path, headers=headers, json=body).status_code == 201
    if target == "intake":
        caller["principal"] = replace(caller["principal"], roles=frozenset({Role.OPERATIONS_MANAGER}))
        expected_resource, expected_reason = f"intake/{intake_id}", "ROLE_DENIED"
    else:
        region = str(uuid4())
        with db.connect() as connection:
            connection.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, intake_id))
            connection.execute("UPDATE identity.properties SET region_id=%s WHERE tenant_id=%s", (region, ids["tenant"]))
            connection.execute("UPDATE identity.properties SET region_id=NULL WHERE property_id=%s", (ids["target"],))
        caller["principal"] = replace(caller["principal"],
            scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({region})))
        # Properties use the existing listing permission matrix; the concrete
        # target ID must still survive its resource projection and denial.
        expected_resource, expected_reason = f"listing/{ids['target']}", "SCOPE_DENIED"
    before = _snapshot(canonical_graph)
    correlation = str(uuid4())
    response = getattr(client, method)(path, headers={**headers, "x-correlation-id": correlation},
        **({"json": body} if method == "post" else {}))
    assert response.status_code == 403 and response.json()["detail"] == expected_reason
    events = client.app.state.audit_log.list_events(correlation_id=correlation)
    assert events[-1].resource == expected_resource
    assert events[-1].metadata["reason"] == expected_reason
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


@pytest.mark.parametrize("outcome", ["NEW", "REVISION"])
def test_human_proposal_api_preserves_intake_and_waits_for_confirmation(canonical_graph, case_api, outcome):
    graph, ids, _, db = canonical_graph
    client, _, store = case_api
    intake_id = _parent(canonical_graph)
    with db.connect() as conn:
        conn.execute("UPDATE intake.intakes SET source_id='synthetic-feed' WHERE intake_id=%s", (intake_id,))
    body = _http_command(canonical_graph)
    listing_candidate = next(c for c in body["candidates"] if c["listing_id"] is not None)
    body.update(outcome=outcome, candidates=[] if outcome == "NEW" else [listing_candidate])
    path = f"/api/v1/intakes/{intake_id}/match-case"
    headers = {"If-Match": 'W/"17"', "Idempotency-Key": f"synthetic-{outcome}-proposal-001"}
    before = _snapshot(canonical_graph)

    created = client.post(path, headers=headers, json=body)

    assert created.status_code == 201, created.text
    receipt = created.json()
    assert receipt["status"] == "PROPOSED" and receipt["version"] == 1
    assert receipt["intake_version"] == 17 and created.headers["etag"] == 'W/"1"'
    selected = client.get(path)
    assert selected.status_code == 200, selected.text
    assert selected.json()["outcome"] == outcome and selected.json()["status"] == "PROPOSED"
    assert len(selected.json()["candidates"]) == (0 if outcome == "NEW" else 1)
    after = _snapshot(canonical_graph)
    for table in ("intake.intakes", "intake.intake_stage_transitions", "identity.match_decisions",
                  "workflow.jobs", "workflow.outbox_events", "identity.properties",
                  "identity.source_identity_edges", "expansion.listings", "expansion.listing_revisions"):
        assert after[table] == before[table]
    assert len(after["audit.audit_events"]) == len(before["audit.audit_events"]) + 1
    assert graph.audit_sink.writer.get_event(ids["tenant"], receipt["audit_event_id"])["action"] == "match.case_created.v1"
    store.refresh(ids["tenant"])
    assert len(store.replays) == 1
    assert client.post(path, headers=headers, json=body).json() == receipt
    changed = {**body, "outcome": "REVISION" if outcome == "NEW" else "NEW",
               "candidates": [listing_candidate] if outcome == "NEW" else []}
    assert client.post(path, headers=headers, json=changed).status_code == 409
    assert _snapshot(canonical_graph) == after


@pytest.mark.parametrize("conflict", ["NEW_REVIEW", "REVISION_MISSING_SOURCE"])
def test_proposal_cannot_skip_existing_review_or_cross_source_revision(canonical_graph, case_api, conflict):
    client, _, _ = case_api
    intake_id = _parent(canonical_graph, state="NEEDS_REVIEW" if conflict == "NEW_REVIEW" else "MATCHING")
    body = _http_command(canonical_graph)
    body.update(outcome="NEW" if conflict == "NEW_REVIEW" else "REVISION",
                candidates=[] if conflict == "NEW_REVIEW" else [next(c for c in body["candidates"] if c["listing_id"])])
    before = _snapshot(canonical_graph)
    response = client.post(f"/api/v1/intakes/{intake_id}/match-case", json=body,
        headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-proposal-conflict-001"})
    assert response.status_code == 409, response.text
    assert _snapshot(canonical_graph) == before


@pytest.mark.parametrize("outcome,candidate_shape", [
    ("NEW", "all"), ("REVISION", "empty"), ("REVISION", "property_only"), ("POSSIBLE_MATCH", "empty"),
    ("REVISION", "missing_listing_version"), ("POSSIBLE_MATCH", "version_without_listing"),
])
def test_proposal_api_rejects_incompatible_candidate_shape_without_writes(
    canonical_graph, case_api, outcome, candidate_shape,
):
    client, _, _ = case_api
    intake_id = _parent(canonical_graph)
    body = _http_command(canonical_graph)
    if candidate_shape == "empty":
        candidates = []
    elif candidate_shape == "all":
        candidates = body["candidates"]
    else:
        listing_required = candidate_shape == "missing_listing_version"
        candidates = [next(c for c in body["candidates"] if (c["listing_id"] is not None) == listing_required)]
        if candidate_shape == "missing_listing_version":
            candidates[0]["listing_version"] = None
        elif candidate_shape == "version_without_listing":
            candidates[0]["listing_version"] = 13
    body.update(outcome=outcome, candidates=candidates)
    before = _snapshot(canonical_graph)
    response = client.post(f"/api/v1/intakes/{intake_id}/match-case", json=body,
        headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-proposal-shape-001"})
    assert response.status_code == 422, response.text
    assert _snapshot(canonical_graph) == before


def test_listing_planning_cannot_treat_matching_label_as_parser_evidence(canonical_graph, case_api):
    client, _, _ = case_api
    intake_id = _parent(canonical_graph)
    body = {**_http_command(canonical_graph), "outcome": "NEW", "candidates": []}
    created = client.post(f"/api/v1/intakes/{intake_id}/match-case", json=body,
        headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-new-without-parser-001"})
    assert created.status_code == 201, created.text
    before = _snapshot(canonical_graph)
    response = client.get(f"/api/v1/match-cases/{created.json()['match_case_id']}/identity-context")
    assert response.status_code == 409, response.text
    assert "CURRENT_PARSER_RESULT_REQUIRED" in response.text
    assert _snapshot(canonical_graph) == before


def test_same_case_manual_context_preserves_original_evidence_and_replays_one_audited_commit(canonical_graph, case_api):
    from dataclasses import replace

    _, ids, _, _ = canonical_graph
    client, caller, _ = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    created = client.post(path, headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-original-context-001"},
        json=_http_command(canonical_graph))
    assert created.status_code == 201, created.text
    receipt = created.json()
    repo = CanonicalMatchCaseRepository(canonical_graph[0])
    original = repo.get_for_intake(ids["tenant"], intake_id)
    caller["principal"] = replace(caller["principal"], subject_id=ids["reviewer"])
    body = _manual_context_command(canonical_graph, receipt)
    headers = {"If-Match": 'W/"18"', "Idempotency-Key": "synthetic-append-context-001"}
    added = client.post(path, headers=headers, json=body)
    assert added.status_code == 201, added.text
    result = added.json()
    assert result["match_case_id"] == receipt["match_case_id"] and result["version"] == 3
    assert result["intake_version"] == 18 and len(result["candidate_ids"]) == 1
    current = repo.get_for_intake(ids["tenant"], intake_id)
    for key, value in original["match_case"].items():
        if key not in {"version", "updated_at"}:
            assert current["match_case"][key] == value, key
    assert current["intake"] == original["intake"]
    assert current["identity_decision_ids"] == original["identity_decision_ids"] == []
    assert current["candidates"][:2] == original["candidates"]
    candidate = current["candidates"][2]
    assert candidate["match_candidate_id"] == result["candidate_ids"][0] and candidate["rank"] == 3
    assert candidate["confidence"] == Decimal("0.3750") and candidate["property_id"] == ids["split_target"]
    assert candidate["evidence"]["manual_context"] == {"case_version": 3, "added_by": ids["reviewer"]}
    audit = canonical_graph[0].audit_sink.writer.get_event(ids["tenant"], result["audit_event_id"])
    assert audit["action"] == "match.manual_context_added.v1" and audit["actor_subject_id"] == ids["reviewer"]
    assert audit["before_value"]["candidate_ids"] == receipt["candidate_ids"]
    assert audit["after_value"]["manual_context"]["added_candidate_ids"] == result["candidate_ids"]
    selected = client.get(path)
    assert selected.status_code == 200
    assert [row["manual_context_version"] for row in selected.json()["candidates"]] == [None, None, 3]
    before_replay = _snapshot(canonical_graph)
    replay = client.post(path, headers=headers, json=body)
    assert replay.status_code == 201 and replay.json() == result
    assert replay.headers["Idempotency-Replayed"] == "true" and _snapshot(canonical_graph) == before_replay
    changed = client.post(path, headers=headers, json={**body, "reason": "Different intent under the same key"})
    assert changed.status_code == 409 and _snapshot(canonical_graph) == before_replay


@pytest.mark.parametrize("change", ["intake_version", "case_version", "case_id", "candidate_version", "duplicate",
    "intake_state", "case_state", "hold", "role"])
def test_manual_context_denial_keeps_original_case_candidates_and_receipts(canonical_graph, case_api, change):
    from dataclasses import replace

    from shared.auth import Role

    _, ids, _, database = canonical_graph
    client, caller, _ = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    original_body = _http_command(canonical_graph)
    created = client.post(path, headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-original-context-002"}, json=original_body)
    assert created.status_code == 201, created.text
    body = _manual_context_command(canonical_graph, created.json())
    headers = {"If-Match": 'W/"18"', "Idempotency-Key": "synthetic-append-context-002"}
    if change == "intake_version":
        headers["If-Match"] = 'W/"17"'
    elif change == "case_version":
        body["append_to_case"]["version"] = 1
    elif change == "case_id":
        body["append_to_case"]["match_case_id"] = str(uuid4())
    elif change == "candidate_version":
        body["candidates"][0]["property_version"] = 2
    elif change == "duplicate":
        body["candidates"] = original_body["candidates"][:1]
    elif change == "role":
        caller["principal"] = replace(caller["principal"], roles=frozenset({Role.OPERATIONS_MANAGER}))
    else:
        with database.connect() as connection:
            if change == "case_state":
                connection.execute("UPDATE identity.match_cases SET status='APPROVED' WHERE match_case_id=%s", (created.json()["match_case_id"],))
            elif change == "hold":
                connection.execute("UPDATE intake.intakes SET legal_hold=true WHERE intake_id=%s", (intake_id,))
            else:
                connection.execute("UPDATE intake.intakes SET processing_state='READY' WHERE intake_id=%s", (intake_id,))
    before = _snapshot(canonical_graph)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code in {403, 404, 409, 422}, response.text
    assert _snapshot(canonical_graph) == before


@pytest.mark.parametrize("outside", ["original", "additional"])
@pytest.mark.parametrize("replayed", [False, True])
def test_manual_context_authorizes_original_and_additional_targets_independently(canonical_graph, case_api, outside, replayed):
    from dataclasses import replace

    from shared.auth import Scope

    _, ids, _, database = canonical_graph
    client, caller, _ = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    created = client.post(path, headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-original-context-003"}, json=_http_command(canonical_graph))
    assert created.status_code == 201, created.text
    body = _manual_context_command(canonical_graph, created.json())
    headers = {"If-Match": 'W/"18"', "Idempotency-Key": "synthetic-append-context-003"}
    if replayed:
        added = client.post(path, headers=headers, json=body)
        assert added.status_code == 201, added.text
    region = str(uuid4())
    with database.connect() as connection:
        connection.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, intake_id))
        connection.execute("UPDATE identity.properties SET region_id=%s WHERE tenant_id=%s", (region, ids["tenant"]))
        connection.execute("UPDATE identity.properties SET region_id=NULL WHERE property_id=%s",
            (ids["source"] if outside == "original" else ids["split_target"],))
    caller["principal"] = replace(caller["principal"], scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({region})))
    before = _snapshot(canonical_graph)
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == 403, response.text
    assert _snapshot(canonical_graph) == before


def test_manual_context_cannot_modify_an_already_proposed_plan(canonical_graph, case_api):
    from tests.integration.test_canonical_identity_proposals_postgresql import _merge

    _, ids, _, _ = canonical_graph
    client, _, _ = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    created = client.post(path, headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-original-context-004"}, json=_http_command(canonical_graph))
    assert created.status_code == 201, created.text
    case_id = created.json()["match_case_id"]
    proposal = client.post(f"/api/v1/match-cases/{case_id}/identity-decisions",
        headers={"If-Match": 'W/"2"', "Idempotency-Key": "synthetic-existing-graph-plan-004"},
        json={"decision_type": "MERGE", "plan": _merge(canonical_graph), "reason": "Explicit independently reviewable original graph plan", "risk_acknowledged": True})
    assert proposal.status_code == 201, proposal.text
    current = client.get(path).json()
    body = _manual_context_command(canonical_graph, current)
    before = _snapshot(canonical_graph)
    response = client.post(path, headers={"If-Match": 'W/"18"', "Idempotency-Key": "synthetic-append-context-004"}, json=body)
    assert response.status_code == 409 and response.json()["detail"] == "MATCH_CASE_PLAN_ALREADY_PROPOSED", response.text
    assert _snapshot(canonical_graph) == before


def test_manual_context_receipt_failure_rolls_back_candidates_case_version_and_audit(canonical_graph, case_api, monkeypatch):
    from shared.infrastructure.persistence.assisted_listing_intake import (
        AssistedIntakePersistenceConflict,
    )

    _, ids, _, _ = canonical_graph
    client, _, store = case_api
    intake_id = _parent(canonical_graph)
    path = f"/api/v1/intakes/{intake_id}/match-case"
    created = client.post(path, headers={"If-Match": 'W/"17"', "Idempotency-Key": "synthetic-original-context-005"}, json=_http_command(canonical_graph))
    assert created.status_code == 201, created.text
    flush = store.flush
    def unavailable():
        flush()
        raise AssistedIntakePersistenceConflict("Synthetic append receipt failure")
    monkeypatch.setattr(store, "flush", unavailable)
    before = _snapshot(canonical_graph)
    response = client.post(path, headers={"If-Match": 'W/"18"', "Idempotency-Key": "synthetic-append-context-005"},
        json=_manual_context_command(canonical_graph, created.json()))
    assert response.status_code == 409 and _snapshot(canonical_graph) == before
    store.refresh(ids["tenant"])
    assert len(store.replays) == 1 and next(iter(store.replays.values()))[1] == created.json()
