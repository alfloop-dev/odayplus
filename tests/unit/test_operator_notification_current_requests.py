"""Real memory workflow transitions + scoped read projection; not live/human UAT."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from modules.opsboard.application.operator_live_repository import (
    OperatorLiveRepositoryError,
    OperatorSectionAvailability,
)
from modules.opsboard.application.operator_tasks import (
    admit_task_center_records,
    build_task_center_records,
)
from modules.opsboard.application.shell import InMemoryShellRepository, ShellNotFound, ShellService
from shared.auth import Action, Principal, Role, Scope, rbac_allows
from shared.workflow.sitescore import (
    DecisionAction,
    DecisionStatus,
    InMemoryDecisionStore,
    SiteScoreDecisionWorkflow,
)
from tests.unit.test_network_canonical_review_projection import report

TENANT = "s4-workflow-tenant"
CANDIDATE = "s4-workflow-candidate"


class Approvals:
    def __init__(self):
        self.rows = {}
    def save(self, approval):
        self.rows[approval.approval_id] = approval
    def get(self, identity):
        return self.rows.get(identity)


def owner_sources(decisions):
    return {"interventions": [], "forecast_alerts": [], "candidates": [], "sitescore_decisions": decisions,
        "listings": [], "ingestion_runs": [], "store_ops_issues": [],
        "sections": {key: OperatorSectionAvailability("available", f"s4-real-memory-{key}", 0)
            for key in ("interventions", "forecastAlerts", "candidates", "siteScoreDecisions", "listings", "ingestionRuns", "storeOpsIssues")}}


class ScopedState:
    def __init__(self, decisions):
        self.decisions = decisions
        self.can_approve = True
        self.owner_available = True
        self.visible = True
        self.signature_matches = True
    def resolve_role(self, **_):
        return {"id": "expansion-manager", "label": "Existing reviewer", "allowedWorkspaces": ["today", "network", "govern"]}
    def get_today(self, **_):
        return {"notifications": []}
    def get_task_center(self, *, subject_id, **_):
        if not self.owner_available:
            raise OperatorLiveRepositoryError("canonical owner unavailable")
        decisions = self.decisions.list_decisions()
        state = {"_meta": {"tenantId": TENANT}, "taskCenterRecords": build_task_center_records(owner_sources(decisions)),
            "decisions": [{"id": row.approval_id if self.signature_matches else "other-signature", "title": "Actual pending signature", "status": "pending", "meta": "SiteScore",
                "target": {"workspace": "network", "tab": "review", "entityId": row.candidate_site_id,
                    "candidateId": row.candidate_site_id, "reviewId": row.decision_id}}
                for row in decisions if row.status is DecisionStatus.PENDING_REVIEW]}
        principal = Principal(subject_id, frozenset({Role.SITE_REVIEWER}), Scope(tenant_id=TENANT))
        snapshot = {"listings": [{"id": "s4-listing", "tenantId": TENANT}],
            "candidates": [{"id": CANDIDATE, "listingId": "s4-listing"}]} if self.visible else {}
        admitted = admit_task_center_records(state, principal=principal, store_repository=None, brand_repository=None,
            listing_snapshot_reader=lambda: snapshot,
            can_read=lambda resource: rbac_allows(principal, resource, Action.VIEW),
            can_approve=lambda resource: self.can_approve and rbac_allows(principal, resource, Action.APPROVE))
        return {**admitted["taskCenterRecords"], "dataOrigin": {"kind": "authoritative", "sourceId": "real-memory-workflow"}}


def setup():
    decisions = InMemoryDecisionStore()
    workflow = SiteScoreDecisionWorkflow(store=decisions, approval_store=Approvals())
    opened = workflow.open_decision(report(CANDIDATE), created_by="actual-author", tenant_id=TENANT, verified_creator=True)
    current = workflow.submit_for_review(opened.decision_id, submitted_by="actual-author", verified_submitter=True)
    state = ScopedState(decisions)
    shell = ShellService(state, repository=InMemoryShellRepository())
    return workflow, current, state, shell


def test_true_submit_ack_return_resubmit_and_terminal_preserve_per_request_identity_and_exact_consumer():
    workflow, current, state, shell = setup()
    context = {"role_id": "expansion-manager", "subject_id": "actual-reviewer"}
    initial = shell.get_notifications(**context)
    first = initial["items"][0]
    assert first["category"] == "approval"
    assert first["sourceEntity"] == {"kind": "sitescore.decision", "id": current.decision_id}
    assert first["occurredAt"] == current.history[-1].at.isoformat()
    query = parse_qs(urlparse(first["sourceHref"]).query)
    assert query["taskId"] == [f"sitescore.decision:{current.decision_id}"]
    selected = shell.get_tasks(**context, view="approvals", task_id=query["taskId"][0], request_key=query["requestKey"][0])
    assert selected["selected"]["reportId"] == current.report_id
    assert selected["selected"]["sourceId"] == current.decision_id
    shell.acknowledge_notification(notification_id=first["notificationId"], **context, idempotency_key="s4-first-ack")
    assert shell.get_notifications(**context)["unacknowledged"] == 0
    assert shell.get_notifications(role_id="expansion-manager", subject_id="second-real-reviewer")["unacknowledged"] == 1
    workflow.decide(current.decision_id, action=DecisionAction.REQUEST_REVISION,
        actor="actual-reviewer", reason="real additional evidence request", network_action="RETURN", required_data=["actual lease"])
    assert shell.get_notifications(**context)["items"] == []
    resubmitted = workflow.submit_for_review(current.decision_id, submitted_by="actual-author", verified_submitter=True)
    second = shell.get_notifications(**context)["items"][0]
    assert resubmitted.approval_id != current.approval_id
    assert second["notificationId"] != first["notificationId"]
    assert second["acknowledged"] is False
    old_target = shell.get_tasks(**context, view="approvals", task_id=query["taskId"][0], request_key=query["requestKey"][0])
    assert old_target["selected"] is None and old_target["selectionUnavailable"] is True
    with pytest.raises(ShellNotFound):
        shell.acknowledge_notification(notification_id=first["notificationId"], **context, idempotency_key="different-stale-ack")
    workflow.decide(current.decision_id, action=DecisionAction.APPROVE, actor="actual-reviewer", reason="real approved result")
    assert shell.get_notifications(**context)["items"] == []
    assert initial["meta"]["approvalCoverage"]["complete"] is False
    assert initial["meta"]["systemAnnouncementCoverage"]["state"] == "unavailable"


def test_new_report_pending_keeps_prior_approved_round_and_new_report_identity_separate():
    workflow, current, state, shell = setup()
    original = workflow.decide(current.decision_id, action=DecisionAction.APPROVE,
        actor="actual-reviewer", reason="original recorded approval").decision
    before = deepcopy(original)
    newer = report(CANDIDATE, report_id="actual-report-version-2", report_version=2, model_version="recorded-model-v2")
    new = workflow.open_decision(newer, created_by="new-author", tenant_id=TENANT,
        previous_decision_id=original.decision_id, verified_creator=True)
    new = workflow.submit_for_review(new.decision_id, submitted_by="new-author", verified_submitter=True)
    notice = shell.get_notifications(role_id="expansion-manager", subject_id="actual-reviewer")["items"]
    assert len(notice) == 1 and notice[0]["sourceEntity"]["id"] == new.decision_id
    assert new.decision_id != original.decision_id and new.approval_id != original.approval_id
    assert state.decisions.get_decision(original.decision_id) == before
    assert new.previous_decision_id == original.decision_id


@pytest.mark.parametrize("gap", ["denied approve", "foreign scope", "missing transition", "unverified submitter", "wrong signature", "owner unavailable"])
def test_unadmitted_or_unproved_requests_never_become_approval_notifications(gap):
    _, current, state, shell = setup()
    if gap == "denied approve":
        state.can_approve = False
    elif gap == "foreign scope":
        state.visible = False
    elif gap == "missing transition":
        state.decisions.save_decision(replace(current, history=()))
    elif gap == "unverified submitter":
        state.decisions.save_decision(replace(current, submission_actor_verified=False))
    elif gap == "wrong signature":
        state.signature_matches = False
    else:
        state.owner_available = False
    value = shell.get_notifications(role_id="expansion-manager", subject_id="actual-reviewer")
    assert value["items"] == [] and value["unacknowledged"] == 0
    assert value["meta"]["approvalCoverage"]["complete"] is False
    assert value["meta"]["systemAnnouncementCoverage"]["state"] == "unavailable"


def test_intervention_current_submission_uses_retained_transition_version_not_read_time():
    raw = owner_sources([])
    at = datetime(2026, 10, 15, 8, tzinfo=UTC)
    raw["interventions"] = [{"intervention_id": "actual-intervention", "store_id": "own-store", "kind": "PRICING",
        "status": "PENDING_APPROVAL", "version": 3, "history": [{"to_status": "PENDING_APPROVAL", "action": "submit_for_approval", "actor": "actual-author", "at": at}]}]
    projection = build_task_center_records(raw)
    first = projection["records"][0]["notificationRequest"]
    assert first["occurredAt"] == at.isoformat()
    assert first["ownerVersion"] == 3 and first["actorSubjectId"] == "actual-author"
    assert build_task_center_records(raw)["records"][0]["notificationRequest"] == first
    raw["interventions"][0]["version"] = 5
    # A changed current owner version alone is not a different submission.
    assert build_task_center_records(raw)["records"][0]["notificationRequest"]["key"] == first["key"]
    raw["interventions"][0]["status"] = "APPROVED"
    assert build_task_center_records(raw)["records"][0]["notificationRequest"] is None



def test_real_intervention_submit_assign_unassign_keeps_original_notification_and_acknowledgement():
    from datetime import timedelta
    from types import SimpleNamespace

    from modules.intervention import (
        InMemoryInterventionRepository,
        InterventionKind,
        InterventionWorkflow,
    )

    workflow = InterventionWorkflow(repository=InMemoryInterventionRepository())
    start = datetime(2026, 10, 15, 8, tzinfo=UTC)
    case = workflow.open_case(store_id="actual-own-store", kind=InterventionKind.PRICE_CHANGE,
        trigger_ref="actual-trigger", expected_outcome="recorded case purpose", planned_start=start,
        planned_end=start + timedelta(days=14), created_by="actual-creator")
    identity = case.intervention_id
    workflow.check_eligibility(identity, eligible=True, actor="actual-creator")
    workflow.propose_action(identity, action_spec={"price_change_pct": -5, "rollback_plan": "restore original"}, actor="actual-submitter")
    workflow.check_conflict(identity, actor="actual-submitter")
    submitted = workflow.submit_for_approval(identity, actor="actual-submitter")
    actual_submit = submitted.history[-1]

    class NativeState:
        def resolve_role(self, **_):
            return {"id": "ops-lead", "label": "Actual operations grant", "allowedWorkspaces": ["today", "store", "govern"]}
        def get_today(self, **_):
            return {"notifications": []}
        def get_task_center(self, *, subject_id, **_):
            current = workflow.get(identity)
            raw = owner_sources([])
            raw["interventions"] = [current]
            pending = [{"id": identity, "status": "pending", "title": "Actual native pending request", "meta": "Store Ops",
                "target": {"workspace": "today", "tab": "interventions", "entityId": identity}}] if current.status.value == "PENDING_APPROVAL" else []
            principal = Principal(subject_id, frozenset({Role.OPERATIONS_MANAGER}), Scope(tenant_id=TENANT, store_ids=frozenset({"actual-own-store"})))
            stores = SimpleNamespace(get_store=lambda _: SimpleNamespace(store_id="actual-own-store", is_current=True,
                tenant_id=TENANT, brand_id="actual-brand", region_code="actual-region"))
            brands = SimpleNamespace(get_brand=lambda _: SimpleNamespace(tenant_id=TENANT))
            admitted = admit_task_center_records({"_meta": {"tenantId": TENANT}, "taskCenterRecords": build_task_center_records(raw), "decisions": pending},
                principal=principal, store_repository=stores, brand_repository=brands, listing_snapshot_reader=lambda: {},
                can_read=lambda resource: rbac_allows(principal, resource, Action.VIEW),
                can_approve=lambda resource: rbac_allows(principal, resource, Action.APPROVE))
            return {**admitted["taskCenterRecords"], "dataOrigin": {"kind": "authoritative", "sourceId": "real-native-memory-workflow"}}

    service = ShellService(NativeState(), repository=InMemoryShellRepository())
    context = {"role_id": "ops-lead", "subject_id": "actual-approver"}
    first = service.get_notifications(**context)["items"][0]
    key = first["sourceRequestKey"]
    assert first["occurredAt"] == actual_submit.at.isoformat()
    service.acknowledge_notification(notification_id=first["notificationId"], **context, idempotency_key="actual-submit-ack")
    assigned = workflow.assign_case(identity, assignee="actual-executor", actor="actual-approver", expected_version=submitted.version)
    assert assigned.version > submitted.version and assigned.history[-1].action == "assign"
    after_assignment = service.get_notifications(**context)["items"][0]
    assert after_assignment["notificationId"] == first["notificationId"] and after_assignment["acknowledged"] is True
    selected_assignment = service.get_tasks(**context, view="approvals", task_id=f"intervention:{identity}", request_key=key)
    assert selected_assignment["selected"]["assigneeId"] == "actual-executor"
    assert selected_assignment["selected"]["notificationRequest"]["actorSubjectId"] == "actual-submitter"
    unassigned = workflow.unassign_case(identity, actor="actual-approver", expected_version=assigned.version)
    assert unassigned.version > assigned.version and unassigned.history[-1].action == "unassign"
    for current in (assigned, unassigned):
        request = build_task_center_records({**owner_sources([]), "interventions": [current]})["records"][0]["notificationRequest"]
        assert request["key"] == key and request["occurredAt"] == actual_submit.at.isoformat()
        assert request["actorSubjectId"] == "actual-submitter"
    after = service.get_notifications(**context)["items"][0]
    assert after["notificationId"] == first["notificationId"] and after["acknowledged"] is True
    selected = service.get_tasks(**context, view="approvals", task_id=f"intervention:{identity}", request_key=key)
    assert selected["selected"]["sourceId"] == identity and selected["selectionUnavailable"] is False
    assert selected["selected"]["assigneeId"] is None
    workflow.approve(identity, actor="actual-approver", reason="real original approval", expected_version=unassigned.version)
    assert service.get_notifications(**context)["items"] == []
    terminal = service.get_tasks(**context, view="approvals", task_id=f"intervention:{identity}", request_key=key)
    assert terminal["selected"] is None and terminal["selectionUnavailable"] is True
