"""Unbound, read-only admission ports and a common SiteScore field projection.

This is not an export endpoint or a complete policy implementation. Production
has no program/purpose, field, retention or sensitive-authorization owner wired
here. A selector identifies a record; neither its spelling nor a caller body is
an approval. Future adapters must read and validate their actual native records.
No approved status, program key, retention duration or source licence is seeded.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal, Protocol

from modules.opsboard.audit.application.sitescore_evidence import (
    BoundSiteScoreAuditEvent,
    ResolvedSiteScoreEvidence,
    SiteScoreCanonicalEvidenceResolver,
    SiteScoreEvidenceDenied,
    SiteScoreEvidenceSelector,
    SiteScoreEvidenceUnavailable,
)
from shared.audit.persistence import EvidenceRetentionPolicy
from shared.auth import DataClassification, Principal

EvidenceFormat = Literal["json", "csv", "pdf"]
RecordKind = Literal["program", "purpose", "classification", "retention", "source", "sensitive"]


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _missing(name: str) -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:" + name)


@dataclass(frozen=True)
class CanonicalRecordReference:
    """Selection only. No approval, actor, classification or retention values."""

    owner: str
    record_id: str
    version: str

    def __post_init__(self) -> None:
        if not all(_text(v) for v in (self.owner, self.record_id, self.version)):
            raise ValueError("Actual owner/record/version reference required")


@dataclass(frozen=True)
class EvidenceAdmissionReferences:
    program: CanonicalRecordReference | None = None
    purpose: CanonicalRecordReference | None = None
    classification: CanonicalRecordReference | None = None
    retention: CanonicalRecordReference | None = None
    sensitive: CanonicalRecordReference | None = None


@dataclass(frozen=True)
class EvidencePolicyScope:
    """Requested scope only; each server owner must authorize its applicability."""

    format: EvidenceFormat
    destination: str
    period_start: datetime
    period_end: datetime

    def __post_init__(self) -> None:
        if (self.format not in ("json", "csv", "pdf") or not _text(self.destination)
                or not isinstance(self.period_start, datetime) or self.period_start.utcoffset() is None
                or not isinstance(self.period_end, datetime) or self.period_end.utcoffset() is None
                or self.period_start > self.period_end):
            raise ValueError("Typed format/destination/time window selection required")


@dataclass(frozen=True)
class EvidenceReadContext:
    principal: Principal
    source: ResolvedSiteScoreEvidence
    as_of: datetime
    references: EvidenceAdmissionReferences
    policy_scope: EvidencePolicyScope | None


@dataclass(frozen=True)
class BoundNativeRecord:
    """An adapter result, not a new business-policy record or approval DTO.

    native_record is kept opaque and is never serialized. The installed owner
    must validate the native record and its real approval evidence before
    returning this binding. There is intentionally no approved=True field.
    No such business-record adapter is installed by this proposal.
    """

    reference: CanonicalRecordReference
    content_sha256: str
    tenant_id: str
    subject_id: str
    source_digest: str
    native_record: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class ProgramPurposeBinding:
    program: BoundNativeRecord
    purpose: BoundNativeRecord
    sections: tuple[str, ...]


@dataclass(frozen=True)
class FieldAuthorityBinding:
    record: BoundNativeRecord
    classification: DataClassification
    decision_fields: tuple[str, ...]
    audit_fields: tuple[str, ...]
    sensitive: bool


@dataclass(frozen=True)
class RetentionBinding:
    record: BoundNativeRecord
    policy: EvidenceRetentionPolicy
    retain_until: datetime


@dataclass(frozen=True)
class SourceMetadataBinding:
    record: BoundNativeRecord
    snapshot_id: str
    content_sha256: str


class ProgramPurposeReadPort(Protocol):
    def read_authorized(self, context: EvidenceReadContext, program: CanonicalRecordReference,
                        purpose: CanonicalRecordReference) -> ProgramPurposeBinding | None:
        """Read actual applicable program/purpose + approval records; no body claims."""


class ClassificationFieldReadPort(Protocol):
    def read_authorized(self, context: EvidenceReadContext,
                        reference: CanonicalRecordReference) -> FieldAuthorityBinding | None:
        """Read current source classification and effective field authority."""


class RetentionReadPort(Protocol):
    def read_authorized(self, context: EvidenceReadContext,
                        reference: CanonicalRecordReference) -> RetentionBinding | None:
        """Read actual applicable retention/hold policy, not generic default days."""


class SourceMetadataReadPort(Protocol):
    def read_snapshot(self, context: EvidenceReadContext,
                      snapshot_id: str) -> SourceMetadataBinding | None:
        """Read the actual recognized immutable snapshot owner, never register one."""


class SensitiveAuthorizationReadPort(Protocol):
    def read_authorized(self, context: EvidenceReadContext, reference: CanonicalRecordReference,
                        fields: FieldAuthorityBinding) -> BoundNativeRecord | None:
        """Read independent authorization bound to exporter/source/purpose/fields."""


@dataclass(frozen=True)
class EvidenceAdmissionOwners:
    program_purpose: ProgramPurposeReadPort | None = None
    classification_fields: ClassificationFieldReadPort | None = None
    retention: RetentionReadPort | None = None
    source_metadata: SourceMetadataReadPort | None = None
    sensitive_authorization: SensitiveAuthorizationReadPort | None = None


DECISION_FIELDS = (
    "decision_id", "decision_version", "candidate_id", "report_id", "report_version",
    "report_sha256", "source_snapshot_ids", "recorded_model_reference",
    "recorded_feature_reference", "recorded_policy_reference", "previous_decision_id",
    "approval_id", "approval_status", "decision_actor", "decided_at",
    "recorded_network_action", "rationale_sha256", "source_digest",
)
AUDIT_FIELDS = (
    "event_id", "event_hash", "action", "outcome", "actor", "correlation_id",
    "occurred_at", "decision_version", "content_sha256",
)


def _projection_value(name: str, value: object) -> bool:
    if name in {"decision_version", "report_version"}:
        return type(value) is int and value >= 1
    if name in {"report_sha256", "rationale_sha256", "source_digest", "event_hash", "content_sha256"}:
        return _hash(value)
    if name == "source_snapshot_ids":
        return isinstance(value, (tuple, list)) and all(_text(item) for item in value)
    if name in {"previous_decision_id", "recorded_network_action"}:
        return value is None or _text(value)
    if name in {"decided_at", "occurred_at"}:
        if not isinstance(value, str):
            return False
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
        except ValueError:
            return False
    return _text(value)


@dataclass(frozen=True)
class SafeSiteScoreSections:
    """Frozen internal projection shared by every renderer; no export authority."""

    source_digest: str
    section_rows: tuple[tuple[str, tuple[tuple[tuple[str, object], ...], ...]], ...]
    export_admitted: bool = field(default=False, init=False)

    def for_format(self, format: EvidenceFormat) -> dict[str, object]:
        if format not in ("json", "csv", "pdf"):
            raise ValueError("Unsupported evidence format")
        # Validate even directly constructed internal values, rather than rely
        # on one constructor being the only possible renderer call path.
        if (not _hash(self.source_digest) or not isinstance(self.section_rows, tuple)
                or not self.section_rows):
            raise _missing("canonical_projection_shape")
        sections = {}
        for section in self.section_rows:
            if not isinstance(section, tuple) or len(section) != 2:
                raise _missing("canonical_projection_shape")
            name, rows = section
            if (not isinstance(name, str) or name not in ("decision_log", "audit")
                    or name in sections or not isinstance(rows, tuple) or not rows
                    or (name == "decision_log" and len(rows) != 1)):
                raise _missing("canonical_projection_shape")
            allowed = DECISION_FIELDS if name == "decision_log" else AUDIT_FIELDS
            projected_rows = []
            for row in rows:
                if not isinstance(row, tuple):
                    raise _missing("canonical_projection_shape")
                mapped = {}
                for item in row:
                    if not isinstance(item, tuple) or len(item) != 2:
                        raise _missing("canonical_projection_shape")
                    key, value = item
                    if (not isinstance(key, str) or key not in allowed or key in mapped
                            or not _projection_value(key, value)
                            or (key == "source_digest" and value != self.source_digest)):
                        raise _missing("canonical_projection_shape")
                    mapped[key] = value
                projected_rows.append(mapped)
            sections[name] = projected_rows
        # Every format gets precisely the same section/field projection. A
        # renderer cannot get raw report/audit metadata from this value.
        payload = {
            "source_digest": self.source_digest,
            "export_admitted": False,
            "sections": sections,
        }
        return json.loads(json.dumps(payload, allow_nan=False))


def project_sitescore_sections(source: ResolvedSiteScoreEvidence, *,
                               decision_fields: tuple[str, ...] = DECISION_FIELDS,
                               audit_fields: tuple[str, ...] = AUDIT_FIELDS) -> SafeSiteScoreSections:
    """Project only internal known source facts; not permission to serve them."""
    if not isinstance(source, ResolvedSiteScoreEvidence) or not _hash(source.source_digest):
        raise _missing("canonical_source")
    if (not isinstance(decision_fields, tuple) or not isinstance(audit_fields, tuple)
            or any(not isinstance(name, str) for name in (*decision_fields, *audit_fields))
            or len(set(decision_fields)) != len(decision_fields)
            or len(set(audit_fields)) != len(audit_fields)
            or any(name not in DECISION_FIELDS for name in decision_fields)
            or any(name not in AUDIT_FIELDS for name in audit_fields)
            or not isinstance(source.sections, tuple) or not source.sections
            or any(not isinstance(name, str) for name in source.sections)
            or any(name not in ("decision_log", "audit") for name in source.sections)):
        raise _missing("supported_section_field_authority")
    if (not isinstance(source.decided_at, datetime)
            or not isinstance(source.source_snapshot_ids, tuple)
            or any(not _text(value) for value in source.source_snapshot_ids)
            or not isinstance(source.events, tuple)
            or any(not isinstance(event, BoundSiteScoreAuditEvent)
                   or not isinstance(event.occurred_at, datetime) for event in source.events)):
        raise _missing("canonical_projection_shape")
    decision = {name: getattr(source, name) for name in DECISION_FIELDS}
    decision["source_snapshot_ids"] = tuple(source.source_snapshot_ids)
    decision["decided_at"] = source.decided_at.isoformat()
    audit = [
        {**{name: getattr(event, name) for name in AUDIT_FIELDS},
         "occurred_at": event.occurred_at.isoformat()}
        for event in source.events
    ]
    rows = {
        "decision_log": (tuple((name, decision[name]) for name in decision_fields),),
        "audit": tuple(tuple((name, event[name]) for name in audit_fields) for event in audit),
    }
    result = SafeSiteScoreSections(source.source_digest, tuple((name, rows[name]) for name in source.sections))
    try:
        result.for_format("json")
    except (TypeError, ValueError) as exc:
        raise _missing("canonical_projection_shape") from exc
    return result


@dataclass(frozen=True)
class ResolvedStage1Evidence:
    """Read-owner facts only; delivery/retained artifact admission is still absent."""

    source: ResolvedSiteScoreEvidence
    program_purpose: ProgramPurposeBinding
    fields: FieldAuthorityBinding
    retention: RetentionBinding
    snapshots: tuple[SourceMetadataBinding, ...]
    sensitive_authorization: BoundNativeRecord | None
    safe_sections: SafeSiteScoreSections
    references: EvidenceAdmissionReferences
    policy_scope: EvidencePolicyScope | None
    export_admitted: bool = field(default=False, init=False)


class SiteScoreStage1Admission:
    """Server-owned read ports. This class is not mounted by any API factory."""

    def __init__(self, source_owner: SiteScoreCanonicalEvidenceResolver,
                 owners: EvidenceAdmissionOwners | None = None) -> None:
        if owners is not None and not isinstance(owners, EvidenceAdmissionOwners):
            raise ValueError("Typed server-owned read ports required")
        self.source_owner = source_owner
        self.owners = owners or EvidenceAdmissionOwners()

    @staticmethod
    def _bound(record: BoundNativeRecord, context: EvidenceReadContext,
               reference: CanonicalRecordReference | None = None) -> None:
        if (not isinstance(record, BoundNativeRecord) or record.native_record is None
                or not isinstance(record.reference, CanonicalRecordReference)
                or not _hash(record.content_sha256)
                or record.tenant_id != context.principal.tenant_id
                or record.subject_id != context.principal.subject_id
                or record.source_digest != context.source.source_digest
                or (reference is not None and record.reference != reference)):
            raise _missing("canonical_owner_record_binding")

    @staticmethod
    def _read(method, *args):
        try:
            return method(*args)
        except (SiteScoreEvidenceDenied, SiteScoreEvidenceUnavailable):
            raise
        except PermissionError as exc:
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SCOPE_DENIED") from exc
        except (OSError, RuntimeError, ValueError, TypeError, LookupError) as exc:
            raise _missing("canonical_admission_owner_unavailable") from exc

    def resolve(self, principal: Principal, selector: SiteScoreEvidenceSelector,
                references: EvidenceAdmissionReferences, *,
                policy_scope: EvidencePolicyScope | None = None) -> ResolvedStage1Evidence:
        if not isinstance(references, EvidenceAdmissionReferences):
            raise ValueError("Typed record selectors required; body claims are not authority")
        if not isinstance(self.source_owner, SiteScoreCanonicalEvidenceResolver):
            raise _missing("canonical_source_owner")
        source = self.source_owner.resolve_source(principal, selector)
        owners = self.owners
        required = (("program", references.program), ("purpose", references.purpose),
                    ("classification", references.classification), ("retention", references.retention))
        if any(not isinstance(value, CanonicalRecordReference) for _name, value in required):
            raise _missing("actual_program_purpose_classification_retention_references")
        if policy_scope is not None and not isinstance(policy_scope, EvidencePolicyScope):
            raise ValueError("Typed selected policy scope required")
        context = EvidenceReadContext(principal, source, datetime.now(UTC), references, policy_scope)
        if (not callable(getattr(owners.program_purpose, "read_authorized", None))
                or not callable(getattr(owners.classification_fields, "read_authorized", None))
                or not callable(getattr(owners.retention, "read_authorized", None))
                or not callable(getattr(owners.source_metadata, "read_snapshot", None))):
            raise _missing("actual_program_classification_retention_source_owners")
        # No native business record adapter is configured by this proposal.
        # Implementations of these read interfaces must validate the real
        # approval/applicability/expiry/revocation facts in their own owner.
        program = self._read(owners.program_purpose.read_authorized, context, references.program, references.purpose)
        if not isinstance(program, ProgramPurposeBinding):
            raise _missing("actual_policy_records")
        self._bound(program.program, context, references.program)
        self._bound(program.purpose, context, references.purpose)
        fields = self._read(owners.classification_fields.read_authorized, context, references.classification)
        if not isinstance(fields, FieldAuthorityBinding):
            raise _missing("actual_policy_records")
        self._bound(fields.record, context, references.classification)
        retention = self._read(owners.retention.read_authorized, context, references.retention)
        if not isinstance(retention, RetentionBinding):
            raise _missing("actual_policy_records")
        self._bound(retention.record, context, references.retention)
        if (not isinstance(program.sections, tuple) or not program.sections
                or any(section not in ("decision_log", "audit") for section in program.sections)
                or any(section not in program.sections for section in source.sections)
                or not isinstance(fields.classification, DataClassification)
                or fields.classification < DataClassification.CONFIDENTIAL
                or type(fields.sensitive) is not bool
                or not isinstance(retention.policy, EvidenceRetentionPolicy)
                or not isinstance(retention.retain_until, datetime)
                or retention.retain_until.tzinfo is None or retention.retain_until <= context.as_of):
            raise _missing("actual_policy_applicability")
        if not principal.scope.permits_classification(fields.classification):
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SCOPE_DENIED")
        if not source.source_snapshot_ids:
            raise _missing("actual_source_metadata")
        snapshots = []
        for snapshot_id in source.source_snapshot_ids:
            snapshot = self._read(owners.source_metadata.read_snapshot, context, snapshot_id)
            if (not isinstance(snapshot, SourceMetadataBinding) or snapshot.snapshot_id != snapshot_id
                    or not _hash(snapshot.content_sha256)
                    or not isinstance(snapshot.record, BoundNativeRecord)
                    or not isinstance(snapshot.record.reference, CanonicalRecordReference)
                    or snapshot.record.reference.record_id != snapshot_id):
                raise _missing("actual_source_metadata")
            self._bound(snapshot.record, context)
            snapshots.append(snapshot)
        sensitive = None
        if fields.sensitive or fields.classification >= DataClassification.RESTRICTED:
            if (not callable(getattr(owners.sensitive_authorization, "read_authorized", None))
                    or not isinstance(references.sensitive, CanonicalRecordReference)):
                raise _missing("independent_sensitive_authorization")
            sensitive = self._read(owners.sensitive_authorization.read_authorized, context, references.sensitive, fields)
            self._bound(sensitive, context, references.sensitive)
        safe = project_sitescore_sections(source, decision_fields=fields.decision_fields,
                                         audit_fields=fields.audit_fields)
        # Re-read current scope and exact frozen owner facts after policy reads.
        if self.source_owner.resolve_source(principal, selector) != source:
            raise _missing("canonical_source_changed")
        return ResolvedStage1Evidence(source, program, fields, retention, tuple(snapshots), sensitive,
                                      safe, references, policy_scope)


def projection_sha256(projection: SafeSiteScoreSections) -> str:
    if not isinstance(projection, SafeSiteScoreSections):
        raise ValueError("Typed common projection required")
    return hashlib.sha256(json.dumps(projection.for_format("json"), sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()
