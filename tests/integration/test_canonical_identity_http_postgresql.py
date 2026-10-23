"""Native HTTP commands using explicitly synthetic actors, policy and evidence."""

import json
from dataclasses import replace
from types import MethodType, SimpleNamespace
from uuid import uuid4

import pytest

from modules.listing.application.identity_delivery import (
    IDENTITY_POLICY_ENV,
    IDENTITY_SIGNING_KEY_ENV,
)
from shared.auth import Principal, Role, Scope
from shared.jobs.queue import JobStatus
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph
from tests.integration.test_canonical_identity_admission_postgresql import _claim
from tests.integration.test_canonical_identity_cases_postgresql import _http_command, _parent
from tests.integration.test_canonical_identity_proposals_postgresql import _merge, _state

pytestmark = pytest.mark.requires_live_env


@pytest.mark.parametrize("service", [False, True])
def test_existing_match_event_publisher_persists_human_v1_and_service_v2_without_fake_actors(canonical_graph, service):
    """Publisher/storage contract only; synthetic rows grant no execution authority."""
    from shared.domain.events import CONSUMER_MODE, validate_event

    graph, ids, _, _ = canonical_graph
    with graph.transaction(ids["tenant"]):
        decision_id = ids["decision"]
        if service:
            decision_id = str(uuid4())
            graph.engine.execute("INSERT INTO identity.match_decisions (match_decision_id,tenant_id,match_case_id,"
                "decision_type,status,proposer_subject_id,proposer_service_principal,reason,before_graph,after_graph) "
                "VALUES (?,?,?,'DUPLICATE','APPROVED',NULL,'native-intake-identity',"
                "'SYNTHETIC_PUBLISHER_ONLY_NOT_AUTHORITY','{}'::jsonb,'{}'::jsonb)",
                (decision_id, ids["tenant"], ids["case"]))
        decision = graph._one("SELECT * FROM identity.match_decisions WHERE tenant_id=? AND match_decision_id=?",
            (ids["tenant"], decision_id))
        case, intake = graph._case(ids["tenant"], ids["case"])
        at = graph.engine.query_one("SELECT clock_timestamp() AS now")["now"]
        graph._match_event(ids["tenant"], decision, status=decision["status"], case_version=case["version"],
            correlation_id=intake["correlation_id"], at=at)
        stored = graph._one("SELECT payload FROM workflow.outbox_events WHERE tenant_id=? AND event_type='match.decided' "
            "AND payload->'payload'->>'match_decision_id'=?", (ids["tenant"], decision_id))["payload"]
        assert stored["event_version"] == (2 if service else 1)
        assert stored["schema_ref"] == ("#/payloads/MatchDecidedV2" if service else "#/payloads/MatchDecidedV1")
        # Original proposer belongs in payload; execution ownership is
        # recorded by the existing execution audit, not inferred here.
        assert stored["actor_ref"] is None
        assert stored["payload"]["proposer_subject_id"] == (None if service else decision["proposer_subject_id"])
        if service:
            assert stored["payload"]["proposer_service_principal"] == "native-intake-identity"
            assert stored["payload"]["reviewer_subject_id"] is None
        else:
            assert "proposer_service_principal" not in stored["payload"]
        assert validate_event(stored) == validate_event(stored, mode=CONSUMER_MODE) == []


