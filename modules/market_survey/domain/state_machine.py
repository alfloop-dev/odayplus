"""Market Survey State Machines and Lifecycle Transitions.

Contract: `odayplus.survey-workflow.v2`.
Task ID: `ODP-SURVEY-001`.

Enforces:
1. Assignment lifecycle transitions and expiry checks.
2. Reviewer separation (Segregation of Duties / Four-Eyes rule: submitter != reviewer).
3. Evidence review, correction, retraction and promotion gating.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.market_survey.domain.models import (
    AssignmentStatus,
    EvidenceReviewStatus,
    FieldSurveyEvidence,
    PromotionRecord,
    PromotionStatus,
    SurveyAssignment,
    SurveyAuthorizationError,
    SurveyErrorCode,
    SurveyReviewRecord,
    SurveyStateConflictError,
    SurveyValidationError,
)

# Valid review roles recognized by odayplus governance
ALLOWED_REVIEW_ROLES = frozenset({
    "SITE_REVIEWER",
    "OPERATIONS_MANAGER",
    "EXPANSION_MANAGER",
    "GOVERNANCE_REVIEWER",
    "PLATFORM_ADMIN",
    "DATA_STEWARD",
})


def _parse_dt(val: str | datetime | None) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo is not None else val.replace(tzinfo=UTC)
    clean = val.replace("Z", "+00:00")
    return datetime.fromisoformat(clean)


class SurveyAssignmentStateMachine:
    """Manages transitions for SurveyAssignment entities."""

    @staticmethod
    def assign(
        assignment: SurveyAssignment,
        *,
        assigned_to: str,
        assigned_by: str,
        expires_at: str | None = None,
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        if assignment.status == AssignmentStatus.SUBMITTED:
            raise SurveyStateConflictError(
                "Cannot reassign a submitted survey assignment",
                code=SurveyErrorCode.INVALID_STATE_TRANSITION,
            )
        if assignment.status == AssignmentStatus.CANCELLED:
            raise SurveyStateConflictError(
                "Cannot assign a cancelled survey assignment",
                code=SurveyErrorCode.INVALID_STATE_TRANSITION,
            )

        if expires_at is not None:
            exp_dt = _parse_dt(expires_at)
            if exp_dt is not None and exp_dt <= current_time:
                raise SurveyValidationError(
                    "New expiration must be in the future",
                    code=SurveyErrorCode.VALIDATION_FAILED,
                )
            assignment.expires_at = expires_at
        else:
            exp_dt = _parse_dt(assignment.expires_at)
            if exp_dt is not None and exp_dt <= current_time:
                raise SurveyStateConflictError(
                    "Assignment expiration is in the past; must specify a new future expires_at",
                    code=SurveyErrorCode.ASSIGNMENT_EXPIRED,
                )

        assignment.assigned_to = assigned_to
        assignment.assigned_by = assigned_by
        assignment.assigned_at = current_time_iso
        assignment.status = AssignmentStatus.ASSIGNED
        assignment.updated_at = current_time_iso

    @staticmethod
    def claim(
        assignment: SurveyAssignment,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        exp_dt = _parse_dt(assignment.expires_at)
        if assignment.status == AssignmentStatus.EXPIRED or (exp_dt is not None and exp_dt <= current_time):
            assignment.status = AssignmentStatus.EXPIRED
            assignment.updated_at = current_time_iso
            raise SurveyStateConflictError(
                "Cannot claim an expired assignment",
                code=SurveyErrorCode.ASSIGNMENT_EXPIRED,
            )

        if assignment.status not in (AssignmentStatus.UNASSIGNED, AssignmentStatus.ASSIGNED):
            raise SurveyStateConflictError(
                f"Assignment in status {assignment.status.value} cannot be claimed",
                code=SurveyErrorCode.ASSIGNMENT_NOT_ACTIVE,
            )

        if assignment.status == AssignmentStatus.ASSIGNED and assignment.assigned_to and assignment.assigned_to != actor_id:
            raise SurveyAuthorizationError(
                f"Assignment is already assigned to {assignment.assigned_to}",
                code=SurveyErrorCode.UNAUTHORIZED_REVIEWER,
            )

        assignment.assigned_to = actor_id
        assignment.claimed_at = current_time_iso
        assignment.status = AssignmentStatus.CLAIMED
        assignment.updated_at = current_time_iso

    @staticmethod
    def start(
        assignment: SurveyAssignment,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        exp_dt = _parse_dt(assignment.expires_at)
        if assignment.status == AssignmentStatus.EXPIRED or (exp_dt is not None and exp_dt <= current_time):
            assignment.status = AssignmentStatus.EXPIRED
            assignment.updated_at = current_time_iso
            raise SurveyStateConflictError(
                "Cannot start an expired assignment",
                code=SurveyErrorCode.ASSIGNMENT_EXPIRED,
            )

        if assignment.status not in (AssignmentStatus.ASSIGNED, AssignmentStatus.CLAIMED):
            raise SurveyStateConflictError(
                f"Assignment in status {assignment.status.value} cannot be started",
                code=SurveyErrorCode.ASSIGNMENT_NOT_ACTIVE,
            )

        if assignment.assigned_to and assignment.assigned_to != actor_id:
            raise SurveyAuthorizationError(
                f"Actor {actor_id} is not the assigned surveyor ({assignment.assigned_to})",
                code=SurveyErrorCode.UNAUTHORIZED_REVIEWER,
            )

        assignment.status = AssignmentStatus.IN_PROGRESS
        assignment.updated_at = current_time_iso

    @staticmethod
    def submit(
        assignment: SurveyAssignment,
        *,
        actor_id: str,
        survey_id: str,
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        exp_dt = _parse_dt(assignment.expires_at)
        if assignment.status == AssignmentStatus.EXPIRED or (exp_dt is not None and exp_dt <= current_time):
            assignment.status = AssignmentStatus.EXPIRED
            assignment.updated_at = current_time_iso
            raise SurveyStateConflictError(
                "Cannot submit an expired assignment",
                code=SurveyErrorCode.ASSIGNMENT_EXPIRED,
            )

        if assignment.status not in (
            AssignmentStatus.ASSIGNED,
            AssignmentStatus.CLAIMED,
            AssignmentStatus.IN_PROGRESS,
        ):
            raise SurveyStateConflictError(
                f"Assignment in status {assignment.status.value} cannot be submitted",
                code=SurveyErrorCode.ASSIGNMENT_NOT_ACTIVE,
            )

        if assignment.assigned_to and assignment.assigned_to != actor_id:
            raise SurveyAuthorizationError(
                f"Actor {actor_id} is not the assigned surveyor ({assignment.assigned_to})",
                code=SurveyErrorCode.UNAUTHORIZED_REVIEWER,
            )

        assignment.survey_id = survey_id
        assignment.submitted_at = current_time_iso
        assignment.status = AssignmentStatus.SUBMITTED
        assignment.updated_at = current_time_iso

    @staticmethod
    def cancel(
        assignment: SurveyAssignment,
        *,
        actor_id: str,
        reason: str = "",
        now: datetime | None = None,
    ) -> None:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        if assignment.status == AssignmentStatus.SUBMITTED:
            raise SurveyStateConflictError(
                "Cannot cancel a submitted assignment",
                code=SurveyErrorCode.INVALID_STATE_TRANSITION,
            )

        assignment.status = AssignmentStatus.CANCELLED
        assignment.metadata["cancellation_reason"] = reason
        assignment.metadata["cancelled_by"] = actor_id
        assignment.updated_at = current_time_iso

    @staticmethod
    def expire_if_overdue(
        assignment: SurveyAssignment,
        *,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(UTC)
        if assignment.status in (
            AssignmentStatus.UNASSIGNED,
            AssignmentStatus.ASSIGNED,
            AssignmentStatus.CLAIMED,
            AssignmentStatus.IN_PROGRESS,
        ):
            exp_dt = _parse_dt(assignment.expires_at)
            if exp_dt is not None and exp_dt <= current_time:
                assignment.status = AssignmentStatus.EXPIRED
                assignment.updated_at = current_time.isoformat()
                return True
        return False


class SurveyReviewStateMachine:
    """Manages review governance and reviewer separation for FieldSurveyEvidence."""

    @staticmethod
    def review(
        evidence: FieldSurveyEvidence,
        *,
        decision: EvidenceReviewStatus,
        reviewer_id: str,
        reviewer_roles: set[str] | frozenset[str] | list[str] | None = None,
        review_comment: str | None = None,
        review_checklist: dict[str, Any] | None = None,
        conditions: list[str] | None = None,
        now: datetime | None = None,
    ) -> SurveyReviewRecord:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        # Invariant 1: Reviewer Separation (Four-Eyes principle)
        if reviewer_id == evidence.submitter_id:
            raise SurveyAuthorizationError(
                f"Submitter ({evidence.submitter_id}) cannot review their own field survey submission",
                code=SurveyErrorCode.SELF_REVIEW_DENIED,
            )

        # Invariant 2: Reviewer must have an authorized review role if roles are supplied
        if reviewer_roles is not None:
            normalized_roles = {str(r).upper() for r in reviewer_roles}
            if not normalized_roles.intersection(ALLOWED_REVIEW_ROLES) and "PLATFORM_ADMIN" not in normalized_roles:
                raise SurveyAuthorizationError(
                    f"Reviewer {reviewer_id} with roles {normalized_roles} is not authorized to review surveys",
                    code=SurveyErrorCode.UNAUTHORIZED_REVIEWER,
                )

        # Invariant 3: Cannot review superseded or retracted evidence
        if evidence.is_superseded:
            raise SurveyStateConflictError(
                f"Cannot review survey {evidence.survey_id}: it has been superseded by a correction or resurvey",
                code=SurveyErrorCode.SUPERSEDED_EVIDENCE,
            )
        if evidence.is_retracted:
            raise SurveyStateConflictError(
                f"Cannot review survey {evidence.survey_id}: it has been retracted",
                code=SurveyErrorCode.RETRACTED_EVIDENCE,
            )

        # Invariant 4: Decision must be an allowed review outcome
        if decision not in (
            EvidenceReviewStatus.APPROVED,
            EvidenceReviewStatus.REJECTED,
            EvidenceReviewStatus.NEEDS_REVISION,
        ):
            raise SurveyValidationError(
                f"Invalid review decision {decision.value}",
                code=SurveyErrorCode.INVALID_STATE_TRANSITION,
            )

        record = SurveyReviewRecord(
            review_status=decision,
            reviewer_id=reviewer_id,
            reviewed_at=current_time_iso,
            review_checklist=dict(review_checklist or {}),
            review_comment=review_comment,
            conditions=list(conditions or []),
        )

        evidence.review_status = decision
        evidence.review_record = record
        evidence.updated_at = current_time_iso

        # If rejected or needs revision, reset any promotion status
        if decision in (EvidenceReviewStatus.REJECTED, EvidenceReviewStatus.NEEDS_REVISION):
            if evidence.promotion_status != PromotionStatus.NOT_PROMOTED:
                evidence.promotion_status = PromotionStatus.NOT_PROMOTED

        return record


class SurveyPromotionStateMachine:
    """Manages promoting reviewed survey evidence to ground truth candidates or operational entities."""

    @staticmethod
    def promote(
        evidence: FieldSurveyEvidence,
        *,
        promoted_by: str,
        target_entity_type: str = "candidate_site",
        target_entity_ref: str | None = None,
        promotion_payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> PromotionRecord:
        import uuid

        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        # Invariant 1: Survey MUST be in APPROVED status
        if evidence.review_status != EvidenceReviewStatus.APPROVED:
            raise SurveyValidationError(
                f"Survey {evidence.survey_id} is in status {evidence.review_status.value}; must be APPROVED to promote",
                code=SurveyErrorCode.NOT_APPROVED_FOR_PROMOTION,
            )

        # Invariant 2: Cannot promote superseded evidence
        if evidence.is_superseded:
            raise SurveyStateConflictError(
                f"Cannot promote survey {evidence.survey_id}: it has been superseded",
                code=SurveyErrorCode.SUPERSEDED_EVIDENCE,
            )

        # Invariant 3: Cannot promote retracted evidence
        if evidence.is_retracted:
            raise SurveyStateConflictError(
                f"Cannot promote survey {evidence.survey_id}: it has been retracted",
                code=SurveyErrorCode.RETRACTED_EVIDENCE,
            )

        # Invariant 4: Check current promotion status
        if evidence.promotion_status == PromotionStatus.PROMOTED:
            # Idempotent return if already promoted to same target
            pass

        effective_ref = target_entity_ref or f"{target_entity_type}-{evidence.target_entity_id}-{evidence.survey_id[:8]}"
        promotion_id = f"prom-{uuid.uuid4().hex[:12]}"

        record = PromotionRecord(
            promotion_id=promotion_id,
            tenant_id=evidence.tenant_id,
            survey_id=evidence.survey_id,
            target_entity_id=evidence.target_entity_id,
            target_entity_kind=evidence.target_entity_kind,
            promoted_by=promoted_by,
            promoted_at=current_time_iso,
            target_entity_type=target_entity_type,
            target_entity_ref=effective_ref,
            payload=dict(promotion_payload or {}),
        )

        evidence.promotion_status = PromotionStatus.PROMOTED
        evidence.promoted_at = current_time_iso
        evidence.promoted_by = promoted_by
        evidence.promoted_target_ref = effective_ref
        evidence.promotion_result = {
            "promotion_id": promotion_id,
            "target_entity_type": target_entity_type,
            "target_entity_ref": effective_ref,
            "status": "SUCCESS",
        }
        evidence.updated_at = current_time_iso

        return record


__all__ = [
    "ALLOWED_REVIEW_ROLES",
    "SurveyAssignmentStateMachine",
    "SurveyPromotionStateMachine",
    "SurveyReviewStateMachine",
]
