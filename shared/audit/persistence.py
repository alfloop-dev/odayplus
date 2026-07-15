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

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from shared.audit.integrity import (
    CHAIN_GENESIS_HASH,
    DEFAULT_AUDIT_INTEGRITY_KEY_ID,
    DEFAULT_AUDIT_SIGNATURE_ALG,
    DEFAULT_AUDIT_SIGNATURE_VERSION,
    DEFAULT_AUDIT_WORM_SINK_ID,
    AuditChainVerification,
    AuditIntegrityIssue,
    canonical_json,
    sha256_hex,
)

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
LEGAL_HOLD_ROLES = frozenset({"legal", "compliance_officer", "records_manager"})
RETENTION_PURGE_ROLES = frozenset(
    {"records_manager", "retention_manager", "compliance_officer"}
)


class EvidenceIntegrityError(ValueError):
    """Raised when retained evidence fails checksum/hash-chain verification."""


class EvidenceGovernanceError(PermissionError):
    """Raised when a retention/legal-hold operation violates SoD policy."""


class EvidenceImmutabilityError(RuntimeError):
    """Raised when product code attempts to overwrite/delete WORM evidence."""


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


@dataclass(frozen=True)
class GovernedEvidenceOperation:
    """Actor context for retention purge and legal-hold operations."""

    actor: str
    role: str
    reason: str
    correlation_id: str
    requested_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "role": self.role,
            "reason": self.reason,
            "correlation_id": self.correlation_id,
            "requested_at": self.requested_at.isoformat(),
        }


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
    sequence: int | None = None
    previous_hash: str | None = None
    record_hash: str | None = None
    signature_key_id: str = DEFAULT_AUDIT_INTEGRITY_KEY_ID
    signature_version: str = DEFAULT_AUDIT_SIGNATURE_VERSION
    signature_alg: str = DEFAULT_AUDIT_SIGNATURE_ALG
    worm_sink_id: str = DEFAULT_AUDIT_WORM_SINK_ID
    governance_log: tuple[dict[str, Any], ...] = ()

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
            "integrity": {
                "sequence": self.sequence,
                "previous_hash": self.previous_hash,
                "record_hash": self.record_hash,
                "signature_key_id": self.signature_key_id,
                "signature_version": self.signature_version,
                "signature_alg": self.signature_alg,
                "worm_sink_id": self.worm_sink_id,
            },
            "governance_log": list(self.governance_log),
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

    def purge_expired(
        self, as_of: datetime, *, context: GovernedEvidenceOperation | None = None
    ) -> list[str]: ...

    def apply_legal_hold(
        self, export_id: str, *, context: GovernedEvidenceOperation
    ) -> RetainedEvidence: ...

    def delete(self, export_id: str) -> None: ...

    def verify_integrity(self) -> AuditChainVerification: ...

    def replay(self, records: Iterable[RetainedEvidence]) -> list[RetainedEvidence]: ...


class InMemoryEvidenceBundleStore:
    """Dict-backed default store (``memory`` mode, unit tests).

    Insertion order is preserved so listings are stable, mirroring the durable
    store's ordinal ordering.
    """

    def __init__(self) -> None:
        self._records: dict[str, RetainedEvidence] = {}

    def save(self, record: RetainedEvidence) -> RetainedEvidence:
        if record.export_id in self._records:
            raise EvidenceImmutabilityError(
                f"retained evidence is append-only; overwrite denied for {record.export_id}"
            )
        previous_hash = (
            list(self._records.values())[-1].record_hash
            if self._records
            else CHAIN_GENESIS_HASH
        )
        stamped = attach_retained_evidence_integrity(
            record,
            sequence=len(self._records) + 1,
            previous_hash=previous_hash or CHAIN_GENESIS_HASH,
        )
        self._records[stamped.export_id] = stamped
        return stamped

    def get(self, export_id: str) -> RetainedEvidence | None:
        record = self._records.get(export_id)
        if record is not None:
            verify_retained_evidence(record)
        return record

    def list_for_program(self, program_id: str) -> list[RetainedEvidence]:
        return [r for r in self.list_all() if r.program_id == program_id]

    def list_all(self) -> list[RetainedEvidence]:
        records = list(self._records.values())
        verify_retained_evidence_chain(records).raise_for_tamper()
        return records

    def list_expired(self, as_of: datetime) -> list[RetainedEvidence]:
        return [r for r in self.list_all() if r.is_expired(as_of)]

    def purge_expired(
        self, as_of: datetime, *, context: GovernedEvidenceOperation | None = None
    ) -> list[str]:
        require_retention_purge_authority(context)
        expired = [r.export_id for r in self.list_expired(as_of)]
        for export_id in expired:
            del self._records[export_id]
        return expired

    def apply_legal_hold(
        self, export_id: str, *, context: GovernedEvidenceOperation
    ) -> RetainedEvidence:
        record = self.get(export_id)
        if record is None:
            raise KeyError(export_id)
        require_legal_hold_authority(context, record)
        updated = replace(
            record,
            legal_hold=True,
            governance_log=(
                *record.governance_log,
                {"operation": "legal_hold", **context.to_dict()},
            ),
        )
        self._records[export_id] = updated
        return updated

    def delete(self, export_id: str) -> None:
        raise EvidenceImmutabilityError(
            f"retained evidence is append-only; delete denied for {export_id}"
        )

    def verify_integrity(self) -> AuditChainVerification:
        return verify_retained_evidence_chain(self._records.values())

    def replay(self, records: Iterable[RetainedEvidence]) -> list[RetainedEvidence]:
        replayed: list[RetainedEvidence] = []
        for record in sorted(records, key=_retained_evidence_replay_key):
            replayed.append(self.save(record))
        return replayed


