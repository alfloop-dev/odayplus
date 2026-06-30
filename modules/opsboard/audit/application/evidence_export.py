from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.opsboard.audit.domain.evidence import (
    AUDIT_EVIDENCE_POLICY_VERSION,
    AuditEvidenceBundle,
    DecisionCard,
    EvidenceExportRequest,
    build_bundle_checksum,
    build_subsidy_matrix,
)
from modules.opsboard.audit.evidence_store import retained_evidence_from_bundle
from shared.audit import AuditEvent, InMemoryAuditLog
from shared.audit.persistence import EvidenceBundleStore, resolve_retention_policy


class AuditEvidenceExportError(ValueError):
    """Raised when an evidence export request is incomplete or invalid."""


class AuditEvidenceExportService:
    """Builds an immutable audit evidence bundle for review and subsidy checks.

    When an ``evidence_store`` is supplied the built bundle is also persisted as
    a hash-stamped, retention-scoped record so the export survives a process
    restart (ODP-PV-011). With no store the service behaves exactly as before.
    """

    def __init__(
        self,
        *,
        audit_log: InMemoryAuditLog | None = None,
        evidence_store: EvidenceBundleStore | None = None,
    ) -> None:
        self.audit_log = audit_log or InMemoryAuditLog()
        self.evidence_store = evidence_store

    def export(
        self,
        request: EvidenceExportRequest,
        *,
        decision_cards: Sequence[DecisionCard],
        generated_at: datetime | None = None,
    ) -> AuditEvidenceBundle:
        try:
            self._validate_request(request)
            if not decision_cards:
                raise AuditEvidenceExportError(
                    "export requires at least one decision card"
                )
        except AuditEvidenceExportError as exc:
            # A rejected sensitive export must leave an audit trail (the denial
            # itself is auditable, not just successful exports).
            if request.sensitive:
                self._record_denial(request, reason=str(exc))
            raise

        normalized_generated_at = generated_at or datetime.now(UTC)
        events = tuple(
            event.to_dict()
            for event in self._events_for_request(
                correlation_ids=request.correlation_ids,
                from_time=request.from_time,
                to_time=request.to_time,
            )
        )
        matrix = build_subsidy_matrix(decision_cards)
        missing = tuple(row.requirement_id for row in matrix if row.status != "READY")
        export_id = f"audit-export-{uuid4()}"
        checksum = build_bundle_checksum(
            export_id=export_id,
            request=request,
            decision_cards=decision_cards,
            audit_events=events,
            subsidy_matrix=matrix,
            generated_at=normalized_generated_at,
        )
        retention_policy = resolve_retention_policy(
            request.data_classification, sensitive=request.sensitive
        )
        retain_until = retention_policy.retain_until(normalized_generated_at)
        export_event = self.audit_log.record(
            AuditEvent(
                event_type="audit.evidence_export.v1",
                actor=request.requested_by,
                action="export",
                resource=f"audit-evidence/{request.program_id}",
                outcome="ready" if not missing else "incomplete",
                correlation_id=request.correlation_ids[0],
                metadata={
                    "export_id": export_id,
                    "purpose": request.purpose,
                    "decision_count": len(decision_cards),
                    "audit_event_count": len(events),
                    "missing_requirements": list(missing),
                    "bundle_checksum": checksum,
                    "policy_version": AUDIT_EVIDENCE_POLICY_VERSION,
                    "retention_class": retention_policy.retention_class,
                    "retain_until": retain_until.isoformat(),
                    "data_classification": request.data_classification,
                },
            )
        )
        bundle = AuditEvidenceBundle(
            export_id=export_id,
            program_id=request.program_id,
            purpose=request.purpose,
            requested_by=request.requested_by,
            period_start=request.from_time,
            period_end=request.to_time,
            generated_at=normalized_generated_at,
            policy_version=AUDIT_EVIDENCE_POLICY_VERSION,
            decision_cards=tuple(decision_cards),
            audit_events=events,
            subsidy_matrix=matrix,
            missing_requirements=missing,
            bundle_checksum=checksum,
            audit_event_id=export_event.event_id,
            retention_class=retention_policy.retention_class,
            retain_until=retain_until,
        )
        if self.evidence_store is not None:
            self.evidence_store.save(
                retained_evidence_from_bundle(
                    bundle,
                    request,
                    retention_policy=retention_policy,
                    correlation_id=request.correlation_ids[0],
                )
            )
        return bundle

    def _events_for_request(
        self,
        *,
        correlation_ids: Sequence[str],
        from_time: datetime,
        to_time: datetime,
    ) -> Iterable[AuditEvent]:
        seen: set[str] = set()
        for correlation_id in correlation_ids:
            for event in self.audit_log.list_events(correlation_id=correlation_id):
                if event.event_id in seen:
                    continue
                if from_time <= event.occurred_at <= to_time:
                    seen.add(event.event_id)
                    yield event

    def _record_denial(self, request: EvidenceExportRequest, *, reason: str) -> None:
        """Audit a denied sensitive export request (durable when the log is)."""

        correlation_id = request.correlation_ids[0] if request.correlation_ids else "unknown"
        self.audit_log.record(
            AuditEvent(
                event_type="audit.evidence_export.v1",
                actor=request.requested_by or "unknown",
                action="export",
                resource=f"audit-evidence/{request.program_id or 'unknown'}",
                outcome="denied",
                correlation_id=correlation_id,
                metadata={
                    "reason": reason,
                    "sensitive": True,
                    "data_classification": request.data_classification,
                    "export_scope": request.export_scope,
                },
            )
        )

    def _validate_request(self, request: EvidenceExportRequest) -> None:
        if not request.program_id.strip():
            raise AuditEvidenceExportError("program_id is required")
        if not request.requested_by.strip():
            raise AuditEvidenceExportError("requested_by is required")
        if not request.purpose.strip():
            raise AuditEvidenceExportError("purpose is required")
        if not request.correlation_ids:
            raise AuditEvidenceExportError("at least one correlation_id is required")
        if request.sensitive and not request.export_scope.strip():
            raise AuditEvidenceExportError("sensitive export requires export_scope")
        if request.from_time > request.to_time:
            raise AuditEvidenceExportError("from_time must be before to_time")


