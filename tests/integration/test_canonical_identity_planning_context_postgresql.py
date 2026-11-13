"""Bounded native planning reads on explicit synthetic PostgreSQL records."""

import json
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from shared.auth import Scope
from shared.infrastructure.persistence.identity_cases_sql import MatchCandidateEvidence
from tests.integration.test_assisted_listing_identity import _add_candidate
from tests.integration.test_assisted_listing_identity import canonical_graph as canonical_graph
from tests.integration.test_canonical_identity_http_postgresql import identity_api as identity_api
from tests.integration.test_canonical_identity_proposals_postgresql import _case, _state

pytestmark = pytest.mark.requires_live_env


def _path(case):
    return f'/api/v1/match-cases/{case["match_case_id"]}/identity-context'


def _read(case, api):
    response = api[0].get(_path(case))
    assert response.status_code == 200, response.text
    assert response.headers["etag"] == f'W/"{case["version"]}"'
    return response.json()


def _plan(context, source, target):
    return {"source_property_ids": [source], "target_property_id": target,
        "expected_property_versions": {row["property_id"]: row["version"] for row in context["properties"]},
        "expected_listing_versions": {row["listing_id"]: row["version"] for row in context["listings"]},
        "expected_edge_versions": {row["edge_id"]: row["edge_version"] for row in context["source_identity_edges"]
                                   if row["property_id"] == source},
        "candidate_reassignment_plan": [{"candidate_site_id": row["candidate_site_id"],
            "expected_version": row["version"], "disposition": "KEEP_HISTORICAL"}
            for row in context["candidate_sites"] if row["property_id"] == source]}


def _propose(case, api, plan):
    response = api[0].post(f'/api/v1/match-cases/{case["match_case_id"]}/identity-decisions',
        json={"decision_type": "MERGE", "plan": plan, "risk_acknowledged": True,
              "reason": "Explicit synthetic native planning read followed by a version-bound proposal"},
        headers={"If-Match": f'W/"{case["version"]}"', "Idempotency-Key": "synthetic-planning-context-proposal-001"})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "PENDING_REVIEW" and response.json()["job_id"] is None


def test_planning_read_returns_actual_complete_metadata_without_business_writes(canonical_graph, identity_api):
    _, ids, _, db = canonical_graph
    case = _case(canonical_graph)
    candidate = _add_candidate(canonical_graph)
    with db.connect() as conn:
        conn.execute("UPDATE intake.intakes SET original_url='https://private.invalid/secret' WHERE intake_id=%s",
            (case["intake_id"],))
        conn.execute("UPDATE identity.source_identity_edges SET source_entity_id='private-provider-key' WHERE edge_id=%s",
            (ids["edge"],))
    before = _state(canonical_graph)
    value = _read(case, identity_api)
    assert value["intake_id"] == case["intake_id"] and value["intake_version"] == 18
    assert {row["property_id"]: row["version"] for row in value["properties"]} == {ids["source"]: 7, ids["target"]: 11}
    assert value["listings"] == [{"listing_id": ids["listing"], "property_id": ids["source"], "version": 13, "lifecycle_state": "ACTIVE"}]
    assert value["source_identity_edges"] == [{"edge_id": ids["edge"], "property_id": ids["source"], "listing_id": ids["listing"], "edge_version": 5}]
    assert value["candidate_sites"] == [{"candidate_site_id": candidate, "property_id": ids["source"], "version": 41, "status": "NEW", "target_format_code": "SYNTHETIC"}]
    assert any(row["listing_version"] == 13 and row["effective_property_id"] == ids["source"] for row in value["anchors"])
    encoded = json.dumps(value)
    for secret in ("private.invalid", "private-provider-key", "normalized_address", "supporting_signals", "evidence", "original_url", "reason"):
        assert secret not in encoded
    assert _state(canonical_graph) == before


def test_current_versions_not_observed_candidate_versions_feed_a_real_native_proposal(canonical_graph, identity_api):
    _, ids, _, db = canonical_graph
    case = _case(canonical_graph)
    candidate = _add_candidate(canonical_graph)
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET version=8 WHERE property_id=%s", (ids["source"],))
        conn.execute("UPDATE expansion.listings SET version=14 WHERE listing_id=%s", (ids["listing"],))
        conn.execute("UPDATE identity.source_identity_edges SET edge_version=6 WHERE edge_id=%s", (ids["edge"],))
        conn.execute("UPDATE expansion.candidate_sites SET version=42 WHERE candidate_site_id=%s", (candidate,))
    value = _read(case, identity_api)
    plan = _plan(value, ids["source"], ids["target"])
    assert plan["expected_property_versions"][ids["source"]] == 8
    assert plan["expected_listing_versions"][ids["listing"]] == 14
    assert plan["expected_edge_versions"][ids["edge"]] == 6
    assert plan["candidate_reassignment_plan"][0]["expected_version"] == 42
    _propose(case, identity_api, plan)