def retained_evidence_integrity_payload(
    record: RetainedEvidence,
    *,
    sequence: int,
    previous_hash: str,
) -> dict[str, Any]:
    """Canonical payload covered by a retained-evidence record hash.

    ``legal_hold`` and ``governance_log`` are governed retention sidecars and
    are intentionally excluded so a legal hold can be applied without mutating
    the immutable bundle content hash chain.
    """

    return {
        "export_id": record.export_id,
        "program_id": record.program_id,
        "purpose": record.purpose,
        "requested_by": record.requested_by,
        "audit_event_id": record.audit_event_id,
        "bundle_checksum": record.bundle_checksum,
        "data_classification": record.data_classification,
        "sensitive": record.sensitive,
        "export_scope": record.export_scope,
        "retention_class": record.retention_class,
        "retain_until": record.retain_until.isoformat(),
        "generated_at": record.generated_at.isoformat(),
        "period_start": record.period_start.isoformat(),
        "period_end": record.period_end.isoformat(),
        "correlation_id": record.correlation_id,
        "bundle": record.bundle,
        "sequence": sequence,
        "previous_hash": previous_hash,
        "signature_key_id": record.signature_key_id,
        "signature_version": record.signature_version,
        "signature_alg": record.signature_alg,
        "worm_sink_id": record.worm_sink_id,
    }


def attach_retained_evidence_integrity(
    record: RetainedEvidence,
    *,
    sequence: int,
    previous_hash: str,
) -> RetainedEvidence:
    record_hash = sha256_hex(
        retained_evidence_integrity_payload(
            record, sequence=sequence, previous_hash=previous_hash
        )
    )
    return replace(
        record,
        sequence=sequence,
        previous_hash=previous_hash,
        record_hash=record_hash,
    )


def verify_retained_evidence(record: RetainedEvidence) -> None:
    if (
        record.sequence is None
        or record.previous_hash is None
        or record.record_hash is None
    ):
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} is missing integrity metadata"
        )
    expected = sha256_hex(
        retained_evidence_integrity_payload(
            record,
            sequence=record.sequence,
            previous_hash=record.previous_hash,
        )
    )
    if expected != record.record_hash:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} hash mismatch"
        )
    if record.bundle.get("bundle_checksum") != record.bundle_checksum:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} bundle checksum mismatch"
        )
    canonical = canonical_json(record.bundle)
    if not canonical:
        raise EvidenceIntegrityError(f"retained evidence {record.export_id} is empty")


def verify_retained_evidence_chain(
    records: Iterable[RetainedEvidence],
) -> AuditChainVerification:
    issues: list[AuditIntegrityIssue] = []
    previous_hash = CHAIN_GENESIS_HASH
    previous_sequence = 0
    for index, record in enumerate(records):
        if record.sequence is None:
            issues.append(AuditIntegrityIssue(index, record.export_id, "missing sequence"))
            continue
        if record.sequence <= previous_sequence:
            issues.append(
                AuditIntegrityIssue(index, record.export_id, "sequence is not monotonic")
            )
        if record.previous_hash != previous_hash:
            issues.append(
                AuditIntegrityIssue(
                    index, record.export_id, "previous hash does not match"
                )
            )
        try:
            verify_retained_evidence(record)
        except EvidenceIntegrityError as exc:
            issues.append(AuditIntegrityIssue(index, record.export_id, str(exc)))
        previous_hash = record.record_hash or previous_hash
        previous_sequence = record.sequence
    return AuditChainVerification(ok=not issues, issues=tuple(issues))


def _retained_evidence_replay_key(record: RetainedEvidence) -> tuple[int, str, str]:
    return (
        record.sequence if record.sequence is not None else 2**63 - 1,
        record.generated_at.isoformat(),
        record.export_id,
    )


def require_legal_hold_authority(
    context: GovernedEvidenceOperation | None, record: RetainedEvidence
) -> None:
    if context is None:
        raise EvidenceGovernanceError("legal hold requires governed operation context")
    if context.role not in LEGAL_HOLD_ROLES:
        raise EvidenceGovernanceError("role is not authorized to apply legal hold")
    if context.actor == record.requested_by:
        raise EvidenceGovernanceError(
            "separation of duties: exporter cannot apply legal hold"
        )
    if not context.reason.strip():
        raise EvidenceGovernanceError("legal hold requires reason")


def require_retention_purge_authority(
    context: GovernedEvidenceOperation | None,
) -> None:
    if context is None:
        raise EvidenceGovernanceError("retention purge requires governed operation context")
    if context.role not in RETENTION_PURGE_ROLES:
        raise EvidenceGovernanceError("role is not authorized to purge retained evidence")
    if not context.reason.strip():
        raise EvidenceGovernanceError("retention purge requires reason")


__all__ = [
    "RETENTION_AUDIT",
    "RETENTION_REGULATORY",
    "RETENTION_STANDARD",
    "EvidenceBundleStore",
    "EvidenceGovernanceError",
    "EvidenceImmutabilityError",
    "EvidenceIntegrityError",
    "EvidenceRetentionPolicy",
    "GovernedEvidenceOperation",
    "InMemoryEvidenceBundleStore",
    "LEGAL_HOLD_ROLES",
    "RetainedEvidence",
    "RETENTION_PURGE_ROLES",
    "attach_retained_evidence_integrity",
    "require_legal_hold_authority",
    "require_retention_purge_authority",
    "resolve_retention_policy",
    "verify_retained_evidence",
    "verify_retained_evidence_chain",
]
