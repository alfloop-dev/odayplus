"""Ordinary owned SQLite source controls, not export/Native PG acceptance.

The retained report, decisions, signature and hash-stamped audit come from
existing canonical producers. No model, source registry or approval policy is
seeded. Candidate scope uses the actual existing Network read owner.
"""
from dataclasses import replace
from uuid import uuid4

import pytest

from modules.opsboard.application.network_listings import NetworkListingService
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreCanonicalEvidenceResolver,
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from shared.audit import AuditEvent
from shared.audit.integrity import AuditIntegrityError
from shared.auth import DataClassification, Principal, Role, Scope
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import (
    DurableApprovalRepository,
    DurableListingRepository,
)
from shared.workflow.sitescore import DecisionAction
from tests.unit.test_network_canonical_review_projection import report
from tests.unit.test_network_review_canonical_action_metadata import (
    ACTOR,
    TENANT,
    components,
    install_real_related_producers,
)


def principal(**changes):
    return replace(Principal(subject_id="offline-evidence-reader",
        roles=frozenset({Role.AUDITOR, Role.SITE_REVIEWER}), scope=Scope(tenant_id=TENANT)), **changes)


def resolver(parts, approvals):
    _engine, scores, decisions, audit, _workflow, service = parts
    return SiteScoreCanonicalEvidenceResolver(decisions=decisions, reports=scores,
        approvals=approvals, audit_log=audit, review_owner=service)


def decide(parts, frozen, *, previous=None):
    _engine, _scores, _decisions, _audit, workflow, _service = parts
    decision = workflow.open_decision(frozen, created_by="offline-source-maker", tenant_id=TENANT,
        correlation_id="offline-create-cid", verified_creator=True, previous_decision_id=previous)
    workflow.submit_for_review(decision.decision_id, submitted_by="offline-source-maker",
        correlation_id="offline-submit-cid", submission_summary="Recorded engineering submission",
        verified_submitter=True)
    outcome = workflow.decide(decision.decision_id, action=DecisionAction.REJECT,
        actor=ACTOR, reason="Recorded engineering decision, not human UAT",
        correlation_id="offline-decide-cid", network_action="REJECT", actor_role_id="siteReviewer")
    return outcome


@pytest.fixture
def case(tmp_path):
    parts = components(tmp_path / "source-evidence.sqlite")
    engine, scores, decisions, _audit, workflow, service = parts
    try:
        approvals, _intake, _candidate = install_real_related_producers(engine, decisions, workflow, service)
        saved = scores.save_report(report())
        outcome = decide(parts, saved)
        yield parts, approvals, outcome
    finally:
        engine.close()


def selected(outcome):
    return SiteScoreEvidenceSelector(outcome.decision.decision_id, outcome.decision.version)


def rows(engine):
    return {name: [tuple(row) for row in engine.query(f"SELECT * FROM {name} ORDER BY 1,2")]
            for name in ("durable_documents", "durable_audit_events", "durable_jobs", "durable_sequences")}


def test_real_retained_decision_signature_and_exact_events_resolve_without_writes(case):
    parts, approvals, outcome = case
    before = rows(parts[0])
    result = resolver(parts, approvals).resolve_source(principal(), selected(outcome))
    assert result.decision_id == outcome.decision.decision_id
    assert result.approval_id == outcome.decision.approval_id
    assert result.approval_id != result.decision_id
    assert result.decision_actor == ACTOR
    assert result.recorded_network_action == "REJECT"
    assert result.events[-1].event_id == outcome.audit_event_id
    assert [event.action for event in result.events] == ["create", "submit", "reject"]
    assert all(event.event_hash and event.content_sha256 for event in result.events)
    assert result.source_snapshot_ids == ("synthetic-snapshot-a",)
    assert result.recorded_model_reference == "synthetic-model-a"
    assert result.export_admitted is False
    assert "source_classification_and_field_authority" in result.missing_admission
    assert not hasattr(result, "reason") and not hasattr(result, "report")
    assert rows(parts[0]) == before
    with pytest.raises(SiteScoreEvidenceUnavailable, match="CANONICAL_EVIDENCE_INPUT_REQUIRED"):
        resolver(parts, approvals).require_export_admission(result)
    assert rows(parts[0]) == before


