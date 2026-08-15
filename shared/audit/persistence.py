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
from shared.audit.worm import AuditWormSink

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
    governance_hash: str | None = None
    purged_at: datetime | None = None
    tombstone_hash: str | None = None

    def is_expired(self, as_of: datetime) -> bool:
        """A record is purgeable only when past retention and not on hold."""

        return self.purged_at is None and not self.legal_hold and as_of >= self.retain_until

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
            "purged_at": self.purged_at.isoformat() if self.purged_at else None,
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
                "governance_hash": self.governance_hash,
                "tombstone_hash": self.tombstone_hash,
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

    def __init__(self, *, worm_sink: AuditWormSink | None = None) -> None:
        self._records: dict[str, RetainedEvidence] = {}
        self._worm_sink = worm_sink

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
            _record_for_sink(record, self._worm_sink),
            sequence=self._next_sequence(),
            previous_hash=previous_hash or CHAIN_GENESIS_HASH,
        )
        if self._worm_sink is not None:
            self._worm_sink.write_retained_evidence(stamped)
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
        expired = self.list_expired(as_of)
        purged: list[str] = []
        for record in expired:
            require_retention_purge_authority(context, record)
            tombstone = tombstone_retained_evidence(record, context=context)
            if self._worm_sink is not None:
                self._worm_sink.write_retained_evidence(tombstone)
            self._records[record.export_id] = tombstone
            purged.append(record.export_id)
        return purged

    def apply_legal_hold(
        self, export_id: str, *, context: GovernedEvidenceOperation
    ) -> RetainedEvidence:
        record = self.get(export_id)
        if record is None:
            raise KeyError(export_id)
        require_legal_hold_authority(context, record)
        updated = append_retained_evidence_governance(
            record,
            operation="legal_hold",
            context=context,
            legal_hold=True,
        )
        if self._worm_sink is not None:
            self._worm_sink.write_retained_evidence(updated)
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
            if record.export_id in self._records:
                raise EvidenceImmutabilityError(
                    f"retained evidence is append-only; overwrite denied for {record.export_id}"
                )
            verify_retained_evidence(record)
            if self._worm_sink is not None:
                self._worm_sink.write_retained_evidence(record)
            self._records[record.export_id] = record
            replayed.append(record)
        return replayed

    def _next_sequence(self) -> int:
        existing = [record.sequence or 0 for record in self._records.values()]
        return max(existing, default=0) + 1