@pytest.fixture
def identity_api(canonical_graph, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.api.app.routes import canonical_identity_decisions, canonical_match_cases
    from apps.api.app.routes.listings import create_assisted_intake_router
    from shared.audit import InMemoryAuditLog
    from shared.infrastructure.persistence import PostgresDocumentStore
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.factory import PersistenceBundle

    graph, ids, _, _ = canonical_graph
    monkeypatch.setenv(IDENTITY_SIGNING_KEY_ENV, "Explicit synthetic native HTTP signing material only")
    monkeypatch.setenv(IDENTITY_POLICY_ENV, json.dumps({"policy_id": "synthetic-native-http-identity",
        "version": 23, "max_attempts": 3, "deadline_seconds": 120, "backoff_seconds": [1, 2]}))
    store = DurableAssistedIntakeStore(PostgresDocumentStore(graph.engine))
    store.configure_canonical_audit(graph.audit_sink)
    bundle = SimpleNamespace(engine=graph.engine, job_queue=graph.queue, assisted_intake_store=store)
    for name in ("canonical_identity_graph", "canonical_match_cases", "canonical_identity_decisions"):
        setattr(bundle, name, MethodType(getattr(PersistenceBundle, name), bundle))
    # This is a synthetic authentication result, not a persona header grant.
    caller = {"principal": Principal(ids["actor"], frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=ids["tenant"]))}
    for module in (canonical_identity_decisions, canonical_match_cases):
        monkeypatch.setattr(module, "principal_from_headers", lambda _: caller["principal"])
    app = FastAPI()
    app.state.persistence_bundle, app.state.audit_log = bundle, InMemoryAuditLog()
    app.include_router(create_assisted_intake_router(store), prefix="/api/v1")
    with TestClient(app) as client:
        yield client, caller, store


def _headers(version, key):
    return {"If-Match": f'W/"{version}"', "Idempotency-Key": key}


def _proposal(case, api, *, plan=None):
    client, _, _ = api
    intake = _parent(case)
    created = client.post(f"/api/v1/intakes/{intake}/match-case", json=_http_command(case),
        headers=_headers(17, "synthetic-native-http-case-001"))
    assert created.status_code == 201, created.text
    if plan is None:
        observed = client.get(f'/api/v1/match-cases/{created.json()["match_case_id"]}/identity-context',
            params={"include_listing_input": "false"})
        assert observed.status_code == 200, observed.text
        view = observed.json()
        assert view.get("listing_input") is None and view["match_case_version"] == created.json()["version"]
        _, ids, _, _ = case
        plan = {"source_property_ids": [ids["source"]], "target_property_id": ids["target"],
            "expected_property_versions": {row["property_id"]: row["version"] for row in view["properties"]},
            "expected_listing_versions": {row["listing_id"]: row["version"] for row in view["listings"]},
            "expected_edge_versions": {row["edge_id"]: row["edge_version"] for row in view["source_identity_edges"]
                if row["property_id"] == ids["source"]}, "candidate_reassignment_plan": []}
    body = {"decision_type": "MERGE", "plan": plan, "risk_acknowledged": True,
            "reason": "Explicit synthetic proposal; review native source and target identity"}
    path = f'/api/v1/match-cases/{created.json()["match_case_id"]}/identity-decisions'
    audit_count = len(client.app.state.audit_log.list_events())
    response = client.post(path, json=body, headers=_headers(2, "synthetic-native-http-proposal-001"))
    assert response.status_code == 201, response.text
    # Isolate N's access events: the earlier J case-entry audit must not hide a
    # Listing id that this command incorrectly replaces with its property id.
    resources = {event.resource for event in client.app.state.audit_log.list_events()[audit_count:]}
    assert {"listing/" + key for key in body["plan"]["expected_listing_versions"]} <= resources
    return response.json(), intake, path, body


def _review_request(case, api, proposal):
    _, ids, _, _ = case
    client, caller, _ = api
    caller["principal"] = replace(caller["principal"], subject_id=ids["reviewer"])
    path = f'/api/v1/canonical-identity-decisions/{proposal["decision_id"]}/actions/review'
    body = {"decision": "APPROVE", "expected_case_version": proposal["match_case_version"],
            "reason": "Independent synthetic reviewer checked the recorded native plan", "risk_acknowledged": True}
    return client, path, body, _headers(proposal["version"], "synthetic-native-http-review-001")


