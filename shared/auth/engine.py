"""Authorization engine: the single decision hook.

Composes the SD-09 §5 clauses in order:

    RBAC (role permits action)
      -> ABAC (scope / classification / isolation)
      -> high-risk feature-flag + separation-of-duties hooks
      -> audit obligation

Every denial is written to the audit log as a security event
(ODP-AC-AUTH-005 / "403 paths write security audit events"). High-risk actions
gate on a feature flag and a separation-of-duties hook (ODP-SA-04 §7,
ODP-SD-09 §5.1) so they can never be approved without an explicit policy path.

The engine reuses the canonical platform audit record from R0-003
(:class:`shared.audit.events.AuditEvent` written through ``InMemoryAuditLog``);
it does not introduce a second audit event shape.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING

from shared.audit.events import InMemoryAuditLog

from .abac import AbacPolicy, AccessRequest, Decision, evaluate_abac

if TYPE_CHECKING:
    from shared.audit.policy import AuditRecorder
from .feature_flags import FeatureFlagRegistry, default_registry
from .identity import RiskLevel
from .rbac import Action, rbac_allows


def high_risk_flag_key(resource_type: str, action: Action) -> str:
    """Conventional flag key gating a high-risk action on a resource type."""

    return f"high_risk.{resource_type}.{action.value}"


class AuthorizationEngine:
    """Coordinates RBAC, ABAC, feature flags, and the audit hook."""

    def __init__(
        self,
        *,
        audit_log: AuditRecorder | None = None,
        flags: FeatureFlagRegistry | None = None,
        policies: Sequence[AbacPolicy] | None = None,
    ) -> None:
        self._audit = audit_log if audit_log is not None else InMemoryAuditLog()
        self._flags = flags if flags is not None else default_registry()
        self._policies = policies

    @property
    def audit_log(self) -> AuditRecorder:
        return self._audit

    def authorize(self, request: AccessRequest, *, on: date | None = None) -> Decision:
        """Return an allow/deny decision and emit audit events as required."""

        decision = self._decide(request, on=on or _today())
        self._record(request, decision)
        return decision

    # -- internal -----------------------------------------------------------

    def _decide(self, request: AccessRequest, *, on: date) -> Decision:
        # Lazy import avoids an import cycle (audit.policy -> auth -> engine).
        from shared.audit.policy import is_high_risk

        if not rbac_allows(request.principal, request.resource.type, request.action):
            return Decision.deny(
                f"role does not permit {request.action.value} on {request.resource.type}",
                policy_id="rbac",
            )

        abac = (
            evaluate_abac(request, self._policies)
            if self._policies is not None
            else evaluate_abac(request)
        )
        if not abac.allowed:
            return abac

        if is_high_risk(request.action):
            return self._check_high_risk(request, on=on)

        return Decision.allow("authorized")

    def _check_high_risk(self, request: AccessRequest, *, on: date) -> Decision:
        """High-risk policy hook: feature flag + separation of duties."""

        key = high_risk_flag_key(request.resource.type, request.action)
        flag = self._flags.get(key)
        # An unregistered high-risk action fails closed: it must be governed by
        # an explicit flag before it can be authorized.
        if flag is None or not flag.is_active(on):
            return Decision.deny(
                f"high-risk action gated by disabled/absent flag {key!r}",
                policy_id="high_risk.feature_flag",
            )

        # Separation of duties: the proposer of a high-risk item may not also
        # approve it (ODP-SD-09 §5 / §4 "提出、驗證、核准、執行不得全部由同一人").
        proposer = request.resource.attributes.get("proposed_by")
        if (
            request.action == Action.APPROVE
            and proposer is not None
            and proposer == request.principal.subject_id
        ):
            return Decision.deny(
                "separation of duties: proposer cannot approve own request",
                policy_id="high_risk.separation_of_duties",
            )

        obligations = {"audit"}
        if request.resource.risk_level >= RiskLevel.HIGH:
            obligations.add("two_person_approval")
        return Decision.allow("high-risk authorized", obligations=frozenset(obligations))

    def _record(self, request: AccessRequest, decision: Decision) -> None:
        from shared.audit.policy import build_security_event, requires_audit

        should_audit = not decision.allowed or requires_audit(
            request.action, request.resource.data_classification
        )
        if not should_audit:
            return
        self._audit.record(build_security_event(request, decision))


def _today() -> date:
    from datetime import UTC, datetime

    return datetime.now(UTC).date()
