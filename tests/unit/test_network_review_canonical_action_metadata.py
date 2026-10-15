"""Network actions survive canonical persistence and ordinary reads.

Identities here are explicit offline test identities, not production grants.
"""

from dataclasses import replace

import pytest

from modules.listing.domain.models import CandidateSiteDraft, ListingDedupKey
from modules.opsboard.application.network_listings import NetworkListingService
from modules.opsboard.application.network_reviews import NetworkReviewService
from shared.domain.models import AddressLocation, CandidateSite, Listing
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.document_store import SqliteDocumentStore
from shared.infrastructure.persistence.engine import SqliteEngine
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore
from shared.infrastructure.persistence.operator_network_listings import (
    DurableAssistedIntakeRepository,
)
from shared.infrastructure.persistence.repositories import (
    DurableApprovalRepository,
    DurableDecisionStore,
    DurableListingRepository,
    DurableSiteScoreRepository,
)
from shared.workflow.sitescore import DecisionAction, SiteScoreDecisionWorkflow
from tests.unit.test_network_canonical_review_projection import report

TENANT = "offline-review-metadata-tenant"
ACTOR = "offline-authorized-reviewer"


def components(path):
    engine = SqliteEngine(path)
    store = TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT)
    scores = DurableSiteScoreRepository(store)
    decisions = DurableDecisionStore(store)
    audit = DurableAuditLog(engine)
    workflow = SiteScoreDecisionWorkflow(store=decisions, audit_log=audit)
    service = NetworkReviewService(seed_fixtures=False, require_canonical=True,
                                  decision_repository=decisions, sitescore_repository=scores,
                                  decision_workflow=workflow)
    return engine, scores, decisions, audit, workflow, service


def decision_args(decision_id, action):
    return dict(review_id=decision_id, decision=action,
                reason="完整記錄核決原因以供正常查詢與稽核",
                conditions="租金調降並提供施工完成證明" if action == "WAIT" else "",
                required_data=["現勘照片", "租約正本"] if action == "RETURN" else [],
                override_ack=True, actor_role_id="siteReviewer", actor_name=ACTOR,
                idempotency_key="offline-key", correlation_id="offline-correlation")


@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT"])
def test_actual_action_metadata_survives_restart_and_frozen_report_change(tmp_path, action):
    path = tmp_path / "metadata.sqlite"
    engine, scores, decisions, audit, workflow, service = components(path)
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    result = service.decide_review(**decision_args(decision.decision_id, action))
    saved = decisions.get_decision(decision.decision_id)
    transition = saved.to_dict()["history"][-1]
    assert transition.get("network_action") == action
    assert transition["conditions"] == decision_args(decision.decision_id, action)["conditions"]
    assert transition["required_data"] == decision_args(decision.decision_id, action)["required_data"]
    assert transition["override_ack"] is True
    assert transition["actor_role_id"] == "siteReviewer"
    assert transition["tenant_id"] == TENANT
    assert transition["report_id"] == frozen.report_id
    assert transition["report_version"] == frozen.report_version
    assert transition["model_version"] == frozen.model_version
    assert isinstance(saved.to_dict()["version"], int) and saved.to_dict()["version"] >= 2
    events = audit.list_events(tenant_id=TENANT)
    event = next(item for item in events if item.event_id == result["records"]["auditId"])
    assert event.actor == ACTOR
    assert event.metadata["network_action"] == action
    assert event.metadata["conditions"] == transition["conditions"]
    assert event.metadata["required_data"] == transition["required_data"]
    assert event.metadata["version"] == saved.to_dict()["version"]
    scores.save_report(replace(report(), model_version="offline-newer-model"))
    engine.close()
    restarted, _, _, _, _, read_service = components(path)
    row = read_service.snapshot()["reviews"][0]
    assert row["reportId"] == frozen.report_id and row["reportVersion"] == frozen.report_version
    assert row["decision"]["decision"] == action
    assert row["decision"]["conditions"] == transition["conditions"]
    assert row["decision"]["requiredData"] == transition["required_data"]
    assert row["decision"]["decidedBy"] == ACTOR
    assert any(item["metadata"].get("network_action") == action and item["actor"] == ACTOR
               for item in read_service.snapshot()["auditEvents"])
    restarted.close()


def test_generic_native_approval_keeps_realization_compatibility(tmp_path):
    engine, scores, _, _, workflow, _ = components(tmp_path / "native.sqlite")
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    workflow.submit_for_review(decision.decision_id, submitted_by="offline-author")
    outcome = workflow.decide(decision.decision_id, action=DecisionAction.APPROVE,
                              actor=ACTOR, reason="原生一般核准相容性測試")
    assert len(outcome.realization_events) == 1
    assert outcome.realization_events[0].report_id == frozen.report_id
    engine.close()


