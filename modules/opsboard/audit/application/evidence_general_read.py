"""Current-authority history and safe JSON downloads for ordinary exports.

Reads never regenerate a retained artifact from fresh scoring data. The real
current candidate/tenant/field guards govern access; immutable retained bytes,
current hold/purge state and the external original-snapshot ACK stay distinct.
"""
from __future__ import annotations

import json
from dataclasses import replace

from modules.opsboard.audit.application.evidence_general_admission import SUPPORTED_CLASSIFICATION
from modules.opsboard.audit.application.evidence_general_formats import render_general_document
from modules.opsboard.audit.application.evidence_general_retention import (
    BUNDLE_SCHEMA,
    GeneralSiteScoreRetainedExport,
    general_retained_document_bytes,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceUnavailable,
)
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.events import AuditEvent
from shared.audit.integrity import sha256_hex
from shared.audit.retained_delivery import JOB_TYPE, verified_retained_receipt
from shared.auth import Action, Principal, rbac_allows
from shared.workflow.sitescore import SiteScoreDecision


def _missing(name: str) -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:" + name)


class GeneralSiteScoreRetainedReader:
    def __init__(self, service: GeneralSiteScoreRetainedExport) -> None:
        if not isinstance(service, GeneralSiteScoreRetainedExport):
            raise _missing("general_export_owner")
        self.service = service

    def _current(self, principal: Principal, export_id: str):
        service = self.service
        service._binding(principal)
        if (not principal.authenticated
                or not rbac_allows(principal, "audit", Action.EXPORT)
                or not rbac_allows(principal, "operator_console", Action.VIEW)
                or not principal.scope.permits_module("operator_console")
                or not rbac_allows(principal, "sitescore", Action.VIEW)
                or not principal.scope.permits_classification(SUPPORTED_CLASSIFICATION)):
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SCOPE_DENIED")
        if not isinstance(export_id, str) or not export_id.strip():
            raise ValueError("Actual export reference required")
        # No record whose ownership cannot yet be verified may reveal whether
        # a retained ID exists, is corrupt, or has this tenant's delivery facts.
        try:
            record = service.delivery.evidence_store.get(export_id)
        except Exception as exc:
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SOURCE_NOT_FOUND") from exc
        if record is None or record.requested_by != principal.subject_id:
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SOURCE_NOT_FOUND")
        if record.purged_at is None:
            boundary = record.bundle.get("identity_boundary")
            if boundary != {"tenant_id": principal.tenant_id, "subject_id": principal.subject_id}:
                raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SOURCE_NOT_FOUND")
        else:
            # Purge deliberately removes the original bundle identity. Its
            # verified immutable export AuditEvent remains the tenant owner;
            # this proof precedes any tenant intent/job or external sink read.
            try:
                anchor = service.delivery.audit_log.get_event(record.audit_event_id,
                    tenant_id=principal.tenant_id)
            except Exception as exc:
                raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SOURCE_NOT_FOUND") from exc
            if (anchor is None or anchor.actor != principal.subject_id
                    or anchor.resource != "audit-evidence/" + record.export_id
                    or anchor.event_type != "audit.evidence_export.v1" or anchor.action != "export"
                    or anchor.metadata.get("tenant_id") != principal.tenant_id
                    or anchor.metadata.get("decision_id") != record.program_id
                    or anchor.metadata.get("bundle_checksum") != record.bundle_checksum):
                raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SOURCE_NOT_FOUND")
        record, intent, _job = service.delivery._facts(export_id)
        event = service.delivery.audit_log.get_event(record.audit_event_id, tenant_id=principal.tenant_id)
        if (event.event_type != "audit.evidence_export.v1" or event.action != "export"
                or event.metadata.get("decision_id") != record.program_id):
            raise _missing("general_export_audit_binding")
        decision = service.admission.source_owner.decisions.get_decision(record.program_id)
        candidate = event.metadata.get("candidate_id")
        if (not isinstance(decision, SiteScoreDecision)
                or decision.tenant_id != principal.tenant_id
                or decision.candidate_site_id != candidate):
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SOURCE_NOT_FOUND")
        service.admission.source_owner._authorize(principal, candidate)
        if record.purged_at is None:
            if record.bundle.get("schema") != BUNDLE_SCHEMA:
                raise _missing("retained_general_schema")
            general_retained_document_bytes(record)
            row = record.bundle["document"]["sections"].get("decision_log")
            if row and (row[0].get("decision_id"), row[0].get("candidate_id")) != (record.program_id, candidate):
                raise _missing("general_export_candidate_binding")
        return record, intent, candidate

    @staticmethod
    def _summary(record, intent) -> dict:
        # Never return native report, audit metadata, internal bundle or actor
        # collection as history. These are the original owner's safe facts.
        receipt = intent.get("receipt")
        return {"export_id": record.export_id, "decision_id": record.program_id,
            "reason": record.purpose, "generated_at": record.generated_at.isoformat(),
            "period_start": record.period_start.isoformat(), "period_end": record.period_end.isoformat(),
            "classification": record.data_classification, "masked": True,
            "retain_until": record.retain_until.isoformat(), "legal_hold": record.legal_hold,
            "purged_at": record.purged_at.isoformat() if record.purged_at else None,
            "delivery_status": intent.get("status"),
            "external_acknowledged": intent.get("status") == "delivered" and isinstance(receipt, dict),
            "download_ready": intent.get("status") == "delivered" and record.purged_at is None}

    def history(self, principal: Principal) -> list[dict]:
        self.service._binding(principal)
        rows = []
        with self.service.engine.transaction():
            for record in self.service.delivery.evidence_store.list_all():
                if record.requested_by != principal.subject_id:
                    continue
                event = self.service.delivery.audit_log.get_event(record.audit_event_id,
                    tenant_id=principal.tenant_id)
                if (event is None or event.resource != "audit-evidence/" + record.export_id
                        or event.metadata.get("retained_delivery_job_type") != JOB_TYPE
                        or not isinstance(event.metadata.get("decision_id"), str)):
                    continue
                try:
                    record, intent, _candidate = self._current(principal, record.export_id)
                except SiteScoreEvidenceDenied:
                    continue
                if intent.get("status") == "delivered":
                    verified_retained_receipt(self.service.delivery.sink_id, intent.get("receipt"), record, intent)
                    if record.purged_at is None:
                        self._external(record, intent)
                rows.append(self._summary(record, intent))
        return rows

    def content(self, principal: Principal, export_id: str, *, correlation_id: str) -> bytes:
        return self.artifact(principal, export_id, correlation_id=correlation_id, format="json").content

    def artifact(self, principal: Principal, export_id: str, *, correlation_id: str, format: str):
        if format not in ("json", "csv", "pdf"):
            raise _missing("unimplemented_artifact_format")
        if not isinstance(correlation_id, str) or not correlation_id.strip():
            raise ValueError("Actual request correlation required")
        service = self.service
        with service.engine.transaction():
            # SQLite transaction() is deferred until a statement starts it;
            # the audit outbox requires the real SQL transaction already active.
            # PostgreSQL keeps the original transaction/connection semantics.
            if getattr(service.engine, "dialect", "") != "postgresql" and not service.engine._conn.in_transaction:
                service.engine.execute("BEGIN IMMEDIATE")
            record, intent, candidate = self._current(principal, export_id)
            if record.purged_at is not None:
                raise _missing("purged_content")
            if intent.get("status") != "delivered":
                raise _missing("retained_external_delivery_pending")
            receipt = self._external(record, intent)
            content = general_retained_document_bytes(record)
            # Assert the exact bytes are the allowlist document, rather than a
            # retained SQL row or a JSON name attached to a different format.
            if json.loads(content) != record.bundle["document"]:
                raise _missing("retained_general_projection")
            artifact = render_general_document(record.bundle["document"], format)
            event = AuditEvent(event_type="audit.evidence_download.v1", actor=principal.subject_id,
                action="export", resource="audit-evidence/" + record.export_id, outcome="response_generated",
                correlation_id=correlation_id, metadata={"tenant_id": principal.tenant_id,
                    "export_id": record.export_id, "reason": record.purpose,
                    "decision_id": record.program_id, "artifact_sha256": artifact.sha256,
                    "artifact_bytes": len(artifact.content), "format": format, "masking_profile": "masked",
                    "canonical_json_sha256": sha256_hex(record.bundle["document"]),
                    "retained_snapshot_checksum": receipt["checksum"],
                    "retained_canonical_json_snapshot_checksum": receipt["checksum"],
                    "retained_canonical_json_external_acknowledged": True,
                    "retained_ack_scope": "original_retained_record_snapshot_including_canonical_json",
                    "generated_artifact_external_acknowledged": False})
            audit_delivery = DurableAuditDelivery(service.engine, principal.tenant_id,
                service.delivery.queue, service.delivery.sink)
            with service.delivery.audit_log.transactional_worm_sink(audit_delivery):
                service.delivery.audit_log.record(event)
            # Recheck immediately before returning; this read never calls a
            # scoring writer or supplies missing execution/outcome facts.
            service.admission.source_owner._authorize(principal, candidate)
            return artifact

    def _external(self, record, intent) -> dict:
        service = self.service
        receipt = verified_retained_receipt(service.delivery.sink_id, intent.get("receipt"), record, intent)
        # General producers always commit the original without hold/purge.
        # Reconstruct that exact snapshot only if its anchored hash matches;
        # legal hold never changes the externally retained original bytes.
        original = replace(record, legal_hold=False, governance_log=(),
            governance_hash=intent["original_governance_hash"], purged_at=None, tombstone_hash=None)
        if sha256_hex(original.to_dict()) != intent["original_snapshot_sha256"]:
            raise _missing("retained_original_snapshot_required")
        lookup = getattr(service.delivery.sink, "lookup_retained_evidence", None)
        actual_receipt = lookup(original) if callable(lookup) else None
        if actual_receipt is None or verified_retained_receipt(service.delivery.sink_id,
                actual_receipt, record, intent) != receipt:
            raise _missing("retained_external_receipt_unavailable")
        return receipt