def test_native_http_case_proposal_independent_review_and_real_worker_share_durable_receipts(canonical_graph, identity_api):
    from shared.infrastructure.persistence import PostgresDocumentStore
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    graph, ids, _, db = canonical_graph
    client, _, _ = identity_api
    proposal, _, path, body = _proposal(canonical_graph, identity_api)
    assert proposal["status"] == "PENDING_REVIEW" and proposal["job_id"] is None
    assert proposal["version"] == 2 and proposal["match_case_version"] == 3
    before = _state(canonical_graph)
    replay = client.post(path, json=body, headers=_headers(2, "synthetic-native-http-proposal-001"))
    assert replay.json() == proposal and replay.headers["idempotency-replayed"] == "true"
    assert _state(canonical_graph) == before
    assert client.post(path, json={**body, "reason": "A different proposal intent under the same recorded key"},
        headers=_headers(2, "synthetic-native-http-proposal-001")).status_code == 409
    assert client.post(path, json=body, headers=_headers(3, "synthetic-native-http-proposal-001")).status_code == 409
    assert _state(canonical_graph) == before
    client, review_path, review_body, headers = _review_request(canonical_graph, identity_api, proposal)
    selected = client.get(review_path.removesuffix("/actions/review"))
    assert selected.status_code == 200 and selected.headers["etag"] == 'W/"2"'
    assert selected.json()["plan"]["expected_property_versions"] == body["plan"]["expected_property_versions"]
    assert "before_graph" not in selected.json() and "attempt_token" not in selected.text
    assert client.post(review_path, json={**review_body, "risk_acknowledged": 1}, headers=headers).status_code == 422
    approved = client.post(review_path, json=review_body, headers=headers)
    assert approved.status_code == 200, approved.text
    receipt = approved.json()
    assert receipt["status"] == "APPROVED" and receipt["job_status"] == "QUEUED"
    assert receipt["job_id"] and receipt["review_audit_event_id"] != receipt["audit_event_id"]
    assert graph.resolve_listing(ids["tenant"], ids["listing"])["effective_property_id"] == ids["source"]
    before = _state(canonical_graph)
    replay = client.post(review_path, json=review_body, headers=headers)
    assert replay.json() == receipt and replay.headers["idempotency-replayed"] == "true"
    assert client.post(review_path, json={**review_body, "decision": "REJECT"}, headers=headers).status_code == 409
    assert _state(canonical_graph) == before
    reopened_engine = PostgresEngine(db.url(), bootstrap=False, validate_schema=False)
    try:
        reopened = DurableAssistedIntakeStore(PostgresDocumentStore(reopened_engine))
        reopened.refresh(ids["tenant"])
        assert receipt in [row[1] for row in reopened.replays.values()]
    finally:
        reopened_engine.close()
    delivery, registry, bundle = _claim(canonical_graph, receipt["job_id"])
    registry.handle(delivery, bundle)
    assert graph.queue.get(receipt["job_id"]).status == JobStatus.SUCCEEDED
    selected = client.get(review_path.removesuffix("/actions/review"))
    assert selected.status_code == 200, selected.text
    assert selected.json()["status"] == "EXECUTED" and selected.json()["job_status"] == "SUCCEEDED"
    assert graph.resolve_listing(ids["tenant"], ids["listing"])["effective_property_id"] == ids["target"]


