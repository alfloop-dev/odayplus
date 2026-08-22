"""Market Survey Infrastructure Repositories.

Contract: `odayplus.survey-workflow.v2`.
Task ID: `ODP-SURVEY-001`.

Provides thread-safe in-memory and durable repository implementations with tenant isolation.
"""

from __future__ import annotations

import copy
import threading
from typing import Protocol, runtime_checkable

from modules.market_survey.domain.models import (
    AssignmentStatus,
    EvidenceReviewStatus,
    FieldSurveyEvidence,
    PromotionRecord,
    PromotionStatus,
    SurveyAssignment,
    SurveyCorrection,
)


@runtime_checkable
class SurveyRepository(Protocol):
    """Interface for persisting and querying market survey domain entities."""

    def save_assignment(self, assignment: SurveyAssignment) -> None:
        ...

    def get_assignment(self, assignment_id: str, tenant_id: str | None = None) -> SurveyAssignment | None:
        ...

    def list_assignments(
        self,
        *,
        tenant_id: str | None = None,
        campaign_id: str | None = None,
        status: AssignmentStatus | str | None = None,
        assigned_to: str | None = None,
        target_entity_id: str | None = None,
    ) -> list[SurveyAssignment]:
        ...

    def save_evidence(self, evidence: FieldSurveyEvidence) -> None:
        ...

    def get_evidence(self, survey_id: str, tenant_id: str | None = None) -> FieldSurveyEvidence | None:
        ...

    def get_evidence_by_observation_id(self, observation_id: str, tenant_id: str | None = None) -> FieldSurveyEvidence | None:
        ...

    def list_evidence(
        self,
        *,
        tenant_id: str | None = None,
        campaign_id: str | None = None,
        target_entity_id: str | None = None,
        review_status: EvidenceReviewStatus | str | None = None,
        promotion_status: PromotionStatus | str | None = None,
        include_superseded: bool = True,
    ) -> list[FieldSurveyEvidence]:
        ...

    def save_correction(self, correction: SurveyCorrection) -> None:
        ...

    def get_corrections_for_survey(self, survey_id: str, tenant_id: str | None = None) -> list[SurveyCorrection]:
        ...

    def save_promotion(self, promotion: PromotionRecord) -> None:
        ...

    def get_promotion(self, promotion_id: str, tenant_id: str | None = None) -> PromotionRecord | None:
        ...

    def get_promotion_for_survey(self, survey_id: str, tenant_id: str | None = None) -> PromotionRecord | None:
        ...


