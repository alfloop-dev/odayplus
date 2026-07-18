from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class IntakeStage(StrEnum):
    SUBMITTED = "SUBMITTED"
    CHECKING_IDENTITY = "CHECKING_IDENTITY"
    READY = "READY"
    CHECKING_SOURCE_POLICY = "CHECKING_SOURCE_POLICY"
    RETRIEVING = "RETRIEVING"
    AWAITING_ASSISTED_ENTRY = "AWAITING_ASSISTED_ENTRY"
    PARSING = "PARSING"
    MATCHING = "MATCHING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ListingState(StrEnum):
    ACTIVE = "ACTIVE"
    REMOVED = "REMOVED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"
    QUARANTINED = "QUARANTINED"
    ARCHIVED = "ARCHIVED"


class IdentityGraphState(StrEnum):
    PROPOSED = "PROPOSED"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    REVERSAL_PENDING = "REVERSAL_PENDING"
    REVERSED = "REVERSED"
    SUPERSEDED = "SUPERSEDED"


class AssignmentState(StrEnum):
    UNASSIGNED = "UNASSIGNED"
    ASSIGNED = "ASSIGNED"
    CLAIMED = "CLAIMED"
    TRANSFERRED = "TRANSFERRED"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"


class SlaState(StrEnum):
    ON_TRACK = "ON_TRACK"
    DUE_SOON = "DUE_SOON"
    OVERDUE = "OVERDUE"
    BREACHED = "BREACHED"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"


class PromotionState(StrEnum):
    REQUESTED = "REQUESTED"
    VALIDATING = "VALIDATING"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    CANDIDATE_CREATING = "CANDIDATE_CREATING"
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    SCORE_QUEUED = "SCORE_QUEUED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SCORE_FAILED = "SCORE_FAILED"


class PrincipalRole(StrEnum):
    EXPANSION_STAFF = "EXPANSION_STAFF"
    EXPANSION_MANAGER = "EXPANSION_MANAGER"
    DATA_STEWARD = "DATA_STEWARD"
    GOVERNANCE_REVIEWER = "GOVERNANCE_REVIEWER"
    PRIVACY_OFFICER = "PRIVACY_OFFICER"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    EMERGENCY_ADMIN = "EMERGENCY_ADMIN"

    # Service principals
    SVC_INTAKE = "SVC_INTAKE"
    SVC_RETRIEVAL = "SVC_RETRIEVAL"
    SVC_PARSER = "SVC_PARSER"
    SVC_MATCHER = "SVC_MATCHER"
    SVC_PROMOTION = "SVC_PROMOTION"
    SVC_SLA = "SVC_SLA"
    SVC_OUTBOX = "SVC_OUTBOX"
    SVC_RECONCILER = "SVC_RECONCILER"


class DenialCode(StrEnum):
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    ROLE_DENIED = "ROLE_DENIED"
    TENANT_SCOPE_DENIED = "TENANT_SCOPE_DENIED"
    SCOPE_DENIED = "SCOPE_DENIED"
    OWNERSHIP_REQUIRED = "OWNERSHIP_REQUIRED"
    ASSIGNMENT_SCOPE_DENIED = "ASSIGNMENT_SCOPE_DENIED"
    SOURCE_SCOPE_DENIED = "SOURCE_SCOPE_DENIED"
    FIELD_MASKED = "FIELD_MASKED"
    DATA_CLASSIFICATION_DENIED = "DATA_CLASSIFICATION_DENIED"
    PURPOSE_REQUIRED = "PURPOSE_REQUIRED"
    WORKFLOW_STATE_DENIED = "WORKFLOW_STATE_DENIED"
    SECOND_ACTOR_REQUIRED = "SECOND_ACTOR_REQUIRED"
    SELF_REVIEW_DENIED = "SELF_REVIEW_DENIED"
    RISK_ACKNOWLEDGEMENT_REQUIRED = "RISK_ACKNOWLEDGEMENT_REQUIRED"
    SOURCE_POLICY_DENIED = "SOURCE_POLICY_DENIED"
    LEGAL_HOLD_CONFLICT = "LEGAL_HOLD_CONFLICT"
    RESIDENCY_DENIED = "RESIDENCY_DENIED"
    EXPORT_APPROVAL_REQUIRED = "EXPORT_APPROVAL_REQUIRED"
    BREAK_GLASS_DENIED = "BREAK_GLASS_DENIED"