def test_native_split_http_preserves_explicit_partitions_through_independent_review_and_worker(canonical_graph, identity_api):
    graph, ids, _, db = canonical_graph
    client, _, _ = identity_api
    listing, edge = str(uuid4()), str(uuid4())
    with db.connect() as conn:
        conn.execute("INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,"
            "lifecycle_state,version) VALUES (%s,%s,%s,'synthetic-feed','http-split-19','ACTIVE',19)",
            (listing, ids["tenant"], ids["source"]))
        conn.execute("INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,"
            "listing_id,property_id,match_strategy,confidence,effective_from,edge_version) "
            "VALUES (%s,%s,'synthetic-feed','http-split-19',%s,%s,'HUMAN',0.75,clock_timestamp()-interval '1 hour',9)",
            (edge, ids["tenant"], listing, ids["source"]))
    intake = _parent(canonical_graph)
    evidence = _http_command(canonical_graph)
    evidence["candidates"].append({"property_id": ids["split_target"], "property_version": 3,
        "listing_id": None, "listing_version": None, "confidence": "0.5",
        "evidence": {"observation": "Explicit synthetic second split target"}})
    created = client.post(f"/api/v1/intakes/{intake}/match-case", json=evidence,
        headers=_headers(17, "synthetic-native-http-split-case"))
    assert created.status_code == 201, created.text
    observed = client.get(f'/api/v1/match-cases/{created.json()["match_case_id"]}/identity-context',
        params={"include_listing_input": "false"})
    assert observed.status_code == 200 and observed.json().get("listing_input") is None, observed.text
    view = observed.json()
    plan = {"source_property_id": ids["source"], "partitions": [
        {"target_property_id": ids["target"], "source_identity_edge_ids": [ids["edge"]]},
        {"target_property_id": ids["split_target"], "source_identity_edge_ids": [edge]},
    ], "expected_property_versions": {row["property_id"]: row["version"] for row in view["properties"]},
        "expected_listing_versions": {row["listing_id"]: row["version"] for row in view["listings"]},
        "expected_edge_versions": {row["edge_id"]: row["edge_version"] for row in view["source_identity_edges"]
            if row["property_id"] == ids["source"]}, "candidate_reassignment_plan": []}
    proposed = client.post(f'/api/v1/match-cases/{created.json()["match_case_id"]}/identity-decisions',
        json={"decision_type": "SPLIT", "plan": plan, "risk_acknowledged": True,
              "reason": "Explicit synthetic split with two separately observed physical properties"},
        headers=_headers(2, "synthetic-native-http-split-proposal"))
    assert proposed.status_code == 201, proposed.text
    client, path, body, headers = _review_request(canonical_graph, identity_api, proposed.json())
    selected = client.get(path.removesuffix("/actions/review"))
    assert selected.status_code == 200 and selected.json()["plan"]["partitions"] == plan["partitions"]
    approved = client.post(path, json=body, headers=headers)
    assert approved.status_code == 200, approved.text
    delivery, registry, bundle = _claim(canonical_graph, approved.json()["job_id"])
    registry.handle(delivery, bundle)
    assert graph.queue.get(delivery.job_id).status == JobStatus.SUCCEEDED
    assert graph.resolve_listing(ids["tenant"], ids["listing"])["effective_property_id"] == ids["target"]
    assert graph.resolve_listing(ids["tenant"], listing)["effective_property_id"] == ids["split_target"]


