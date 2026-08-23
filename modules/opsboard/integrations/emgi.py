"""OpsBoard human approval and audit for the NetPlan/EMGI decision product.

Consumes contract: odayplus.netplan-emgi.v1

NetPlan's EMGI integration (:mod:`modules.netplan.integrations.emgi`) decides
which candidate sites are *admissible* — never which ones are approved.  This
module owns the other half of the acceptance criteria: the final decision on a
network plan is made by a human operator in OpsBoard, and every step of that
decision is written to an audit trail that carries the evidence and policy
versions the machine used.

Fail-closed rules:

- a review packet only opens on a document that validates against
  ``odayplus.netplan-emgi.v1``;
- a service identity may open a review but may never decide one;
- approval requires an operator holding an approver role, and a plan with no
  admitted candidate cannot be approved into a binding state;
- rejecting or returning a plan requires a reason, mirroring the existing
  OpsBoard governance return/reject gate;
- nothing is released for execution until the packet is ``APPROVED``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from modules.netplan.integrations.emgi import (
    CONTRACT_ID as NETPLAN_EMGI_CONTRACT_ID,
)
from modules.netplan.integrations.emgi import (
    CONTRACT_VERSION as NETPLAN_EMGI_CONTRACT_VERSION,
)
from modules.netplan.integrations.emgi import (
    CandidateAdmission,
    NetPlanEmgiDocument,
    netplan_emgi_document_digest,
    validate_netplan_emgi_document,
)
from shared.auth import Principal, Role

#: Bumped whenever the approval gate itself changes.  Persisted on the packet
#: so an archived approval says which rules it was taken under.
APPROVAL_POLICY_VERSION = "opsboard-netplan-emgi-approval-v1"

#: Roles allowed to take the binding network-plan decision.  Deliberately
#: narrow: expansion analysts prepare plans, these roles commit to them.
DEFAULT_APPROVER_ROLES: frozenset[Role] = frozenset(
    {Role.EXECUTIVE, Role.OPERATIONS_MANAGER, Role.SITE_REVIEWER}
)


class ApprovalState(StrEnum):
    PENDING_HUMAN_APPROVAL = "PENDING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"


class ApprovalAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RETURN = "RETURN"


_TERMINAL_STATE: dict[ApprovalAction, ApprovalState] = {
    ApprovalAction.APPROVE: ApprovalState.APPROVED,
    ApprovalAction.REJECT: ApprovalState.REJECTED,
    ApprovalAction.RETURN: ApprovalState.RETURNED,
}

#: Actions that may not be taken without an operator-supplied reason.
_REASON_REQUIRED: frozenset[ApprovalAction] = frozenset(
    {ApprovalAction.REJECT, ApprovalAction.RETURN}
)


class OpsBoardEmgiApprovalError(Exception):
    """Base error for the OpsBoard NetPlan/EMGI approval surface."""


class OpsBoardEmgiApprovalNotFound(OpsBoardEmgiApprovalError):
    """Raised when a review packet does not exist."""


class OpsBoardEmgiApprovalConflict(OpsBoardEmgiApprovalError):
    """Raised when a packet has already been decided."""


class OpsBoardEmgiApprovalPolicyError(OpsBoardEmgiApprovalError):
    """Raised when a decision violates the human-approval policy."""


def _text(value: Any) -> str | None:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@dataclass(frozen=True, slots=True)
class ApprovalActor:
    """Who is acting on a review packet, and whether they are a human."""

    subject_id: str
    roles: frozenset[Role] = frozenset()
    token_type: str = "oidc"
    authenticated: bool = True

    @classmethod
    def from_principal(cls, principal: Principal) -> ApprovalActor:
        attributes = principal.attributes or {}
        return cls(
            subject_id=principal.subject_id,
            roles=frozenset(principal.roles),
            token_type=str(attributes.get("token_type") or "oidc"),
            authenticated=principal.authenticated,
        )

    @property
    def is_human(self) -> bool:
        """A service principal is machine-to-machine, never an approver."""

        return (
            self.authenticated
            and self.token_type != "service"
            and not self.subject_id.startswith("service:")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "roles": sorted(role.value for role in self.roles),
            "token_type": self.token_type,
            "is_human": self.is_human,
        }


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable entry in a packet's audit trail."""

    event_id: str
    packet_id: str
    event_type: str
    actor_id: str
    occurred_at: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "packet_id": self.packet_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "occurred_at": self.occurred_at,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class HumanApprovalRecord:
    """The binding human decision on a network plan."""

    action: ApprovalAction
    actor: ApprovalActor
    decided_at: str
    reason: str | None = None
    approved_candidate_site_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "actor": self.actor.to_dict(),
            "decided_at": self.decided_at,
            "reason": self.reason,
            "approved_candidate_site_ids": list(self.approved_candidate_site_ids),
        }


