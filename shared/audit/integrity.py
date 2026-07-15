"""Hash-chain integrity primitives for immutable audit records.

The platform audit trail is append-only at the writer contract, but append-only
is not enough for governance evidence: a restored trail must also prove record
order and detect byte-level tampering. This module keeps that proof small and
runtime-native by stamping each record with a SHA-256 hash chained to the
previous record plus explicit key/version/sink metadata.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

CHAIN_GENESIS_HASH = "0" * 64
DEFAULT_AUDIT_INTEGRITY_KEY_ID = "odp-audit-hash-chain-key-v1"
DEFAULT_AUDIT_SIGNATURE_VERSION = "2026-07-15.v1"
DEFAULT_AUDIT_SIGNATURE_ALG = "sha256-hash-chain"
DEFAULT_AUDIT_WORM_SINK_ID = "odp-local-worm-audit-sink"


class AuditIntegrityError(ValueError):
    """Raised when an audit record or chain fails integrity verification."""


class AuditImmutabilityError(RuntimeError):
    """Raised when code attempts to mutate an append-only audit sink."""


@dataclass(frozen=True)
class AuditIntegrityIssue:
    index: int
    record_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "record_id": self.record_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AuditChainVerification:
    ok: bool
    issues: tuple[AuditIntegrityIssue, ...]

    def raise_for_tamper(self) -> None:
        if not self.ok:
            reasons = "; ".join(issue.reason for issue in self.issues)
            raise AuditIntegrityError(f"audit hash chain verification failed: {reasons}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def event_integrity_payload(
    event: Any,
    *,
    sequence: int,
    previous_hash: str,
    key_id: str,
    signature_version: str,
    signature_alg: str,
    sink_id: str,
) -> dict[str, Any]:
    """Return the canonical payload covered by an audit event hash."""

    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "actor": event.actor,
        "action": event.action,
        "resource": event.resource,
        "outcome": event.outcome,
        "correlation_id": event.correlation_id,
        "job_id": event.job_id,
        "metadata": event.metadata,
        "occurred_at": event.occurred_at.isoformat(),
        "sequence": sequence,
        "previous_hash": previous_hash,
        "signature_key_id": key_id,
        "signature_version": signature_version,
        "signature_alg": signature_alg,
        "worm_sink_id": sink_id,
    }


def attach_audit_event_integrity(
    event: Any,
    *,
    sequence: int,
    previous_hash: str,
    key_id: str = DEFAULT_AUDIT_INTEGRITY_KEY_ID,
    signature_version: str = DEFAULT_AUDIT_SIGNATURE_VERSION,
    signature_alg: str = DEFAULT_AUDIT_SIGNATURE_ALG,
    sink_id: str = DEFAULT_AUDIT_WORM_SINK_ID,
) -> Any:
    """Stamp ``event`` in place while preserving object identity.

    ``AuditEvent`` is frozen for normal callers. The append-only sink is the
    single trusted writer, so it owns integrity stamping and uses
    ``object.__setattr__`` internally to avoid replacing objects that callers
    may keep by identity.
    """

    payload = event_integrity_payload(
        event,
        sequence=sequence,
        previous_hash=previous_hash,
        key_id=key_id,
        signature_version=signature_version,
        signature_alg=signature_alg,
        sink_id=sink_id,
    )
    event_hash = sha256_hex(payload)
    object.__setattr__(event, "sequence", sequence)
    object.__setattr__(event, "previous_hash", previous_hash)
    object.__setattr__(event, "event_hash", event_hash)
    object.__setattr__(event, "signature_key_id", key_id)
    object.__setattr__(event, "signature_version", signature_version)
    object.__setattr__(event, "signature_alg", signature_alg)
    object.__setattr__(event, "worm_sink_id", sink_id)
    return event


def verify_audit_event_integrity(event: Any) -> bool:
    if event.sequence is None or event.previous_hash is None or event.event_hash is None:
        return False
    expected = sha256_hex(
        event_integrity_payload(
            event,
            sequence=event.sequence,
            previous_hash=event.previous_hash,
            key_id=event.signature_key_id,
            signature_version=event.signature_version,
            signature_alg=event.signature_alg,
            sink_id=event.worm_sink_id,
        )
    )
    return expected == event.event_hash


def verify_audit_chain(events: Iterable[Any]) -> AuditChainVerification:
    issues: list[AuditIntegrityIssue] = []
    previous_hash = CHAIN_GENESIS_HASH
    previous_sequence = 0
    for index, event in enumerate(events):
        record_id = getattr(event, "event_id", f"index:{index}")
        if event.sequence is None:
            issues.append(AuditIntegrityIssue(index, record_id, "missing sequence"))
            continue
        if event.sequence <= previous_sequence:
            issues.append(
                AuditIntegrityIssue(index, record_id, "sequence is not monotonic")
            )
        if event.previous_hash != previous_hash:
            issues.append(
                AuditIntegrityIssue(index, record_id, "previous hash does not match")
            )
        if not verify_audit_event_integrity(event):
            issues.append(AuditIntegrityIssue(index, record_id, "event hash mismatch"))
        previous_hash = event.event_hash or previous_hash
        previous_sequence = event.sequence
    return AuditChainVerification(ok=not issues, issues=tuple(issues))