def test_native_nullable_listing_parent_is_preserved_with_an_actual_active_edge(canonical_graph, identity_api):
    _, ids, _, db = canonical_graph
    case = _case(canonical_graph)
    with db.connect() as conn:
        conn.execute("UPDATE expansion.listings SET property_id=NULL WHERE listing_id=%s", (ids["listing"],))
    value = _read(case, identity_api)
    assert value["listings"][0]["property_id"] is None
    assert any(row["effective_property_id"] == ids["source"] and row["listing_version"] == 13 for row in value["anchors"])
    _propose(case, identity_api, _plan(value, ids["source"], ids["target"]))


def _historical_alias_case(canonical_graph):
    _, ids, _, db = canonical_graph
    candidates = (MatchCandidateEvidence(ids["source"], 7, Decimal("0.75"), {"synthetic": True}, ids["listing"], 13),
                  MatchCandidateEvidence(ids["split_target"], 3, Decimal("0.50"), {"synthetic": True}))
    case = _case(canonical_graph, candidates=candidates)
    # Explicit stored history fixture: a Listing with no source edge keeps its
    # immutable original parent, while the native resolver follows its redirect.
    with db.connect() as conn:
        conn.execute("DELETE FROM identity.source_identity_edges WHERE edge_id=%s", (ids["edge"],))
        conn.execute("UPDATE identity.properties SET status='MERGED',current_redirect_property_id=%s WHERE property_id=%s", (ids["target"], ids["source"]))
        conn.execute("INSERT INTO identity.property_redirects (tenant_id,from_property_id,to_property_id,decision_id,effective_from) VALUES (%s,%s,%s,%s,clock_timestamp()-interval '1 hour')",
            (ids["tenant"], ids["source"], ids["target"], ids["decision"]))
    return case


def test_historical_listing_alias_is_authorized_without_inventing_direct_closure_versions(canonical_graph, identity_api):
    _, ids, _, _ = canonical_graph
    case = _historical_alias_case(canonical_graph)
    value = _read(case, identity_api)
    assert {row["property_id"] for row in value["properties"]} == {ids["target"], ids["split_target"]}
    assert value["listings"] == [] and value["source_identity_edges"] == []
    assert any(row["listing_id"] == ids["listing"] and row["listing_version"] == 13
               and row["effective_property_id"] == ids["target"] for row in value["anchors"])
    _propose(case, identity_api, _plan(value, ids["target"], ids["split_target"]))


@pytest.mark.parametrize("change,status", [("anonymous", 401), ("roles", 403), ("tenant", 404),
    ("bad_actor", 403), ("bad_tenant", 403), ("storage", 503), ("audit", 503)])
def test_planning_read_rechecks_actual_actor_tenant_and_runtime(canonical_graph, identity_api, change, status):
    _, ids, _, _ = canonical_graph
    case = _case(canonical_graph)
    client, caller, _ = identity_api
    principal = caller["principal"]
    if change == "anonymous":
        caller["principal"] = replace(principal, authenticated=False)
    elif change == "roles":
        caller["principal"] = replace(principal, roles=frozenset())
    elif change == "tenant":
        caller["principal"] = replace(principal, scope=Scope(tenant_id=str(uuid4())))
    elif change == "bad_actor":
        caller["principal"] = replace(principal, subject_id="not-native")
    elif change == "bad_tenant":
        caller["principal"] = replace(principal, scope=Scope(tenant_id="not-native"))
    elif change == "storage":
        client.app.state.persistence_bundle = None
    elif change == "audit":
        client.app.state.audit_log = None
    before = _state(canonical_graph)
    response = client.get(_path(case))
    assert response.status_code == status, response.text
    assert "anchors" not in response.json() and _state(canonical_graph) == before


