"""Tenant-owned assignment documents, with native Survey models and real CAS.

This adapter deliberately exposes only assignment persistence. It does not
pretend that caller-supplied media metadata is an uploaded content artifact.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from typing import Any

from modules.market_survey.domain.content import SurveyAttachmentRecord
from modules.market_survey.domain.models import (
    EvidenceReviewStatus,
    FieldSurveyEvidence,
    PromotionStatus,
    SurveyAssignment,
)
from shared.infrastructure.persistence.operator_domains import TenantScopedDocumentStore


class SurveyOwnerUnavailable(RuntimeError):
    pass


class SurveyRevisionConflict(ValueError):
    pass


class DurableSurveyAssignmentRepository:
    COLLECTION = "market-survey.assignments.v1"
    MEDIA = "market-survey.attachments.v1"
    EVIDENCE = "market-survey.evidence.v1"

    def __init__(self, store: Any) -> None:
        self._store = store
        self._command = ContextVar("survey_assignment_command", default=False)

    @property
    def engine(self) -> Any:
        return self._store.engine

    @property
    def tenant_id(self) -> str:
        return getattr(self._store, "tenant_id", "")

    def for_tenant(self, tenant_id: str) -> DurableSurveyAssignmentRepository:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise SurveyOwnerUnavailable("SURVEY_TENANT_REQUIRED")
        tenant_id = tenant_id.strip()
        if isinstance(self._store, TenantScopedDocumentStore):
            if self.tenant_id != tenant_id:
                raise SurveyOwnerUnavailable("SURVEY_TENANT_REBIND_DENIED")
            return self
        return type(self)(TenantScopedDocumentStore(self._store, tenant_id))

    def _tenant(self) -> str:
        if not self.tenant_id:
            raise SurveyOwnerUnavailable("SURVEY_TENANT_REQUIRED")
        return self.tenant_id

    def _row(self, identity: str) -> tuple[SurveyAssignment, int] | None:
        tenant = self._tenant()
        row = self._store.get_versioned(self.COLLECTION, identity)
        if row is None:
            return None
        assignment, revision = row
        if (not isinstance(assignment, SurveyAssignment)
                or assignment.tenant_id != tenant
                or assignment.assignment_id != identity
                or type(revision) is not int or revision < 1):
            raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_BINDING_INVALID")
        return deepcopy(assignment), revision

    def get_versioned(self, identity: str) -> tuple[SurveyAssignment, int] | None:
        return self._row(identity)

    def get_assignment(self, assignment_id: str,
                       tenant_id: str | None = None) -> SurveyAssignment | None:
        if tenant_id is not None and tenant_id != self._tenant():
            return None
        row = self._row(assignment_id)
        return None if row is None else row[0]

    def list_assignments(self, *, tenant_id: str | None = None,
                         campaign_id: str | None = None, status: Any = None,
                         assigned_to: str | None = None,
                         target_entity_id: str | None = None) -> list[SurveyAssignment]:
        tenant = self._tenant()
        if tenant_id is not None and tenant_id != tenant:
            return []
        values = self._store.list_all(self.COLLECTION)
        if any(not isinstance(row, SurveyAssignment) or row.tenant_id != tenant
               for row in values):
            raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_BINDING_INVALID")
        ids = [row.assignment_id for row in values]
        if len(ids) != len(set(ids)):
            raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_ID_AMBIGUOUS")
        wanted_status = getattr(status, "value", status)
        result = []
        for value in values:
            row = self._row(value.assignment_id)
            if row is None:
                raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_DISAPPEARED")
            item = row[0]
            if ((campaign_id is None or item.campaign_id == campaign_id)
                    and (wanted_status is None or item.status.value == wanted_status)
                    and (assigned_to is None or item.assigned_to == assigned_to)
                    and (target_entity_id is None or item.target_entity_id == target_entity_id)):
                result.append(item)
        return result

    @contextmanager
    def command(self):
        tenant = self._tenant()
        engine = self.engine
        with engine.lock, engine.transaction():
            if getattr(engine, "dialect", "") == "postgresql":
                engine.query_one("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                                 ("market-survey.assignments:" + tenant,))
            elif not engine._conn.in_transaction:
                engine.execute("BEGIN IMMEDIATE")
            token = self._command.set(True)
            try:
                yield
            finally:
                self._command.reset(token)

    def save_assignment(self, assignment: SurveyAssignment) -> None:
        if not self._command.get():
            raise SurveyOwnerUnavailable("SURVEY_COMMAND_TRANSACTION_REQUIRED")
        if not isinstance(assignment, SurveyAssignment) or assignment.tenant_id != self._tenant():
            raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_BINDING_INVALID")
        current = self._row(assignment.assignment_id)
        if current is not None:
            old = current[0]
            if any(getattr(old, field) != getattr(assignment, field) for field in (
                "assignment_id", "tenant_id", "target_entity_id", "target_entity_kind",
                "created_at", "created_by", "campaign_id", "survey_type",
            )):
                raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_IDENTITY_IMMUTABLE")
        expected = None if current is None else current[1]
        written = self._store.compare_and_put(self.COLLECTION, assignment.assignment_id,
                                             deepcopy(assignment), expected_version=expected)
        if written != (1 if expected is None else expected + 1):
            raise SurveyRevisionConflict("SURVEY_REVISION_CONFLICT")


    def _assignment_binding(self, value: Any) -> SurveyAssignment:
        assignment = self.get_assignment(value.assignment_id)
        if (assignment is None or value.tenant_id != self._tenant()
                or assignment.target_entity_id != value.target_id
                or assignment.target_entity_kind.value != value.target_kind):
            raise SurveyOwnerUnavailable("SURVEY_ATTACHMENT_PARENT_INVALID")
        return assignment

    def _media_collection(self, assignment_id: str) -> str:
        if not isinstance(assignment_id, str) or not assignment_id.strip():
            raise SurveyOwnerUnavailable("SURVEY_ATTACHMENT_PARENT_INVALID")
        return self.MEDIA + ":" + assignment_id

    def get_media(self, media_id: str, assignment_id: str) -> SurveyAttachmentRecord | None:
        value = self._store.get(self._media_collection(assignment_id), media_id)
        if value is None:
            return None
        # Before a requested parent is proved, a foreign parent reference is
        # indistinguishable from an unknown attachment and never opens bytes.
        if isinstance(value, SurveyAttachmentRecord) and (value.assignment_id != assignment_id
                or value.tenant_id != self._tenant()):
            return None
        if (not isinstance(value, SurveyAttachmentRecord) or value.media_id != media_id
                or value.tenant_id != self._tenant()
                or type(value.object_generation) is not int or value.object_generation < 1
                or type(value.size_bytes) is not int or value.size_bytes < 1):
            raise SurveyOwnerUnavailable("SURVEY_ATTACHMENT_BINDING_INVALID")
        self._assignment_binding(value)
        return deepcopy(value)

    def list_media(self, assignment_id: str) -> list[SurveyAttachmentRecord]:
        values = self._store.list_all(self._media_collection(assignment_id))
        ids = [value.media_id for value in values if isinstance(value, SurveyAttachmentRecord)]
        if len(ids) != len(values) or len(ids) != len(set(ids)):
            raise SurveyOwnerUnavailable("SURVEY_ATTACHMENT_IDENTITIES_INVALID")
        result = []
        for identity in ids:
            value = self.get_media(identity, assignment_id)
            if value is None:
                raise SurveyOwnerUnavailable("SURVEY_ATTACHMENT_PARENT_INVALID")
            if value.assignment_id == assignment_id:
                result.append(value)
        return result

    def save_media(self, value: SurveyAttachmentRecord) -> None:
        if not self._command.get() or not isinstance(value, SurveyAttachmentRecord):
            raise SurveyOwnerUnavailable("SURVEY_COMMAND_TRANSACTION_REQUIRED")
        self._assignment_binding(value)
        current = self.get_media(value.media_id, value.assignment_id)
        if current is not None:
            if current != value:
                raise SurveyRevisionConflict("SURVEY_ATTACHMENT_IMMUTABLE")
            return
        if self._store.compare_and_put(self._media_collection(value.assignment_id), value.media_id, deepcopy(value),
                expected_version=None) != 1:
            raise SurveyRevisionConflict("SURVEY_ATTACHMENT_IMMUTABLE")

    def get_evidence(self, survey_id: str, tenant_id: str | None = None) -> FieldSurveyEvidence | None:
        if tenant_id is not None and tenant_id != self._tenant():
            return None
        row = self.get_evidence_versioned(survey_id)
        return None if row is None else row[0]

    def get_evidence_versioned(self, survey_id: str) -> tuple[FieldSurveyEvidence, int] | None:
        row = self._store.get_versioned(self.EVIDENCE, survey_id)
        if row is None:
            return None
        value, revision = row
        if (not isinstance(value, FieldSurveyEvidence) or value.tenant_id != self._tenant()
                or value.survey_id != survey_id or type(revision) is not int or revision < 1):
            raise SurveyOwnerUnavailable("SURVEY_EVIDENCE_BINDING_INVALID")
        assignment = self.get_assignment(value.assignment_id)
        if (assignment is None or assignment.target_entity_id != value.target_entity_id
                or assignment.target_entity_kind != value.target_entity_kind
                or assignment.survey_id != survey_id):
            raise SurveyOwnerUnavailable("SURVEY_EVIDENCE_PARENT_INVALID")
        return deepcopy(value), revision

    def save_evidence_review(self, value: FieldSurveyEvidence, *, expected_version: int) -> None:
        """Narrow native review CAS; submitted observations remain immutable."""
        if (not self._command.get() or not isinstance(value, FieldSurveyEvidence)
                or value.tenant_id != self._tenant()):
            raise SurveyOwnerUnavailable("SURVEY_COMMAND_TRANSACTION_REQUIRED")
        row = self.get_evidence_versioned(value.survey_id)
        if row is None or type(expected_version) is not int or row[1] != expected_version:
            raise SurveyRevisionConflict("SURVEY_EVIDENCE_REVISION_CONFLICT")
        previous = row[0]
        record = value.review_record
        if (value.review_status not in {EvidenceReviewStatus.APPROVED,
                EvidenceReviewStatus.REJECTED, EvidenceReviewStatus.NEEDS_REVISION}
                or record is None or record.review_status != value.review_status
                or record.reviewer_id == value.submitter_id or not record.reviewer_id
                or record.reviewed_at != value.updated_at
                or previous.is_retracted or previous.is_superseded
                or replace(value, review_status=previous.review_status, review_record=previous.review_record,
                    updated_at=previous.updated_at, promotion_status=previous.promotion_status) != previous):
            raise SurveyOwnerUnavailable("SURVEY_REVIEW_MUTATION_INVALID")
        if (value.promotion_status != previous.promotion_status
                and not (value.promotion_status == PromotionStatus.NOT_PROMOTED
                    and value.review_status in {EvidenceReviewStatus.REJECTED, EvidenceReviewStatus.NEEDS_REVISION})):
            raise SurveyOwnerUnavailable("SURVEY_REVIEW_MUTATION_INVALID")
        if self._store.compare_and_put(self.EVIDENCE, value.survey_id, deepcopy(value),
                expected_version=expected_version) != expected_version + 1:
            raise SurveyRevisionConflict("SURVEY_EVIDENCE_REVISION_CONFLICT")

    def save_evidence(self, value: FieldSurveyEvidence) -> None:
        if (not self._command.get() or not isinstance(value, FieldSurveyEvidence)
                or value.tenant_id != self._tenant()):
            raise SurveyOwnerUnavailable("SURVEY_COMMAND_TRANSACTION_REQUIRED")
        assignment = self.get_assignment(value.assignment_id)
        if (assignment is None or assignment.survey_id != value.survey_id
                or assignment.target_entity_id != value.target_entity_id
                or assignment.target_entity_kind != value.target_entity_kind):
            raise SurveyOwnerUnavailable("SURVEY_EVIDENCE_PARENT_INVALID")
        if self._store.compare_and_put(self.EVIDENCE, value.survey_id, deepcopy(value),
                expected_version=None) != 1:
            raise SurveyRevisionConflict("SURVEY_EVIDENCE_IMMUTABLE")