def test_reopen_uses_frozen_report_even_after_new_report_is_saved(case):
    parts, approvals, outcome = case
    first = resolver(parts, approvals).resolve_source(principal(), selected(outcome))
    parts[1].save_report(replace(report(), model_version="offline-newer-model"))
    second = resolver(parts, approvals).resolve_source(principal(), selected(outcome))
    assert second == first
    path = parts[0].path
    parts[0].close()
    reopened = components(path)
    try:
        engine, _scores, decisions, _audit, workflow, service = reopened
        store = TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT)
        persisted_approvals = DurableApprovalRepository(store)
        workflow._approval_store = persisted_approvals
        owner = NetworkListingService(listing_repository=DurableListingRepository(store),
            intake_repository=DurableAssistedIntakeRepository(store), seed_fixtures=False, tenant_id=TENANT)
        owner._review_decision_repository = decisions
        service._candidate_workflow_service = owner
        before = rows(engine)
        assert resolver(reopened, persisted_approvals).resolve_source(principal(), selected(outcome)) == first
        assert rows(engine) == before
    finally:
        reopened[0].close()


@pytest.mark.parametrize("change", ["tenant", "store", "clearance", "role", "unauthenticated"])
def test_actual_current_scope_and_permissions_are_not_inherited_from_history(case, change):
    parts, approvals, outcome = case
    actor = principal()
    if change == "tenant":
        actor = replace(actor, scope=replace(actor.scope, tenant_id="offline-foreign-tenant"))
    elif change == "store":
        actor = replace(actor, scope=replace(actor.scope, store_ids=frozenset({"offline-denied-store"})))
    elif change == "clearance":
        actor = replace(actor, scope=replace(actor.scope, clearance=DataClassification.PUBLIC))
    elif change == "role":
        actor = replace(actor, roles=frozenset({Role.SITE_REVIEWER}))
    else:
        actor = replace(actor, authenticated=False)
    before = rows(parts[0])
    with pytest.raises((SiteScoreEvidenceDenied, SiteScoreEvidenceUnavailable)):
        resolver(parts, approvals).resolve_source(actor, selected(outcome))
    assert rows(parts[0]) == before


def test_same_correlation_in_foreign_tenant_or_resource_is_never_copied(case):
    parts, approvals, outcome = case
    for tenant, resource in (("offline-foreign-tenant", f"sitescore/decision/{outcome.decision.decision_id}"),
                             (TENANT, "sitescore/decision/offline-unrelated-decision")):
        parts[3].record(AuditEvent(event_type="sitescore.decision.v1", actor="offline-other",
            action="reject", resource=resource, outcome="rejected", correlation_id="offline-decide-cid",
            metadata={"tenant_id": tenant, "secret": "must-not-enter-resolved-references"}))
    result = resolver(parts, approvals).resolve_source(principal(), selected(outcome))
    assert len(result.events) == 3
    assert all(event.actor != "offline-other" for event in result.events)
    assert parts[3].get_event(outcome.audit_event_id, tenant_id="offline-foreign-tenant") is None


