"""Canonical field-task commands around the existing native Survey lifecycle.

The legacy in-memory workflow stays intact. This owner requires durable SQL,
current canonical parents and trusted principals; media uses verified object bytes.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from typing import Any

from modules.market_survey.application.survey_service import MarketSurveyService
from modules.market_survey.domain.content import SurveyAttachmentRecord
from modules.market_survey.domain.models import (
    AssignmentStatus,
    MediaKind,
    SurveyAuthorizationError,
    SurveyLocation,
    SurveyStateConflictError,
    SurveyType,
    TargetEntityKind,
)
from modules.market_survey.infrastructure.assignment_repository import (
    DurableSurveyAssignmentRepository,
    SurveyOwnerUnavailable,
    SurveyRevisionConflict,
)
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.auth import Action
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue


class SurveyAssignmentNotFound(LookupError):
    audit_event: Any = None


class SurveyAssignmentForbidden(PermissionError):
    audit_event: Any = None


def audited_denial(method):
    @wraps(method)
    def invoke(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (SurveyAssignmentNotFound, SurveyAssignmentForbidden) as exc:
            # Command contexts have unwound/rolled back before this record.
            # A denied object check must not silently lose its security audit.
            if exc.audit_event is not None:
                self.audit_log.record(exc.audit_event)
            raise
    return invoke


def required_text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("SURVEY_REQUIRED_TEXT")
    return value.strip()


class _CommandAudit:
    """Keep the native events, binding their actor and reason to this command."""

    def __init__(self, actual: Any, *, tenant: str, subject: str, reason: str):
        self.actual, self.tenant, self.subject, self.reason = actual, tenant, subject, reason
        self.ids: list[str] = []

    def record(self, event):
        if event.actor != self.subject or event.metadata.get("tenant_id") != self.tenant:
            raise SurveyOwnerUnavailable("SURVEY_AUDIT_ACTOR_BINDING_INVALID")
        recorded = self.actual.record(replace(event, metadata={**event.metadata,
            "reason": self.reason, "actor_evidence": "authenticated_principal.v1"}))
        self.ids.append(recorded.event_id)
        return recorded


class CanonicalSurveyAssignmentService:
    def __init__(self, *, repository: DurableSurveyAssignmentRepository,
                 audit_log: Any, queue: Any, parents: Any, clock: Any = None) -> None:
        self.repository, self.audit_log, self.queue, self.parents = repository, audit_log, queue, parents
        self.clock = clock or (lambda: datetime.now(UTC))

    def _binding(self, principal: Any) -> DurableSurveyAssignmentRepository:
        self.parents.require_principal(principal, Action.VIEW)
        if (not isinstance(self.repository, DurableSurveyAssignmentRepository)
                or not isinstance(self.audit_log, DurableAuditLog)
                or not isinstance(self.queue, DurableJobQueue)):
            raise SurveyOwnerUnavailable("SURVEY_DURABLE_SQL_OWNER_REQUIRED")
        repo = self.repository.for_tenant(principal.tenant_id)
        if (repo.engine is not self.audit_log._engine
                or repo.engine is not self.queue._engine
                or repo.engine is not self.parents.engine):
            raise SurveyOwnerUnavailable("SURVEY_SAME_SQL_OWNER_REQUIRED")
        return repo

    def _time(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise SurveyOwnerUnavailable("SURVEY_CLOCK_UNAVAILABLE")
        return now.astimezone(UTC)

    def _media_availability(self) -> dict[str, str]:
        try:
            self.parents.content()
        except SurveyOwnerUnavailable:
            return {"state": "unavailable", "reasonCode": "SURVEY_CONTENT_OWNER_UNAVAILABLE"}
        # This confirms only a configured server adapter. Actual credential,
        # IAM, generation and bytes are verified by each content operation.
        return {"state": "configured", "reasonCode": "SURVEY_CONTENT_READBACK_REQUIRED"}

    def _view(self, repo: Any, assignment: Any) -> dict[str, Any]:
        row = repo.get_versioned(assignment.assignment_id)
        if row is None:
            raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_DISAPPEARED")
        return {**row[0].to_dict(), "revision": row[1],
                "mediaAvailability": self._media_availability()}

    @audited_denial
    def list(self, principal: Any, *, target_kind: str, target_id: str) -> dict[str, Any]:
        repo = self._binding(principal)
        self.parents.read(principal, target_kind, target_id, Action.VIEW)
        rows = [row for row in repo.list_assignments(target_entity_id=target_id)
                if row.target_entity_kind.value == target_kind]
        return {"items": [self._view(repo, row) for row in rows],
                "targetKind": target_kind, "targetId": target_id,
                "mediaAvailability": self._media_availability()}

    @audited_denial
    def get(self, principal: Any, identity: str) -> dict[str, Any]:
        repo = self._binding(principal)
        row = repo.get_assignment(identity)
        if row is None:
            raise SurveyAssignmentNotFound("SURVEY_ASSIGNMENT_NOT_FOUND")
        self.parents.read(principal, row.target_entity_kind.value, row.target_entity_id, Action.VIEW)
        return self._view(repo, row)

    @audited_denial
    def assignees(self, principal: Any, *, target_kind: str, target_id: str) -> dict[str, Any]:
        self._binding(principal)
        self.parents.read(principal, target_kind, target_id, Action.CREATE)
        return {"items": self.parents.assignees(principal, target_kind, target_id)}

    @contextmanager
    def _transaction(self, repo: Any, principal: Any, target_kind: str, target_id: str,
                     action: Action, assigned_to: str | None):
        # Parent fences precede the assignment fence, consistently for all commands.
        # SQLite BEGIN IMMEDIATE is entered before the WORM transactional sink.
        with self.parents.guard(principal, target_kind, target_id, action=action), repo.command(), ExitStack() as stack:
            if self.audit_log._worm_sink is not None:
                delivery = DurableAuditDelivery(repo.engine, principal.tenant_id,
                                                self.queue, self.audit_log._worm_sink)
                stack.enter_context(self.audit_log.transactional_worm_sink(delivery))
            self.parents.read(principal, target_kind, target_id, action)
            if assigned_to is not None:
                self.parents.assignee(principal, target_kind, target_id, assigned_to, action)
            yield

    @audited_denial
    def command(self, principal: Any, *, operation: str, payload: dict[str, Any],
                idempotency_key: str, correlation_id: str,
                assignment_id: str | None = None) -> dict[str, Any]:
        repo = self._binding(principal)
        if operation not in {"create", "assign", "claim", "start", "cancel"}:
            raise ValueError("SURVEY_ASSIGNMENT_OPERATION_UNSUPPORTED")
        action = Action.CREATE if operation == "create" else Action.UPDATE
        self.parents.require_principal(principal, action)
        key, reason = required_text(idempotency_key), required_text(payload.get("reason"))
        correlation_id = required_text(correlation_id)
        if correlation_id != self.parents.correlation_id:
            raise SurveyOwnerUnavailable("SURVEY_CORRELATION_BINDING_INVALID")
        if operation == "create":
            if assignment_id is not None:
                raise ValueError("SURVEY_CREATE_ID_SERVER_OWNED")
            kind, target = required_text(payload.get("targetKind")), required_text(payload.get("targetId"))
        else:
            row = repo.get_assignment(required_text(assignment_id))
            if row is None:
                raise SurveyAssignmentNotFound("SURVEY_ASSIGNMENT_NOT_FOUND")
            kind, target = row.target_entity_kind.value, row.target_entity_id
        # Replayed commands must still pass current parent/recipient admission.
        self.parents.read(principal, kind, target, action)
        assigned_to = payload.get("assignedTo")
        if operation == "assign" or operation == "create" and assigned_to is not None:
            assigned_to = required_text(assigned_to)
            self.parents.assignee(principal, kind, target, assigned_to, action)
        if payload.get("expiresAt") is not None:
            expiration = datetime.fromisoformat(required_text(payload["expiresAt"]).replace("Z", "+00:00"))
            if expiration.utcoffset() is None:
                raise ValueError("SURVEY_TIMEZONE_REQUIRED")

        def execute(_receipt_id: str) -> dict[str, Any]:
            now = self._time()
            audit = _CommandAudit(self.audit_log, tenant=principal.tenant_id,
                                  subject=principal.subject_id, reason=reason)
            native = MarketSurveyService(repository=repo, audit_log=audit)
            if operation == "create":
                expiration = datetime.fromisoformat(required_text(payload.get("expiresAt")).replace("Z", "+00:00"))
                if expiration.utcoffset() is None or expiration <= now:
                    raise ValueError("SURVEY_FUTURE_DEADLINE_REQUIRED")
                # No business campaign is inferred. Empty means no campaign reference.
                value = native.create_assignment(tenant_id=principal.tenant_id,
                    campaign_id="", target_entity_id=target, target_entity_kind=TargetEntityKind(kind),
                    survey_type=SurveyType.PHYSICAL_FEASIBILITY, expires_at=expiration,
                    created_by=principal.subject_id, assigned_to=assigned_to,
                    instructions={"reason": reason}, correlation_id=correlation_id, now=now)
            else:
                current = repo.get_versioned(assignment_id)
                if current is None:
                    raise SurveyAssignmentNotFound("SURVEY_ASSIGNMENT_NOT_FOUND")
                value, version = current
                if value.target_entity_kind.value != kind or value.target_entity_id != target:
                    raise SurveyOwnerUnavailable("SURVEY_ASSIGNMENT_PARENT_CHANGED")
                expected = payload.get("expectedRevision")
                if type(expected) is not int or expected != version:
                    raise SurveyRevisionConflict("SURVEY_REVISION_CONFLICT")
                if operation == "assign":
                    # Refresh the real account/scope inside the same command boundary.
                    self.parents.assignee(principal, kind, target, assigned_to, action)
                    value = native.assign_survey(assignment_id, assigned_to=assigned_to,
                        assigned_by=principal.subject_id, expires_at=payload.get("expiresAt"),
                        tenant_id=principal.tenant_id, correlation_id=correlation_id, now=now)
                elif operation == "claim":
                    value = native.claim_assignment(assignment_id, actor_id=principal.subject_id,
                        tenant_id=principal.tenant_id, correlation_id=correlation_id, now=now)
                elif operation == "start":
                    value = native.start_survey(assignment_id, actor_id=principal.subject_id,
                        tenant_id=principal.tenant_id, correlation_id=correlation_id, now=now)
                else:
                    value = native.cancel_assignment(assignment_id, actor_id=principal.subject_id,
                        reason=reason, tenant_id=principal.tenant_id, correlation_id=correlation_id, now=now)
            return {"assignment": self._view(repo, value), "auditEventIds": audit.ids}

        receipts = TenantScopedCommandReceiptStore(queue=self.queue, service="market-survey.assignments",
            transaction_factory=lambda: self._transaction(repo, principal, kind, target, action, assigned_to))
        scope = ":".join([operation, assignment_id or "create", sha256(principal.subject_id.encode()).hexdigest()])
        outcome = receipts.run(tenant_id=principal.tenant_id, idempotency_key=key, scope=scope,
            payload={"operation": operation, "assignment_id": assignment_id, "input": payload,
                     "target_kind": kind, "target_id": target,
                     "actor": principal.subject_id, "roles": sorted(role.value for role in principal.roles)},
            correlation_id=correlation_id, operation=execute)
        return {**outcome.value, "replayed": outcome.replayed, "receiptId": outcome.receipt_id}


    def _owned_assignment(self, principal: Any, identity: str, action: Action):
        repo = self._binding(principal)
        self.parents.require_principal(principal, action)
        assignment = repo.get_assignment(required_text(identity))
        if assignment is None:
            raise SurveyAssignmentNotFound("SURVEY_ASSIGNMENT_NOT_FOUND")
        self.parents.read(principal, assignment.target_entity_kind.value, assignment.target_entity_id, action)
        return repo, assignment

    @staticmethod
    def _same_surveyor(assignment: Any, principal: Any) -> None:
        if assignment.assigned_to and assignment.assigned_to != principal.subject_id:
            raise SurveyAuthorizationError("Only the actual assigned surveyor may submit or attach field evidence")

    def _submission_ready(self, assignment: Any, principal: Any) -> None:
        # Same native submission rules; this does not approve/promote evidence.
        self._same_surveyor(assignment, principal)
        expires = datetime.fromisoformat(assignment.expires_at.replace("Z", "+00:00"))
        if (expires.utcoffset() is None or expires <= self._time()
                or assignment.status not in {AssignmentStatus.ASSIGNED, AssignmentStatus.CLAIMED,
                                             AssignmentStatus.IN_PROGRESS}):
            raise SurveyStateConflictError("Assignment cannot accept a new field submission")

    @contextmanager
    def _submission_transaction(self, repo: Any, principal: Any, assignment: Any):
        with self._transaction(repo, principal, assignment.target_entity_kind.value,
                               assignment.target_entity_id, Action.UPDATE, None):
            current = repo.get_assignment(assignment.assignment_id)
            if current is None:
                raise SurveyAssignmentNotFound("SURVEY_ASSIGNMENT_NOT_FOUND")
            self._same_surveyor(current, principal)
            yield

    def _expected_assignment(self, repo: Any, identity: str, payload: dict[str, Any]):
        current = repo.get_versioned(identity)
        if current is None:
            raise SurveyAssignmentNotFound("SURVEY_ASSIGNMENT_NOT_FOUND")
        if type(payload.get("expectedRevision")) is not int or payload["expectedRevision"] != current[1]:
            raise SurveyRevisionConflict("SURVEY_REVISION_CONFLICT")
        return current[0]

    @audited_denial
    def attachments(self, principal: Any, identity: str) -> dict[str, Any]:
        repo, assignment = self._owned_assignment(principal, identity, Action.VIEW)
        # Missing provider does not falsify actual saved metadata or prevent
        # reading the assignment; only byte operations require its binding.
        try:
            self.parents.require_principal(principal, Action.UPDATE)
        except SurveyAssignmentForbidden:
            can_manage = False
        else:
            can_manage = True
        return {"assignmentId": assignment.assignment_id, "assignment": self._view(repo, assignment),
                "currentSubjectId": principal.subject_id, "canRequestUpdate": can_manage,
                "items": [row.public() for row in repo.list_media(identity)]}

    @audited_denial
    def upload_attachment(self, principal: Any, *, identity: str, payload: dict[str, Any],
                          idempotency_key: str, correlation_id: str) -> dict[str, Any]:
        repo, assignment = self._owned_assignment(principal, identity, Action.UPDATE)
        self._same_surveyor(assignment, principal)
        reason, key = required_text(payload.get("reason")), required_text(idempotency_key)
        if correlation_id != self.parents.correlation_id:
            raise SurveyOwnerUnavailable("SURVEY_CORRELATION_BINDING_INVALID")
        try:
            data = base64.b64decode(required_text(payload.get("dataBase64")), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("SURVEY_ATTACHMENT_BYTES_INVALID") from exc
        if not data:
            raise ValueError("SURVEY_ATTACHMENT_BYTES_REQUIRED")
        captured = datetime.fromisoformat(required_text(payload.get("capturedAt")).replace("Z", "+00:00"))
        if captured.utcoffset() is None:
            raise ValueError("SURVEY_TIMEZONE_REQUIRED")
        kind = MediaKind(required_text(payload.get("mediaKind")))
        content_type = required_text(payload.get("contentType"))
        if "\r" in content_type or "\n" in content_type:
            raise ValueError("SURVEY_CONTENT_TYPE_INVALID")
        caption = payload.get("caption")
        if caption is not None and not isinstance(caption, str):
            raise ValueError("SURVEY_CAPTION_INVALID")
        digest = sha256(data).hexdigest()
        binding = sha256(json.dumps([principal.tenant_id, identity, principal.subject_id, key],
            separators=(",", ":")).encode()).hexdigest()
        media_id = "media-" + binding
        content = self.parents.content()
        canonical_payload = {k: v for k, v in payload.items() if k != "dataBase64"}
        canonical_payload["actualSha256"] = digest

        def execute(_receipt_id):
            current = self._expected_assignment(repo, identity, payload)
            self._submission_ready(current, principal)
            uri, generation = content.put_verified(tenant=principal.tenant_id,
                key=f"tenants/{principal.tenant_id}/market-survey/assignments/{identity}/attachments/{media_id}",
                data=data, content_type=content_type)
            row = SurveyAttachmentRecord(media_id, principal.tenant_id, identity,
                current.target_entity_id, current.target_entity_kind.value, principal.subject_id,
                uri, generation, digest, len(data), content_type, kind, captured.isoformat(),
                caption, self._time().isoformat())
            repo.save_media(row)
            audit = _CommandAudit(self.audit_log, tenant=principal.tenant_id,
                                  subject=principal.subject_id, reason=reason)
            from shared.audit import AuditEvent
            audit.record(AuditEvent(event_type="market_survey.attachment_uploaded.v1",
                actor=principal.subject_id, action="upload_attachment", resource=f"survey_assignment/{identity}",
                outcome="verified", correlation_id=correlation_id,
                metadata={"tenant_id": principal.tenant_id, "assignment_id": identity,
                          "media_id": media_id, "sha256": digest, "size_bytes": len(data),
                          "object_generation": generation}))
            return {"attachment": row.public(), "auditEventIds": audit.ids}

        receipts = TenantScopedCommandReceiptStore(queue=self.queue, service="market-survey.attachments",
            transaction_factory=lambda: self._submission_transaction(repo, principal, assignment))
        outcome = receipts.run(tenant_id=principal.tenant_id, idempotency_key=key,
            scope="upload:" + identity + ":" + sha256(principal.subject_id.encode()).hexdigest(),
            payload={"input": canonical_payload, "actor": principal.subject_id,
                     "roles": sorted(role.value for role in principal.roles)},
            correlation_id=correlation_id, operation=execute)
        actual = repo.get_media(media_id, identity)
        if actual is None or actual.assignment_id != identity or actual.uploader_id != principal.subject_id:
            raise SurveyOwnerUnavailable("SURVEY_ATTACHMENT_BINDING_INVALID")
        content.read_verified(actual)
        return {"attachment": actual.public(), "auditEventIds": outcome.value["auditEventIds"],
                "receiptId": outcome.receipt_id, "replayed": outcome.replayed}

    @audited_denial
    def submit_evidence(self, principal: Any, *, identity: str, payload: dict[str, Any],
                        idempotency_key: str, correlation_id: str) -> dict[str, Any]:
        repo, assignment = self._owned_assignment(principal, identity, Action.UPDATE)
        self._same_surveyor(assignment, principal)
        reason = required_text(payload.get("reason"))
        if correlation_id != self.parents.correlation_id:
            raise SurveyOwnerUnavailable("SURVEY_CORRELATION_BINDING_INVALID")
        media_ids = payload.get("mediaIds", [])
        if (not isinstance(media_ids, list) or not all(isinstance(item, str) and item.strip() for item in media_ids)
                or len(media_ids) != len(set(media_ids))):
            raise ValueError("SURVEY_MEDIA_IDS_INVALID")
        location = payload.get("location")
        if (not isinstance(location, dict) or set(location) - {"latitude", "longitude", "address", "h3_index", "srid"}
                or any(isinstance(location.get(k), bool) or not isinstance(location.get(k), (int, float))
                       or not math.isfinite(location[k]) for k in ("latitude", "longitude"))
                or not -90 <= location["latitude"] <= 90 or not -180 <= location["longitude"] <= 180):
            raise ValueError("SURVEY_LOCATION_INVALID")
        confidence = payload.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError("SURVEY_CONFIDENCE_INVALID")
        attributes = payload.get("attributes", {})
        if not isinstance(attributes, dict):
            raise ValueError("SURVEY_ATTRIBUTES_INVALID")
        try:
            json.dumps(attributes, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("SURVEY_ATTRIBUTES_INVALID") from exc
        surveyed_at = datetime.fromisoformat(required_text(payload.get("surveyedAt")).replace("Z", "+00:00"))
        if surveyed_at.utcoffset() is None:
            raise ValueError("SURVEY_TIMEZONE_REQUIRED")

        def execute(_receipt_id):
            current = self._expected_assignment(repo, identity, payload)
            self._submission_ready(current, principal)
            attachments = []
            if media_ids:
                content = self.parents.content()
                for media_id in media_ids:
                    row = repo.get_media(media_id, identity)
                    if (row is None or row.assignment_id != identity or row.tenant_id != principal.tenant_id
                            or row.uploader_id != principal.subject_id):
                        raise SurveyAssignmentNotFound("SURVEY_ATTACHMENT_NOT_FOUND")
                    content.read_verified(row)
                    attachments.append(row.native_attachment())
            audit = _CommandAudit(self.audit_log, tenant=principal.tenant_id,
                                  subject=principal.subject_id, reason=reason)
            native = MarketSurveyService(repository=repo, audit_log=audit)
            current, evidence = native.submit_survey(identity, actor_id=principal.subject_id,
                tenant_id=principal.tenant_id, location=SurveyLocation.from_dict(location), attributes=attributes,
                media_attachments=attachments, surveyed_at=surveyed_at,
                confidence=confidence, metadata={"actor_evidence": "authenticated_principal.v1", "reason": reason},
                correlation_id=correlation_id, now=self._time())
            return {"assignment": self._view(repo, current), "surveyId": evidence.survey_id,
                    "observationId": evidence.observation_id, "auditEventIds": audit.ids}

        receipts = TenantScopedCommandReceiptStore(queue=self.queue, service="market-survey.submission",
            transaction_factory=lambda: self._submission_transaction(repo, principal, assignment))
        outcome = receipts.run(tenant_id=principal.tenant_id, idempotency_key=required_text(idempotency_key),
            scope="submit:" + identity + ":" + sha256(principal.subject_id.encode()).hexdigest(),
            payload={"input": payload, "actor": principal.subject_id,
                     "roles": sorted(role.value for role in principal.roles)},
            correlation_id=correlation_id, operation=execute)
        evidence = repo.get_evidence(outcome.value["surveyId"])
        if evidence is None or evidence.assignment_id != identity or evidence.submitter_id != principal.subject_id:
            raise SurveyOwnerUnavailable("SURVEY_EVIDENCE_BINDING_INVALID")
        return {**outcome.value, "receiptId": outcome.receipt_id, "replayed": outcome.replayed}

    @audited_denial
    def download_attachment(self, principal: Any, *, identity: str, media_id: str) -> dict[str, Any]:
        repo, assignment = self._owned_assignment(principal, identity, Action.VIEW)
        row = repo.get_media(required_text(media_id), identity)
        if row is None or row.assignment_id != identity:
            raise SurveyAssignmentNotFound("SURVEY_ATTACHMENT_NOT_FOUND")
        content = self.parents.content()
        with self._transaction(repo, principal, assignment.target_entity_kind.value,
                               assignment.target_entity_id, Action.VIEW, None):
            data = content.read_verified(row)
            from shared.audit import AuditEvent
            event = self.audit_log.record(AuditEvent(event_type="market_survey.attachment_downloaded.v1",
                actor=principal.subject_id, action="download_attachment", resource=f"survey_assignment/{identity}",
                outcome="verified", correlation_id=self.parents.correlation_id,
                metadata={"tenant_id": principal.tenant_id, "media_id": row.media_id,
                          "sha256": row.sha256, "size_bytes": row.size_bytes}))
        return {"data": data, "mediaId": row.media_id, "sha256": row.sha256, "auditEventId": event.event_id}

    @audited_denial
    def evidence(self, principal: Any, *, identity: str) -> dict[str, Any]:
        repo, assignment = self._owned_assignment(principal, identity, Action.VIEW)
        row = repo.get_evidence(assignment.survey_id) if assignment.survey_id else None
        if row is None:
            raise SurveyAssignmentNotFound("SURVEY_EVIDENCE_NOT_FOUND")
        value = row.to_dict()
        value["media_attachments"] = [repo.get_media(item.media_id, identity).public() for item in row.media_attachments]
        return value
