"""Actual general Decision/Audit JSON generation in one retained SQL commit.

Audit Evidence UI Spec section 9 uses the real export reason and shared policy;
no approved program/purpose/retention records are invented. This is an internal
service, not an HTTP mount, a subsidy eligibility claim or complete QA03 UAT.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from modules.opsboard.audit.application.evidence_general_admission import (
    GeneralDecisionEvidenceSelection,
    GeneralSiteScoreEvidenceAdmission,
    general_policy_fingerprint,
)
from modules.opsboard.audit.application.evidence_general_document import (
    CHAIN_GENERAL_SCHEMA,
    validate_general_document,
)
from modules.opsboard.audit.application.evidence_general_document import (
    LIMITS as LIMITS,
)
from modules.opsboard.audit.application.evidence_projection import (
    EvidencePolicyScope,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from modules.opsboard.audit.domain.evidence import AUDIT_EVIDENCE_POLICY_VERSION
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.events import AuditEvent
from shared.audit.integrity import canonical_json, sha256_hex
from shared.audit.persistence import (
    RetainedEvidence,
    resolve_retention_policy,
    verify_retained_evidence,
)
from shared.audit.policy import AuditOutcome
from shared.audit.retained_delivery import JOB_TYPE, RetainedEvidenceDelivery
from shared.auth import DataClassification, Principal
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue

BUNDLE_SCHEMA = "sitescore.general-decision-audit-retained.v1"


def _missing(name: str) -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:" + name)


def _scope_dict(scope: EvidencePolicyScope) -> dict:
    return {**asdict(scope), "period_start": scope.period_start.isoformat(), "period_end": scope.period_end.isoformat()}


def general_retained_document_bytes(record: RetainedEvidence) -> bytes:
    """Revalidate the complete fixed schema; never serialize native opaque rows."""
    verify_retained_evidence(record)
    if record.purged_at is not None:
        raise _missing("purged_content")
    bundle = record.bundle
    if (not isinstance(bundle, dict) or set(bundle) != {"schema", "identity_boundary", "source_digest",
            "document", "selected_scope", "selection", "policy", "artifact_sha256", "bundle_checksum"}
            or bundle["schema"] != BUNDLE_SCHEMA):
        raise _missing("retained_general_schema")
    boundary = bundle["identity_boundary"]
    document = bundle["document"]
    if (not isinstance(boundary, dict) or set(boundary) != {"tenant_id", "subject_id"}
            or not all(isinstance(v, str) and v.strip() for v in boundary.values())
            or boundary["subject_id"] != record.requested_by
            or not isinstance(document, dict) or document.get("source_digest") != bundle["source_digest"]):
        raise _missing("retained_general_schema")
    validate_general_document(document)
    try:
        scope = bundle["selected_scope"]
        if not isinstance(scope, dict) or set(scope) != {"format", "destination", "period_start", "period_end"}:
            raise ValueError("Invalid scope")
        selected_scope = EvidencePolicyScope(scope["format"], scope["destination"],
            datetime.fromisoformat(scope["period_start"]), datetime.fromisoformat(scope["period_end"]))
        selected = bundle["selection"]
        if not isinstance(selected, dict) or set(selected) != {"selector", "reason", "program_label"}:
            raise ValueError("Invalid selection")
        source_selector = selected["selector"]
        if not isinstance(source_selector, dict) or set(source_selector) != {
                "decision_id", "expected_decision_version", "sections"}:
            raise ValueError("Invalid source selector")
        if not isinstance(source_selector["sections"], (list, tuple)):
            raise ValueError("Invalid source sections container")
        selector = SiteScoreEvidenceSelector(source_selector["decision_id"],
            source_selector["expected_decision_version"], tuple(source_selector["sections"]))
        selection = GeneralDecisionEvidenceSelection(selector, selected["reason"], selected_scope,
                                                      selected["program_label"])
        if (selected_scope.format != "json" or selected_scope.destination != "authenticated-response"
                or tuple(document["sections"]) != selector.sections or record.program_id != selector.decision_id
                or record.purpose != selection.reason
                or record.period_start != selected_scope.period_start or record.period_end != selected_scope.period_end):
            raise ValueError("Stored scope/actor differs")
    except (KeyError, TypeError, ValueError) as exc:
        raise _missing("retained_general_selection") from exc
    classification = DataClassification.CONFIDENTIAL
    retention = resolve_retention_policy(classification.name.lower(), sensitive=False)
    expected_policy = {"policy_version": AUDIT_EVIDENCE_POLICY_VERSION,
        "policy_fingerprint": general_policy_fingerprint(classification, include_recorded_chain=document["schema"] == CHAIN_GENERAL_SCHEMA), "classification": classification.name,
        "retention_class": retention.retention_class, "retention_days": retention.retention_days,
        "resource_type": "sitescore", "program_label_is_selection_only": True}
    if (bundle["policy"] != expected_policy or record.data_classification != "confidential"
            or record.sensitive is not False or record.retention_class != retention.retention_class
            or record.retain_until != retention.retain_until(record.generated_at)
            or record.export_scope != ",".join(selector.sections)
            or sha256_hex(document) != bundle["artifact_sha256"]
            or sha256_hex({k: v for k, v in bundle.items() if k != "bundle_checksum"}) != record.bundle_checksum
            or bundle["bundle_checksum"] != record.bundle_checksum):
        raise _missing("retained_general_policy_or_hash")
    return canonical_json(document).encode("utf-8")


class GeneralSiteScoreRetainedExport:
    def __init__(self, admission: GeneralSiteScoreEvidenceAdmission, delivery: RetainedEvidenceDelivery,
                 receipts: TenantScopedCommandReceiptStore) -> None:
        if (not isinstance(admission, GeneralSiteScoreEvidenceAdmission)
                or not isinstance(delivery, RetainedEvidenceDelivery)
                or not isinstance(receipts, TenantScopedCommandReceiptStore)
                or not receipts.is_durable or receipts.queue is not delivery.queue
                or not isinstance(receipts.queue, DurableJobQueue)
                or receipts.transaction_factory != delivery.engine.transaction):
            raise _missing("same_engine_general_export_owner")
        self.admission, self.delivery, self.receipts = admission, delivery, receipts
        self.engine = delivery.engine

    def _binding(self, actor: Principal) -> None:
        if (not isinstance(actor, Principal) or self.delivery.store.tenant_id != actor.tenant_id
                or self.delivery.engine is not self.engine or self.delivery.queue._engine is not self.engine
                or self.delivery.evidence_store._engine is not self.engine
                or self.delivery.audit_log._engine is not self.engine
                or self.delivery.evidence_store._worm_sink is not self.delivery.sink
                or self.delivery.audit_log._worm_sink is not self.delivery.sink
                or self.receipts.queue is not self.delivery.queue
                or self.receipts.transaction_factory != self.engine.transaction
                or self.admission.source_owner._binding(actor.tenant_id) is not self.engine
                or self.admission.source_owner.audit_log is not self.delivery.audit_log):
            raise _missing("same_engine_general_source_owner")

    def export(self, actor: Principal, selection: GeneralDecisionEvidenceSelection, *,
               idempotency_key: str, correlation_id: str, expected_document_sha256: str | None = None) -> dict:
        if (not isinstance(selection, GeneralDecisionEvidenceSelection)
                or not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or not isinstance(correlation_id, str) or not correlation_id.strip()):
            raise ValueError("Actual selected source/reason, idempotency and correlation required")
        if expected_document_sha256 is not None and (not isinstance(expected_document_sha256, str)
                or len(expected_document_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_document_sha256)):
            raise ValueError("Actual preview document SHA256 precondition required")
        self._binding(actor)
        with self.engine.transaction():
            if getattr(self.engine, "dialect", "") != "postgresql" and not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            admitted = self.admission.resolve(actor, selection)
            if expected_document_sha256 is not None and sha256_hex(admitted.document()) != expected_document_sha256:
                raise _missing("preview_document_changed")
            selected = {"selector": asdict(selection.selector), "reason": selection.reason,
                        "program_label": selection.program_label}
            payload = {"selection": selected, "scope": _scope_dict(selection.scope),
                       "source_digest": admitted.source.source_digest, "subject_id": actor.subject_id,
                       "policy_fingerprint": admitted.policy_fingerprint}
            if expected_document_sha256 is not None:
                payload["expected_document_sha256"] = expected_document_sha256

            def operation(_receipt_id):
                current = self.admission.resolve(actor, selection)
                if current != admitted:
                    raise _missing("general_source_or_policy_changed_before_commit")
                generated = datetime.now(UTC)
                export_id, audit_id = str(uuid4()), str(uuid4())
                document = current.document()
                policy = {"policy_version": AUDIT_EVIDENCE_POLICY_VERSION,
                    "policy_fingerprint": current.policy_fingerprint, "classification": current.classification.name,
                    "retention_class": current.retention_policy.retention_class,
                    "retention_days": current.retention_policy.retention_days,
                    "resource_type": "sitescore", "program_label_is_selection_only": True}
                bundle = {"schema": BUNDLE_SCHEMA,
                    "identity_boundary": {"tenant_id": actor.tenant_id, "subject_id": actor.subject_id},
                    "source_digest": current.source.source_digest, "document": document,
                    "selected_scope": payload["scope"], "selection": selected, "policy": policy,
                    "artifact_sha256": sha256_hex(document)}
                bundle["bundle_checksum"] = sha256_hex(bundle)
                event = AuditEvent(event_id=audit_id, event_type="audit.evidence_export.v1", actor=actor.subject_id,
                    action="export", resource="audit-evidence/" + export_id, outcome=AuditOutcome.SUCCESS,
                    correlation_id=correlation_id, metadata={"tenant_id": actor.tenant_id,
                        "reason": selection.reason, "purpose": selection.reason,
                        "program_label": selection.program_label, "program_label_is_selection_only": True,
                        "decision_id": current.source.decision_id, "candidate_id": current.source.candidate_id,
                        "selected_sections": list(selection.selector.sections), "selected_scope": payload["scope"],
                        "classification": current.classification.name, "masking_profile": "masked",
                        "policy_version": AUDIT_EVIDENCE_POLICY_VERSION,
                        "decision_count": int("decision_log" in selection.selector.sections),
                        "audit_event_count": len(current.source.events) if "audit" in selection.selector.sections else 0,
                        "bundle_checksum": bundle["bundle_checksum"], "artifact_sha256": bundle["artifact_sha256"],
                        "source_digest": current.source.source_digest, "artifact_status": "generated_pending_delivery",
                        "external_acknowledged": False, "retained_delivery_job_type": JOB_TYPE})
                audit_delivery = DurableAuditDelivery(self.engine, actor.tenant_id, self.delivery.queue, self.delivery.sink)
                with self.delivery.audit_log.transactional_worm_sink(audit_delivery):
                    self.delivery.audit_log.record(event)
                # The legacy non-null program column groups this general export
                # by its real decision ID, never a fabricated subsidy program.
                record = RetainedEvidence(export_id=export_id, program_id=current.source.decision_id,
                    purpose=selection.reason, requested_by=actor.subject_id, audit_event_id=audit_id,
                    bundle_checksum=bundle["bundle_checksum"], data_classification=current.classification.name.lower(),
                    sensitive=False, export_scope=",".join(selection.selector.sections),
                    retention_class=current.retention_policy.retention_class,
                    retain_until=current.retention_policy.retain_until(generated), generated_at=generated,
                    period_start=selection.scope.period_start, period_end=selection.scope.period_end,
                    correlation_id=correlation_id, bundle=bundle)
                saved = self.delivery.evidence_store.save_pending_delivery(record, self.delivery)
                content = general_retained_document_bytes(saved)
                return {"export_id": saved.export_id, "record_hash": saved.record_hash,
                        "artifact_sha256": bundle["artifact_sha256"], "artifact_bytes": len(content),
                        "artifact_media_type": "application/json", "delivery_status": "pending",
                        "external_acknowledged": False, "download_ready": False}

            result = self.receipts.run(tenant_id=actor.tenant_id, idempotency_key=idempotency_key,
                scope="sitescore-general-export:" + actor.subject_id + ":" + selection.selector.decision_id,
                payload=payload, correlation_id=correlation_id, operation=operation)
            return {**result.value, "receipt_id": result.receipt_id, "replayed": result.replayed}