class DomainValidationError(Exception):
    def __init__(self, code: DenialCode, message: str) -> None:
        super().__init__(f"[{code.value}] {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: PrincipalRole
    tenant_id: str
    scopes: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class TransitionContext:
    actor: Actor
    idempotency_key: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    version_before: int | None = None
    risk_acknowledged: bool = False
    reason: str = ""
    second_actor: Actor | None = None
    purpose: str | None = None
    current_time: datetime = field(default_factory=lambda: datetime.now(UTC))


# Aggregates
@dataclass
class IntakeAggregate:
    id: str
    tenant_id: str
    stage: IntakeStage
    version: int
    created_by: str
    source_id: str
    canonical_url: str | None = None
    policy: str | None = None
    is_cancelled: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None
    owner_id: str | None = None  # Owner for submission ownership check


@dataclass
class ListingAggregate:
    id: str
    tenant_id: str
    status: ListingState
    version: int
    has_legal_hold: bool = False
    last_observed_at: datetime | None = None
    owner_id: str | None = None


@dataclass
class IdentityDecisionAggregate:
    id: str
    tenant_id: str
    status: IdentityGraphState
    version: int
    proposer_id: str | None = None
    reviewer_id: str | None = None
    decision_type: str = ""  # MERGE, SPLIT, UNMERGE, etc.


@dataclass
class AssignmentAggregate:
    id: str
    tenant_id: str
    status: AssignmentState
    version: int
    assignee_id: str | None = None
    owner_id: str | None = None
    due_at: datetime | None = None


@dataclass
class SlaInstanceAggregate:
    id: str
    tenant_id: str
    status: SlaState
    version: int
    due_at: datetime
    paused_reason: str | None = None
    resume_at: datetime | None = None


@dataclass
class PromotionAggregate:
    id: str
    tenant_id: str
    status: PromotionState
    version: int
    proposer_id: str | None = None
    approver_id: str | None = None


# State Transition Engines

class BaseStateMachine:
    @staticmethod
    def _validate_common(entity: Any, context: TransitionContext) -> None:
        # Tenant Isolation Check
        if context.actor.tenant_id != entity.tenant_id:
            raise DomainValidationError(
                DenialCode.TENANT_SCOPE_DENIED,
                f"Tenant isolation mismatch: Actor tenant {context.actor.tenant_id} "
                f"cannot access entity tenant {entity.tenant_id}."
            )

        # Concurrency / Version Check (If-Match requirement)
        if context.version_before is not None and entity.version != context.version_before:
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Version conflict: Expected version {context.version_before}, but got {entity.version}."
            )