@pytest.mark.parametrize("fault", ["report", "signature", "missing-event", "duplicate-event", "tamper"])
def test_missing_conflicting_or_tampered_canonical_facts_never_resolve(case, fault):
    parts, approvals, outcome = case
    engine, _scores, decisions, audit, _workflow, _service = parts
    if fault == "report":
        retained = decisions.get_report(outcome.decision.decision_id)
        changed = replace(retained, report_id="offline-wrong-report")
        with pytest.raises(ValueError, match="IMMUTABLE"):
            decisions.save_report(outcome.decision.decision_id, changed)
        # Deliberately malformed historical row: bypass only the fixture's raw
        # document port, preserving the public immutable-writer denial above.
        decisions._store.put(decisions._REPORTS, outcome.decision.decision_id, changed)
    elif fault == "signature":
        signature = approvals.get(outcome.decision.approval_id)
        approvals.save(replace(signature, approver_id="offline-wrong-actor"))
    elif fault == "missing-event":
        engine.execute("DELETE FROM durable_audit_events WHERE event_id = ?", (outcome.audit_event_id,))
    elif fault == "duplicate-event":
        event = audit.get_event(outcome.audit_event_id, tenant_id=TENANT)
        audit.record(replace(event, event_id=str(uuid4())))
    else:
        engine.execute("UPDATE durable_audit_events SET actor = ? WHERE event_id = ?",
                       ("offline-tampered", outcome.audit_event_id))
    before = rows(engine)
    with pytest.raises((SiteScoreEvidenceUnavailable, AuditIntegrityError)):
        resolver(parts, approvals).resolve_source(principal(), selected(outcome))
    assert rows(engine) == before


@pytest.mark.parametrize("sections", [("outcome",), ("sla",), ("decision_log", "decision_log"), ()])
def test_unsupported_sections_are_not_faked_as_complete(sections):
    with pytest.raises(ValueError):
        SiteScoreEvidenceSelector("sitescore-decision-offline", 3, sections)


def test_stale_decision_version_and_pending_signature_fail_closed(case):
    parts, approvals, outcome = case
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="VERSION_CHANGED"):
        resolver(parts, approvals).resolve_source(principal(), replace(selected(outcome), expected_decision_version=2))
    assert rows(parts[0]) == before
    signature = approvals.get(outcome.decision.approval_id)
    approvals.save(replace(signature, approval_status="pending", approver_id="", approved_at=None))
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="SIGNATURE_REQUIRED"):
        resolver(parts, approvals).resolve_source(principal(), selected(outcome))
    assert rows(parts[0]) == before


@pytest.mark.parametrize("modules,allowed", [
    (frozenset({"sitescore"}), False),
    (frozenset({"operator_console", "sitescore"}), True),
])
def test_operator_module_scope_is_required_without_inventing_an_audit_module_gate(case, modules, allowed):
    parts, approvals, outcome = case
    actor = principal(scope=Scope(tenant_id=TENANT, modules=modules))
    before = rows(parts[0])
    if allowed:
        result = resolver(parts, approvals).resolve_source(actor, selected(outcome))
        assert result.events[-1].event_id == outcome.audit_event_id
        assert result.export_admitted is False
    else:
        with pytest.raises(SiteScoreEvidenceDenied, match="SCOPE_DENIED"):
            resolver(parts, approvals).resolve_source(actor, selected(outcome))
    assert rows(parts[0]) == before


@pytest.mark.parametrize("fault", ["from-status", "transition-tenant"])
def test_illegal_retained_history_cannot_borrow_unchanged_valid_events(case, fault):
    from shared.workflow.sitescore import DecisionStatus

    parts, approvals, outcome = case
    decision = parts[2].get_decision(outcome.decision.decision_id)
    first = decision.history[0]
    broken = (replace(first, from_status=DecisionStatus.PENDING_REVIEW) if fault == "from-status"
              else replace(first, tenant_id="offline-foreign-transition"))
    # Deliberately corrupt the fixture's retained historical aggregate only.
    # Existing writers and the actual immutable audit remain untouched.
    parts[2]._store.put(parts[2]._DECISIONS, decision.decision_id,
                        replace(decision, history=(broken, *decision.history[1:])))
    before = rows(parts[0])
    with pytest.raises(SiteScoreEvidenceUnavailable, match="TRANSITION_HISTORY_REQUIRED"):
        resolver(parts, approvals).resolve_source(principal(), selected(outcome))
    assert rows(parts[0]) == before