@dataclass(frozen=True, slots=True)
class NetPlanApprovalPacket:
    """A netplan-emgi document parked in OpsBoard awaiting a human decision."""

    packet_id: str
    tenant_id: str
    scenario_key: str
    manifest_id: str
    document_id: str
    document_digest: str
    state: ApprovalState
    opened_at: str
    requested_by: ApprovalActor
    candidates: tuple[Mapping[str, Any], ...] = ()
    policy_versions: Mapping[str, Any] = field(default_factory=dict)
    decision: HumanApprovalRecord | None = None
    audit_trail: tuple[AuditEvent, ...] = ()

    @property
    def admitted_candidate_site_ids(self) -> tuple[str, ...]:
        return tuple(
            str(candidate["candidate_site_id"])
            for candidate in self.candidates
            if candidate.get("admission") == CandidateAdmission.ADMITTED.value
        )

    @property
    def withheld_candidate_site_ids(self) -> tuple[str, ...]:
        return tuple(
            str(candidate["candidate_site_id"])
            for candidate in self.candidates
            if candidate.get("admission") != CandidateAdmission.ADMITTED.value
        )

    @property
    def is_approved(self) -> bool:
        return self.state == ApprovalState.APPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "tenant_id": self.tenant_id,
            "scenario_key": self.scenario_key,
            "manifest_id": self.manifest_id,
            "document_id": self.document_id,
            "document_digest": self.document_digest,
            "document_contract_id": NETPLAN_EMGI_CONTRACT_ID,
            "document_contract_version": NETPLAN_EMGI_CONTRACT_VERSION,
            "state": self.state.value,
            "opened_at": self.opened_at,
            "requested_by": self.requested_by.to_dict(),
            "candidates": [dict(candidate) for candidate in self.candidates],
            "policy_versions": dict(self.policy_versions),
            "decision": self.decision.to_dict() if self.decision else None,
            "audit_trail": [event.to_dict() for event in self.audit_trail],
        }


