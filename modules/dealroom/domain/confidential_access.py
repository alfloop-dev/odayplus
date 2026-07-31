"""Confidential access classification, RBAC/ABAC audit, and redaction policies for DealRoom AVM."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from shared.auth.identity import Principal, Role
from shared.auth.rbac import Action, rbac_allows


class ConfidentialLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    PUBLIC = "PUBLIC"


class ConfidentialAccessDecision(StrEnum):
    PERMIT = "PERMIT"
    DENY = "DENY"


class ConfidentialLeakError(RuntimeError):
    """Raised when raw unmasked confidential financial values are detected in evidence or receipts."""


REDACTED_PLACEHOLDER = "[REDACTED_CONFIDENTIAL_VALUE]"

# Disallowed roles specifically forbidden from viewing or exporting confidential AVM deal outcomes
CONFIDENTIAL_AVM_DISALLOWED_ROLES = frozenset(
    {
        Role.REGIONAL_SUPERVISOR,
        Role.FRANCHISEE,
        Role.MARKETING_MANAGER,
    }
)


@dataclass(frozen=True)
class ConfidentialAccessAttempt:
    actor_id: str
    role: Role | str
    resource: str
    action: Action | str
    attempted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        role_str = self.role.value if isinstance(self.role, Role) else str(self.role)
        action_str = self.action.value if isinstance(self.action, Action) else str(self.action)
        return {
            "actor_id": self.actor_id,
            "role": role_str,
            "resource": self.resource,
            "action": action_str,
            "attempted_at": self.attempted_at.isoformat(),
            "context": self.context,
        }


def redact_confidential_value(val: Any) -> str:
    """Mask confidential numeric or string value for audit logging and public receipts."""
    if val is None:
        return "[NONE]"
    return REDACTED_PLACEHOLDER


def assert_no_confidential_leak(payload: Any, *, forbidden_raw_values: Sequence[float | str] = ()) -> None:
    """Scan payload (dict, list, or JSON string) for unmasked raw confidential transaction values.

    Raises ConfidentialLeakError if raw unmasked values are detected.
    """
    serialized = json.dumps(payload, default=str) if not isinstance(payload, str) else payload

    # Check for direct raw values passed in forbidden set
    for raw in forbidden_raw_values:
        if raw is None:
            continue
        raw_str = str(raw)
        if raw_str and len(raw_str) > 3 and raw_str in serialized:
            raise ConfidentialLeakError(
                f"Confidential raw value {raw_str!r} leaked in receipt payload"
            )

    # Regex scan for unmasked high-value currency figures matching raw confidential prices
    patterns = [
        r'"realized_price"\s*:\s*\d+',
        r'"realized_transaction_price"\s*:\s*\d+',
        r'"raw_transaction_price"\s*:\s*\d+',
        r'"unmasked_price"\s*:\s*\d+',
    ]
    for pattern in patterns:
        match = re.search(pattern, serialized, re.IGNORECASE)
        if match:
            raise ConfidentialLeakError(
                f"Confidential price field pattern {match.group(0)!r} leaked in receipt"
            )


class ConfidentialAccessAuditor:
    """Audits access requests against RBAC/ABAC rules for DealRoom AVM outcome data."""

    ALLOWED_RESOURCES = frozenset(
        {"dealroom", "avm", "avm_outcome", "model_ready.valuation_view"}
    )
    ALLOWED_ACTIONS = frozenset({Action.VIEW, Action.EXPORT})


    @classmethod
    def evaluate_access(
        cls,
        attempt: ConfidentialAccessAttempt,
        confidentiality: ConfidentialLevel = ConfidentialLevel.HIGH,
    ) -> tuple[ConfidentialAccessDecision, str, dict[str, Any]]:
        """Evaluate if access attempt is permitted and return (decision, reason, audit_receipt)."""
        role = attempt.role if isinstance(attempt.role, Role) else None
        action = attempt.action if isinstance(attempt.action, Action) else None

        # ABAC Context Attributes Extraction
        is_authenticated = bool(attempt.context.get("authenticated", True))
        data_room_access = bool(attempt.context.get("data_room_access", True))
        clearance_val = attempt.context.get("clearance", confidentiality.value if is_authenticated else "PUBLIC")
        tenant_matched = bool(attempt.context.get("tenant_matched", True))

        # Clearance hierarchy ranking
        clearance_levels = {
            "PUBLIC": 0,
            ConfidentialLevel.PUBLIC.value: 0,
            ConfidentialLevel.LOW.value: 1,
            ConfidentialLevel.MEDIUM.value: 2,
            ConfidentialLevel.HIGH.value: 3,
        }

        user_clearance = clearance_levels.get(str(clearance_val).upper(), 0)
        required_clearance = clearance_levels.get(confidentiality.value, 3)

        # Exact RBAC check using canonical Principal
        rbac_permitted = False
        if role is not None and action is not None and is_authenticated:
            principal = Principal(
                subject_id=attempt.actor_id,
                roles=frozenset({role}),
                authenticated=is_authenticated,
            )
            rbac_permitted = rbac_allows(principal, attempt.resource, action)

        # ABAC resource & action scope check
        resource_in_scope = (
            attempt.resource in cls.ALLOWED_RESOURCES
            or attempt.resource.startswith("dealroom/")
            or attempt.resource.startswith("avm/")
        )
        action_in_scope = action in cls.ALLOWED_ACTIONS if action is not None else False

        role_disallowed = role in CONFIDENTIAL_AVM_DISALLOWED_ROLES
        role_authorized = role in (Role.FINANCE_LEGAL, Role.PLATFORM_ADMIN)

        role_repr = role.value if role else str(attempt.role)

        if not is_authenticated:
            reason = f"Principal {attempt.actor_id!r} is not authenticated"
            decision = ConfidentialAccessDecision.DENY
        elif not data_room_access:
            reason = f"Principal {attempt.actor_id!r} lacks required data_room_access authority"
            decision = ConfidentialAccessDecision.DENY
        elif not tenant_matched:
            reason = f"Tenant authority mismatch for principal {attempt.actor_id!r}"
            decision = ConfidentialAccessDecision.DENY
        elif user_clearance < required_clearance:
            reason = f"Insufficient clearance level {clearance_val!r} for confidentiality {confidentiality.value!r}"
            decision = ConfidentialAccessDecision.DENY
        elif confidentiality == ConfidentialLevel.HIGH:
            if role_disallowed:
                reason = f"Role {role_repr!r} is explicitly forbidden from high-confidentiality AVM outcome data"
                decision = ConfidentialAccessDecision.DENY
            elif not resource_in_scope:
                reason = f"Resource {attempt.resource!r} is outside AVM dealroom audit scope"
                decision = ConfidentialAccessDecision.DENY
            elif not action_in_scope:
                reason = f"Action {attempt.action!r} is not authorized for confidential AVM dealroom data"
                decision = ConfidentialAccessDecision.DENY
            elif rbac_permitted and role_authorized:
                reason = f"Access granted to confidential resource {attempt.resource!r} for role {attempt.role!r}"
                decision = ConfidentialAccessDecision.PERMIT
            else:
                reason = f"Access denied to resource {attempt.resource!r} for role {attempt.role!r}"
                decision = ConfidentialAccessDecision.DENY
        else:
            if resource_in_scope and action_in_scope and rbac_permitted:
                reason = f"Access granted for role {attempt.role!r}"
                decision = ConfidentialAccessDecision.PERMIT
            else:
                reason = f"Access denied for role {attempt.role!r}"
                decision = ConfidentialAccessDecision.DENY

        receipt = {
            "actor_id": attempt.actor_id,
            "role": attempt.role.value if isinstance(attempt.role, Role) else str(attempt.role),
            "resource": attempt.resource,
            "action": attempt.action.value if isinstance(attempt.action, Action) else str(attempt.action),
            "confidentiality_level": confidentiality.value,
            "decision": decision.value,
            "reason": reason,
            "evaluated_at": datetime.now(UTC).isoformat(),
            "redacted_sample": REDACTED_PLACEHOLDER,
        }
        return decision, reason, receipt
