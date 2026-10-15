"""Internal Stage2 SQL commit boundary, not an HTTP export/download endpoint.

Only Stage1's common allowlist document is retained. There is no fabricated
DecisionCard READY, Outcome or SLA section and no external write in commit().
Production remains unavailable until real admission owner ports are installed.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from modules.opsboard.audit.application.evidence_admission import (
    CanonicalRecordReference,
    EvidenceAdmissionReferences,
    EvidencePolicyScope,
    SafeSiteScoreSections,
    SiteScoreStage1Admission,
)
from modules.opsboard.audit.application.sitescore_evidence import (
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from shared.audit.durable_delivery import DurableAuditDelivery
from shared.audit.events import AuditEvent
from shared.audit.integrity import canonical_json, sha256_hex
from shared.audit.persistence import RetainedEvidence, verify_retained_evidence
from shared.audit.retained_delivery import JOB_TYPE, RetainedEvidenceDelivery
from shared.auth import Principal
from shared.infrastructure.persistence.command_receipts import TenantScopedCommandReceiptStore
from shared.infrastructure.persistence.job_queue import DurableJobQueue

SCHEMA = "sitescore.canonical-sections.v1"


def _missing(field: str) -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:" + field)


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def retained_document_bytes(record: RetainedEvidence) -> bytes:
    """Re-validate frozen renderer input without serializing native records."""
    verify_retained_evidence(record)
    bundle = record.bundle
    if isinstance(bundle, dict) and bundle.get("schema") == "sitescore.general-decision-audit-retained.v1":
        from modules.opsboard.audit.application.evidence_general_retention import (
            general_retained_document_bytes,
        )

        return general_retained_document_bytes(record)
    if (not isinstance(bundle, dict) or set(bundle) != {
        "schema", "identity_boundary", "source_digest", "document", "selected_scope",
        "admission_records", "artifact_sha256", "bundle_checksum",
    } or bundle["schema"] != SCHEMA):
        raise _missing("retained_schema")
    boundary = bundle["identity_boundary"]
    document = bundle["document"]
    if (not isinstance(boundary, dict) or set(boundary) != {"tenant_id", "subject_id"}
            or not all(isinstance(value, str) and value.strip() for value in boundary.values())
            or boundary["subject_id"] != record.requested_by
            or not isinstance(document, dict)
            or set(document) != {"source_digest", "export_admitted", "sections"}
            or document["export_admitted"] is not False
            or document["source_digest"] != bundle["source_digest"]
            or not isinstance(document["sections"], dict)):
        raise _missing("retained_schema")
    rows = []
    for name, section in document["sections"].items():
        if not isinstance(section, list) or not all(isinstance(row, dict) for row in section):
            raise _missing("retained_schema")
        rows.append((name, tuple(tuple(row.items()) for row in section)))
    projected = SafeSiteScoreSections(bundle["source_digest"], tuple(rows)).for_format("json")
    if projected != document:
        raise _missing("retained_projection")
    scope = bundle["selected_scope"]
    if not isinstance(scope, dict) or set(scope) != {"format", "destination", "period_start", "period_end"}:
        raise _missing("retained_scope")
    try:
        EvidencePolicyScope(scope["format"], scope["destination"], datetime.fromisoformat(scope["period_start"]),
                            datetime.fromisoformat(scope["period_end"]))
        if scope["format"] != "json":
            raise ValueError("Only actual canonical JSON bytes are implemented")
    except (TypeError, ValueError) as exc:
        raise _missing("retained_scope") from exc
    references = bundle["admission_records"]
    if not isinstance(references, list) or not references:
        raise _missing("retained_admission_references")
    kinds = set()
    for row in references:
        if (not isinstance(row, dict) or set(row) != {"kind", "owner", "record_id", "version", "content_sha256"}
                or row["kind"] not in {"program", "purpose", "classification", "retention", "source", "sensitive"}
                or not _digest(row["content_sha256"])):
            raise _missing("retained_admission_references")
        CanonicalRecordReference(row["owner"], row["record_id"], row["version"])
        if row["kind"] != "source" and row["kind"] in kinds:
            raise _missing("retained_admission_references")
        kinds.add(row["kind"])
    if not {"program", "purpose", "classification", "retention", "source"} <= kinds:
        raise _missing("retained_admission_references")
    content = canonical_json(document).encode("utf-8")
    if (sha256_hex(document) != bundle["artifact_sha256"]
            or sha256_hex({k: v for k, v in bundle.items() if k != "bundle_checksum"}) != record.bundle_checksum):
        raise _missing("retained_bytes_checksum")
    return content


def _authority_fingerprint(value) -> str:
    records = [value.program_purpose.program, value.program_purpose.purpose,
               value.fields.record, value.retention.record, *(snapshot.record for snapshot in value.snapshots)]
    if value.sensitive_authorization is not None:
        records.append(value.sensitive_authorization)
    return sha256_hex({"records": [{**asdict(row.reference), "sha256": row.content_sha256} for row in records],
                       "fields": [value.fields.decision_fields, value.fields.audit_fields],
                       "classification": value.fields.classification.name, "sensitive": value.fields.sensitive,
                       "retain_until": value.retention.retain_until.isoformat(),
                       "retention_class": value.retention.policy.retention_class,
                       "projection": value.safe_sections.for_format("json")})


class SiteScoreRetainedCommit:
    """One same-engine transaction for admission, record, audit, jobs and receipt.

    This internal constructor receives server-owned objects, never request body
    policy implementations. No API factory installs it in this source stage.
    """

    def __init__(self, admission: SiteScoreStage1Admission, delivery: RetainedEvidenceDelivery,
                 receipts: TenantScopedCommandReceiptStore) -> None:
        if (not isinstance(admission, SiteScoreStage1Admission)
                or not isinstance(delivery, RetainedEvidenceDelivery)
                or not isinstance(receipts, TenantScopedCommandReceiptStore)
                or not receipts.is_durable or receipts.queue is not delivery.queue
                or not isinstance(receipts.queue, DurableJobQueue)
                or receipts.transaction_factory != delivery.engine.transaction):
            raise _missing("same_engine_durable_command_owner")
        self.admission, self.delivery, self.receipts = admission, delivery, receipts
        self.engine = delivery.engine

    def _binding(self, actor: Principal) -> None:
        if (not isinstance(actor, Principal) or self.delivery.store.tenant_id != actor.tenant_id
                or self.admission.source_owner._binding(actor.tenant_id) is not self.engine
                or self.admission.source_owner.audit_log is not self.delivery.audit_log):
            raise _missing("same_engine_canonical_source_owner")
        owners = self.admission.owners
        ports = (owners.program_purpose, owners.classification_fields, owners.retention,
                 owners.source_metadata)
        if owners.sensitive_authorization is not None:
            ports += (owners.sensitive_authorization,)
        if any(getattr(owner, "engine", None) is not self.engine for owner in ports):
            raise _missing("same_engine_admission_read_owners")

    def commit(self, actor: Principal, selector: SiteScoreEvidenceSelector,
               references: EvidenceAdmissionReferences, *, policy_scope: EvidencePolicyScope,
               idempotency_key: str, correlation_id: str) -> dict[str, Any]:
        if (not isinstance(policy_scope, EvidencePolicyScope)
                or not isinstance(idempotency_key, str) or not idempotency_key.strip()
                or not isinstance(correlation_id, str) or not correlation_id.strip()):
            raise _missing("selected_scope_and_idempotency")
        if policy_scope.format != "json":
            raise _missing("unimplemented_artifact_format")
        if not isinstance(references, EvidenceAdmissionReferences) or not isinstance(selector, SiteScoreEvidenceSelector):
            raise ValueError("Typed source and record selectors required")
        self._binding(actor)
        with self.engine.transaction():
            if getattr(self.engine, "dialect", "") != "postgresql" and not self.engine._conn.in_transaction:
                self.engine.execute("BEGIN IMMEDIATE")
            admitted = self.admission.resolve(actor, selector, references, policy_scope=policy_scope)
            moments = [event.occurred_at for event in admitted.source.events]
            if (not moments or min(moments) < policy_scope.period_start
                    or max(moments) > policy_scope.period_end):
                raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_PERIOD_DENIED")
            payload = {"selector": asdict(selector), "references": asdict(references),
                       "scope": {**asdict(policy_scope), "period_start": policy_scope.period_start.isoformat(),
                                 "period_end": policy_scope.period_end.isoformat()},
                       "source_digest": admitted.source.source_digest, "subject_id": actor.subject_id,
                       "authority_fingerprint": _authority_fingerprint(admitted)}

            def operation(_receipt_id):
                current = self.admission.resolve(actor, selector, references, policy_scope=policy_scope)
                if current != admitted:
                    raise _missing("admission_changed_before_commit")
                generated = datetime.now(UTC)
                if current.retention.retain_until <= generated:
                    raise _missing("applicable_retention")
                export_id, audit_id = str(uuid4()), str(uuid4())
                document = current.safe_sections.for_format("json")
                records = [("program", current.program_purpose.program), ("purpose", current.program_purpose.purpose),
                           ("classification", current.fields.record), ("retention", current.retention.record)]
                records.extend(("source", snapshot.record) for snapshot in current.snapshots)
                if current.sensitive_authorization is not None:
                    records.append(("sensitive", current.sensitive_authorization))
                bundle = {"schema": SCHEMA, "identity_boundary": {"tenant_id": actor.tenant_id,
                           "subject_id": actor.subject_id}, "source_digest": current.source.source_digest,
                          "document": document, "selected_scope": payload["scope"],
                          "admission_records": [{"kind": kind, **asdict(row.reference),
                                                  "content_sha256": row.content_sha256} for kind, row in records],
                          "artifact_sha256": sha256_hex(document)}
                checksum = sha256_hex(bundle)
                bundle["bundle_checksum"] = checksum
                event = AuditEvent(event_id=audit_id, event_type="audit.evidence.retained.v1", actor=actor.subject_id,
                                   action="retain", resource="audit-evidence/" + export_id, outcome="PENDING_DELIVERY",
                                   correlation_id=correlation_id, metadata={"tenant_id": actor.tenant_id,
                                       "bundle_checksum": checksum, "source_digest": current.source.source_digest,
                                       "retained_delivery_job_type": JOB_TYPE})
                audit_delivery = DurableAuditDelivery(self.engine, actor.tenant_id, self.delivery.queue, self.delivery.sink)
                with self.delivery.audit_log.transactional_worm_sink(audit_delivery):
                    self.delivery.audit_log.record(event)
                raw = RetainedEvidence(export_id=export_id, program_id=current.program_purpose.program.reference.record_id,
                                       purpose=current.program_purpose.purpose.reference.record_id, requested_by=actor.subject_id,
                                       audit_event_id=audit_id, bundle_checksum=checksum,
                                       data_classification=current.fields.classification.name.lower(),
                                       sensitive=current.fields.sensitive, export_scope=",".join(current.source.sections),
                                       retention_class=current.retention.policy.retention_class,
                                       retain_until=current.retention.retain_until, generated_at=generated,
                                       period_start=policy_scope.period_start, period_end=policy_scope.period_end,
                                       correlation_id=correlation_id, bundle=bundle)
                saved = self.delivery.evidence_store.save_pending_delivery(raw, self.delivery)
                retained_document_bytes(saved)
                return {"export_id": saved.export_id, "record_hash": saved.record_hash,
                        "artifact_sha256": bundle["artifact_sha256"], "artifact_media_type": "application/json",
                        "delivery_status": "pending",
                        "external_acknowledged": False, "export_admitted": False}

            result = self.receipts.run(tenant_id=actor.tenant_id, idempotency_key=idempotency_key,
                                       scope="sitescore-evidence:" + actor.subject_id + ":" + selector.decision_id,
                                       payload=payload, correlation_id=correlation_id, operation=operation)
            return {**result.value, "receipt_id": result.receipt_id, "replayed": result.replayed}