class IntakeStateMachine(BaseStateMachine):
    """Enforces transitions of the Intake processing pipeline."""

    LEGAL_TRANSITIONS: dict[IntakeStage | None, set[IntakeStage]] = {
        None: {IntakeStage.SUBMITTED},
        IntakeStage.SUBMITTED: {IntakeStage.CHECKING_IDENTITY, IntakeStage.CANCELLED},
        IntakeStage.CHECKING_IDENTITY: {IntakeStage.READY, IntakeStage.CHECKING_SOURCE_POLICY},
        IntakeStage.CHECKING_SOURCE_POLICY: {
            IntakeStage.RETRIEVING,
            IntakeStage.AWAITING_ASSISTED_ENTRY,
            IntakeStage.QUARANTINED
        },
        IntakeStage.RETRIEVING: {IntakeStage.PARSING, IntakeStage.FAILED, IntakeStage.QUARANTINED},
        IntakeStage.AWAITING_ASSISTED_ENTRY: {IntakeStage.PARSING, IntakeStage.CANCELLED},
        IntakeStage.PARSING: {IntakeStage.MATCHING, IntakeStage.NEEDS_REVIEW, IntakeStage.FAILED},
        IntakeStage.MATCHING: {IntakeStage.READY, IntakeStage.NEEDS_REVIEW, IntakeStage.QUARANTINED},
        IntakeStage.NEEDS_REVIEW: {IntakeStage.READY, IntakeStage.QUARANTINED, IntakeStage.CANCELLED},
        IntakeStage.QUARANTINED: {IntakeStage.CHECKING_SOURCE_POLICY, IntakeStage.NEEDS_REVIEW},
        IntakeStage.FAILED: {IntakeStage.RETRIEVING, IntakeStage.PARSING},
        IntakeStage.CANCELLED: set(),
        IntakeStage.READY: set(),
    }

    @classmethod
    def transition(cls, entity: IntakeAggregate | None, target: IntakeStage, context: TransitionContext) -> None:
        source = entity.stage if entity else None

        # 1. Basic transition rule check
        if target not in cls.LEGAL_TRANSITIONS.get(source, set()):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Invalid intake transition from {source} to {target}."
            )

        if entity is None:
            # Creation (None -> SUBMITTED)
            # Permission check: Expansion staff, manager, steward, or SVC_INTAKE
            allowed_roles = {
                PrincipalRole.EXPANSION_STAFF,
                PrincipalRole.EXPANSION_MANAGER,
                PrincipalRole.DATA_STEWARD,
                PrincipalRole.SVC_INTAKE,
                PrincipalRole.EMERGENCY_ADMIN
            }
            if context.actor.role not in allowed_roles:
                raise DomainValidationError(
                    DenialCode.ROLE_DENIED,
                    f"Role {context.actor.role} is not authorized to submit intakes."
                )
            if not context.idempotency_key:
                raise DomainValidationError(
                    DenialCode.WORKFLOW_STATE_DENIED,
                    "Idempotency key is required for submitting an intake."
                )
            return

        # Common checks for existing entity (tenant and version check)
        cls._validate_common(entity, context)

        # 2. Transition-specific checks (Role, Precondition, etc.)
        role = context.actor.role

        if target == IntakeStage.CHECKING_IDENTITY:
            if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(
                    DenialCode.ROLE_DENIED,
                    f"Role {role} is not authorized to process intake identity check."
                )

        elif target == IntakeStage.READY:
            if source == IntakeStage.CHECKING_IDENTITY:
                if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(
                        DenialCode.ROLE_DENIED,
                        f"Role {role} is not authorized to resolve identity."
                    )
            elif source == IntakeStage.MATCHING:
                if role not in {PrincipalRole.SVC_MATCHER, PrincipalRole.DATA_STEWARD, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(
                        DenialCode.ROLE_DENIED,
                        f"Role {role} is not authorized to resolve match to READY."
                    )
            elif source == IntakeStage.NEEDS_REVIEW:
                # Needs independent review if high-risk or segregation rule applies
                if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(
                        DenialCode.ROLE_DENIED,
                        f"Role {role} is not authorized to decide review to READY."
                    )
                # Proposer-reviewer segregation
                if entity.owner_id and context.actor.actor_id == entity.owner_id:
                    raise DomainValidationError(
                        DenialCode.SELF_REVIEW_DENIED,
                        "Proposer cannot approve own submission."
                    )

        elif target == IntakeStage.CHECKING_SOURCE_POLICY:
            if source == IntakeStage.CHECKING_IDENTITY:
                if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Only system or admin can evaluate policy.")
            elif source == IntakeStage.QUARANTINED:
                if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Only steward or admin can reopen quarantine.")
                # Segregation for high risk
                if context.second_actor is None:
                    raise DomainValidationError(
                        DenialCode.SECOND_ACTOR_REQUIRED,
                        "Independent review/second actor required to reopen quarantine."
                    )

        elif target == IntakeStage.RETRIEVING:
            if source == IntakeStage.CHECKING_SOURCE_POLICY:
                if role not in {PrincipalRole.SVC_RETRIEVAL, PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Retrieval policy must be verified.")
            elif source == IntakeStage.FAILED:
                if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EXPANSION_STAFF, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Replay requires steward or owner staff.")
                if entity.owner_id and role == PrincipalRole.EXPANSION_STAFF and context.actor.actor_id != entity.owner_id:
                    raise DomainValidationError(DenialCode.OWNERSHIP_REQUIRED, "Staff can only replay own failures.")

        elif target == IntakeStage.AWAITING_ASSISTED_ENTRY:
            if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Fallback to assisted entry must be system-initiated.")

        elif target == IntakeStage.QUARANTINED:
            # Section 2 transitions to Quarantined
            if source == IntakeStage.CHECKING_SOURCE_POLICY:
                if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Policy service must flag quarantine.")
            elif source == IntakeStage.RETRIEVING:
                if role not in {PrincipalRole.SVC_RETRIEVAL, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Retrieval service must flag quarantine.")
            elif source == IntakeStage.MATCHING:
                if role not in {PrincipalRole.SVC_MATCHER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Matcher or steward must flag quarantine.")
            elif source == IntakeStage.NEEDS_REVIEW:
                if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Manager or steward must flag quarantine.")

        elif target == IntakeStage.PARSING:
            if source == IntakeStage.RETRIEVING:
                if role not in {PrincipalRole.SVC_RETRIEVAL, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Only retrieval worker can start parsing.")
            elif source == IntakeStage.AWAITING_ASSISTED_ENTRY:
                if role not in {PrincipalRole.EXPANSION_STAFF, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Operator correction required for parsing.")
                # Ownership constraint: own submission or assigned area
                if entity.owner_id and role == PrincipalRole.EXPANSION_STAFF and context.actor.actor_id != entity.owner_id:
                    raise DomainValidationError(DenialCode.OWNERSHIP_REQUIRED, "Only submitter or assignee can correct.")
            elif source == IntakeStage.FAILED:
                if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Only steward can replay parsing failure.")

        elif target == IntakeStage.MATCHING:
            if role not in {PrincipalRole.SVC_PARSER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only parser can initiate matching.")

        elif target == IntakeStage.NEEDS_REVIEW:
            if source == IntakeStage.PARSING:
                if role not in {PrincipalRole.SVC_PARSER, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Parser worker must trigger review routing.")
            elif source == IntakeStage.MATCHING:
                if role not in {PrincipalRole.SVC_MATCHER, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Matcher must route review.")
            elif source == IntakeStage.QUARANTINED:
                if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Steward must reopen quarantine to review.")

        elif target == IntakeStage.FAILED:
            if role not in {PrincipalRole.SVC_RETRIEVAL, PrincipalRole.SVC_PARSER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only active workers can fail jobs.")

        elif target == IntakeStage.CANCELLED:
            if role not in {PrincipalRole.EXPANSION_STAFF, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Cancellation unauthorized.")
            # Staff can only cancel their own submission
            if role == PrincipalRole.EXPANSION_STAFF and entity.owner_id and context.actor.actor_id != entity.owner_id:
                raise DomainValidationError(DenialCode.OWNERSHIP_REQUIRED, "Staff can only cancel own submissions.")

        # Update entity stage
        entity.stage = target
        entity.version += 1


class ListingStateMachine(BaseStateMachine):
    """Enforces transitions of the Listing lifecycle."""

    LEGAL_TRANSITIONS: dict[ListingState | None, set[ListingState]] = {
        None: {ListingState.ACTIVE},
        ListingState.ACTIVE: {
            ListingState.ACTIVE,
            ListingState.REMOVED,
            ListingState.EXPIRED,
            ListingState.STALE,
            ListingState.QUARANTINED,
            ListingState.ARCHIVED
        },
        ListingState.REMOVED: {ListingState.ACTIVE, ListingState.ARCHIVED},
        ListingState.EXPIRED: {ListingState.ACTIVE, ListingState.ARCHIVED},
        ListingState.STALE: {ListingState.ACTIVE, ListingState.ARCHIVED},
        ListingState.QUARANTINED: {ListingState.ACTIVE, ListingState.ARCHIVED},
        ListingState.ARCHIVED: set(),
    }

    @classmethod
    def transition(cls, entity: ListingAggregate | None, target: ListingState, context: TransitionContext) -> None:
        source = entity.status if entity else None

        if target not in cls.LEGAL_TRANSITIONS.get(source, set()):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Invalid listing transition from {source} to {target}."
            )

        if entity is None:
            # Creation (None -> ACTIVE)
            if context.actor.role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only intake service or manager can create listings.")
            return

        cls._validate_common(entity, context)
        role = context.actor.role

        if target == ListingState.ACTIVE:
            if source == ListingState.ACTIVE:  # Revision
                if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Revision requires intake or manager.")
            elif source in {ListingState.REMOVED, ListingState.EXPIRED, ListingState.STALE}:
                if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Reactivating requires intake/manager/steward.")
            elif source == ListingState.QUARANTINED:
                if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Quarantine release requires data steward.")
                if context.second_actor is None:
                    raise DomainValidationError(
                        DenialCode.SECOND_ACTOR_REQUIRED,
                        "Independent manager or second actor approval required to release listing quarantine."
                    )

        elif target in {ListingState.REMOVED, ListingState.EXPIRED, ListingState.STALE}:
            if role not in {PrincipalRole.SVC_RETRIEVAL, PrincipalRole.SVC_RECONCILER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Observation/expire requires service or steward.")

        elif target == ListingState.QUARANTINED:
            if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.PRIVACY_OFFICER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Quarantine requires manager/steward/privacy.")

        elif target == ListingState.ARCHIVED:
            if entity.has_legal_hold:
                raise DomainValidationError(DenialCode.LEGAL_HOLD_CONFLICT, "Listing is under legal hold and cannot be archived.")
            if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.GOVERNANCE_REVIEWER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Archiving requires records manager/governance.")

        entity.status = target
        entity.version += 1


class IdentityDecisionStateMachine(BaseStateMachine):
    """Enforces transitions of identity graph decisions."""

    LEGAL_TRANSITIONS: dict[IdentityGraphState | None, set[IdentityGraphState]] = {
        None: {IdentityGraphState.PROPOSED},
        IdentityGraphState.PROPOSED: {IdentityGraphState.APPROVED, IdentityGraphState.PENDING_REVIEW},
        IdentityGraphState.PENDING_REVIEW: {IdentityGraphState.APPROVED, IdentityGraphState.REJECTED},
        IdentityGraphState.APPROVED: {IdentityGraphState.EXECUTING, IdentityGraphState.SUPERSEDED},
        IdentityGraphState.EXECUTING: {IdentityGraphState.EXECUTED, IdentityGraphState.FAILED},
        IdentityGraphState.EXECUTED: {IdentityGraphState.REVERSAL_PENDING},
        IdentityGraphState.REVERSAL_PENDING: {IdentityGraphState.REVERSED, IdentityGraphState.REJECTED},
        IdentityGraphState.FAILED: {IdentityGraphState.PENDING_REVIEW},
        IdentityGraphState.REJECTED: set(),
        IdentityGraphState.REVERSED: set(),
        IdentityGraphState.SUPERSEDED: set(),
    }

    @classmethod
    def transition(cls, entity: IdentityDecisionAggregate | None, target: IdentityGraphState, context: TransitionContext) -> None:
        source = entity.status if entity else None

        if target not in cls.LEGAL_TRANSITIONS.get(source, set()):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Invalid identity decision transition from {source} to {target}."
            )

        if entity is None:
            if context.actor.role not in {PrincipalRole.SVC_MATCHER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only matcher or steward can propose identity cases.")
            return

        cls._validate_common(entity, context)
        role = context.actor.role

        if target == IdentityGraphState.APPROVED:
            if source == IdentityGraphState.PROPOSED:
                if role not in {PrincipalRole.SVC_MATCHER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Only system or steward can approve exact matches.")
            elif source == IdentityGraphState.PENDING_REVIEW:
                if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Approval requires manager or steward.")
                # Proposer / Reviewer segregation
                if entity.proposer_id and context.actor.actor_id == entity.proposer_id:
                    raise DomainValidationError(
                        DenialCode.SELF_REVIEW_DENIED,
                        "Proposer cannot approve own identity decision."
                    )

        elif target == IdentityGraphState.PENDING_REVIEW:
            if source == IdentityGraphState.PROPOSED:
                if role not in {PrincipalRole.SVC_MATCHER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Only matcher or steward can route to review.")
            elif source == IdentityGraphState.FAILED:
                if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Steward must retry failed executions.")

        elif target == IdentityGraphState.REJECTED:
            if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only manager or steward can reject.")

        elif target == IdentityGraphState.EXECUTING:
            if role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.SVC_PROMOTION, PrincipalRole.SVC_RECONCILER, PrincipalRole.EMERGENCY_ADMIN}:
                # SVC_INTAKE or similar service executor
                pass

        elif target == IdentityGraphState.EXECUTED:
            # Automatically completed by identity service
            pass

        elif target == IdentityGraphState.REVERSAL_PENDING:
            if role not in {PrincipalRole.DATA_STEWARD, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only steward or manager can request reversal.")

        elif target == IdentityGraphState.REVERSED:
            if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only manager or steward can approve reversal.")
            if context.second_actor is None:
                raise DomainValidationError(
                    DenialCode.SECOND_ACTOR_REQUIRED,
                    "Reversal approval requires second actor review."
                )

        entity.status = target
        entity.version += 1


class AssignmentStateMachine(BaseStateMachine):
    """Enforces transitions of workflow task assignments."""

    LEGAL_TRANSITIONS: dict[AssignmentState | None, set[AssignmentState]] = {
        None: {AssignmentState.UNASSIGNED},
        AssignmentState.UNASSIGNED: {AssignmentState.ASSIGNED},
        AssignmentState.ASSIGNED: {
            AssignmentState.CLAIMED,
            AssignmentState.TRANSFERRED,
            AssignmentState.ESCALATED
        },
        AssignmentState.CLAIMED: {
            AssignmentState.TRANSFERRED,
            AssignmentState.ESCALATED,
            AssignmentState.COMPLETED
        },
        AssignmentState.TRANSFERRED: {AssignmentState.ASSIGNED},
        AssignmentState.ESCALATED: {AssignmentState.CLAIMED, AssignmentState.COMPLETED},
        AssignmentState.COMPLETED: set(),
    }

    @classmethod
    def transition(cls, entity: AssignmentAggregate | None, target: AssignmentState, context: TransitionContext) -> None:
        source = entity.status if entity else None

        if target not in cls.LEGAL_TRANSITIONS.get(source, set()):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Invalid assignment transition from {source} to {target}."
            )

        if entity is None:
            if context.actor.role not in {PrincipalRole.SVC_INTAKE, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only system can initialize assignments.")
            return

        cls._validate_common(entity, context)
        role = context.actor.role

        if target == AssignmentState.ASSIGNED:
            if source == AssignmentState.UNASSIGNED:
                if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.SVC_SLA, PrincipalRole.EMERGENCY_ADMIN}:
                    raise DomainValidationError(DenialCode.ROLE_DENIED, "Only manager, steward, or SLA router can assign.")
            elif source == AssignmentState.TRANSFERRED:
                # Accept transfer: target owner must accept
                if entity.assignee_id and context.actor.actor_id != entity.assignee_id:
                    raise DomainValidationError(DenialCode.OWNERSHIP_REQUIRED, "Only target assignee can accept transfer.")

        elif target == AssignmentState.CLAIMED:
            if role not in {PrincipalRole.EXPANSION_STAFF, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only human operator can claim.")
            # Can only claim if you are the assignee
            if entity.assignee_id and context.actor.actor_id != entity.assignee_id:
                raise DomainValidationError(
                    DenialCode.OWNERSHIP_REQUIRED,
                    "Only assigned user can claim this task."
                )

        elif target == AssignmentState.TRANSFERRED:
            # Transfer: only current assignee or manager can transfer
            if role != PrincipalRole.EXPANSION_MANAGER and entity.assignee_id and context.actor.actor_id != entity.assignee_id:
                raise DomainValidationError(DenialCode.OWNERSHIP_REQUIRED, "Only assignee or manager can transfer.")

        elif target == AssignmentState.ESCALATED:
            if role not in {PrincipalRole.SVC_SLA, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only SLA service or manager can escalate.")

        elif target == AssignmentState.COMPLETED:
            # Completion requires assignee to be the performer or manager
            if role != PrincipalRole.EXPANSION_MANAGER and entity.assignee_id and context.actor.actor_id != entity.assignee_id:
                raise DomainValidationError(DenialCode.OWNERSHIP_REQUIRED, "Only assignee or manager can complete.")

        entity.status = target
        entity.version += 1


class SlaStateMachine(BaseStateMachine):
    """Enforces state changes of the SLA tracker."""

    LEGAL_TRANSITIONS: dict[SlaState | None, set[SlaState]] = {
        None: {SlaState.ON_TRACK},
        SlaState.ON_TRACK: {SlaState.DUE_SOON, SlaState.OVERDUE, SlaState.BREACHED, SlaState.COMPLETED, SlaState.PAUSED},
        SlaState.DUE_SOON: {SlaState.OVERDUE, SlaState.BREACHED, SlaState.COMPLETED, SlaState.PAUSED},
        SlaState.OVERDUE: {SlaState.BREACHED, SlaState.COMPLETED, SlaState.PAUSED},
        SlaState.BREACHED: {SlaState.COMPLETED},
        SlaState.PAUSED: {SlaState.ON_TRACK, SlaState.DUE_SOON, SlaState.OVERDUE, SlaState.BREACHED, SlaState.COMPLETED},
        SlaState.COMPLETED: set(),
    }

    @classmethod
    def transition(cls, entity: SlaInstanceAggregate | None, target: SlaState, context: TransitionContext) -> None:
        source = entity.status if entity else None

        if target not in cls.LEGAL_TRANSITIONS.get(source, set()):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Invalid SLA transition from {source} to {target}."
            )

        if entity is None:
            if context.actor.role not in {PrincipalRole.SVC_SLA, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only SLA service can initialize SLA tracking.")
            return

        cls._validate_common(entity, context)
        role = context.actor.role

        if role not in {PrincipalRole.SVC_SLA, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.EMERGENCY_ADMIN}:
            # Human updates (e.g. pausing) must be manager or admin
            if target == SlaState.PAUSED:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Only manager can pause SLA.")

        entity.status = target
        entity.version += 1


class PromotionStateMachine(BaseStateMachine):
    """Enforces transitions of the candidate promotion saga."""

    LEGAL_TRANSITIONS: dict[PromotionState | None, set[PromotionState]] = {
        None: {PromotionState.REQUESTED},
        PromotionState.REQUESTED: {PromotionState.VALIDATING},
        PromotionState.VALIDATING: {PromotionState.REJECTED, PromotionState.APPROVED},
        PromotionState.APPROVED: {PromotionState.CANDIDATE_CREATING},
        PromotionState.CANDIDATE_CREATING: {PromotionState.CANDIDATE_CREATED, PromotionState.FAILED},
        PromotionState.CANDIDATE_CREATED: {PromotionState.SCORE_QUEUED},
        PromotionState.SCORE_QUEUED: {PromotionState.COMPLETED, PromotionState.SCORE_FAILED},
        PromotionState.SCORE_FAILED: {PromotionState.SCORE_QUEUED},  # retry scoring
        PromotionState.FAILED: {PromotionState.CANDIDATE_CREATING},  # retry candidate creation
        PromotionState.COMPLETED: set(),
        PromotionState.REJECTED: set(),
    }

    @classmethod
    def transition(cls, entity: PromotionAggregate | None, target: PromotionState, context: TransitionContext) -> None:
        source = entity.status if entity else None

        if target not in cls.LEGAL_TRANSITIONS.get(source, set()):
            raise DomainValidationError(
                DenialCode.WORKFLOW_STATE_DENIED,
                f"Invalid promotion transition from {source} to {target}."
            )

        if entity is None:
            # Creation (None -> REQUESTED)
            if context.actor.role not in {PrincipalRole.EXPANSION_STAFF, PrincipalRole.EXPANSION_MANAGER, PrincipalRole.DATA_STEWARD, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Proposing promotion requires staff, manager, or steward.")
            return

        cls._validate_common(entity, context)
        role = context.actor.role

        if target == PromotionState.VALIDATING:
            if role not in {PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Validation must be system-initiated.")

        elif target == PromotionState.APPROVED:
            if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Approval requires expansion manager.")
            # Proposer/Reviewer segregation
            if entity.proposer_id and context.actor.actor_id == entity.proposer_id:
                if context.second_actor is None:
                    raise DomainValidationError(
                        DenialCode.SELF_REVIEW_DENIED,
                        "Manager cannot self-approve own proposed promotion without independent second actor."
                    )

        elif target == PromotionState.REJECTED:
            if role not in {PrincipalRole.EXPANSION_MANAGER, PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Rejection requires manager or promotion service.")

        elif target == PromotionState.CANDIDATE_CREATING:
            if role not in {PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "Candidate creation must be service-initiated.")

        elif target == PromotionState.CANDIDATE_CREATED:
            if role not in {PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "System must flag candidate created.")

        elif target == PromotionState.SCORE_QUEUED:
            if role not in {PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "System must enqueue score job.")

        elif target == PromotionState.COMPLETED:
            if role not in {PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "System must complete promotion saga.")

        elif target == PromotionState.SCORE_FAILED:
            if role not in {PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "System must report score failure.")

        elif target == PromotionState.FAILED:
            if role not in {PrincipalRole.SVC_PROMOTION, PrincipalRole.EMERGENCY_ADMIN}:
                raise DomainValidationError(DenialCode.ROLE_DENIED, "System must report creation failure.")

        entity.status = target
        entity.version += 1
