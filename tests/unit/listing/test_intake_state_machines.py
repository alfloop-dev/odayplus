from __future__ import annotations

from datetime import UTC, datetime

import pytest

from modules.listing.domain.intake_states import (
    Actor,
    AssignmentAggregate,
    AssignmentState,
    AssignmentStateMachine,
    DenialCode,
    DomainValidationError,
    IdentityDecisionAggregate,
    IdentityDecisionStateMachine,
    IdentityGraphState,
    IntakeAggregate,
    IntakeStage,
    IntakeStateMachine,
    ListingAggregate,
    ListingState,
    ListingStateMachine,
    PrincipalRole,
    PromotionAggregate,
    PromotionState,
    PromotionStateMachine,
    SlaInstanceAggregate,
    SlaState,
    SlaStateMachine,
    TransitionContext,
)


@pytest.fixture
def manager_actor() -> Actor:
    return Actor(actor_id="mgr-1", role=PrincipalRole.EXPANSION_MANAGER, tenant_id="tenant-a")


@pytest.fixture
def staff_actor() -> Actor:
    return Actor(actor_id="staff-1", role=PrincipalRole.EXPANSION_STAFF, tenant_id="tenant-a")


@pytest.fixture
def steward_actor() -> Actor:
    return Actor(actor_id="steward-1", role=PrincipalRole.DATA_STEWARD, tenant_id="tenant-a")


@pytest.fixture
def sys_actor() -> Actor:
    return Actor(actor_id="sys", role=PrincipalRole.SVC_INTAKE, tenant_id="tenant-a")


# --- Unit Tests for Intake Stage Machine ---


@pytest.mark.parametrize("role", [PrincipalRole.SVC_INTAKE, PrincipalRole.EXPANSION_STAFF,
                                  PrincipalRole.DATA_STEWARD, PrincipalRole.SVC_RETRIEVAL])
def test_source_authorization_wait_is_a_policy_service_decision(role):
    entity = IntakeAggregate(id="synthetic-auth-intake", tenant_id="tenant-a",
        stage=IntakeStage.CHECKING_SOURCE_POLICY, version=3, created_by="synthetic-author", source_id="synthetic-source")
    context = TransitionContext(actor=Actor("synthetic-actor", role, "tenant-a"), version_before=3)
    if role == PrincipalRole.SVC_INTAKE:
        IntakeStateMachine.transition(entity, IntakeStage.AWAITING_SOURCE_AUTHORIZATION, context)
        assert entity.stage == IntakeStage.AWAITING_SOURCE_AUTHORIZATION and entity.version == 4
    else:
        with pytest.raises(DomainValidationError) as error:
            IntakeStateMachine.transition(entity, IntakeStage.AWAITING_SOURCE_AUTHORIZATION, context)
        assert error.value.code == DenialCode.ROLE_DENIED
        assert entity.stage == IntakeStage.CHECKING_SOURCE_POLICY and entity.version == 3


@pytest.mark.parametrize("target", [IntakeStage.RETRIEVING, IntakeStage.AWAITING_ASSISTED_ENTRY, IntakeStage.PARSING])
def test_waiting_for_source_authorization_is_never_a_direct_access_grant(target, sys_actor):
    entity = IntakeAggregate(id="synthetic-auth-intake", tenant_id="tenant-a",
        stage=IntakeStage.AWAITING_SOURCE_AUTHORIZATION, version=4, created_by="synthetic-author", source_id="synthetic-source")
    with pytest.raises(DomainValidationError) as error:
        IntakeStateMachine.transition(entity, target, TransitionContext(actor=sys_actor, version_before=4))
    assert error.value.code == DenialCode.WORKFLOW_STATE_DENIED
    assert entity.stage == IntakeStage.AWAITING_SOURCE_AUTHORIZATION and entity.version == 4


