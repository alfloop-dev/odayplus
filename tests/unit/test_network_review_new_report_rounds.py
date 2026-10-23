"""User-approved new review rounds; memory only, no SQL/auth/runtime claims."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from modules.listing.domain.models import CandidateSiteDraft
from modules.opsboard.application.network_listings import NetworkListingService
from modules.opsboard.application.network_review_submission import NetworkReviewSubmissionOwner
from modules.opsboard.application.network_reviews import NetworkReviewConflict
from modules.sitescore import InMemorySiteScoreRepository
from shared.domain.models import AddressLocation, CandidateSite, Listing
from shared.workflow.sitescore import (
    CandidateSiteRealizationHook,
    DecisionAction,
    DecisionStatus,
    InMemoryDecisionStore,
    SiteScoreDecisionError,
    SiteScoreDecisionWorkflow,
)
from tests.unit.test_network_canonical_review_projection import report

TENANT = "offline-new-round-tenant"
CANDIDATE = "offline-new-round-candidate"


class MemoryApprovals:
    def __init__(self):
        self.rows = {}

    def save(self, value):
        self.rows[value.approval_id] = value

    def get(self, key):
        return self.rows.get(key)


def components():
    scores = InMemorySiteScoreRepository()
    decisions = InMemoryDecisionStore()
    decisions.tenant_id = TENANT
    approvals = MemoryApprovals()
    hook = CandidateSiteRealizationHook()
    workflow = SiteScoreDecisionWorkflow(store=decisions, approval_store=approvals, hooks=[hook])
    first = scores.save_report(report(CANDIDATE))
    original = workflow.open_decision(first, created_by="original-author", tenant_id=TENANT)
    workflow.submit_for_review(original.decision_id, submitted_by="original-author")
    original = workflow.decide(original.decision_id, action=DecisionAction.APPROVE,
                               actor="independent-checker", reason="original recorded approval").decision
    owner = NetworkReviewSubmissionOwner(SimpleNamespace(
        _sitescore_repository=scores, _decision_repository=decisions, _decision_workflow=workflow))
    address = AddressLocation(address_id="offline-address")
    listing = Listing(listing_id="offline-listing", address_id=address.address_id)
    draft = CandidateSiteDraft(listing=listing, address=address, candidate_site=CandidateSite(
        candidate_site_id=CANDIDATE, listing_id=listing.listing_id,
        address_id=address.address_id, site_status="approved"), review_id=original.decision_id)
    return scores, decisions, approvals, workflow, hook, original, draft, owner


@pytest.mark.parametrize("state", ["approved", "opened"])
def test_newer_saved_report_selects_new_round_without_changing_old_graph(state):
    scores, decisions, approvals, _, hook, original, draft, owner = components()
    draft.candidate_site = replace(draft.candidate_site, site_status=state)
    draft.existing_opening_record = {"recordId": "offline-original-opening-record", "retained": True}
    newer = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), draft))
    selected, retained, latest = owner._target(draft, None)
    assert selected is None and retained == newer and latest == newer
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), draft) == before
    assert decisions.get_decision(original.decision_id).status is DecisionStatus.APPROVED


@pytest.mark.parametrize("state", ["approved", "opened"])
def test_same_report_cannot_reopen_terminal_round(state):
    _, _, _, _, _, _, draft, owner = components()
    draft.candidate_site = replace(draft.candidate_site, site_status=state)
    with pytest.raises(NetworkReviewConflict):
        owner._target(draft, None)


def test_initial_rejected_candidate_and_rejected_decision_keep_restriction():
    scores, decisions, _, _, _, original, draft, owner = components()
    scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    decisions.save_decision(replace(original, status=DecisionStatus.REJECTED, network_action="REJECT"))
    for state in ("approved", "opened", "rejected"):
        draft.candidate_site = replace(draft.candidate_site, site_status=state)
        with pytest.raises(NetworkReviewConflict):
            owner._target(draft, None)


def test_foreign_pending_pointer_does_not_create_second_pending_round():
    scores, _, _, workflow, _, _, draft, owner = components()
    newer = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    other = workflow.open_decision(newer, created_by="other-author", tenant_id=TENANT)
    workflow.submit_for_review(other.decision_id, submitted_by="other-author")
    with pytest.raises(NetworkReviewConflict):
        owner._target(draft, None)


@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT"])
def test_separate_round_retains_original_approval_report_history_and_realization(action):
    scores, decisions, approvals, workflow, hook, original, _, _ = components()
    newer = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    old_graph = deepcopy((decisions.get_decision(original.decision_id),
                          decisions.get_report(original.decision_id),
                          approvals.get(original.approval_id), hook.get(CANDIDATE)))
    new = workflow.open_decision(newer, created_by="new-author", tenant_id=TENANT,
                                 previous_decision_id=original.decision_id)
    new = workflow.submit_for_review(new.decision_id, submitted_by="new-author")
    assert new.decision_id != original.decision_id and new.approval_id != original.approval_id
    outcome = workflow.decide(new.decision_id, action={
        "GO": DecisionAction.APPROVE, "WAIT": DecisionAction.APPROVE,
        "RETURN": DecisionAction.REQUEST_REVISION, "REJECT": DecisionAction.REJECT,
    }[action], actor="new-checker", reason="independent new-round recorded result", network_action=action,
        conditions="new evidence condition" if action == "WAIT" else "",
        required_data=["new lease"] if action == "RETURN" else [])
    assert outcome.decision.previous_decision_id == original.decision_id
    assert outcome.realization_events == ()
    assert outcome.decision.to_dict()["previous_decision_id"] == original.decision_id
    assert decisions.get_report(new.decision_id) == newer
    assert (decisions.get_decision(original.decision_id), decisions.get_report(original.decision_id),
            approvals.get(original.approval_id), hook.get(CANDIDATE)) == old_graph


def test_return_reuses_new_round_frozen_report_even_when_latest_changes():
    scores, decisions, _, workflow, _, original, draft, owner = components()
    second = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    new = workflow.open_decision(second, created_by="new-author", tenant_id=TENANT,
                                 previous_decision_id=original.decision_id)
    workflow.submit_for_review(new.decision_id, submitted_by="new-author")
    returned = workflow.decide(new.decision_id, action=DecisionAction.REQUEST_REVISION,
        actor="new-checker", reason="recorded requested data for new round", network_action="RETURN",
        required_data=["new lease"]).decision
    draft.review_id = new.decision_id
    third = scores.save_report(report(CANDIDATE, model_version="offline-model-v3"))
    selected, retained, latest = owner._target(draft, new.decision_id)
    assert selected == returned and retained == second and latest == third
    resubmitted = workflow.submit_for_review(selected.decision_id, submitted_by="new-author")
    assert resubmitted.decision_id == new.decision_id
    assert decisions.get_report(new.decision_id) == second
    assert resubmitted.previous_decision_id == original.decision_id


@pytest.mark.parametrize("state", ["approved", "opened"])
@pytest.mark.parametrize("action", ["GO", "WAIT", "REJECT"])
def test_third_report_starts_third_round_after_new_round_result_without_revoking_original(state, action):
    scores, decisions, approvals, workflow, hook, original, draft, owner = components()
    draft.candidate_site = replace(draft.candidate_site, site_status=state)
    old_graph = deepcopy((original, decisions.get_report(original.decision_id),
                          approvals.get(original.approval_id), hook.get(CANDIDATE)))
    second = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    new = workflow.open_decision(second, created_by="second-author", tenant_id=TENANT,
                                 previous_decision_id=original.decision_id)
    workflow.submit_for_review(new.decision_id, submitted_by="second-author")
    completed = workflow.decide(new.decision_id,
        action=DecisionAction.REJECT if action == "REJECT" else DecisionAction.APPROVE,
        actor="second-checker", reason="recorded independent second review", network_action=action,
        conditions="second review condition" if action == "WAIT" else "").decision
    draft.review_id = completed.decision_id
    third_report = scores.save_report(report(CANDIDATE, model_version="offline-model-v3"))
    selected, retained, latest = owner._target(draft, None)
    assert selected is None and retained == latest == third_report
    third = workflow.open_decision(retained, created_by="third-author", tenant_id=TENANT,
                                   previous_decision_id=draft.review_id)
    third = workflow.submit_for_review(third.decision_id, submitted_by="third-author")
    assert third.previous_decision_id == completed.decision_id
    assert completed.previous_decision_id == original.decision_id
    assert draft.candidate_site.site_status == state
    assert (decisions.get_decision(original.decision_id), decisions.get_report(original.decision_id),
            approvals.get(original.approval_id), hook.get(CANDIDATE)) == old_graph


@pytest.mark.parametrize("corruption", ["cycle", "missing-report", "wrong-retained-report", "invalid-version"])
def test_invalid_predecessor_chain_cannot_open_another_round(corruption):
    scores, decisions, _, workflow, _, original, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    if corruption == "cycle":
        decisions.save_decision(replace(original, previous_decision_id=original.decision_id))
    elif corruption == "missing-report":
        decisions._reports.pop(original.decision_id)
    elif corruption == "wrong-retained-report":
        decisions._reports[original.decision_id] = replace(decisions.get_report(original.decision_id),
                                                           model_version="wrong-model")
    else:
        decisions.save_decision(replace(original, version=True))
    before = deepcopy((decisions._decisions, decisions._reports))
    with pytest.raises(SiteScoreDecisionError):
        workflow.open_decision(latest, created_by="new-author", tenant_id=TENANT,
                               previous_decision_id=original.decision_id)
    assert (decisions._decisions, decisions._reports) == before


@pytest.mark.parametrize("corruption", ["missing", "foreign-decision", "wrong-status", "wrong-actor", "wrong-time"])
def test_previous_approval_must_remain_actual_and_bound_before_opening(corruption):
    scores, decisions, approvals, workflow, hook, original, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    approval = approvals.get(original.approval_id)
    if corruption == "missing":
        approvals.rows.pop(original.approval_id)
    else:
        changes = {"foreign-decision": {"decision_id": "foreign-decision"},
                   "wrong-status": {"approval_status": "returned"},
                   "wrong-actor": {"approver_id": "foreign-actor"},
                   "wrong-time": {"approved_at": approval.approved_at + timedelta(minutes=1)}}[corruption]
        approvals.rows[original.approval_id] = replace(approval, **changes)
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE)))
    with pytest.raises(SiteScoreDecisionError):
        workflow.open_decision(latest, created_by="new-author", tenant_id=TENANT,
                               previous_decision_id=original.decision_id)
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE)) == before


def test_new_round_without_actual_approval_store_is_unavailable():
    scores, decisions, _, workflow, _, original, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    workflow._approval_store = None
    before = deepcopy((decisions._decisions, decisions._reports))
    with pytest.raises(SiteScoreDecisionError):
        workflow.open_decision(latest, created_by="new-author", tenant_id=TENANT,
                               previous_decision_id=original.decision_id)
    assert (decisions._decisions, decisions._reports) == before


@pytest.mark.parametrize("corruption", ["status", "report", "tenant", "version", "action"])
def test_actual_previous_terminal_transition_must_match_decision_before_new_round(corruption):
    scores, decisions, approvals, workflow, hook, original, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    changes = {"status": {"to_status": DecisionStatus.REJECTED},
               "report": {"report_id": "foreign-report"}, "tenant": {"tenant_id": "foreign-tenant"},
               "version": {"version": True}, "action": {"network_action": "REJECT"}}[corruption]
    bad = replace(original, history=(*original.history[:-1], replace(original.history[-1], **changes)))
    decisions.save_decision(bad)
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        workflow.open_decision(latest, created_by="new-author", tenant_id=TENANT,
                               previous_decision_id=original.decision_id)
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


def _terminal_coherence_fixture(entry, previous_status=DecisionStatus.APPROVED, include_latest=False):
    scores, decisions, approvals, workflow, hook, previous, _, _ = components()
    if previous_status is DecisionStatus.REJECTED:
        second = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
        prior = workflow.open_decision(second, created_by="second-author", tenant_id=TENANT,
                                      previous_decision_id=previous.decision_id)
        workflow.submit_for_review(prior.decision_id, submitted_by="second-author")
        previous = workflow.decide(prior.decision_id, action=DecisionAction.REJECT,
            actor="second-checker", reason="Recorded rejected second report", network_action="REJECT").decision
    latest = scores.save_report(report(CANDIDATE, model_version="offline-current-model"))
    if entry == "open":
        def operation():
            return workflow.open_decision(latest, created_by="current-author", tenant_id=TENANT,
                                          previous_decision_id=previous.decision_id)
    else:
        current = workflow.open_decision(latest, created_by="current-author", tenant_id=TENANT,
                                         previous_decision_id=previous.decision_id)
        workflow.submit_for_review(current.decision_id, submitted_by="current-author")
        if entry == "resubmit":
            workflow.decide(current.decision_id, action=DecisionAction.REQUEST_REVISION,
                actor="current-checker", reason="Recorded current report missing lease", network_action="RETURN",
                required_data=["lease"])
            def operation():
                return workflow.submit_for_review(current.decision_id, submitted_by="current-author")
        else:
            def operation():
                return workflow.decide(current.decision_id, action=DecisionAction.APPROVE,
                    actor="current-checker", reason="Recorded independent current report approval")
    values = decisions, approvals, workflow, hook, previous, operation
    return (*values, latest) if include_latest else values


@pytest.mark.parametrize("entry", ["open", "decide", "resubmit"])
@pytest.mark.parametrize("previous_status,bad_action", [
    (DecisionStatus.APPROVED, "REJECT"), (DecisionStatus.APPROVED, "RETURN"),
    (DecisionStatus.APPROVED, "UNKNOWN"), (DecisionStatus.REJECTED, "GO"),
    (DecisionStatus.REJECTED, "WAIT"),
])
def test_coordinated_previous_network_action_must_match_terminal_status_without_any_write(entry, previous_status, bad_action):
    decisions, approvals, workflow, hook, previous, operation = _terminal_coherence_fixture(entry, previous_status)
    # Decision and last history are coordinated; actual Approval/status/actor/time remain intact.
    decisions.save_decision(replace(previous, network_action=bad_action,
        history=(*previous.history[:-1], replace(previous.history[-1], network_action=bad_action))))
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        operation()
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


@pytest.mark.parametrize("entry", ["open", "decide", "resubmit"])
@pytest.mark.parametrize("field,value", [
    ("conditions", "foreign lease condition"), ("required_data", ("foreign missing item",)),
    ("override_ack", True), ("actor_role_id", "foreign-recorded-role"),
])
def test_previous_terminal_ancillary_fields_match_actual_decision_without_any_write(entry, field, value):
    decisions, approvals, workflow, hook, previous, operation = _terminal_coherence_fixture(entry)
    decisions.save_decision(replace(previous,
        history=(*previous.history[:-1], replace(previous.history[-1], **{field: value}))))
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        operation()
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


@pytest.mark.parametrize("entry", ["open", "decide", "resubmit"])
def test_coordinated_previous_wait_requires_its_recorded_condition_without_any_write(entry):
    decisions, approvals, workflow, hook, previous, operation = _terminal_coherence_fixture(entry)
    decisions.save_decision(replace(previous, network_action="WAIT",
        history=(*previous.history[:-1], replace(previous.history[-1], network_action="WAIT"))))
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        operation()
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


def test_legacy_native_approved_previous_round_with_unknown_network_action_remains_usable():
    decisions, _, workflow, _, previous, operation = _terminal_coherence_fixture("open")
    decisions.save_decision(replace(previous, network_action=None,
        history=(*previous.history[:-1], replace(previous.history[-1], network_action=None))))
    newer = operation()
    assert newer.previous_decision_id == previous.decision_id
    assert workflow.get(previous.decision_id).network_action is None


@pytest.mark.parametrize("entry", ["open", "decide", "resubmit"])
def test_previous_lookup_key_must_match_actual_decision_body_without_any_write(entry):
    decisions, approvals, workflow, hook, previous, operation, latest = _terminal_coherence_fixture(entry, include_latest=True)
    # Raw memory-store corruption; normal save_decision always keys by body ID.
    alias = "offline-existing-review-alias"
    decisions._decisions[alias] = previous
    if entry == "open":
        def operation():
            return workflow.open_decision(latest, created_by="current-author", tenant_id=TENANT,
                                          previous_decision_id=alias)
    else:
        current = next(d for d in decisions.list_decisions() if d.report_id == latest.report_id)
        decisions.save_decision(replace(current, previous_decision_id=alias,
            history=tuple(replace(item, previous_decision_id=alias) for item in current.history)))
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        operation()
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


@pytest.mark.parametrize("state", ["approved", "opened"])
def test_candidate_review_pointer_alias_cannot_select_another_actual_decision_body(state):
    scores, decisions, approvals, workflow, hook, previous, candidate, owner = components()
    scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    alias = "offline-existing-review-alias"
    decisions._decisions[alias] = previous
    candidate.review_id = alias
    candidate.candidate_site = replace(candidate.candidate_site, site_status=state)
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events(), candidate))
    with pytest.raises(NetworkReviewConflict):
        owner._target(candidate, None)
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events(), candidate) == before


@pytest.mark.parametrize("entry", ["open", "decide", "resubmit"])
def test_actual_previous_lookup_identity_normal_control(entry):
    decisions, _, _, _, previous, operation = _terminal_coherence_fixture(entry)
    assert decisions.get_decision(previous.decision_id).decision_id == previous.decision_id
    outcome = operation()
    current = outcome.decision if entry == "decide" else outcome
    assert current.previous_decision_id == previous.decision_id


@pytest.mark.parametrize("entry", ["open", "decide", "resubmit"])
def test_coordinated_missing_previous_approval_time_is_not_approval_evidence(entry):
    decisions, approvals, workflow, hook, previous, operation = _terminal_coherence_fixture(entry)
    approvals.rows[previous.approval_id] = replace(approvals.get(previous.approval_id), approved_at=None)
    decisions.save_decision(replace(previous,
        history=(*previous.history[:-1], replace(previous.history[-1], at=None))))
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        operation()
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


def test_previous_approval_datetime_without_timezone_remains_compatible():
    decisions, approvals, _, _, previous, operation = _terminal_coherence_fixture("open")
    native_time = previous.history[-1].at.replace(tzinfo=None)
    approvals.rows[previous.approval_id] = replace(approvals.get(previous.approval_id), approved_at=native_time)
    decisions.save_decision(replace(previous,
        history=(*previous.history[:-1], replace(previous.history[-1], at=native_time))))
    assert operation().previous_decision_id == previous.decision_id


@pytest.mark.parametrize("bad", ["appended-approved", "appended-foreign-submit"])
def test_current_pending_tail_cannot_change_after_submission_without_any_write(bad):
    decisions, approvals, workflow, hook, _, operation, latest = _terminal_coherence_fixture("decide", include_latest=True)
    current = next(d for d in decisions.list_decisions() if d.report_id == latest.report_id)
    tail = replace(current.history[-1], version=current.version + 1,
        action="APPROVE" if bad == "appended-approved" else "submit",
        to_status=DecisionStatus.APPROVED if bad == "appended-approved" else DecisionStatus.PENDING_REVIEW,
        actor="foreign-history-actor", submitted_by="foreign-history-actor")
    decisions.save_decision(replace(current, history=(*current.history, tail)))
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        operation()
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


def test_new_system_recommended_round_with_empty_history_can_submit_normally():
    scores, decisions, _, workflow, _, previous, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    current = workflow.open_decision(latest, created_by="current-author", tenant_id=TENANT,
                                     previous_decision_id=previous.decision_id)
    assert current.status is DecisionStatus.SYSTEM_RECOMMENDED and current.version == 1 and current.history == ()
    submitted = workflow.submit_for_review(current.decision_id, submitted_by="current-author")
    assert submitted.status is DecisionStatus.PENDING_REVIEW and submitted.version == 2
    assert decisions.get_report(current.decision_id) == latest


def test_returned_draft_same_round_current_tail_can_resubmit_normally():
    decisions, _, _, _, previous, operation, latest = _terminal_coherence_fixture("resubmit", include_latest=True)
    returned = next(d for d in decisions.list_decisions() if d.report_id == latest.report_id)
    assert returned.status is DecisionStatus.DRAFT and returned.history[-1].action == "REQUEST_REVISION"
    resubmitted = operation()
    assert resubmitted.decision_id == returned.decision_id
    assert resubmitted.previous_decision_id == previous.decision_id
    assert resubmitted.status is DecisionStatus.PENDING_REVIEW
    assert decisions.get_report(returned.decision_id) == latest


@pytest.mark.parametrize("corruption", ["missing-decision", "wrong-tenant", "cycle", "deleted-link", "missing-report", "missing-approval"])
@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT"])
def test_pending_new_round_rechecks_previous_chain_before_any_transition(corruption, action):
    scores, decisions, approvals, workflow, hook, original, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    new = workflow.open_decision(latest, created_by="new-author", tenant_id=TENANT,
                                 previous_decision_id=original.decision_id)
    workflow.submit_for_review(new.decision_id, submitted_by="new-author")
    if corruption == "missing-decision":
        decisions._decisions.pop(original.decision_id)
    elif corruption == "wrong-tenant":
        decisions.save_decision(replace(original, tenant_id="foreign-tenant"))
    elif corruption == "cycle":
        decisions.save_decision(replace(original, previous_decision_id=new.decision_id))
    elif corruption == "deleted-link":
        decisions.save_decision(replace(decisions.get_decision(new.decision_id), previous_decision_id=None))
    elif corruption == "missing-report":
        decisions._reports.pop(original.decision_id)
    else:
        approvals.rows.pop(original.approval_id)
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        workflow.decide(new.decision_id, action={"GO": DecisionAction.APPROVE, "WAIT": DecisionAction.APPROVE,
            "RETURN": DecisionAction.REQUEST_REVISION, "REJECT": DecisionAction.REJECT}[action],
            actor="new-checker", reason="corrupt prior chain cannot authorize new outcome", network_action=action,
            conditions="recorded condition" if action == "WAIT" else "",
            required_data=["new lease"] if action == "RETURN" else [])
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


def test_legacy_round_json_has_no_new_predecessor_fields():
    _, _, _, workflow, _, original, _, _ = components()
    value = original.to_dict()
    assert "previous_decision_id" not in value
    assert all("previous_decision_id" not in item for item in value["history"])
    assert all("previous_decision_id" not in item.metadata for item in workflow.audit_log.list_events())


@pytest.mark.parametrize("corruption", ["deleted-link", "missing-approval", "wrong-approval-status"])
def test_returned_round_rechecks_chain_before_creating_new_pending_approval(corruption):
    scores, decisions, approvals, workflow, hook, original, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    new = workflow.open_decision(latest, created_by="new-author", tenant_id=TENANT,
                                 previous_decision_id=original.decision_id)
    workflow.submit_for_review(new.decision_id, submitted_by="new-author")
    returned = workflow.decide(new.decision_id, action=DecisionAction.REQUEST_REVISION,
        actor="new-checker", reason="recorded returned new-round data requirement", network_action="RETURN",
        required_data=["new lease"]).decision
    if corruption == "deleted-link":
        decisions.save_decision(replace(returned, previous_decision_id=None))
    elif corruption == "missing-approval":
        approvals.rows.pop(original.approval_id)
    else:
        approvals.rows[original.approval_id] = replace(approvals.get(original.approval_id), approval_status="returned")
    before = deepcopy((decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()))
    with pytest.raises(SiteScoreDecisionError):
        workflow.submit_for_review(new.decision_id, submitted_by="new-author")
    assert (decisions._decisions, decisions._reports, approvals.rows, hook.get(CANDIDATE), workflow.audit_log.list_events()) == before


@pytest.mark.parametrize("bad", ["foreign-candidate", "foreign-tenant", "not-terminal", "same-report"])
def test_new_round_predecessor_must_be_actual_approved_same_candidate_tenant_and_older(bad):
    scores, decisions, _, workflow, _, original, _, _ = components()
    latest = scores.save_report(report(CANDIDATE, model_version="offline-model-v2"))
    changes = {"foreign-candidate": {"candidate_site_id": "foreign-candidate"},
               "foreign-tenant": {"tenant_id": "foreign-tenant"},
               "not-terminal": {"status": DecisionStatus.PENDING_REVIEW},
               "same-report": {"report_id": latest.report_id, "report_version": latest.report_version}}[bad]
    decisions.save_decision(replace(original, **changes))
    before = deepcopy((decisions._decisions, decisions._reports))
    with pytest.raises(SiteScoreDecisionError):
        workflow.open_decision(latest, created_by="new-author", tenant_id=TENANT,
                               previous_decision_id=original.decision_id)
    assert (decisions._decisions, decisions._reports) == before


class MemoryCandidateOwner:
    def __init__(self, candidate):
        self.candidate = candidate

    def list_candidates(self):
        return [self.candidate]

    def save_review_candidate(self, value, *, expected_review_id, expected_state):
        assert self.candidate.review_id == expected_review_id
        assert self.candidate.candidate_site.site_status == expected_state
        self.candidate = value


class MemoryMetadataOwner:
    def __init__(self):
        self.metadata = {}

    def get_candidate_metadata(self, key):
        return deepcopy(self.metadata)

    def save_review_metadata(self, key, value, *, expected_owned):
        assert {k: deepcopy(self.metadata[k]) for k in ("reviewWorkflow", "reviewSubmission")
                if k in self.metadata} == expected_owned
        self.metadata = deepcopy(value)


@pytest.mark.parametrize("state", ["approved", "opened"])
@pytest.mark.parametrize("action", ["GO", "WAIT", "RETURN", "REJECT"])
def test_new_round_outcome_preserves_physical_and_projected_operational_state(state, action):
    _, decisions, _, _, _, original, draft, _ = components()
    draft.candidate_site = replace(draft.candidate_site, site_status=state)
    draft.existing_opening_record = {"recordId": "offline-original-opening-record", "retained": True}
    new = replace(original, decision_id="offline-new-review", previous_decision_id=original.decision_id,
                  network_action=action, required_data=("new lease",) if action == "RETURN" else (),
                  conditions="new condition" if action == "WAIT" else "")
    decisions.save_decision(new)
    draft.review_id = new.decision_id
    candidate_owner, metadata_owner = MemoryCandidateOwner(draft), MemoryMetadataOwner()
    service = NetworkListingService.__new__(NetworkListingService)
    service._listing_repository, service._intakes = candidate_owner, metadata_owner
    service._tenant_id, service._seed_fixtures = TENANT, False
    service._review_decision_repository = decisions
    review = service.record_review_decision(new)
    assert review["status"] == {"GO": "approved", "WAIT": "onhold", "RETURN": "needdata", "REJECT": "rejected"}[action]
    assert candidate_owner.candidate.candidate_site.site_status == state
    assert candidate_owner.candidate.existing_opening_record == {"recordId": "offline-original-opening-record", "retained": True}
    projected = service._candidate_to_dict(candidate_owner.candidate)
    assert projected["status"] == state
    assert projected["reviewWorkflow"]["networkAction"] == action