def test_native_http_rejection_preserves_graph_and_creates_no_placeholder_job(canonical_graph, identity_api, monkeypatch):
    graph, ids, _, db = canonical_graph
    proposal, _, _, _ = _proposal(canonical_graph, identity_api)
    client, path, body, headers = _review_request(canonical_graph, identity_api, proposal)
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (ids["source"],))
    rejection = {**body, "decision": "REJECT", "risk_acknowledged": False}
    caller = identity_api[1]
    reviewer = caller["principal"]
    caller["principal"] = replace(reviewer, roles=frozenset({Role.EXPANSION_USER}))
    before = _state(canonical_graph)
    assert client.post(path, json=rejection, headers=headers).status_code == 403
    assert _state(canonical_graph) == before
    caller["principal"] = reviewer
    # Rejection needs no execution reservation or signing configuration.
    monkeypatch.delenv(IDENTITY_POLICY_ENV)
    monkeypatch.delenv(IDENTITY_SIGNING_KEY_ENV)
    result = client.post(path, json=rejection, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "REJECTED" and result.json()["job_id"] is None
    with db.connect() as conn:
        assert conn.execute("SELECT count(*) FROM workflow.jobs WHERE job_type='identity-execution' AND aggregate_id=%s",
            (proposal["decision_id"],)).fetchone()[0] == 0
    assert graph.resolve_listing(ids["tenant"], ids["listing"])["effective_property_id"] == ids["source"]


@pytest.mark.parametrize("change", ["property", "listing", "intake", "case", "decision", "hold"])
def test_review_http_rechecks_native_versions_and_hold_without_partial_admission(canonical_graph, identity_api, change):
    _, ids, _, db = canonical_graph
    proposal, intake, _, _ = _proposal(canonical_graph, identity_api)
    client, path, body, headers = _review_request(canonical_graph, identity_api, proposal)
    with db.connect() as conn:
        if change == "property":
            conn.execute("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (ids["source"],))
        elif change == "listing":
            conn.execute("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s", (ids["listing"],))
        elif change in {"intake", "hold"}:
            update = "version=version+1" if change == "intake" else "legal_hold=true"
            conn.execute("UPDATE intake.intakes SET " + update + " WHERE intake_id=%s", (intake,))
        elif change == "case":
            conn.execute("UPDATE identity.match_cases SET version=version+1 WHERE match_case_id=%s", (proposal["match_case_id"],))
        else:
            conn.execute("UPDATE identity.match_decisions SET version=version+1 WHERE match_decision_id=%s", (proposal["decision_id"],))
    before = _state(canonical_graph)
    result = client.post(path, json=body, headers=headers)
    assert result.status_code == 409, result.text
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("revocation", ["role", "tenant", "parent_scope", "graph_scope", "self", "anonymous"])
def test_review_http_replay_reauthorizes_actual_actor_parent_and_graph(canonical_graph, identity_api, revocation):
    from shared.infrastructure.persistence.audit_log import DurableAuditLog

    _, ids, _, db = canonical_graph
    proposal, intake, _, _ = _proposal(canonical_graph, identity_api)
    client, path, body, headers = _review_request(canonical_graph, identity_api, proposal)
    assert client.post(path, json=body, headers=headers).status_code == 200
    caller = identity_api[1]
    principal = caller["principal"]
    if revocation == "role":
        caller["principal"] = replace(principal, roles=frozenset({Role.OPERATIONS_MANAGER}))
    elif revocation == "tenant":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=str(uuid4())))
    elif revocation == "self":
        caller["principal"] = replace(principal, subject_id=ids["actor"])
    elif revocation == "anonymous":
        caller["principal"] = replace(principal, authenticated=False)
    else:
        region = str(uuid4())
        caller["principal"] = replace(principal, scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({region})))
        if revocation == "graph_scope":
            with db.connect() as conn:
                conn.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, intake))
    # Use the real SQL-backed security log, not just an in-process spy. Denial
    # persistence must survive the business transaction that it rolls back.
    audit = DurableAuditLog(canonical_graph[0].engine)
    client.app.state.audit_log = audit
    before = _state(canonical_graph)
    response = client.post(path, json=body, headers=headers)
    assert response.status_code in {401, 403, 404}, response.text
    if response.status_code in {401, 403}:
        denied = [event for event in audit.list_events() if event.outcome == "deny"]
        assert len(denied) == 1 and denied[0].actor == caller["principal"].subject_id
        assert body["reason"] not in json.dumps(denied[0].to_dict())
    if revocation != "self":
        readback = client.get(path.removesuffix("/actions/review"))
        assert readback.status_code in {401, 403, 404}
        if readback.status_code in {401, 403}:
            assert audit.list_events()[-1].action == "view"
    if revocation == "anonymous":
        proposed_path = f'/api/v1/match-cases/{proposal["match_case_id"]}/identity-decisions'
        proposed_body = {"decision_type": "MERGE", "plan": _merge(canonical_graph), "risk_acknowledged": True,
                         "reason": "Explicit synthetic unauthenticated proposal must be refused"}
        denied = client.post(proposed_path, json=proposed_body,
            headers=_headers(2, "synthetic-native-http-unauthenticated-proposal"))
        assert denied.status_code == 401
        event = audit.list_events()[-1]
        assert event.action == "update" and event.resource == "match_case/" + proposal["match_case_id"]
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("phase", ["proposal", "review"])
def test_native_http_receipt_write_failure_rolls_back_every_native_and_transport_write(canonical_graph, identity_api, monkeypatch, phase):
    from shared.infrastructure.persistence.assisted_listing_intake import (
        AssistedIntakePersistenceConflict,
    )

    client, _, store = identity_api
    if phase == "review":
        proposal, _, _, _ = _proposal(canonical_graph, identity_api)
        client, path, body, headers = _review_request(canonical_graph, identity_api, proposal)
    else:
        intake = _parent(canonical_graph)
        created = client.post(f"/api/v1/intakes/{intake}/match-case", json=_http_command(canonical_graph),
            headers=_headers(17, "synthetic-native-http-case-rollback"))
        assert created.status_code == 201, created.text
        path = f'/api/v1/match-cases/{created.json()["match_case_id"]}/identity-decisions'
        body = {"decision_type": "MERGE", "plan": _merge(canonical_graph), "risk_acknowledged": True,
                "reason": "Explicit synthetic proposal intended to roll back on receipt failure"}
        headers = _headers(2, "synthetic-native-http-proposal-rollback")
    before = _state(canonical_graph)
    store.refresh(canonical_graph[1]["tenant"])
    receipts_before = dict(store.replays)
    flush = store.flush

    def fail_after_flush():
        flush()
        raise AssistedIntakePersistenceConflict("Explicit synthetic native HTTP receipt failure")

    monkeypatch.setattr(store, "flush", fail_after_flush)
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 409, response.text
    assert _state(canonical_graph) == before
    store.refresh(canonical_graph[1]["tenant"])
    assert store.replays == receipts_before


