"""Native proposal/review controls with explicit synthetic evidence and actors."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest

from modules.listing.domain.identity_graph import IdentityConflictError, IdentityNotFoundError
from shared.audit.errors import CanonicalAuditUnavailable
from shared.domain.events import EventContractValidator, validate_event
from shared.infrastructure.persistence.identity_cases_sql import (
    CanonicalMatchCaseRepository,
    MatchCandidateEvidence,
)
from shared.infrastructure.persistence.identity_decisions_sql import (
    CanonicalIdentityDecisionRepository,
)
from tests.integration.test_assisted_listing_identity import (
    _add_candidate,
)
from tests.integration.test_assisted_listing_identity import (
    canonical_graph as canonical_graph,
)
from tests.integration.test_canonical_identity_cases_postgresql import _command, _parent

pytestmark = pytest.mark.requires_live_env

TABLES = (
    "intake.intakes", "intake.intake_stage_transitions", "identity.match_cases", "identity.match_candidates",
    "identity.match_decisions", "identity.properties", "identity.source_identity_edges", "identity.property_redirects",
    "expansion.listings", "expansion.promotion_decisions", "expansion.candidate_sites", "audit.audit_events",
    "workflow.outbox_events", "workflow.jobs", "durable_jobs",
)
GRAPH_TABLES = {"identity.properties", "identity.source_identity_edges", "identity.property_redirects",
                "expansion.listings", "expansion.promotion_decisions", "expansion.candidate_sites", "workflow.jobs"}


def _state(case):
    graph, ids, _, _ = case
    with graph.transaction(ids["tenant"]):
        # This entire isolated PostgreSQL database is synthetic. Capture all
        # tenants as well as audits/deliveries to detect any partial mutation.
        return {table: sorted(graph.engine.query("SELECT * FROM " + table),
            key=lambda row: json.dumps(row, sort_keys=True)) for table in TABLES}


def _case(case, *, candidates=None):
    graph, ids, _, _ = case
    intake_id = _parent(case)
    command = _command(case)
    if candidates is not None:
        command["candidates"] = candidates
    return CanonicalMatchCaseRepository(graph).create_review_case(ids["tenant"], intake_id, **command)


def _merge(case):
    _, ids, _, _ = case
    return {"source_property_ids": [ids["source"]], "target_property_id": ids["target"],
        "expected_property_versions": {ids["source"]: 7, ids["target"]: 11},
        "expected_listing_versions": {ids["listing"]: 13}, "expected_edge_versions": {ids["edge"]: 5},
        "candidate_reassignment_plan": []}


def _propose(case, *, plan=None, context=None, kind="MERGE"):
    graph, ids, _, _ = case
    context = context or _case(case)
    repo = CanonicalIdentityDecisionRepository(graph)
    result = repo.propose(ids["tenant"], context["match_case_id"], kind=kind, plan=plan or _merge(case),
        expected_case_version=2, actor_id=ids["actor"], risk_acknowledged=True,
        reason="Explicit synthetic identity review; no production approval")
    return repo, result


def _review(case, repo, proposal, **overrides):
    _, ids, _, _ = case
    return repo.review(ids["tenant"], proposal["decision_id"], expected_decision_version=2,
        expected_case_version=3, reviewer_id=ids["reviewer"], approve=True,
        reason="Independent synthetic reviewer accepts the recorded graph plan", risk_acknowledged=True, **overrides)


def test_proposal_and_independent_approval_save_actual_context_without_graph_or_job_mutation(canonical_graph):
    graph, ids, _, _ = canonical_graph
    context = _case(canonical_graph)
    before = _state(canonical_graph)
    repo, proposal = _propose(canonical_graph, context=context)
    assert proposal["status"] == "PENDING_REVIEW" and proposal["version"] == 2
    assert proposal["match_case_version"] == 3 and proposal["job_id"] is None
    selected = repo.get(ids["tenant"], proposal["decision_id"])
    assert selected["match_case"]["match_case_id"] == context["match_case_id"]
    assert selected["decision"]["before_graph"]["intake_version"] == 18
    assert selected["decision"]["after_graph"]["proposal_audit_event_id"] == proposal["audit_event_id"]
    approved = _review(canonical_graph, repo, proposal)
    assert approved["status"] == "APPROVED" and approved["version"] == 3
    assert approved["match_case_version"] == 4 and approved["job_id"] is None
    current = repo.get(ids["tenant"], proposal["decision_id"])
    assert current["decision"]["reviewer_subject_id"] == ids["reviewer"]
    assert current["decision"]["after_graph"]["review"]["audit_event_id"] == approved["audit_event_id"]
    assert current["match_case"]["status"] == "APPROVED" and current["intake"]["processing_state"] == "NEEDS_REVIEW"
    after = _state(canonical_graph)
    assert {k: before[k] for k in GRAPH_TABLES} == {k: after[k] for k in GRAPH_TABLES}
    assert len(after["audit.audit_events"]) == len(before["audit.audit_events"]) + 3
    assert len(after["workflow.outbox_events"]) == len(before["workflow.outbox_events"]) + 2
    catalog = EventContractValidator().catalog
    for row in after["workflow.outbox_events"]:
        event = json.loads(row["payload"])
        assert validate_event(event) == []
        assert event["producer"] == catalog[event["event_type"]]["producer"]
    audit = graph.audit_sink.writer.get_event(ids["tenant"], approved["audit_event_id"])
    assert audit["resource_id"] == proposal["decision_id"] and audit["after_value"]["status"] == "APPROVED"
    assert graph.audit_sink.writer.verify_chain(ids["tenant"]).ok


@pytest.mark.parametrize("change,error", [
    ("property", "VERSION_CONFLICT"), ("listing", "LISTING_VERSION_CONFLICT"),
    ("case", "MATCH_CASE_VERSION_CONFLICT"), ("hold", "LEGAL_HOLD_CONFLICT"),
    ("intake_version", "INTAKE_VERSION_CONFLICT"),
    ("self", "SECOND_ACTOR_REQUIRED"), ("decision_version", "IDENTITY_DECISION_VERSION_CONFLICT"),
])
def test_review_rejects_changed_native_authority_and_self_review_atomically(canonical_graph, change, error):
    _, ids, _, db = canonical_graph
    repo, proposal = _propose(canonical_graph)
    with db.connect() as conn:
        if change == "property":
            conn.execute("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (ids["source"],))
        elif change == "listing":
            conn.execute("UPDATE expansion.listings SET version=version+1 WHERE listing_id=%s", (ids["listing"],))
        elif change == "case":
            conn.execute("UPDATE identity.match_cases SET version=version+1 WHERE match_case_id=%s", (proposal["match_case_id"],))
        elif change == "hold":
            conn.execute("UPDATE intake.intakes SET legal_hold=true WHERE intake_id="
                "(SELECT intake_id FROM identity.match_cases WHERE match_case_id=%s)", (proposal["match_case_id"],))
        elif change == "intake_version":
            conn.execute("UPDATE intake.intakes SET version=version+1 WHERE intake_id="
                "(SELECT intake_id FROM identity.match_cases WHERE match_case_id=%s)", (proposal["match_case_id"],))
    before = _state(canonical_graph)
    with pytest.raises(IdentityConflictError, match=error):
        repo.review(ids["tenant"], proposal["decision_id"],
            expected_decision_version=99 if change == "decision_version" else 2, expected_case_version=3,
            reviewer_id=ids["actor"] if change == "self" else ids["reviewer"], approve=True,
            reason="Explicit synthetic changed-authority negative control", risk_acknowledged=True)
    assert _state(canonical_graph) == before


def test_plan_cannot_use_an_unrelated_available_case_or_tenant(canonical_graph):
    graph, ids, _, _ = canonical_graph
    source = next(c for c in _command(canonical_graph)["candidates"] if c.property_id == ids["source"])
    context = _case(canonical_graph, candidates=(source,))
    before = _state(canonical_graph)
    repo = CanonicalIdentityDecisionRepository(graph)
    for tenant, error in [(ids["tenant"], IdentityConflictError), (str(uuid4()), IdentityNotFoundError)]:
        with pytest.raises(error):
            repo.propose(tenant, context["match_case_id"], kind="MERGE", plan=_merge(canonical_graph),
                expected_case_version=2, actor_id=ids["actor"], reason="Explicit synthetic unrelated case control", risk_acknowledged=True)
    assert _state(canonical_graph) == before


def test_independent_rejection_remains_possible_when_original_graph_versions_change(canonical_graph):
    _, ids, _, db = canonical_graph
    repo, proposal = _propose(canonical_graph)
    with db.connect() as conn:
        conn.execute("UPDATE identity.properties SET version=version+1 WHERE property_id=%s", (ids["source"],))
    before = _state(canonical_graph)
    result = repo.review(ids["tenant"], proposal["decision_id"], expected_decision_version=2,
        expected_case_version=3, reviewer_id=ids["reviewer"], approve=False,
        reason="Reject the stale synthetic proposal", risk_acknowledged=False)
    assert result["status"] == "REJECTED" and result["job_id"] is None
    after = _state(canonical_graph)
    assert {k: before[k] for k in GRAPH_TABLES} == {k: after[k] for k in GRAPH_TABLES}
    assert repo.get(ids["tenant"], result["decision_id"])["match_case"]["status"] == "REJECTED"


@pytest.mark.parametrize("phase", ["proposal", "approval"])
def test_audit_failure_rolls_back_entire_native_proposal_or_review(canonical_graph, monkeypatch, phase):
    graph, _, _, _ = canonical_graph
    context = _case(canonical_graph)
    repo, proposal = _propose(canonical_graph, context=context) if phase == "approval" else (None, None)
    before = _state(canonical_graph)
    persist = graph.audit_sink.persist

    def unavailable(audit, scope):
        if audit["action"] in {"identity.decision_proposed.v1", "identity.decision_approved.v1"}:
            raise CanonicalAuditUnavailable("Explicit synthetic final audit failure")
        return persist(audit, scope)

    monkeypatch.setattr(graph.audit_sink, "persist", unavailable)
    with pytest.raises(CanonicalAuditUnavailable):
        _review(canonical_graph, repo, proposal) if phase == "approval" else _propose(canonical_graph, context=context)
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("disposition", ["KEEP_HISTORICAL", "REQUIRE_REVIEW", "REASSIGN"])
def test_candidate_dispositions_are_preflighted_but_approval_never_rebinds_them(canonical_graph, disposition):
    _, ids, _, db = canonical_graph
    candidate = _add_candidate(canonical_graph)
    plan = _merge(canonical_graph)
    plan["candidate_reassignment_plan"] = [{"candidate_site_id": candidate, "expected_version": 41,
        "disposition": disposition, "target_property_id": ids["target"] if disposition == "REASSIGN" else None}]
    repo, proposal = _propose(canonical_graph, plan=plan)
    _review(canonical_graph, repo, proposal)
    with db.connect() as conn:
        row = conn.execute("SELECT property_id,version FROM expansion.candidate_sites WHERE candidate_site_id=%s", (candidate,)).fetchone()
        assert (str(row[0]), row[1]) == (ids["source"], 41)


def test_candidate_format_collision_is_rejected_before_a_proposal_is_saved(canonical_graph):
    _, ids, _, _ = canonical_graph
    candidate = _add_candidate(canonical_graph)
    _add_candidate(canonical_graph, property_key="target")
    context = _case(canonical_graph)
    before = _state(canonical_graph)
    plan = _merge(canonical_graph)
    plan["candidate_reassignment_plan"] = [{"candidate_site_id": candidate, "expected_version": 41,
        "disposition": "REASSIGN", "target_property_id": ids["target"]}]
    with pytest.raises(IdentityConflictError, match="CANDIDATE_PLAN_CONFLICT"):
        _propose(canonical_graph, plan=plan, context=context)
    assert _state(canonical_graph) == before


@pytest.mark.parametrize("duplicate_target", [False, True])
def test_split_requires_two_distinct_native_targets_and_complete_disjoint_partitions(canonical_graph, duplicate_target):
    _, ids, _, db = canonical_graph
    listing, edge = str(uuid4()), str(uuid4())
    with db.connect() as conn:
        conn.execute("INSERT INTO expansion.listings (listing_id,tenant_id,property_id,source_id,source_listing_id,"
            "lifecycle_state,version) VALUES (%s,%s,%s,'synthetic-feed','source-18','ACTIVE',19)",
            (listing, ids["tenant"], ids["source"]))
        conn.execute("INSERT INTO identity.source_identity_edges (edge_id,tenant_id,source_id,source_entity_id,"
            "listing_id,property_id,match_strategy,confidence,effective_from,edge_version) "
            "VALUES (%s,%s,'synthetic-feed','source-18',%s,%s,'HUMAN',0.75,clock_timestamp()-interval '1 hour',9)",
            (edge, ids["tenant"], listing, ids["source"]))
    candidates = (*_command(canonical_graph)["candidates"],
        MatchCandidateEvidence(ids["split_target"], 3, Decimal("0.50"), {"observation": "Explicit synthetic second split target"}))
    context = _case(canonical_graph, candidates=candidates)
    plan = {"source_property_id": ids["source"], "partitions": [
        {"target_property_id": ids["target"], "source_identity_edge_ids": [ids["edge"]]},
        {"target_property_id": ids["target"] if duplicate_target else ids["split_target"], "source_identity_edge_ids": [edge]},
    ], "expected_property_versions": {ids["source"]: 7, ids["target"]: 11, ids["split_target"]: 3},
        "expected_listing_versions": {ids["listing"]: 13, listing: 19},
        "expected_edge_versions": {ids["edge"]: 5, edge: 9}, "candidate_reassignment_plan": []}
    before = _state(canonical_graph)
    if duplicate_target:
        with pytest.raises(ValueError, match="distinct targets"):
            _propose(canonical_graph, plan=plan, context=context, kind="SPLIT")
        assert _state(canonical_graph) == before
    else:
        repo, proposal = _propose(canonical_graph, plan=plan, context=context, kind="SPLIT")
        assert _review(canonical_graph, repo, proposal)["status"] == "APPROVED"
        after = _state(canonical_graph)
        assert {k: before[k] for k in GRAPH_TABLES} == {k: after[k] for k in GRAPH_TABLES}