@pytest.mark.parametrize("state", ["case", "intake", "deleted", "hold"])
def test_planning_read_does_not_bypass_current_case_intake_or_hold_state(canonical_graph, identity_api, state):
    _, _, _, db = canonical_graph
    case = _case(canonical_graph)
    with db.connect() as conn:
        if state == "case":
            conn.execute("UPDATE identity.match_cases SET status='APPROVED' WHERE match_case_id=%s", (case["match_case_id"],))
        elif state == "intake":
            conn.execute("UPDATE intake.intakes SET processing_state='SUBMITTED' WHERE intake_id=%s", (case["intake_id"],))
        elif state == "deleted":
            conn.execute("UPDATE intake.intakes SET deleted_at=clock_timestamp() WHERE intake_id=%s", (case["intake_id"],))
        else:
            conn.execute("UPDATE intake.intakes SET legal_hold=true WHERE intake_id=%s", (case["intake_id"],))
    before = _state(canonical_graph)
    assert identity_api[0].get(_path(case)).status_code == 409
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("resource", ["parent", "property", "listing", "candidate"])
def test_native_context_requires_actual_property_region_for_every_resource(canonical_graph, identity_api, resource):
    _, ids, _, db = canonical_graph
    if resource == "candidate":
        candidates = tuple(MatchCandidateEvidence(ids[key], version, Decimal("0.5"), {"synthetic": True})
                           for key, version in (("target", 11), ("split_target", 3)))
        case = _case(canonical_graph, candidates=candidates)
        _add_candidate(canonical_graph, property_key="target")
    else:
        case = _case(canonical_graph)
    region = str(uuid4())
    with db.connect() as conn:
        if resource != "parent":
            conn.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, case["intake_id"]))
        if resource in {"listing", "candidate"}:
            conn.execute("UPDATE identity.properties SET region_id=%s WHERE tenant_id=%s", (region, ids["tenant"]))
    caller = identity_api[1]
    caller["principal"] = replace(caller["principal"], scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({region})))
    before = _state(canonical_graph)
    response = identity_api[0].get(_path(case))
    # Listing and CandidateSite have no region column of their own. Their
    # actual graph/bound Property supplies it; the Intake never supplies it.
    if resource in {"listing", "candidate"}:
        assert response.status_code == 200, response.text
        assert response.json()["properties"]
        assert response.json()["candidate_sites" if resource == "candidate" else "listings"]
    else:
        assert response.status_code == 403, response.text
        assert "properties" not in response.json()
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("collection", ["anchors", "listings", "edges", "candidates"])
def test_oversized_native_context_is_not_silently_truncated(canonical_graph, identity_api, collection):
    _, ids, _, db = canonical_graph
    case = _case(canonical_graph)
    with db.connect() as conn:
        if collection == "anchors":
            conn.execute("INSERT INTO identity.match_candidates (tenant_id,match_case_id,property_id,rank,confidence,evidence) SELECT %s,%s,%s,n,0.5,'{}'::jsonb FROM generate_series(3,103) n", (ids["tenant"], case["match_case_id"], ids["source"]))
        elif collection == "listings":
            conn.execute("INSERT INTO expansion.listings (tenant_id,property_id,source_id,lifecycle_state) SELECT %s,%s,'synthetic-feed','ACTIVE' FROM generate_series(1,1001)", (ids["tenant"], ids["source"]))
        elif collection == "edges":
            conn.execute("INSERT INTO identity.source_identity_edges (tenant_id,source_id,source_entity_id,property_id,match_strategy,confidence) SELECT %s,'synthetic-feed','overflow-'||n,%s,'HUMAN',0.5 FROM generate_series(1,1001) n", (ids["tenant"], ids["source"]))
        else:
            conn.execute("WITH promotions AS (INSERT INTO expansion.promotion_decisions "
                "(promotion_decision_id,tenant_id,intake_id,listing_id,property_id,target_format_code,status,proposer_subject_id,reason,gate_snapshot) "
                "SELECT gen_random_uuid(),%s,%s,%s,%s,'OVERFLOW-'||n,'COMPLETED',%s,'Synthetic cap fixture','{}'::jsonb "
                "FROM generate_series(1,1001) n RETURNING promotion_decision_id,target_format_code) "
                "INSERT INTO expansion.candidate_sites (tenant_id,property_id,source_listing_id,promotion_decision_id,target_format_code,status) "
                "SELECT %s,%s,%s,promotion_decision_id,target_format_code,'NEW' FROM promotions",
                (ids["tenant"], ids["intake"], ids["listing"], ids["source"], ids["actor"], ids["tenant"], ids["source"], ids["listing"]))
    before = _state(canonical_graph)
    response = identity_api[0].get(_path(case))
    assert response.status_code == 409 and response.json()["detail"] == "IDENTITY_PLANNING_CONTEXT_TOO_LARGE"
    assert _state(canonical_graph) == before


def test_historical_anchor_listing_outside_direct_closure_still_requires_its_own_scope(canonical_graph, identity_api):
    _, ids, _, db = canonical_graph
    case = _historical_alias_case(canonical_graph)
    region = str(uuid4())
    with db.connect() as conn:
        conn.execute("UPDATE intake.intakes SET region_id=%s WHERE intake_id=%s", (region, case["intake_id"]))
        conn.execute("UPDATE identity.properties SET region_id=%s WHERE tenant_id=%s", (region, ids["tenant"]))
        conn.execute("UPDATE identity.properties SET region_id=%s WHERE property_id=%s", (str(uuid4()), ids["source"]))
    caller = identity_api[1]
    caller["principal"] = replace(caller["principal"], scope=Scope(tenant_id=ids["tenant"], region_ids=frozenset({region})))
    before = _state(canonical_graph)
    audit = identity_api[0].app.state.audit_log
    audit_count = len(audit.list_events())
    response = identity_api[0].get(_path(case))
    assert response.status_code == 200, response.text
    assert response.json()["listings"] == []
    assert any(row["listing_id"] == ids["listing"] and row["effective_property_id"] == ids["target"]
               for row in response.json()["anchors"])
    # The Listing is outside the direct closure, but still has a distinct
    # authorization event. Its original parent cannot supply current region.
    assert f"listing/{ids['listing']}" in {event.resource for event in audit.list_events()[audit_count:]}
    assert _state(canonical_graph) == before
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET region_id=%s WHERE property_id=%s", (str(uuid4()), ids["target"]))
    before = _state(canonical_graph)
    response = identity_api[0].get(_path(case))
    assert response.status_code == 403, response.text
    assert "anchors" not in response.json() and _state(canonical_graph) == before
