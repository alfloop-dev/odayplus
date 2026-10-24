"""Pure canonical evidence scope and fixed reference projection.

No program/purpose/retention approval records, owner ports, policy decisions or
external write belong here. General section-9 exports share only this schema;
format validation returns projection inputs, never an actual CSV/PDF file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from modules.opsboard.audit.application.sitescore_evidence import (
    BoundSiteScoreAuditEvent,
    ResolvedSiteScoreEvidence,
    SiteScoreEvidenceUnavailable,
)

EvidenceFormat = Literal["json", "csv", "pdf"]

def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())

def _hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

def _missing(name: str) -> SiteScoreEvidenceUnavailable:
    return SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED:" + name)

@dataclass(frozen=True)
class EvidencePolicyScope:
    """Requested artifact/date window; current source and shared policy validate it."""

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