class OpsBoardEmgiApprovalService:
    """Holds NetPlan/EMGI plans until a human operator decides on them."""

    def __init__(
        self,
        *,
        approver_roles: Iterable[Role] | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.approver_roles = frozenset(
            approver_roles if approver_roles is not None else DEFAULT_APPROVER_ROLES
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: uuid4().hex)
        self._packets: dict[str, NetPlanApprovalPacket] = {}

    def open_review(
        self,
        document: NetPlanEmgiDocument | Mapping[str, Any],
        *,
        requested_by: ApprovalActor | Principal,
    ) -> NetPlanApprovalPacket:
        """Park a validated netplan-emgi document for human review."""

        # Validate before anything is persisted: OpsBoard must never show an
        # operator a plan whose provenance was never checked.
        validate_netplan_emgi_document(document)
        data = document.to_dict() if isinstance(document, NetPlanEmgiDocument) else dict(document)
        # Pin the packet to the exact bytes reviewed, whether the caller handed
        # us a live document or a stored payload.
        digest = netplan_emgi_document_digest(data)
        actor = self._as_actor(requested_by)
        now = self._clock().isoformat()
        packet_id = f"netplan-emgi-approval-{self._id_factory()}"

        policy_versions = {
            "opsboard_approval": APPROVAL_POLICY_VERSION,
            "netplan_emgi_document": dict(data.get("policy") or {}),
            "candidate_evidence": {
                str(candidate.get("candidate_site_id")): dict(
                    (candidate.get("evidence") or {}).get("policy_versions") or {}
                )
                for candidate in data.get("candidates") or ()
            },
        }
        packet = NetPlanApprovalPacket(
            packet_id=packet_id,
            tenant_id=str(data["tenant_id"]),
            scenario_key=str(data["scenario_key"]),
            manifest_id=str(data["manifest_id"]),
            document_id=str(data["document_id"]),
            document_digest=digest,
            state=ApprovalState.PENDING_HUMAN_APPROVAL,
            opened_at=now,
            requested_by=actor,
            candidates=tuple(dict(candidate) for candidate in data.get("candidates") or ()),
            policy_versions=policy_versions,
        )
        packet = self._append_audit(
            packet,
            event_type="netplan_emgi.review_opened",
            actor=actor,
            occurred_at=now,
            payload={
                "document_id": packet.document_id,
                "document_digest": packet.document_digest,
                "manifest_id": packet.manifest_id,
                "admitted_candidate_site_ids": list(packet.admitted_candidate_site_ids),
                "withheld_candidate_site_ids": list(packet.withheld_candidate_site_ids),
                "policy_versions": policy_versions,
            },
        )
        self._packets[packet.packet_id] = packet
        return packet

    def get(self, packet_id: str) -> NetPlanApprovalPacket:
        packet = self._packets.get(packet_id)
        if packet is None:
            raise OpsBoardEmgiApprovalNotFound(f"Unknown approval packet: {packet_id}")
        return packet

    def record_decision(
        self,
        packet_id: str,
        *,
        actor: ApprovalActor | Principal,
        action: ApprovalAction | str,
        reason: str | None = None,
    ) -> NetPlanApprovalPacket:
        """Record the binding human decision and its audit entry."""

        packet = self.get(packet_id)
        if packet.state != ApprovalState.PENDING_HUMAN_APPROVAL:
            raise OpsBoardEmgiApprovalConflict(
                f"Packet {packet_id} is already {packet.state.value}"
            )

        try:
            resolved_action = ApprovalAction(action)
        except ValueError as error:
            raise OpsBoardEmgiApprovalPolicyError(f"Unknown approval action: {action!r}") from error

        approver = self._as_actor(actor)
        if not approver.authenticated:
            raise OpsBoardEmgiApprovalPolicyError(
                "The final NetPlan decision must be recorded by an authenticated human "
                f"operator; '{approver.subject_id}' is not authenticated"
            )
        if not approver.is_human:
            raise OpsBoardEmgiApprovalPolicyError(
                "The final NetPlan decision must be recorded by an authenticated human "
                f"operator; '{approver.subject_id}' is a service identity"
            )
        if not (approver.roles & self.approver_roles):
            raise OpsBoardEmgiApprovalPolicyError(
                f"'{approver.subject_id}' does not hold a NetPlan approver role"
            )

        cleaned_reason = _text(reason)
        if resolved_action in _REASON_REQUIRED and cleaned_reason is None:
            raise OpsBoardEmgiApprovalPolicyError(
                f"A reason is required to {resolved_action.value.lower()} a network plan"
            )

        admitted = packet.admitted_candidate_site_ids
        if resolved_action == ApprovalAction.APPROVE and not admitted:
            # Approving an empty plan would turn a machine "withheld" into a
            # binding go-ahead with nothing behind it.
            raise OpsBoardEmgiApprovalPolicyError(
                "A network plan with no admitted candidate site cannot be approved"
            )

        now = self._clock().isoformat()
        record = HumanApprovalRecord(
            action=resolved_action,
            actor=approver,
            decided_at=now,
            reason=cleaned_reason,
            approved_candidate_site_ids=(
                admitted if resolved_action == ApprovalAction.APPROVE else ()
            ),
        )
        decided = replace(packet, state=_TERMINAL_STATE[resolved_action], decision=record)
        decided = self._append_audit(
            decided,
            event_type=f"netplan_emgi.{resolved_action.value.lower()}d",
            actor=approver,
            occurred_at=now,
            payload={
                "document_id": decided.document_id,
                "document_digest": decided.document_digest,
                "manifest_id": decided.manifest_id,
                "reason": cleaned_reason,
                "approved_candidate_site_ids": list(record.approved_candidate_site_ids),
                "policy_versions": dict(decided.policy_versions),
            },
        )
        self._packets[decided.packet_id] = decided
        return decided

    def approved_candidate_site_ids(self, packet_id: str) -> tuple[str, ...]:
        """Sites released for binding execution, or an error if not approved."""

        packet = self.get(packet_id)
        if not packet.is_approved or packet.decision is None:
            raise OpsBoardEmgiApprovalPolicyError(
                f"Packet {packet_id} is {packet.state.value}; no site is released for execution"
            )
        return packet.decision.approved_candidate_site_ids

    def evidence_bundle(self, packet_id: str) -> dict[str, Any]:
        """Audit-ready export of the decision, its evidence and its trail."""

        packet = self.get(packet_id)
        return {
            "packet_id": packet.packet_id,
            "tenant_id": packet.tenant_id,
            "scenario_key": packet.scenario_key,
            "manifest_id": packet.manifest_id,
            "document_id": packet.document_id,
            "document_digest": packet.document_digest,
            "document_contract_id": NETPLAN_EMGI_CONTRACT_ID,
            "document_contract_version": NETPLAN_EMGI_CONTRACT_VERSION,
            "state": packet.state.value,
            "policy_versions": dict(packet.policy_versions),
            "candidate_evidence": {
                str(candidate.get("candidate_site_id")): dict(candidate.get("evidence") or {})
                for candidate in packet.candidates
            },
            "decision": packet.decision.to_dict() if packet.decision else None,
            "audit_trail": [event.to_dict() for event in packet.audit_trail],
        }

    def _append_audit(
        self,
        packet: NetPlanApprovalPacket,
        *,
        event_type: str,
        actor: ApprovalActor,
        occurred_at: str,
        payload: Mapping[str, Any],
    ) -> NetPlanApprovalPacket:
        event = AuditEvent(
            event_id=f"evt-{self._id_factory()}",
            packet_id=packet.packet_id,
            event_type=event_type,
            actor_id=actor.subject_id,
            occurred_at=occurred_at,
            payload=dict(payload),
        )
        return replace(packet, audit_trail=(*packet.audit_trail, event))

    @staticmethod
    def _as_actor(value: ApprovalActor | Principal) -> ApprovalActor:
        if isinstance(value, ApprovalActor):
            return value
        if isinstance(value, Principal):
            return ApprovalActor.from_principal(value)
        raise OpsBoardEmgiApprovalPolicyError(
            "An actor must be an ApprovalActor or an authenticated Principal"
        )


def admitted_candidates(
    document: NetPlanEmgiDocument | Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Return the admitted candidate payloads of a netplan-emgi document."""

    data = document.to_dict() if isinstance(document, NetPlanEmgiDocument) else document
    candidates = data.get("candidates") or ()
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        return ()
    return tuple(
        candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
        and candidate.get("admission") == CandidateAdmission.ADMITTED.value
    )


__all__ = [
    "APPROVAL_POLICY_VERSION",
    "DEFAULT_APPROVER_ROLES",
    "ApprovalAction",
    "ApprovalActor",
    "ApprovalState",
    "AuditEvent",
    "HumanApprovalRecord",
    "NetPlanApprovalPacket",
    "OpsBoardEmgiApprovalConflict",
    "OpsBoardEmgiApprovalError",
    "OpsBoardEmgiApprovalNotFound",
    "OpsBoardEmgiApprovalPolicyError",
    "OpsBoardEmgiApprovalService",
    "admitted_candidates",
]