@pytest.mark.parametrize("status", ["DRAFT", "APPROVED"])
def test_legacy_unknown_action_is_not_inferred_from_shared_status(tmp_path, status):
    from shared.workflow.sitescore import DecisionStatus
    engine, scores, decisions, _, workflow, service = components(tmp_path / "legacy.sqlite")
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    decisions.save_decision(replace(decision, status=DecisionStatus(status), network_action=None))
    before = engine.query("SELECT data FROM durable_documents ORDER BY collection, doc_id")
    row = service.snapshot()["reviews"][0]
    assert row["decision"] is None and row["status"] == "unknown" and row["decided"] is False
    assert row["canonicalDecisionStatus"] == status
    assert engine.query("SELECT data FROM durable_documents ORDER BY collection, doc_id") == before
    engine.close()


def test_wait_is_conditional_approval_and_never_runs_go_realization(tmp_path):
    engine, scores, _, _, workflow, service = components(tmp_path / "wait.sqlite")
    hooks = []
    workflow.register_hook(hooks.append)
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    result = service.decide_review(**decision_args(decision.decision_id, "WAIT"))
    assert result["decision"]["decision_status"] == "APPROVED"
    assert result["decision"]["realization_events"] == [] and hooks == []
    row = service.snapshot()["reviews"][0]
    assert row["status"] == "onhold" and row["decision"]["decision"] == "WAIT"
    assert row["canonicalDecisionStatus"] == "APPROVED"
    engine.close()


def install_real_related_producers(engine, decisions, workflow, service):
    store = TenantScopedDocumentStore(SqliteDocumentStore(engine), TENANT)
    approvals = DurableApprovalRepository(store)
    workflow._approval_store = approvals
    listings = DurableListingRepository(store)
    intake = DurableAssistedIntakeRepository(store)
    listing = Listing(listing_id="offline-listing", address_id="offline-address", tenant_id=TENANT)
    address = AddressLocation(address_id="offline-address", raw_address="offline recorded address", tenant_id=TENANT)
    listings.save_listing(listing, address, ListingDedupKey("offline-source", "offline-property", "offline address", 0, 0))
    candidate = CandidateSite(candidate_site_id=report().candidate_site_id, listing_id=listing.listing_id,
                              address_id=address.address_id, site_status="scored")
    listings.save_candidate(CandidateSiteDraft(listing=listing, address=address, candidate_site=candidate))
    candidate_service = NetworkListingService(listing_repository=listings, intake_repository=intake,
                                             seed_fixtures=False, tenant_id=TENANT)
    candidate_service._review_decision_repository = decisions
    service._candidate_workflow_service = candidate_service
    return approvals, intake, candidate_service


@pytest.mark.parametrize(("action", "operator_status", "approval_status"), [
    ("GO", "approved", "approved"), ("WAIT", "onhold", "approved"),
    ("RETURN", "needdata", "returned"), ("REJECT", "rejected", "rejected"),
])
def test_actual_review_candidate_and_own_approval_documents(action, operator_status, approval_status, tmp_path):
    engine, scores, decisions, _, workflow, service = components(tmp_path / "related.sqlite")
    approvals, intake, candidate_service = install_real_related_producers(engine, decisions, workflow, service)
    frozen = scores.save_report(report())
    decision = workflow.open_decision(frozen, created_by="offline-author", tenant_id=TENANT)
    result = service.decide_review(**decision_args(decision.decision_id, action))
    approval_id = result["records"].get("approvalId")
    assert approval_id and approval_id != decision.decision_id
    approval = approvals.get(approval_id)
    assert approval.approval_status == approval_status and approval.approver_id == ACTOR
    assert approval.decision_id == decision.decision_id
    metadata = intake.get_candidate_metadata(frozen.candidate_site_id)["reviewWorkflow"]
    assert metadata["networkAction"] == action and metadata["status"] == operator_status
    assert metadata["reportId"] == frozen.report_id and metadata["approvalId"] == approval_id
    assert metadata["requiredData"] == decision_args(decision.decision_id, action)["required_data"]
    stored_review = next(row for row in service.export_state()["reviews"] if row["id"] == decision.decision_id)
    assert stored_review["networkAction"] == action and stored_review["status"] == operator_status
    reloaded_candidate = candidate_service._candidate_to_dict(candidate_service._listing_repository.list_candidates()[0])
    assert reloaded_candidate["status"] == operator_status
    if action in {"WAIT", "RETURN"}:
        assert reloaded_candidate["canonicalSiteStatus"] == "scored"
    engine.close()
