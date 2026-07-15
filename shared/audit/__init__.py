"""Shared audit primitives.

The canonical audit record and in-memory log come from R0-003
(:mod:`shared.audit.events`). ODP-R0-007 adds audit/security policy on top:
which actions must be audited, PII masking, and a builder that emits a security
audit event for authorization decisions (ODP-SD-09 §11/§7, ODP-AC-AUTH-005).
"""

from shared.audit.events import AuditEvent, InMemoryAuditLog
from shared.audit.integrity import (
    AuditChainVerification,
    AuditImmutabilityError,
    AuditIntegrityError,
)

from .persistence import (
    EvidenceBundleStore,
    EvidenceGovernanceError,
    EvidenceImmutabilityError,
    EvidenceIntegrityError,
    EvidenceRetentionPolicy,
    GovernedEvidenceOperation,
    InMemoryEvidenceBundleStore,
    RetainedEvidence,
    verify_retained_evidence_chain,
    resolve_retention_policy,
)
from .policy import (
    ALWAYS_AUDITED_ACTIONS,
    HIGH_RISK_ACTIONS,
    SECURITY_EVENT_TYPE,
    AuditOutcome,
    AuditRecorder,
    build_security_event,
    is_high_risk,
    mask_email,
    mask_phone,
    mask_text,
    requires_audit,
)

__all__ = [
    "ALWAYS_AUDITED_ACTIONS",
    "AuditChainVerification",
    "AuditEvent",
    "AuditImmutabilityError",
    "AuditIntegrityError",
    "AuditOutcome",
    "AuditRecorder",
    "EvidenceBundleStore",
    "EvidenceGovernanceError",
    "EvidenceImmutabilityError",
    "EvidenceIntegrityError",
    "EvidenceRetentionPolicy",
    "GovernedEvidenceOperation",
    "HIGH_RISK_ACTIONS",
    "InMemoryAuditLog",
    "InMemoryEvidenceBundleStore",
    "RetainedEvidence",
    "SECURITY_EVENT_TYPE",
    "build_security_event",
    "is_high_risk",
    "mask_email",
    "mask_phone",
    "mask_text",
    "requires_audit",
    "resolve_retention_policy",
    "verify_retained_evidence_chain",
]
