from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modules.listing.application.assignment_sla import (
    AssignmentSlaService,
    InMemoryAssignmentRepository,
    InMemorySlaRepository,
)
from modules.listing.application.intake_workflow import (
    InMemoryIntakeRepository,
    IntakeWorkflowService,
)
from modules.listing.domain.intake_states import (
    Actor,
    AssignmentState,
    DenialCode,
    DomainValidationError,
    IdentityDecisionAggregate,
    IdentityDecisionStateMachine,
    IdentityGraphState,
    IntakeStage,
    ListingAggregate,
    ListingState,
    ListingStateMachine,
    PrincipalRole,
    PromotionAggregate,
    PromotionState,
    PromotionStateMachine,
    SlaState,
    TransitionContext,
)


@pytest.fixture
def base_context() -> TransitionContext:
    actor = Actor(
        actor_id="steward-1",
        role=PrincipalRole.DATA_STEWARD,
        tenant_id="tenant-a"
    )
    return TransitionContext(
        actor=actor,
        idempotency_key="idem-key-123",
        correlation_id="corr-123"
    )


# --- 1. INTAKE STATE MACHINE TESTS ---

def test_intake_submitted_creation(base_context: TransitionContext) -> None:
    repo = InMemoryIntakeRepository()
    service = IntakeWorkflowService(repo)

    # 1. Successful submission
    intake = service.submit_intake(
        intake_id="IN-1",
        tenant_id="tenant-a",
        source_id="src-1",
        canonical_url="https://example.com/listing-1",
        context=base_context
    )
    assert intake.stage == IntakeStage.SUBMITTED
    assert intake.version == 1
    assert len(service.emitted_events) == 1
    assert service.emitted_events[0]["event_type"] == "intake.submitted.v1"

    # 2. Denied role for creation
    staff_actor = Actor(actor_id="guest-1", role=PrincipalRole.GOVERNANCE_REVIEWER, tenant_id="tenant-a")
    bad_context = TransitionContext(actor=staff_actor, idempotency_key="idem-2")
    with pytest.raises(DomainValidationError) as exc:
        service.submit_intake("IN-2", "tenant-a", "src-1", None, bad_context)
    assert exc.value.code == DenialCode.ROLE_DENIED


def test_intake_legal_transitions_path(base_context: TransitionContext) -> None:
    repo = InMemoryIntakeRepository()
    service = IntakeWorkflowService(repo)

    # 1. submit
    intake = service.submit_intake("IN-1", "tenant-a", "src-1", "url-1", base_context)

    # 2. start identity check (requires SVC_INTAKE)
    system_context = TransitionContext(
        actor=Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="tenant-a"),
        correlation_id="corr-2"
    )
    service.start_identity_check("IN-1", system_context)
    assert intake.stage == IntakeStage.CHECKING_IDENTITY

    # 3. start source policy evaluation (requires SVC_INTAKE)
    service.start_source_policy_evaluation("IN-1", system_context)
    assert intake.stage == IntakeStage.CHECKING_SOURCE_POLICY

    # 4. approve retrieval (requires SVC_RETRIEVAL/SVC_INTAKE)
    retrieval_context = TransitionContext(
        actor=Actor(actor_id="retriever", role=PrincipalRole.SVC_RETRIEVAL, tenant_id="tenant-a"),
        correlation_id="corr-3"
    )
    service.approve_retrieval("IN-1", "APPROVED_RETRIEVAL", retrieval_context)
    assert intake.stage == IntakeStage.RETRIEVING

    # 5. start parsing (requires SVC_RETRIEVAL)
    service.start_parsing_from_retrieval("IN-1", "snap-123", retrieval_context)
    assert intake.stage == IntakeStage.PARSING

    # 6. complete parsing -> MATCHING (requires SVC_PARSER)
    parser_context = TransitionContext(
        actor=Actor(actor_id="parser", role=PrincipalRole.SVC_PARSER, tenant_id="tenant-a")
    )
    service.complete_parsing("IN-1", "run-1", parser_context)
    assert intake.stage == IntakeStage.MATCHING

    # 7. resolve match -> READY (requires SVC_MATCHER/DATA_STEWARD/EXPANSION_MANAGER)
    matcher_context = TransitionContext(
        actor=Actor(actor_id="matcher", role=PrincipalRole.SVC_MATCHER, tenant_id="tenant-a")
    )
    service.resolve_match("IN-1", "NEW", "L-1", matcher_context)
    assert intake.stage == IntakeStage.READY


