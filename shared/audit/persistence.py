"""Durable audit-evidence retention contract (ODP-PV-011).

ODP-PV-009 made audit *events* restart-survivable (columnar
``durable_audit_events``). This module adds the parallel contract for the
*evidence bundles* an export produces: an immutable, hash-stamped record that
captures who exported what, why, under which privacy scope, and how long it must
be retained.

It is deliberately generic — it imports no module domain types — so both the
in-memory default (used in ``memory`` mode and unit tests) and the durable
SQLite store (``modules.opsboard.audit.evidence_store``) implement the same
:class:`EvidenceBundleStore` surface. The opsboard layer owns the projection
from its ``AuditEvidenceBundle`` into a :class:`RetainedEvidence` record.

Retention rule of thumb (ODP-SD-09 §11 audit retention, subsidy evidence):
restricted / sensitive subsidy evidence is kept on a regulatory horizon, normal
audit evidence on a multi-year horizon, and low-sensitivity exports on a short
horizon. A ``legal_hold`` flag freezes a record against purge regardless of its
retention window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

# --- retention policy --------------------------------------------------------

# Retention classes and their windows in days. Subsidy / restricted evidence
# follows a regulatory horizon; everything else falls back to shorter windows.
RETENTION_REGULATORY = "regulatory-7y"
RETENTION_AUDIT = "audit-5y"
RETENTION_STANDARD = "standard-1y"

_RETENTION_DAYS: dict[str, int] = {
    RETENTION_REGULATORY: 2557,  # ~7 years
    RETENTION_AUDIT: 1826,  # ~5 years
    RETENTION_STANDARD: 365,
}

_REGULATORY_CLASSIFICATIONS = frozenset({"restricted", "secret", "top_secret"})
_AUDIT_CLASSIFICATIONS = frozenset({"confidential"})


@dataclass(frozen=True)
class EvidenceRetentionPolicy:
    """A named retention window applied to a persisted evidence bundle."""

    retention_class: str
    retention_days: int

    def retain_until(self, generated_at: datetime) -> datetime:
        return generated_at + timedelta(days=self.retention_days)


def resolve_retention_policy(
    data_classification: str,
    *,
    sensitive: bool = False,
) -> EvidenceRetentionPolicy:
    """Pick the retention window for an export's privacy scope.

    Restricted (or otherwise highly classified) data, or any export flagged
    ``sensitive``, follows the regulatory horizon; confidential data follows the
    audit horizon; everything else uses the standard horizon.
    """

    classification = (data_classification or "").strip().lower()
    if sensitive or classification in _REGULATORY_CLASSIFICATIONS:
        retention_class = RETENTION_REGULATORY
    elif classification in _AUDIT_CLASSIFICATIONS:
        retention_class = RETENTION_AUDIT
    else:
        retention_class = RETENTION_STANDARD
    return EvidenceRetentionPolicy(
        retention_class=retention_class,
        retention_days=_RETENTION_DAYS[retention_class],
    )


# --- persisted record --------------------------------------------------------


@dataclass(frozen=True)
class RetainedEvidence:
    """Durable projection of an exported audit-evidence bundle.

    Queryable columns (program, checksum, retention, privacy scope, correlation)
    live as fields; the full bundle is preserved as ``bundle`` so an export can
    be reproduced byte-for-byte after a restart.
    """

    export_id: str
    program_id: str
    purpose: str  # reason the export was produced
    requested_by: str  # actor who produced the export
    audit_event_id: str
    bundle_checksum: str  # content hash of the exported bundle
    data_classification: str  # privacy scope: classification
    sensitive: bool  # privacy scope: sensitive flag
    export_scope: str  # privacy scope: tenant/region/program selector
    retention_class: str
    retain_until: datetime
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    correlation_id: str
    bundle: dict[str, Any] = field(default_factory=dict)
    legal_hold: bool = False

    def is_expired(self, as_of: datetime) -> bool:
        """A record is purgeable only when past retention and not on hold."""

        return not self.legal_hold and as_of >= self.retain_until

    def summary(self) -> dict[str, Any]:
        """Metadata view without the full bundle payload (for listings)."""

        return {
            "export_id": self.export_id,
            "program_id": self.program_id,
            "purpose": self.purpose,
            "requested_by": self.requested_by,
            "audit_event_id": self.audit_event_id,
            "bundle_checksum": self.bundle_checksum,
            "data_classification": self.data_classification,
            "sensitive": self.sensitive,
            "export_scope": self.export_scope,
            "retention_class": self.retention_class,
            "retain_until": self.retain_until.isoformat(),
            "legal_hold": self.legal_hold,
            "generated_at": self.generated_at.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "correlation_id": self.correlation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """Full view: retention/privacy summary plus the preserved bundle."""

        return {**self.summary(), "bundle": self.bundle}


# --- store contract + in-memory default --------------------------------------


@runtime_checkable
class EvidenceBundleStore(Protocol):
    """Anything that can persist and retrieve retained evidence bundles."""

    def save(self, record: RetainedEvidence) -> RetainedEvidence: ...

    def get(self, export_id: str) -> RetainedEvidence | None: ...

    def list_for_program(self, program_id: str) -> list[RetainedEvidence]: ...

    def list_all(self) -> list[RetainedEvidence]: ...

    def list_expired(self, as_of: datetime) -> list[RetainedEvidence]: ...

    def purge_expired(self, as_of: datetime) -> list[str]: ...


class InMemoryEvidenceBundleStore:
    """Dict-backed default store (``memory`` mode, unit tests).

    Insertion order is preserved so listings are stable, mirroring the durable
    store's ordinal ordering.
    """

    def __init__(self) -> None:
        self._records: dict[str, RetainedEvidence] = {}

    def save(self, record: RetainedEvidence) -> RetainedEvidence:
        self._records[record.export_id] = record
        return record

    def get(self, export_id: str) -> RetainedEvidence | None:
        return self._records.get(export_id)

    def list_for_program(self, program_id: str) -> list[RetainedEvidence]:
        return [r for r in self._records.values() if r.program_id == program_id]

    def list_all(self) -> list[RetainedEvidence]:
        return list(self._records.values())

    def list_expired(self, as_of: datetime) -> list[RetainedEvidence]:
        return [r for r in self._records.values() if r.is_expired(as_of)]

    def purge_expired(self, as_of: datetime) -> list[str]:
        expired = [r.export_id for r in self.list_expired(as_of)]
        for export_id in expired:
            del self._records[export_id]
        return expired


__all__ = [
    "RETENTION_AUDIT",
    "RETENTION_REGULATORY",
    "RETENTION_STANDARD",
    "EvidenceBundleStore",
    "EvidenceRetentionPolicy",
    "InMemoryEvidenceBundleStore",
    "RetainedEvidence",
    "resolve_retention_policy",
]
