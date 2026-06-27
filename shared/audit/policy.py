"""Audit policy: which actions must be audited, PII masking, and security
audit-event construction.

Source baseline: ODP-SD-09 §11 (audited actions), §7 (PII masking),
ODP-SA-04 §7 (high-risk actions), §9 ODP-AC-AUTH-005, ODP-SA-08 §5.

This module is additive on top of the canonical platform audit record
(:class:`shared.audit.events.AuditEvent` + ``InMemoryAuditLog`` from R0-003):
it does not redefine the record, it provides the security-domain policy and a
builder that emits a canonical ``AuditEvent`` for authorization decisions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from shared.auth.identity import DataClassification
from shared.auth.rbac import Action

from .events import AuditEvent

if TYPE_CHECKING:
    from shared.auth.abac import AccessRequest, Decision


# Canonical security audit outcomes (usable directly as the AuditEvent.outcome
# string, since StrEnum members are str).
class AuditOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    SUCCESS = "success"
    FAILURE = "failure"


# event_type used for all authorization decisions written to the audit log.
SECURITY_EVENT_TYPE = "security.authorization"


@runtime_checkable
class AuditRecorder(Protocol):
    """Anything that can record an audit event (e.g. ``InMemoryAuditLog``)."""

    def record(self, event: AuditEvent) -> AuditEvent: ...


# High-risk verbs that always require policy hooks + audit (ODP-SA-04 §7).
HIGH_RISK_ACTIONS: frozenset[Action] = frozenset(
    {Action.APPROVE, Action.EXECUTE, Action.PUBLISH, Action.OVERRIDE, Action.ROLLBACK}
)

# Actions that must always be audited regardless of risk (ODP-SD-09 §11):
# create/modify/approve/reject/override of decisions, exports, releases, etc.
ALWAYS_AUDITED_ACTIONS: frozenset[Action] = HIGH_RISK_ACTIONS | frozenset(
    {Action.CREATE, Action.UPDATE, Action.DELETE, Action.EXPORT}
)

# Viewing or exporting data at/above this level must be audited (SD-09 §11.2).
AUDIT_VISIBILITY_THRESHOLD = DataClassification.RESTRICTED


def is_high_risk(action: Action) -> bool:
    return action in HIGH_RISK_ACTIONS


def requires_audit(
    action: Action,
    data_classification: DataClassification = DataClassification.CONFIDENTIAL,
) -> bool:
    """True when the action/data combination must be written to the audit log."""

    if action in ALWAYS_AUDITED_ACTIONS:
        return True
    # Reads of restricted data (PII, valuations, deal room) are audited too.
    return data_classification >= AUDIT_VISIBILITY_THRESHOLD


# --- PII masking (ODP-SD-09 §7, ODP-SA-08 §5) ------------------------------

def mask_phone(value: str, *, visible: int = 3) -> str:
    """Mask a phone number, keeping the last ``visible`` digits (SD-09 §7)."""

    digits = [c for c in value if c.isdigit()]
    if len(digits) <= visible:
        return "*" * len(digits)
    masked = "*" * (len(digits) - visible) + "".join(digits[-visible:])
    return masked


def mask_email(value: str) -> str:
    """Mask the local part of an email, keeping the first character + domain."""

    if "@" not in value:
        return mask_text(value)
    local, _, domain = value.partition("@")
    if not local:
        return "@" + domain
    head = local[0]
    return f"{head}{'*' * (len(local) - 1)}@{domain}"


def mask_text(value: str, *, visible: int = 0) -> str:
    """Generic masking: replace all but the last ``visible`` characters."""

    if visible <= 0 or len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[-visible:]


# --- security audit events (ODP-AC-AUTH-005) -------------------------------

def build_security_event(request: AccessRequest, decision: Decision) -> AuditEvent:
    """Build a canonical AuditEvent for an authorization decision.

    ODP-AC-AUTH-005 requires permission failures to record actor, time, IP,
    resource, and reason. Actor/time/resource map to AuditEvent fields;
    IP/reason/policy and scope ids live in ``metadata``.
    """

    resource = request.resource
    outcome = AuditOutcome.ALLOW if decision.allowed else AuditOutcome.DENY
    resource_ref = resource.type
    if resource.resource_id:
        resource_ref = f"{resource.type}/{resource.resource_id}"
    correlation_id = (
        request.environment.attributes.get("correlation_id")
        if request.environment.attributes
        else None
    )
    return AuditEvent(
        event_type=SECURITY_EVENT_TYPE,
        actor=request.principal.subject_id,
        action=request.action.value,
        resource=resource_ref,
        outcome=outcome.value,
        correlation_id=correlation_id or "unknown",
        metadata={
            "source_ip": request.environment.source_ip,
            "reason": decision.reason,
            "policy_id": decision.policy_id,
            "tenant_id": resource.tenant_id or request.principal.tenant_id,
            "data_classification": resource.data_classification.name,
            "obligations": sorted(decision.obligations),
        },
    )