def test_intake_quarantine_and_reopen(base_context: TransitionContext) -> None:
    repo = InMemoryIntakeRepository()
    service = IntakeWorkflowService(repo)

    intake = service.submit_intake("IN-1", "tenant-a", "src-1", "url-1", base_context)

    # Move to CHECKING_IDENTITY -> CHECKING_SOURCE_POLICY
    sys_context = TransitionContext(actor=Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="tenant-a"))
    service.start_identity_check("IN-1", sys_context)
    service.start_source_policy_evaluation("IN-1", sys_context)

    # Quarantine from policy service
    service.quarantine_policy("IN-1", "SOURCE_BLOCKED", sys_context)
    assert intake.stage == IntakeStage.QUARANTINED

    # Attempt reopen without second actor (should fail)
    steward_context = TransitionContext(
        actor=Actor(actor_id="steward-1", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a"),
        reason="policy updated"
    )
    with pytest.raises(DomainValidationError) as exc:
        service.reopen_quarantine_policy("IN-1", steward_context)
    assert exc.value.code == DenialCode.SECOND_ACTOR_REQUIRED

    # Reopen with second actor
    steward_context_2p = TransitionContext(
        actor=Actor(actor_id="steward-1", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a"),
        second_actor=Actor(actor_id="steward-2", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a"),
        reason="policy updated"
    )
    service.reopen_quarantine_policy("IN-1", steward_context_2p)
    assert intake.stage == IntakeStage.CHECKING_SOURCE_POLICY


def test_intake_tenant_isolation(base_context: TransitionContext) -> None:
    repo = InMemoryIntakeRepository()
    service = IntakeWorkflowService(repo)
    service.submit_intake("IN-1", "tenant-a", "src-1", "url-1", base_context)

    # Try to execute transition with tenant-b actor (should fail)
    bad_actor = Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="tenant-b")
    bad_context = TransitionContext(actor=bad_actor)

    with pytest.raises(DomainValidationError) as exc:
        service.start_identity_check("IN-1", bad_context)
    assert exc.value.code == DenialCode.TENANT_SCOPE_DENIED


def test_intake_concurrency_version_conflict(base_context: TransitionContext) -> None:
    repo = InMemoryIntakeRepository()
    service = IntakeWorkflowService(repo)
    service.submit_intake("IN-1", "tenant-a", "src-1", "url-1", base_context)

    # Submit with wrong version_before (should fail)
    sys_context = TransitionContext(
        actor=Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="tenant-a"),
        version_before=999
    )
    with pytest.raises(DomainValidationError) as exc:
        service.start_identity_check("IN-1", sys_context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED


def test_intake_segregation_needs_review_to_ready(base_context: TransitionContext) -> None:
    repo = InMemoryIntakeRepository()
    service = IntakeWorkflowService(repo)

    # Create submission by staff-1
    staff_context = TransitionContext(
        actor=Actor(actor_id="staff-1", role=PrincipalRole.EXPANSION_STAFF, tenant_id="tenant-a"),
        idempotency_key="idem-1"
    )
    intake = service.submit_intake("IN-1", "tenant-a", "src-1", "url-1", staff_context)

    # Move to CHECKING_IDENTITY -> CHECKING_SOURCE_POLICY -> AWAITING_ASSISTED_ENTRY
    sys_context = TransitionContext(actor=Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="tenant-a"))
    service.start_identity_check("IN-1", sys_context)
    service.start_source_policy_evaluation("IN-1", sys_context)
    service.require_assisted_entry("IN-1", sys_context)

    # Complete assisted entry by staff-1
    service.complete_assisted_entry("IN-1", {"rent": 50000}, staff_context)

    # Move to MATCHING -> Needs review
    parser_context = TransitionContext(actor=Actor(actor_id="parser", role=PrincipalRole.SVC_PARSER, tenant_id="tenant-a"))
    service.complete_parsing("IN-1", "run-1", parser_context)
    
    matcher_context = TransitionContext(actor=Actor(actor_id="matcher", role=PrincipalRole.SVC_MATCHER, tenant_id="tenant-a"))
    service.route_review_from_matching("IN-1", ["L-100"], matcher_context)
    assert intake.stage == IntakeStage.NEEDS_REVIEW

    # staff-1 (proposer) attempts to approve review -> Self Review Denied!
    staff_manager_own_context = TransitionContext(
        actor=Actor(actor_id="staff-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    with pytest.raises(DomainValidationError) as exc:
        service.decide_review("IN-1", "APPROVE", "looks good", staff_manager_own_context)
    assert exc.value.code == DenialCode.SELF_REVIEW_DENIED

    # Different manager approves -> Success
    manager_context = TransitionContext(
        actor=Actor(actor_id="manager-2", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    service.decide_review("IN-1", "APPROVE", "looks good", manager_context)
    assert intake.stage == IntakeStage.READY


# --- 2. LISTING LIFECYCLE TESTS ---

def test_listing_transitions_success(base_context: TransitionContext) -> None:
    # 1. Create active listing
    entity = ListingAggregate(id="L-1", tenant_id="tenant-a", status=ListingState.ACTIVE, version=1)

    # Revision: Active -> Active
    manager_context = TransitionContext(
        actor=Actor(actor_id="mgr", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    ListingStateMachine.transition(entity, ListingState.ACTIVE, manager_context)
    assert entity.status == ListingState.ACTIVE
    assert entity.version == 2

    # Active -> STALE
    steward_context = TransitionContext(
        actor=Actor(actor_id="steward", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a")
    )
    ListingStateMachine.transition(entity, ListingState.STALE, steward_context)
    assert entity.status == ListingState.STALE

    # STALE -> ACTIVE
    ListingStateMachine.transition(entity, ListingState.ACTIVE, steward_context)
    assert entity.status == ListingState.ACTIVE

    # Active -> QUARANTINED
    ListingStateMachine.transition(entity, ListingState.QUARANTINED, steward_context)
    assert entity.status == ListingState.QUARANTINED


def test_listing_quarantine_segregation(base_context: TransitionContext) -> None:
    entity = ListingAggregate(id="L-1", tenant_id="tenant-a", status=ListingState.QUARANTINED, version=1)

    # Reopen quarantine without second actor (should fail)
    steward_context = TransitionContext(
        actor=Actor(actor_id="steward-1", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a")
    )
    with pytest.raises(DomainValidationError) as exc:
        ListingStateMachine.transition(entity, ListingState.ACTIVE, steward_context)
    assert exc.value.code == DenialCode.SECOND_ACTOR_REQUIRED

    # Reopen quarantine with second actor (should succeed)
    steward_2p = TransitionContext(
        actor=Actor(actor_id="steward-1", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a"),
        second_actor=Actor(actor_id="mgr-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    ListingStateMachine.transition(entity, ListingState.ACTIVE, steward_2p)
    assert entity.status == ListingState.ACTIVE


def test_listing_archiving_legal_hold(base_context: TransitionContext) -> None:
    entity = ListingAggregate(id="L-1", tenant_id="tenant-a", status=ListingState.ACTIVE, version=1, has_legal_hold=True)

    steward_context = TransitionContext(
        actor=Actor(actor_id="steward-1", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a")
    )
    # Archive should fail due to legal hold
    with pytest.raises(DomainValidationError) as exc:
        ListingStateMachine.transition(entity, ListingState.ARCHIVED, steward_context)
    assert exc.value.code == DenialCode.LEGAL_HOLD_CONFLICT


# --- 3. IDENTITY GRAPH DECISION TESTS ---

def test_identity_decision_flow(base_context: TransitionContext) -> None:
    entity = IdentityDecisionAggregate(
        id="DEC-1",
        tenant_id="tenant-a",
        status=IdentityGraphState.PROPOSED,
        version=1,
        proposer_id="steward-1",
        decision_type="MERGE"
    )

    # Propose -> PENDING_REVIEW
    steward_context = TransitionContext(
        actor=Actor(actor_id="steward-1", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a")
    )
    IdentityDecisionStateMachine.transition(entity, IdentityGraphState.PENDING_REVIEW, steward_context)
    assert entity.status == IdentityGraphState.PENDING_REVIEW

    # Approve (requires segregation - steward-1 cannot approve)
    manager_context = TransitionContext(
        actor=Actor(actor_id="steward-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    with pytest.raises(DomainValidationError) as exc:
        IdentityDecisionStateMachine.transition(entity, IdentityGraphState.APPROVED, manager_context)
    assert exc.value.code == DenialCode.SELF_REVIEW_DENIED

    # Different manager approves
    manager_2_context = TransitionContext(
        actor=Actor(actor_id="manager-2", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    IdentityDecisionStateMachine.transition(entity, IdentityGraphState.APPROVED, manager_2_context)
    assert entity.status == IdentityGraphState.APPROVED


# --- 4. ASSIGNMENT & SLA TESTS ---

def test_assignment_lifecycle(base_context: TransitionContext) -> None:
    assignment_repo = InMemoryAssignmentRepository()
    sla_repo = InMemorySlaRepository()
    service = AssignmentSlaService(assignment_repo, sla_repo)

    # 1. Create
    system_context = TransitionContext(actor=Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="tenant-a"))
    assignment = service.create_assignment("AS-1", "tenant-a", system_context)
    assert assignment.status == AssignmentState.UNASSIGNED

    # 2. Assign (requires manager/steward/SLA)
    manager_context = TransitionContext(actor=Actor(actor_id="mgr-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a"))
    due = datetime.now(UTC) + timedelta(hours=4)
    service.assign_task("AS-1", "staff-1", due, manager_context)
    assert assignment.status == AssignmentState.ASSIGNED
    assert assignment.assignee_id == "staff-1"

    # 3. Claim (requires principal == assignee)
    bad_staff_context = TransitionContext(actor=Actor(actor_id="staff-2", role=PrincipalRole.EXPANSION_STAFF, tenant_id="tenant-a"))
    with pytest.raises(DomainValidationError) as exc:
        service.claim_task("AS-1", bad_staff_context)
    assert exc.value.code == DenialCode.OWNERSHIP_REQUIRED

    staff_context = TransitionContext(actor=Actor(actor_id="staff-1", role=PrincipalRole.EXPANSION_STAFF, tenant_id="tenant-a"))
    service.claim_task("AS-1", staff_context)
    assert assignment.status == AssignmentState.CLAIMED
    assert assignment.owner_id == "staff-1"

    # 4. Transfer
    service.transfer_task("AS-1", "staff-3", staff_context)
    assert assignment.status == AssignmentState.TRANSFERRED
    assert assignment.assignee_id == "staff-3"

    # 5. Accept transfer (assignee accepts)
    staff_3_context = TransitionContext(actor=Actor(actor_id="staff-3", role=PrincipalRole.EXPANSION_STAFF, tenant_id="tenant-a"))
    service.accept_transfer("AS-1", staff_3_context)
    assert assignment.status == AssignmentState.ASSIGNED


def test_sla_derived_and_pause_behavior() -> None:
    assignment_repo = InMemoryAssignmentRepository()
    sla_repo = InMemorySlaRepository()
    service = AssignmentSlaService(assignment_repo, sla_repo)

    due = datetime.now(UTC) + timedelta(hours=4)
    system_context = TransitionContext(actor=Actor(actor_id="sys", role=PrincipalRole.SVC_SLA, tenant_id="tenant-a"))

    # 1. Create SLA
    sla = service.create_sla("SLA-1", "tenant-a", due, system_context)
    assert sla.status == SlaState.ON_TRACK

    # 2. Derive State: still on track
    eval_context_on_track = TransitionContext(
        actor=Actor(actor_id="sys", role=PrincipalRole.SVC_SLA, tenant_id="tenant-a"),
        current_time=datetime.now(UTC)
    )
    service.update_sla_derived_state("SLA-1", eval_context_on_track)
    assert sla.status == SlaState.ON_TRACK

    # 3. Derive State: close to due -> DUE_SOON
    eval_context_due_soon = TransitionContext(
        actor=Actor(actor_id="sys", role=PrincipalRole.SVC_SLA, tenant_id="tenant-a"),
        current_time=due - timedelta(minutes=90)
    )
    service.update_sla_derived_state("SLA-1", eval_context_due_soon)
    assert sla.status == SlaState.DUE_SOON

    # 4. Derive State: past due -> OVERDUE
    eval_context_overdue = TransitionContext(
        actor=Actor(actor_id="sys", role=PrincipalRole.SVC_SLA, tenant_id="tenant-a"),
        current_time=due + timedelta(minutes=10)
    )
    service.update_sla_derived_state("SLA-1", eval_context_overdue)
    assert sla.status == SlaState.OVERDUE

    # 5. Pause SLA
    manager_context = TransitionContext(actor=Actor(actor_id="mgr-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a"))
    service.pause_sla("SLA-1", "waiting on broker", due + timedelta(hours=2), manager_context)
    assert sla.status == SlaState.PAUSED
    assert sla.paused_reason == "waiting on broker"

    # Derived state check does not affect paused SLAs
    service.update_sla_derived_state("SLA-1", eval_context_overdue)
    assert sla.status == SlaState.PAUSED

    # 6. Resume SLA
    service.resume_sla("SLA-1", eval_context_overdue)
    # Since current time is past due, should resume as OVERDUE
    assert sla.status == SlaState.OVERDUE


# --- 5. CANDIDATE PROMOTION TESTS ---

def test_promotion_saga_flow() -> None:
    entity = PromotionAggregate(
        id="PROM-1",
        tenant_id="tenant-a",
        status=PromotionState.REQUESTED,
        version=1,
        proposer_id="staff-1"
    )

    sys_context = TransitionContext(actor=Actor(actor_id="sys", role=PrincipalRole.SVC_PROMOTION, tenant_id="tenant-a"))

    # Requested -> VALIDATING
    PromotionStateMachine.transition(entity, PromotionState.VALIDATING, sys_context)
    assert entity.status == PromotionState.VALIDATING

    # Validating -> APPROVED (segregation: mgr-1 approves staff-1 proposal)
    mgr_context = TransitionContext(
        actor=Actor(actor_id="mgr-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    PromotionStateMachine.transition(entity, PromotionState.APPROVED, mgr_context)
    assert entity.status == PromotionState.APPROVED

    # Approved -> Creating
    PromotionStateMachine.transition(entity, PromotionState.CANDIDATE_CREATING, sys_context)
    assert entity.status == PromotionState.CANDIDATE_CREATING

    # Creating -> Created
    PromotionStateMachine.transition(entity, PromotionState.CANDIDATE_CREATED, sys_context)
    assert entity.status == PromotionState.CANDIDATE_CREATED


def test_promotion_self_approval_denied() -> None:
    entity = PromotionAggregate(
        id="PROM-1",
        tenant_id="tenant-a",
        status=PromotionState.VALIDATING,
        version=1,
        proposer_id="mgr-1"  # proposer is manager
    )

    # manager-1 attempts to self-approve without second actor -> Denied
    mgr_context = TransitionContext(
        actor=Actor(actor_id="mgr-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    with pytest.raises(DomainValidationError) as exc:
        PromotionStateMachine.transition(entity, PromotionState.APPROVED, mgr_context)
    assert exc.value.code == DenialCode.SELF_REVIEW_DENIED

    # Different manager approves or manager-1 approves with second_actor
    mgr_context_2p = TransitionContext(
        actor=Actor(actor_id="mgr-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a"),
        second_actor=Actor(actor_id="mgr-2", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")
    )
    PromotionStateMachine.transition(entity, PromotionState.APPROVED, mgr_context_2p)
    assert entity.status == PromotionState.APPROVED
