"""Market Survey Application Service.

Contract: `odayplus.survey-workflow.v2`.
Task ID: `ODP-SURVEY-001`.

Orchestrates:
1. Field survey assignment management and SLA/expiry sweeps.
2. Platform field survey observation ingestion as evidence snapshots (not ground truth).
3. Reviewer separation (Segregation of Duties) and governance reviews.
4. Survey corrections, resurveys, retractions, and lineage graphs.
5. Canonical promotion to candidate sites / operational entities.
6. Audit trail emission for all governance and state transitions.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from modules.market_survey.domain.models import (
    SURVEY_WORKFLOW_CONTRACT,
    SURVEY_WORKFLOW_VERSION,
    AssignmentStatus,
    EvidenceReviewStatus,
    FieldSurveyEvidence,
    MediaAttachment,
    PromotionRecord,
    PromotionStatus,
    SurveyAssignment,
    SurveyCorrection,
    SurveyErrorCode,
    SurveyLifecycleKind,
    SurveyLocation,
    SurveyNotFoundError,
    SurveyStateConflictError,
    SurveyType,
    SurveyValidationError,
    TargetEntityKind,
)
from modules.market_survey.domain.state_machine import (
    SurveyAssignmentStateMachine,
    SurveyPromotionStateMachine,
    SurveyReviewStateMachine,
)
from modules.market_survey.infrastructure.repositories import (
    InMemorySurveyRepository,
    SurveyRepository,
)
from shared.audit import AuditEvent, InMemoryAuditLog


def _ensure_dt_str(dt: str | datetime | None) -> str:
    if dt is None:
        return datetime.now(UTC).isoformat()
    if isinstance(dt, datetime):
        return dt.isoformat() if dt.tzinfo is not None else dt.replace(tzinfo=UTC).isoformat()
    return str(dt)


class MarketSurveyService:
    """Core application service for field survey lifecycle and governance."""

    def __init__(
        self,
        repository: SurveyRepository | None = None,
        audit_log: InMemoryAuditLog | None = None,
        promotion_hooks: list[Callable[[PromotionRecord], None]] | None = None,
    ) -> None:
        self.repository = repository or InMemorySurveyRepository()
        self.audit_log = audit_log or InMemoryAuditLog()
        self.promotion_hooks = promotion_hooks or []

    # -----------------------------------------------------------------------
    # Assignment Management
    # -----------------------------------------------------------------------

    def create_assignment(
        self,
        *,
        tenant_id: str,
        campaign_id: str,
        target_entity_id: str,
        target_entity_kind: TargetEntityKind | str,
        survey_type: SurveyType | str,
        expires_at: str | datetime,
        created_by: str,
        assigned_to: str | None = None,
        instructions: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> SurveyAssignment:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()
        expires_at_iso = _ensure_dt_str(expires_at)

        target_kind = (
            TargetEntityKind(target_entity_kind)
            if isinstance(target_entity_kind, str)
            else target_entity_kind
        )
        stype = SurveyType(survey_type) if isinstance(survey_type, str) else survey_type

        status = AssignmentStatus.ASSIGNED if assigned_to else AssignmentStatus.UNASSIGNED
        assignment_id = f"asgn-{uuid.uuid4().hex[:12]}"

        assignment = SurveyAssignment(
            assignment_id=assignment_id,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            target_entity_id=target_entity_id,
            target_entity_kind=target_kind,
            survey_type=stype,
            status=status,
            expires_at=expires_at_iso,
            created_at=current_time_iso,
            created_by=created_by,
            assigned_to=assigned_to,
            assigned_by=created_by if assigned_to else None,
            assigned_at=current_time_iso if assigned_to else None,
            instructions=dict(instructions or {}),
            metadata=dict(metadata or {}),
            updated_at=current_time_iso,
        )

        self.repository.save_assignment(assignment)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.assignment_created.v1",
                actor=created_by,
                action="create_assignment",
                resource=f"survey_assignment/{assignment_id}",
                outcome="created",
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": tenant_id,
                    "campaign_id": campaign_id,
                    "target_entity_id": target_entity_id,
                    "assigned_to": assigned_to,
                    "expires_at": expires_at_iso,
                },
            )
        )

        return assignment

    def assign_survey(
        self,
        assignment_id: str,
        *,
        assigned_to: str,
        assigned_by: str,
        expires_at: str | datetime | None = None,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> SurveyAssignment:
        assignment = self.repository.get_assignment(assignment_id, tenant_id=tenant_id)
        if assignment is None:
            raise SurveyNotFoundError(
                f"Survey assignment {assignment_id} not found",
                code=SurveyErrorCode.ASSIGNMENT_NOT_FOUND,
            )

        exp_str = _ensure_dt_str(expires_at) if expires_at else None
        SurveyAssignmentStateMachine.assign(
            assignment,
            assigned_to=assigned_to,
            assigned_by=assigned_by,
            expires_at=exp_str,
            now=now,
        )

        self.repository.save_assignment(assignment)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.assignment_updated.v1",
                actor=assigned_by,
                action="assign_survey",
                resource=f"survey_assignment/{assignment_id}",
                outcome="assigned",
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": assignment.tenant_id,
                    "assigned_to": assigned_to,
                    "status": assignment.status.value,
                },
            )
        )

        return assignment

    def claim_assignment(
        self,
        assignment_id: str,
        *,
        actor_id: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> SurveyAssignment:
        assignment = self.repository.get_assignment(assignment_id, tenant_id=tenant_id)
        if assignment is None:
            raise SurveyNotFoundError(
                f"Survey assignment {assignment_id} not found",
                code=SurveyErrorCode.ASSIGNMENT_NOT_FOUND,
            )

        SurveyAssignmentStateMachine.claim(assignment, actor_id=actor_id, now=now)
        self.repository.save_assignment(assignment)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.assignment_claimed.v1",
                actor=actor_id,
                action="claim_assignment",
                resource=f"survey_assignment/{assignment_id}",
                outcome="claimed",
                correlation_id=correlation_id or "",
                metadata={"tenant_id": assignment.tenant_id, "claimed_by": actor_id},
            )
        )

        return assignment

    def start_survey(
        self,
        assignment_id: str,
        *,
        actor_id: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> SurveyAssignment:
        assignment = self.repository.get_assignment(assignment_id, tenant_id=tenant_id)
        if assignment is None:
            raise SurveyNotFoundError(
                f"Survey assignment {assignment_id} not found",
                code=SurveyErrorCode.ASSIGNMENT_NOT_FOUND,
            )

        SurveyAssignmentStateMachine.start(assignment, actor_id=actor_id, now=now)
        self.repository.save_assignment(assignment)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.assignment_started.v1",
                actor=actor_id,
                action="start_survey",
                resource=f"survey_assignment/{assignment_id}",
                outcome="in_progress",
                correlation_id=correlation_id or "",
                metadata={"tenant_id": assignment.tenant_id, "actor": actor_id},
            )
        )

        return assignment

    def submit_survey(
        self,
        assignment_id: str,
        *,
        actor_id: str,
        location: SurveyLocation | Mapping[str, Any],
        attributes: Mapping[str, Any] | None = None,
        media_attachments: list[MediaAttachment | Mapping[str, Any]] | None = None,
        surveyed_at: str | datetime | None = None,
        confidence: float | None = 1.0,
        metadata: Mapping[str, Any] | None = None,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[SurveyAssignment, FieldSurveyEvidence]:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        assignment = self.repository.get_assignment(assignment_id, tenant_id=tenant_id)
        if assignment is None:
            raise SurveyNotFoundError(
                f"Survey assignment {assignment_id} not found",
                code=SurveyErrorCode.ASSIGNMENT_NOT_FOUND,
            )

        survey_id = f"srv-{uuid.uuid4().hex[:12]}"
        observation_id = f"obs-{uuid.uuid4().hex[:12]}"
        blob_id = f"blob-{uuid.uuid4().hex[:12]}"

        SurveyAssignmentStateMachine.submit(
            assignment,
            actor_id=actor_id,
            survey_id=survey_id,
            now=now,
        )

        loc = (
            SurveyLocation.from_dict(location)
            if isinstance(location, Mapping)
            else location
        )
        parsed_media = []
        for m in media_attachments or []:
            parsed_media.append(MediaAttachment.from_dict(m) if isinstance(m, Mapping) else m)

        surveyed_at_iso = _ensure_dt_str(surveyed_at) if surveyed_at else current_time_iso

        # Evidence starts at PENDING_REVIEW: Invariant: Evidence, not ground truth!
        evidence = FieldSurveyEvidence(
            survey_id=survey_id,
            tenant_id=assignment.tenant_id,
            blob_id=blob_id,
            campaign_id=assignment.campaign_id,
            target_entity_id=assignment.target_entity_id,
            target_entity_kind=assignment.target_entity_kind,
            survey_type=assignment.survey_type,
            lifecycle_kind=SurveyLifecycleKind.INITIAL,
            submitter_id=actor_id,
            surveyed_at=surveyed_at_iso,
            submitted_at=current_time_iso,
            location=loc,
            observation_id=observation_id,
            attributes=dict(attributes or {}),
            media_attachments=parsed_media,
            confidence=confidence,
            assignment_id=assignment_id,
            review_status=EvidenceReviewStatus.PENDING_REVIEW,
            promotion_status=PromotionStatus.NOT_PROMOTED,
            metadata=dict(metadata or {}),
            created_at=current_time_iso,
            updated_at=current_time_iso,
        )

        self.repository.save_assignment(assignment)
        self.repository.save_evidence(evidence)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.survey_submitted.v1",
                actor=actor_id,
                action="submit_survey",
                resource=f"field_survey/{survey_id}",
                outcome="submitted",
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": assignment.tenant_id,
                    "assignment_id": assignment_id,
                    "survey_id": survey_id,
                    "observation_id": observation_id,
                    "review_status": evidence.review_status.value,
                },
            )
        )

        return assignment, evidence

    def cancel_assignment(
        self,
        assignment_id: str,
        *,
        actor_id: str,
        reason: str = "",
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> SurveyAssignment:
        assignment = self.repository.get_assignment(assignment_id, tenant_id=tenant_id)
        if assignment is None:
            raise SurveyNotFoundError(
                f"Survey assignment {assignment_id} not found",
                code=SurveyErrorCode.ASSIGNMENT_NOT_FOUND,
            )

        SurveyAssignmentStateMachine.cancel(assignment, actor_id=actor_id, reason=reason, now=now)
        self.repository.save_assignment(assignment)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.assignment_cancelled.v1",
                actor=actor_id,
                action="cancel_assignment",
                resource=f"survey_assignment/{assignment_id}",
                outcome="cancelled",
                correlation_id=correlation_id or "",
                metadata={"tenant_id": assignment.tenant_id, "reason": reason},
            )
        )

        return assignment

    # -----------------------------------------------------------------------
    # Platform Observation Ingestion (emgi.field-survey.v1 Evidence Intake)
    # -----------------------------------------------------------------------

    def ingest_platform_observation(
        self,
        observation: Any,
        *,
        tenant_id: str,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> FieldSurveyEvidence:
        """Ingest a platform field-survey observation as evidence (NOT automatic ground truth)."""
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        # Handle dataclass or dict
        if hasattr(observation, "to_dict"):
            data = observation.to_dict()
        elif isinstance(observation, Mapping):
            data = dict(observation)
        else:
            raise SurveyValidationError(
                f"Unsupported observation format: {type(observation)}",
                code=SurveyErrorCode.VALIDATION_FAILED,
            )

        survey_id = data.get("survey_id") or f"srv-{uuid.uuid4().hex[:12]}"
        observation_id = str(data["observation_id"])
        blob_id = str(data["blob_id"])
        campaign_id = str(data["campaign_id"])
        target_entity_id = str(data["target_entity_id"])
        target_entity_kind = TargetEntityKind(data["target_entity_kind"])
        survey_type = SurveyType(data["survey_type"])
        lifecycle_kind = SurveyLifecycleKind(data.get("lifecycle_kind", SurveyLifecycleKind.INITIAL))
        submitter_id = str(data["submitter_id"])
        surveyed_at = str(data["surveyed_at"])
        submitted_at = str(data.get("submitted_at", current_time_iso))
        location = SurveyLocation.from_dict(data["location"])
        attributes = dict(data.get("attributes", {}))
        media_attachments = [MediaAttachment.from_dict(m) for m in data.get("media_attachments", [])]
        confidence = float(data["confidence"]) if data.get("confidence") is not None else 1.0
        replaces_survey_id = data.get("replaces_survey_id")
        replaces_observation_id = data.get("replaces_observation_id")

        # Check if this replaces an existing observation or survey
        if replaces_observation_id:
            prior = self.repository.get_evidence_by_observation_id(replaces_observation_id, tenant_id=tenant_id)
            if prior:
                prior.is_superseded = True
                prior.updated_at = current_time_iso
                self.repository.save_evidence(prior)
                if not replaces_survey_id:
                    replaces_survey_id = prior.survey_id

        if replaces_survey_id:
            prior = self.repository.get_evidence(replaces_survey_id, tenant_id=tenant_id)
            if prior and not prior.is_superseded:
                prior.is_superseded = True
                prior.updated_at = current_time_iso
                self.repository.save_evidence(prior)

        # Invariant 2: Platform survey ingestion is evidence, not ground truth!
        # Review status defaults to PENDING_REVIEW; promotion status defaults to NOT_PROMOTED.
        evidence = FieldSurveyEvidence(
            survey_id=survey_id,
            tenant_id=tenant_id,
            blob_id=blob_id,
            campaign_id=campaign_id,
            target_entity_id=target_entity_id,
            target_entity_kind=target_entity_kind,
            survey_type=survey_type,
            lifecycle_kind=lifecycle_kind,
            submitter_id=submitter_id,
            surveyed_at=surveyed_at,
            submitted_at=submitted_at,
            location=location,
            observation_id=observation_id,
            attributes=attributes,
            media_attachments=media_attachments,
            confidence=confidence,
            review_status=EvidenceReviewStatus.PENDING_REVIEW,
            promotion_status=PromotionStatus.NOT_PROMOTED,
            replaces_survey_id=replaces_survey_id,
            replaces_observation_id=replaces_observation_id,
            metadata=dict(data.get("metadata", {})),
            created_at=current_time_iso,
            updated_at=current_time_iso,
        )

        self.repository.save_evidence(evidence)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.platform_evidence_ingested.v1",
                actor="data_platform",
                action="ingest_platform_evidence",
                resource=f"field_survey/{survey_id}",
                outcome="ingested_as_evidence",
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": tenant_id,
                    "observation_id": observation_id,
                    "survey_id": survey_id,
                    "target_entity_id": target_entity_id,
                    "lifecycle_kind": lifecycle_kind.value,
                    "review_status": evidence.review_status.value,
                },
            )
        )

        return evidence

    def ingest_platform_document(
        self,
        document: Any,
        *,
        tenant_id: str,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> list[FieldSurveyEvidence]:
        """Ingest a full FieldSurveyDocument (contract emgi.field-survey.v1)."""
        if hasattr(document, "to_dict"):
            doc_dict = document.to_dict()
        elif isinstance(document, Mapping):
            doc_dict = dict(document)
        else:
            raise SurveyValidationError(
                f"Unsupported document format: {type(document)}",
                code=SurveyErrorCode.VALIDATION_FAILED,
            )

        ingested_items = []
        for obs in doc_dict.get("field_surveys", []):
            ev = self.ingest_platform_observation(
                obs,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                now=now,
            )
            ingested_items.append(ev)

        # Handle any retractions declared in document
        for ret in doc_dict.get("retractions", []):
            obs_id = ret.get("observation_id")
            survey_id = ret.get("survey_id")
            reason = ret.get("reason", "platform retraction")
            if obs_id:
                ev = self.repository.get_evidence_by_observation_id(obs_id, tenant_id=tenant_id)
                if ev:
                    self.retract_survey(ev.survey_id, retracted_by="data_platform", reason=reason, tenant_id=tenant_id, correlation_id=correlation_id)
            elif survey_id:
                self.retract_survey(survey_id, retracted_by="data_platform", reason=reason, tenant_id=tenant_id, correlation_id=correlation_id)

        return ingested_items

    # -----------------------------------------------------------------------
    # Review Governance (Reviewer Separation)
    # -----------------------------------------------------------------------

    def review_survey(
        self,
        survey_id: str,
        *,
        decision: EvidenceReviewStatus | str,
        reviewer_id: str,
        reviewer_roles: set[str] | frozenset[str] | list[str] | None = None,
        review_comment: str | None = None,
        review_checklist: Mapping[str, Any] | None = None,
        conditions: list[str] | None = None,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> FieldSurveyEvidence:
        """Review field survey observation enforcing reviewer separation (submitter != reviewer)."""
        evidence = self.repository.get_evidence(survey_id, tenant_id=tenant_id)
        if evidence is None:
            raise SurveyNotFoundError(
                f"Survey evidence {survey_id} not found",
                code=SurveyErrorCode.EVIDENCE_NOT_FOUND,
            )

        dec = EvidenceReviewStatus(decision) if isinstance(decision, str) else decision

        SurveyReviewStateMachine.review(
            evidence,
            decision=dec,
            reviewer_id=reviewer_id,
            reviewer_roles=reviewer_roles,
            review_comment=review_comment,
            review_checklist=dict(review_checklist or {}),
            conditions=conditions,
            now=now,
        )

        self.repository.save_evidence(evidence)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.survey_reviewed.v1",
                actor=reviewer_id,
                action="review_survey",
                resource=f"field_survey/{survey_id}",
                outcome=dec.value,
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": evidence.tenant_id,
                    "survey_id": survey_id,
                    "reviewer_id": reviewer_id,
                    "decision": dec.value,
                    "review_comment": review_comment,
                },
            )
        )

        return evidence

    # -----------------------------------------------------------------------
    # Correction & Resurvey
    # -----------------------------------------------------------------------

    def correct_survey(
        self,
        original_survey_id: str,
        *,
        corrected_by: str,
        reason: str,
        delta_attributes: Mapping[str, Any],
        location: SurveyLocation | Mapping[str, Any] | None = None,
        media_attachments: list[MediaAttachment | Mapping[str, Any]] | None = None,
        lifecycle_kind: SurveyLifecycleKind = SurveyLifecycleKind.CORRECTION,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[FieldSurveyEvidence, SurveyCorrection]:
        """Submit a correction or resurvey referencing an existing survey evidence."""
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        original = self.repository.get_evidence(original_survey_id, tenant_id=tenant_id)
        if original is None:
            raise SurveyNotFoundError(
                f"Original survey evidence {original_survey_id} not found",
                code=SurveyErrorCode.EVIDENCE_NOT_FOUND,
            )

        if original.is_superseded:
            raise SurveyStateConflictError(
                f"Original survey {original_survey_id} is already superseded",
                code=SurveyErrorCode.SUPERSEDED_EVIDENCE,
            )

        new_survey_id = f"srv-{uuid.uuid4().hex[:12]}"
        new_obs_id = f"obs-{uuid.uuid4().hex[:12]}"
        new_blob_id = f"blob-{uuid.uuid4().hex[:12]}"
        correction_id = f"corr-{uuid.uuid4().hex[:12]}"

        # Mark original as superseded
        original.is_superseded = True
        original.updated_at = current_time_iso
        self.repository.save_evidence(original)

        # Merge attributes
        merged_attrs = dict(original.attributes)
        merged_attrs.update(delta_attributes)

        loc = (
            SurveyLocation.from_dict(location)
            if isinstance(location, Mapping)
            else location or original.location
        )

        parsed_media = []
        if media_attachments is not None:
            for m in media_attachments:
                parsed_media.append(MediaAttachment.from_dict(m) if isinstance(m, Mapping) else m)
        else:
            parsed_media = list(original.media_attachments)

        new_evidence = FieldSurveyEvidence(
            survey_id=new_survey_id,
            tenant_id=original.tenant_id,
            blob_id=new_blob_id,
            campaign_id=original.campaign_id,
            target_entity_id=original.target_entity_id,
            target_entity_kind=original.target_entity_kind,
            survey_type=original.survey_type,
            lifecycle_kind=lifecycle_kind,
            submitter_id=corrected_by,
            surveyed_at=current_time_iso,
            submitted_at=current_time_iso,
            location=loc,
            observation_id=new_obs_id,
            attributes=merged_attrs,
            media_attachments=parsed_media,
            confidence=original.confidence,
            assignment_id=original.assignment_id,
            review_status=EvidenceReviewStatus.PENDING_REVIEW,
            promotion_status=PromotionStatus.NOT_PROMOTED,
            replaces_survey_id=original.survey_id,
            replaces_observation_id=original.observation_id,
            created_at=current_time_iso,
            updated_at=current_time_iso,
        )

        correction = SurveyCorrection(
            correction_id=correction_id,
            tenant_id=original.tenant_id,
            original_survey_id=original_survey_id,
            new_survey_id=new_survey_id,
            reason=reason,
            corrected_by=corrected_by,
            corrected_at=current_time_iso,
            delta_attributes=dict(delta_attributes),
        )

        self.repository.save_evidence(new_evidence)
        self.repository.save_correction(correction)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.survey_corrected.v1",
                actor=corrected_by,
                action="correct_survey",
                resource=f"field_survey/{new_survey_id}",
                outcome="corrected",
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": original.tenant_id,
                    "original_survey_id": original_survey_id,
                    "new_survey_id": new_survey_id,
                    "correction_id": correction_id,
                    "reason": reason,
                },
            )
        )

        return new_evidence, correction

    def retract_survey(
        self,
        survey_id: str,
        *,
        retracted_by: str,
        reason: str,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> FieldSurveyEvidence:
        current_time = now or datetime.now(UTC)
        current_time_iso = current_time.isoformat()

        evidence = self.repository.get_evidence(survey_id, tenant_id=tenant_id)
        if evidence is None:
            raise SurveyNotFoundError(
                f"Survey evidence {survey_id} not found",
                code=SurveyErrorCode.EVIDENCE_NOT_FOUND,
            )

        evidence.is_retracted = True
        evidence.retraction_reason = reason
        evidence.updated_at = current_time_iso
        self.repository.save_evidence(evidence)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.survey_retracted.v1",
                actor=retracted_by,
                action="retract_survey",
                resource=f"field_survey/{survey_id}",
                outcome="retracted",
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": evidence.tenant_id,
                    "survey_id": survey_id,
                    "reason": reason,
                },
            )
        )

        return evidence

    # -----------------------------------------------------------------------
    # Promotion to Ground Truth Operational Truth
    # -----------------------------------------------------------------------

    def promote_survey(
        self,
        survey_id: str,
        *,
        promoted_by: str,
        target_entity_type: str = "candidate_site",
        target_entity_ref: str | None = None,
        promotion_payload: Mapping[str, Any] | None = None,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> PromotionRecord:
        """Promote reviewed and approved survey evidence into canonical operational entities."""
        evidence = self.repository.get_evidence(survey_id, tenant_id=tenant_id)
        if evidence is None:
            raise SurveyNotFoundError(
                f"Survey evidence {survey_id} not found",
                code=SurveyErrorCode.EVIDENCE_NOT_FOUND,
            )

        record = SurveyPromotionStateMachine.promote(
            evidence,
            promoted_by=promoted_by,
            target_entity_type=target_entity_type,
            target_entity_ref=target_entity_ref,
            promotion_payload=dict(promotion_payload or {}),
            now=now,
        )

        self.repository.save_evidence(evidence)
        self.repository.save_promotion(record)

        for hook in self.promotion_hooks:
            hook(record)

        self.audit_log.record(
            AuditEvent(
                event_type="market_survey.survey_promoted.v1",
                actor=promoted_by,
                action="promote_survey",
                resource=f"field_survey/{survey_id}",
                outcome="promoted",
                correlation_id=correlation_id or "",
                metadata={
                    "tenant_id": evidence.tenant_id,
                    "survey_id": survey_id,
                    "promotion_id": record.promotion_id,
                    "target_entity_type": target_entity_type,
                    "target_entity_ref": record.target_entity_ref,
                },
            )
        )

        return record

    # -----------------------------------------------------------------------
    # SLA & Expiry Management
    # -----------------------------------------------------------------------

    def check_and_expire_assignments(
        self,
        *,
        tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> list[SurveyAssignment]:
        """Scan active assignments and mark overdue ones as EXPIRED."""
        current_time = now or datetime.now(UTC)
        assignments = self.repository.list_assignments(tenant_id=tenant_id)
        expired_list = []

        for asgn in assignments:
            if SurveyAssignmentStateMachine.expire_if_overdue(asgn, now=current_time):
                self.repository.save_assignment(asgn)
                expired_list.append(asgn)
                self.audit_log.record(
                    AuditEvent(
                        event_type="market_survey.assignment_expired.v1",
                        actor="system_sla_worker",
                        action="expire_assignment",
                        resource=f"survey_assignment/{asgn.assignment_id}",
                        outcome="expired",
                        correlation_id="",
                        metadata={
                            "tenant_id": asgn.tenant_id,
                            "assignment_id": asgn.assignment_id,
                            "expires_at": asgn.expires_at,
                        },
                    )
                )

        return expired_list

    # -----------------------------------------------------------------------
    # Query & Lineage APIs
    # -----------------------------------------------------------------------

    def get_assignment(self, assignment_id: str, tenant_id: str | None = None) -> SurveyAssignment | None:
        return self.repository.get_assignment(assignment_id, tenant_id=tenant_id)

    def list_assignments(
        self,
        *,
        tenant_id: str | None = None,
        campaign_id: str | None = None,
        status: AssignmentStatus | str | None = None,
        assigned_to: str | None = None,
        target_entity_id: str | None = None,
    ) -> list[SurveyAssignment]:
        return self.repository.list_assignments(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            status=status,
            assigned_to=assigned_to,
            target_entity_id=target_entity_id,
        )

    def get_survey(self, survey_id: str, tenant_id: str | None = None) -> FieldSurveyEvidence | None:
        return self.repository.get_evidence(survey_id, tenant_id=tenant_id)

    def list_surveys(
        self,
        *,
        tenant_id: str | None = None,
        campaign_id: str | None = None,
        target_entity_id: str | None = None,
        review_status: EvidenceReviewStatus | str | None = None,
        promotion_status: PromotionStatus | str | None = None,
        include_superseded: bool = True,
    ) -> list[FieldSurveyEvidence]:
        return self.repository.list_evidence(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            target_entity_id=target_entity_id,
            review_status=review_status,
            promotion_status=promotion_status,
            include_superseded=include_superseded,
        )

    def get_survey_lineage(self, survey_id: str, tenant_id: str | None = None) -> dict[str, Any]:
        evidence = self.repository.get_evidence(survey_id, tenant_id=tenant_id)
        if evidence is None:
            raise SurveyNotFoundError(
                f"Survey {survey_id} not found",
                code=SurveyErrorCode.EVIDENCE_NOT_FOUND,
            )

        corrections = self.repository.get_corrections_for_survey(survey_id, tenant_id=tenant_id)
        promotion = self.repository.get_promotion_for_survey(survey_id, tenant_id=tenant_id)

        # Build ancestry chain
        ancestry = []
        curr_id = evidence.replaces_survey_id
        while curr_id:
            ancestor = self.repository.get_evidence(curr_id, tenant_id=tenant_id)
            if not ancestor:
                break
            ancestry.append(ancestor.to_dict())
            curr_id = ancestor.replaces_survey_id

        return {
            "survey": evidence.to_dict(),
            "review_record": evidence.review_record.to_dict() if evidence.review_record else None,
            "corrections": [c.to_dict() for c in corrections],
            "promotion": promotion.to_dict() if promotion else None,
            "ancestry": ancestry,
            "contract": SURVEY_WORKFLOW_CONTRACT,
            "version": SURVEY_WORKFLOW_VERSION,
        }


__all__ = [
    "MarketSurveyService",
]