def test_intake_cannot_transition_from_terminal_ready(manager_actor: Actor) -> None:
    entity = IntakeAggregate(
        id="IN-1",
        tenant_id="tenant-a",
        stage=IntakeStage.READY,
        version=1,
        created_by="staff-1",
        source_id="src-1",
    )
    context = TransitionContext(actor=manager_actor)
    # READY is a terminal state, no further transitions allowed from it
    with pytest.raises(DomainValidationError) as exc:
        IntakeStateMachine.transition(entity, IntakeStage.SUBMITTED, context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED


def test_intake_cannot_transition_from_terminal_cancelled(manager_actor: Actor) -> None:
    entity = IntakeAggregate(
        id="IN-1",
        tenant_id="tenant-a",
        stage=IntakeStage.CANCELLED,
        version=1,
        created_by="staff-1",
        source_id="src-1",
    )
    context = TransitionContext(actor=manager_actor)
    # CANCELLED is a terminal state
    with pytest.raises(DomainValidationError) as exc:
        IntakeStateMachine.transition(entity, IntakeStage.READY, context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED


def test_intake_idempotency_requirement_on_submission(manager_actor: Actor) -> None:
    context = TransitionContext(actor=manager_actor, idempotency_key=None)
    # Submitting requires an idempotency key
    with pytest.raises(DomainValidationError) as exc:
        IntakeStateMachine.transition(None, IntakeStage.SUBMITTED, context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED


def test_intake_staff_ownership_on_cancellation(staff_actor: Actor) -> None:
    entity = IntakeAggregate(
        id="IN-1",
        tenant_id="tenant-a",
        stage=IntakeStage.SUBMITTED,
        version=1,
        created_by="staff-2",  # Submitted by staff-2
        source_id="src-1",
        owner_id="staff-2",
    )
    # staff-1 attempts to cancel staff-2's submission -> Denied!
    context = TransitionContext(actor=staff_actor)
    with pytest.raises(DomainValidationError) as exc:
        IntakeStateMachine.transition(entity, IntakeStage.CANCELLED, context)
    assert exc.value.code == DenialCode.OWNERSHIP_REQUIRED


# --- Unit Tests for Listing Lifecycle ---


def test_listing_archived_is_terminal(steward_actor: Actor) -> None:
    entity = ListingAggregate(
        id="L-1", tenant_id="tenant-a", status=ListingState.ARCHIVED, version=1
    )
    context = TransitionContext(actor=steward_actor)
    with pytest.raises(DomainValidationError) as exc:
        ListingStateMachine.transition(entity, ListingState.ACTIVE, context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED


def test_listing_steward_role_required_for_archive(staff_actor: Actor) -> None:
    entity = ListingAggregate(id="L-1", tenant_id="tenant-a", status=ListingState.ACTIVE, version=1)
    context = TransitionContext(actor=staff_actor)
    # Expansion staff role is not allowed to archive
    with pytest.raises(DomainValidationError) as exc:
        ListingStateMachine.transition(entity, ListingState.ARCHIVED, context)
    assert exc.value.code == DenialCode.ROLE_DENIED


# --- Unit Tests for Identity Resolution Decisions ---


def test_identity_decision_rejected_is_terminal(steward_actor: Actor) -> None:
    entity = IdentityDecisionAggregate(
        id="DEC-1", tenant_id="tenant-a", status=IdentityGraphState.REJECTED, version=1
    )
    context = TransitionContext(actor=steward_actor)
    with pytest.raises(DomainValidationError) as exc:
        IdentityDecisionStateMachine.transition(entity, IdentityGraphState.APPROVED, context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED


def test_identity_decision_failed_to_pending_review(steward_actor: Actor) -> None:
    entity = IdentityDecisionAggregate(
        id="DEC-1", tenant_id="tenant-a", status=IdentityGraphState.FAILED, version=1
    )
    context = TransitionContext(actor=steward_actor)
    IdentityDecisionStateMachine.transition(entity, IdentityGraphState.PENDING_REVIEW, context)
    assert entity.status == IdentityGraphState.PENDING_REVIEW


# --- Unit Tests for Assignments ---


def test_assignment_completed_is_terminal(manager_actor: Actor) -> None:
    entity = AssignmentAggregate(
        id="AS-1", tenant_id="tenant-a", status=AssignmentState.COMPLETED, version=1
    )
    context = TransitionContext(actor=manager_actor)
    with pytest.raises(DomainValidationError) as exc:
        AssignmentStateMachine.transition(entity, AssignmentState.ASSIGNED, context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED


# --- Unit Tests for SLA Tracker ---


def test_sla_paused_requires_manager_role(staff_actor: Actor) -> None:
    entity = SlaInstanceAggregate(
        id="SLA-1",
        tenant_id="tenant-a",
        status=SlaState.ON_TRACK,
        version=1,
        due_at=datetime.now(UTC),
    )
    context = TransitionContext(actor=staff_actor)
    # Staff cannot pause SLA
    with pytest.raises(DomainValidationError) as exc:
        SlaStateMachine.transition(entity, SlaState.PAUSED, context)
    assert exc.value.code == DenialCode.ROLE_DENIED


# --- Unit Tests for Candidate Promotion Saga ---


def test_promotion_completed_is_terminal(sys_actor: Actor) -> None:
    entity = PromotionAggregate(
        id="PROM-1", tenant_id="tenant-a", status=PromotionState.COMPLETED, version=1
    )
    context = TransitionContext(actor=sys_actor)
    with pytest.raises(DomainValidationError) as exc:
        PromotionStateMachine.transition(entity, PromotionState.VALIDATING, context)
    assert exc.value.code == DenialCode.WORKFLOW_STATE_DENIED