@pytest.mark.parametrize("missing", [IDENTITY_POLICY_ENV, IDENTITY_SIGNING_KEY_ENV])
def test_review_http_missing_admission_configuration_rolls_back_approval(canonical_graph, identity_api, monkeypatch, missing):
    proposal, _, _, _ = _proposal(canonical_graph, identity_api)
    client, path, body, headers = _review_request(canonical_graph, identity_api, proposal)
    monkeypatch.delenv(missing)
    before = _state(canonical_graph)
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 503, response.text
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("change", ["actor", "plan", "version", "risk", "numeric_risk", "duplicate_version_identity"])
def test_proposal_http_does_not_accept_caller_selected_actor_or_implicit_native_versions(canonical_graph, identity_api, change):
    client, _, _ = identity_api
    intake = _parent(canonical_graph)
    created = client.post(f"/api/v1/intakes/{intake}/match-case", json=_http_command(canonical_graph),
        headers=_headers(17, "synthetic-native-http-case-invalid"))
    assert created.status_code == 201, created.text
    body = {"decision_type": "MERGE", "plan": _merge(canonical_graph), "risk_acknowledged": True,
            "reason": "Explicit synthetic identity proposal with invalid caller input"}
    if change == "actor":
        body["reviewer_id"] = str(uuid4())
    elif change == "plan":
        del body["plan"]["expected_listing_versions"]
    elif change == "version":
        body["plan"]["expected_property_versions"][canonical_graph[1]["source"]] = True
    elif change == "numeric_risk":
        body["risk_acknowledged"] = 1
    elif change == "duplicate_version_identity":
        source = canonical_graph[1]["source"]
        body["plan"]["expected_property_versions"]["{" + source + "}"] = 999
    else:
        body["risk_acknowledged"] = False
    before = _state(canonical_graph)
    path = f'/api/v1/match-cases/{created.json()["match_case_id"]}/identity-decisions'
    response = client.post(path, json=body, headers=_headers(2, "synthetic-native-http-invalid-001"))
    assert response.status_code == 422, response.text
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("outcome", ["allow", "deny"])
def test_native_http_security_audit_failure_keeps_allow_closed_and_denial_enforced(canonical_graph, identity_api, monkeypatch, outcome):
    proposal, _, _, _ = _proposal(canonical_graph, identity_api)
    client, path, body, headers = _review_request(canonical_graph, identity_api, proposal)
    if outcome == "deny":
        caller = identity_api[1]
        caller["principal"] = replace(caller["principal"], roles=frozenset({Role.OPERATIONS_MANAGER}))
    audit = client.app.state.audit_log
    record = audit.record

    def unavailable(event):
        if event.outcome == outcome:
            raise RuntimeError("Explicit synthetic security audit failure")
        return record(event)

    monkeypatch.setattr(audit, "record", unavailable)
    before = _state(canonical_graph)
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == (503 if outcome == "allow" else 403), response.text
    assert _state(canonical_graph) == before