class InMemorySurveyRepository:
    """Thread-safe, tenant-isolated in-memory survey repository."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._assignments: dict[str, SurveyAssignment] = {}
        self._evidence: dict[str, FieldSurveyEvidence] = {}
        self._observation_index: dict[str, str] = {}  # observation_id -> survey_id
        self._corrections: list[SurveyCorrection] = []
        self._promotions: dict[str, PromotionRecord] = {}  # promotion_id -> record
        self._survey_promotions: dict[str, str] = {}  # survey_id -> promotion_id

    def save_assignment(self, assignment: SurveyAssignment) -> None:
        with self._lock:
            # Store a deepcopy to avoid accidental external mutation
            self._assignments[assignment.assignment_id] = copy.deepcopy(assignment)

    def get_assignment(self, assignment_id: str, tenant_id: str | None = None) -> SurveyAssignment | None:
        with self._lock:
            asgn = self._assignments.get(assignment_id)
            if asgn is None:
                return None
            if tenant_id and asgn.tenant_id != tenant_id:
                return None
            return copy.deepcopy(asgn)

    def list_assignments(
        self,
        *,
        tenant_id: str | None = None,
        campaign_id: str | None = None,
        status: AssignmentStatus | str | None = None,
        assigned_to: str | None = None,
        target_entity_id: str | None = None,
    ) -> list[SurveyAssignment]:
        with self._lock:
            status_val = status.value if isinstance(status, AssignmentStatus) else status
            results = []
            for asgn in self._assignments.values():
                if tenant_id and asgn.tenant_id != tenant_id:
                    continue
                if campaign_id and asgn.campaign_id != campaign_id:
                    continue
                if status_val and asgn.status.value != status_val:
                    continue
                if assigned_to and asgn.assigned_to != assigned_to:
                    continue
                if target_entity_id and asgn.target_entity_id != target_entity_id:
                    continue
                results.append(copy.deepcopy(asgn))
            return results

    def save_evidence(self, evidence: FieldSurveyEvidence) -> None:
        with self._lock:
            self._evidence[evidence.survey_id] = copy.deepcopy(evidence)
            self._observation_index[evidence.observation_id] = evidence.survey_id

    def get_evidence(self, survey_id: str, tenant_id: str | None = None) -> FieldSurveyEvidence | None:
        with self._lock:
            ev = self._evidence.get(survey_id)
            if ev is None:
                return None
            if tenant_id and ev.tenant_id != tenant_id:
                return None
            return copy.deepcopy(ev)

    def get_evidence_by_observation_id(self, observation_id: str, tenant_id: str | None = None) -> FieldSurveyEvidence | None:
        with self._lock:
            survey_id = self._observation_index.get(observation_id)
            if not survey_id:
                return None
            return self.get_evidence(survey_id, tenant_id=tenant_id)

    def list_evidence(
        self,
        *,
        tenant_id: str | None = None,
        campaign_id: str | None = None,
        target_entity_id: str | None = None,
        review_status: EvidenceReviewStatus | str | None = None,
        promotion_status: PromotionStatus | str | None = None,
        include_superseded: bool = True,
    ) -> list[FieldSurveyEvidence]:
        with self._lock:
            rev_val = review_status.value if isinstance(review_status, EvidenceReviewStatus) else review_status
            prom_val = promotion_status.value if isinstance(promotion_status, PromotionStatus) else promotion_status
            results = []
            for ev in self._evidence.values():
                if tenant_id and ev.tenant_id != tenant_id:
                    continue
                if campaign_id and ev.campaign_id != campaign_id:
                    continue
                if target_entity_id and ev.target_entity_id != target_entity_id:
                    continue
                if rev_val and ev.review_status.value != rev_val:
                    continue
                if prom_val and ev.promotion_status.value != prom_val:
                    continue
                if not include_superseded and ev.is_superseded:
                    continue
                results.append(copy.deepcopy(ev))
            return results

    def save_correction(self, correction: SurveyCorrection) -> None:
        with self._lock:
            self._corrections.append(copy.deepcopy(correction))

    def get_corrections_for_survey(self, survey_id: str, tenant_id: str | None = None) -> list[SurveyCorrection]:
        with self._lock:
            results = []
            for c in self._corrections:
                if tenant_id and c.tenant_id != tenant_id:
                    continue
                if c.original_survey_id == survey_id or c.new_survey_id == survey_id:
                    results.append(copy.deepcopy(c))
            return results

    def save_promotion(self, promotion: PromotionRecord) -> None:
        with self._lock:
            self._promotions[promotion.promotion_id] = copy.deepcopy(promotion)
            self._survey_promotions[promotion.survey_id] = promotion.promotion_id

    def get_promotion(self, promotion_id: str, tenant_id: str | None = None) -> PromotionRecord | None:
        with self._lock:
            p = self._promotions.get(promotion_id)
            if p is None:
                return None
            if tenant_id and p.tenant_id != tenant_id:
                return None
            return copy.deepcopy(p)

    def get_promotion_for_survey(self, survey_id: str, tenant_id: str | None = None) -> PromotionRecord | None:
        with self._lock:
            prom_id = self._survey_promotions.get(survey_id)
            if not prom_id:
                return None
            return self.get_promotion(prom_id, tenant_id=tenant_id)

    def clear(self) -> None:
        with self._lock:
            self._assignments.clear()
            self._evidence.clear()
            self._observation_index.clear()
            self._corrections.clear()
            self._promotions.clear()
            self._survey_promotions.clear()


__all__ = [
    "InMemorySurveyRepository",
    "SurveyRepository",
]