def decision_card_from_mapping(payload: dict[str, Any]) -> DecisionCard:
    """Parse API payloads into a typed card while keeping route code thin."""

    card = DecisionCard(
        decision_id=str(payload["decision_id"]),
        decision_type=str(payload["decision_type"]),
        module=str(payload["module"]),
        title=str(payload["title"]),
        subject_ref=str(payload["subject_ref"]),
        outcome=str(payload["outcome"]),
        owner=str(payload["owner"]),
        decided_at=_parse_time(str(payload["decided_at"])),
        rationale=str(payload["rationale"]),
        input_snapshot_id=str(payload["input_snapshot_id"]),
        evidence_refs=tuple(str(item) for item in payload.get("evidence_refs", ())),
        model_refs=tuple(str(item) for item in payload.get("model_refs", ())),
        policy_refs=tuple(str(item) for item in payload.get("policy_refs", ())),
        audit_event_ids=tuple(str(item) for item in payload.get("audit_event_ids", ())),
        subsidy_requirements=tuple(
            str(item) for item in payload.get("subsidy_requirements", ())
        ),
        controls=tuple(str(item) for item in payload.get("controls", ())),
        prediction_ref=_optional_str(payload.get("prediction_ref")),
        recommendation_ref=_optional_str(payload.get("recommendation_ref")),
        approval_ref=_optional_str(payload.get("approval_ref")),
        execution_ref=_optional_str(payload.get("execution_ref")),
        outcome_ref=_optional_str(payload.get("outcome_ref")),
        feature_version=_optional_str(payload.get("feature_version")),
        data_snapshot_id=_optional_str(payload.get("data_snapshot_id")),
        artifact_hash=_optional_str(payload.get("artifact_hash")),
        risk_flags=tuple(str(item) for item in payload.get("risk_flags", ())),
        metrics={str(key): value for key, value in payload.get("metrics", {}).items()},
    )
    return replace(card, readiness=card.resolve_readiness())


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