def retained_evidence_integrity_payload(
    record: RetainedEvidence,
    *,
    sequence: int,
    previous_hash: str,
) -> dict[str, Any]:
    """Canonical payload covered by a retained-evidence record hash.

    Mutable governance state is covered by ``governance_hash`` so legal holds
    and purge tombstones can be appended without rewriting the immutable content
    hash that successors point at.
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


def retained_evidence_governance_payload(record: RetainedEvidence) -> dict[str, Any]:
    """Canonical payload for retention/legal-hold sidecar integrity."""

    return {
        "export_id": record.export_id,
        "sequence": record.sequence,
        "record_hash": record.record_hash,
        "legal_hold": record.legal_hold,
        "purged_at": record.purged_at.isoformat() if record.purged_at else None,
        "governance_log": list(record.governance_log),
        "signature_key_id": record.signature_key_id,
        "signature_version": record.signature_version,
        "signature_alg": record.signature_alg,
        "worm_sink_id": record.worm_sink_id,
    }


def retained_evidence_tombstone_payload(record: RetainedEvidence) -> dict[str, Any]:
    """Canonical payload for a purged record tombstone."""

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
        "sequence": record.sequence,
        "previous_hash": record.previous_hash,
        "record_hash": record.record_hash,
        "purged_at": record.purged_at.isoformat() if record.purged_at else None,
        "tombstone_bundle": record.bundle,
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
        governance_hash=sha256_hex(
            retained_evidence_governance_payload(
                replace(record, sequence=sequence, previous_hash=previous_hash, record_hash=record_hash)
            )
        ),
    )


def append_retained_evidence_governance(
    record: RetainedEvidence,
    *,
    operation: str,
    context: GovernedEvidenceOperation,
    legal_hold: bool | None = None,
    purged_at: datetime | None = None,
    bundle: dict[str, Any] | None = None,
) -> RetainedEvidence:
    """Append a tamper-evident governance operation to a retained record."""

    previous_governance_hash = (
        record.governance_log[-1].get("governance_hash")
        if record.governance_log
        else record.record_hash
    ) or CHAIN_GENESIS_HASH
    entry = {
        "operation": operation,
        **context.to_dict(),
        "previous_governance_hash": previous_governance_hash,
        "target_record_hash": record.record_hash,
    }
    entry["governance_hash"] = sha256_hex(
        {
            "export_id": record.export_id,
            "sequence": record.sequence,
            **{key: value for key, value in entry.items() if key != "governance_hash"},
        }
    )
    updated = replace(
        record,
        legal_hold=record.legal_hold if legal_hold is None else legal_hold,
        purged_at=record.purged_at if purged_at is None else purged_at,
        bundle=record.bundle if bundle is None else bundle,
        governance_log=(*record.governance_log, entry),
    )
    return attach_retained_evidence_governance_integrity(updated)


def tombstone_retained_evidence(
    record: RetainedEvidence,
    *,
    context: GovernedEvidenceOperation | None,
) -> RetainedEvidence:
    if context is None:
        raise EvidenceGovernanceError("retention purge requires governed operation context")
    purged_at = context.requested_at
    tombstone_bundle = {
        "purged": True,
        "export_id": record.export_id,
        "program_id": record.program_id,
        "bundle_checksum": record.bundle_checksum,
        "record_hash": record.record_hash,
        "retention_class": record.retention_class,
        "retain_until": record.retain_until.isoformat(),
        "purged_at": purged_at.isoformat(),
        "purge_correlation_id": context.correlation_id,
    }
    governed = append_retained_evidence_governance(
        record,
        operation="retention_purge",
        context=context,
        purged_at=purged_at,
        bundle=tombstone_bundle,
    )
    return attach_retained_evidence_governance_integrity(
        replace(
            governed,
            tombstone_hash=sha256_hex(retained_evidence_tombstone_payload(governed)),
        )
    )


def attach_retained_evidence_governance_integrity(
    record: RetainedEvidence,
) -> RetainedEvidence:
    return replace(
        record,
        governance_hash=sha256_hex(retained_evidence_governance_payload(record)),
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
    if record.purged_at is None:
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
    else:
        _verify_retained_evidence_tombstone(record)
    _verify_retained_evidence_governance(record)


def _verify_retained_evidence_tombstone(record: RetainedEvidence) -> None:
    if record.tombstone_hash is None:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} purge tombstone hash missing"
        )
    if record.bundle.get("purged") is not True:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} purge tombstone missing"
        )
    if record.bundle.get("record_hash") != record.record_hash:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} purge tombstone record hash mismatch"
        )
    if record.bundle.get("bundle_checksum") != record.bundle_checksum:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} purge tombstone checksum mismatch"
        )
    expected = sha256_hex(retained_evidence_tombstone_payload(record))
    if expected != record.tombstone_hash:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} purge tombstone hash mismatch"
        )


def _verify_retained_evidence_governance(record: RetainedEvidence) -> None:
    if record.governance_hash is None:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} governance hash missing"
        )
    previous_governance_hash = record.record_hash or CHAIN_GENESIS_HASH
    legal_hold_seen = False
    purge_seen = False
    for index, entry in enumerate(record.governance_log):
        if entry.get("previous_governance_hash") != previous_governance_hash:
            raise EvidenceIntegrityError(
                f"retained evidence {record.export_id} governance chain mismatch at {index}"
            )
        expected = sha256_hex(
            {
                "export_id": record.export_id,
                "sequence": record.sequence,
                **{
                    key: value
                    for key, value in entry.items()
                    if key != "governance_hash"
                },
            }
        )
        if entry.get("governance_hash") != expected:
            raise EvidenceIntegrityError(
                f"retained evidence {record.export_id} governance hash mismatch at {index}"
            )
        previous_governance_hash = str(entry["governance_hash"])
        legal_hold_seen = legal_hold_seen or entry.get("operation") == "legal_hold"
        purge_seen = purge_seen or entry.get("operation") == "retention_purge"
    if record.legal_hold != legal_hold_seen:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} legal hold state mismatch"
        )
    if (record.purged_at is not None) != purge_seen:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} purge state mismatch"
        )
    expected_governance_hash = sha256_hex(retained_evidence_governance_payload(record))
    if expected_governance_hash != record.governance_hash:
        raise EvidenceIntegrityError(
            f"retained evidence {record.export_id} governance state hash mismatch"
        )


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
    record: RetainedEvidence | None = None,
) -> None:
    if context is None:
        raise EvidenceGovernanceError("retention purge requires governed operation context")
    if context.role not in RETENTION_PURGE_ROLES:
        raise EvidenceGovernanceError("role is not authorized to purge retained evidence")
    if not context.reason.strip():
        raise EvidenceGovernanceError("retention purge requires reason")
    if record is None:
        return
    if context.actor == record.requested_by:
        raise EvidenceGovernanceError(
            "separation of duties: exporter cannot purge retained evidence"
        )
    export_governance = record.bundle.get("export_governance")
    if isinstance(export_governance, dict) and context.actor == export_governance.get(
        "authorized_by"
    ):
        raise EvidenceGovernanceError(
            "separation of duties: export authorizer cannot purge retained evidence"
        )


def _record_for_sink(
    record: RetainedEvidence, worm_sink: AuditWormSink | None
) -> RetainedEvidence:
    if record.record_hash is not None:
        return record
    if worm_sink is None:
        return record
    return replace(record, worm_sink_id=worm_sink.sink_id)


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
    "append_retained_evidence_governance",
    "require_legal_hold_authority",
    "require_retention_purge_authority",
    "resolve_retention_policy",
    "tombstone_retained_evidence",
    "verify_retained_evidence",
    "verify_retained_evidence_chain",
]
