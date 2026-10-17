"""Read genuine SiteScore evidence, without admitting an export.

This internal resolver has no HTTP mount, program approval or export policy.
It resolves existing owner facts only; no UI row, current model, source label,
or caller-provided authorization can complete the missing admission.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from modules.opsboard.application.network_reviews import (
    NetworkReviewConflict,
    NetworkReviewNotFound,
    NetworkReviewRoleError,
    NetworkReviewRuntimeUnavailable,
    NetworkReviewService,
)
from modules.sitescore.domain.scoring import SiteScoreReport
from shared.audit import AuditEvent
from shared.auth import Action, Principal
from shared.auth.rbac import rbac_allows
from shared.domain.models import Approval
from shared.infrastructure.persistence.audit_log import DurableAuditLog
from shared.infrastructure.persistence.repositories import (
    DurableApprovalRepository,
    DurableDecisionStore,
    DurableSiteScoreRepository,
)
from shared.workflow.sitescore import (
    DecisionStatus,
    DecisionTransition,
    SiteScoreDecision,
    SiteScoreDecisionError,
)

Section = Literal["decision_log", "audit"]


class SiteScoreEvidenceUnavailable(RuntimeError):
    """Missing or conflicting canonical evidence; no export admission."""


class SiteScoreEvidenceDenied(PermissionError):
    """The current verified principal cannot resolve this source."""


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_FACTS_INVALID") from exc
    return hashlib.sha256(encoded).hexdigest()


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class SiteScoreEvidenceSelector:
    decision_id: str
    expected_decision_version: int
    sections: tuple[Section, ...] = ("decision_log", "audit")

    def __post_init__(self) -> None:
        # Canonical decision IDs have the sitescore-decision- prefix; they are
        # not bare UUIDs. Actual ownership, rather than a guessed format, joins.
        if (not _text(self.decision_id) or type(self.expected_decision_version) is not int
                or self.expected_decision_version < 1
                or not isinstance(self.sections, tuple) or not self.sections
                or any(section not in ("decision_log", "audit") for section in self.sections)
                or len(set(self.sections)) != len(self.sections)):
            raise ValueError("Unsupported or invalid SiteScore evidence selector")


@dataclass(frozen=True)
class BoundSiteScoreAuditEvent:
    event_id: str
    event_hash: str
    action: str
    outcome: str
    actor: str
    correlation_id: str
    occurred_at: datetime
    decision_version: int
    content_sha256: str


@dataclass(frozen=True)
class ResolvedSiteScoreEvidence:
    """Internal allowlisted references; never an exported DecisionCard.

    Raw report, arbitrary audit metadata, contact/address data and free-form
    reasons are deliberately absent. A later admitted projector must resolve
    field authority before rendering any such content in any format.
    """

    tenant_id: str
    candidate_id: str
    decision_id: str
    decision_version: int
    report_id: str
    report_version: int
    report_sha256: str
    source_snapshot_ids: tuple[str, ...]
    recorded_model_reference: str
    recorded_feature_reference: str
    recorded_policy_reference: str
    previous_decision_id: str | None
    approval_id: str
    approval_status: str
    decision_actor: str
    decided_at: datetime
    recorded_network_action: str | None
    rationale_sha256: str
    events: tuple[BoundSiteScoreAuditEvent, ...]
    sections: tuple[Section, ...]
    source_digest: str
    missing_admission: tuple[str, ...] = field(default=(
        "approved_program_scope", "source_classification_and_field_authority",
        "applicable_approved_retention", "source_snapshot_metadata",
        "artifact_and_delivery_admission",
    ), init=False)
    export_admitted: bool = field(default=False, init=False)


class SiteScoreCanonicalEvidenceResolver:
    def __init__(self, *, decisions: DurableDecisionStore, reports: DurableSiteScoreRepository,
                 approvals: DurableApprovalRepository, audit_log: DurableAuditLog,
                 review_owner: NetworkReviewService) -> None:
        self.decisions, self.reports, self.approvals = decisions, reports, approvals
        self.audit_log, self.review_owner = audit_log, review_owner

    def _binding(self, tenant: str):
        if (not isinstance(self.decisions, DurableDecisionStore)
                or not isinstance(self.reports, DurableSiteScoreRepository)
                or not isinstance(self.approvals, DurableApprovalRepository)
                or not isinstance(self.audit_log, DurableAuditLog)
                or not isinstance(self.review_owner, NetworkReviewService)):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_OWNER_REQUIRED")
        engine = self.decisions.engine
        workflow = self.review_owner._decision_workflow
        if (engine is None or any(owner.engine is not engine or owner.tenant_id != tenant
                                  for owner in (self.decisions, self.reports, self.approvals))
                or self.audit_log._engine is not engine or workflow is None
                or workflow.audit_log is not self.audit_log
                or workflow._store is not self.decisions or workflow._approval_store is not self.approvals
                or self.review_owner._decision_repository is not self.decisions
                or self.review_owner._sitescore_repository is not self.reports):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_OWNER_BINDING_REQUIRED")
        return engine

    def resolve_source(self, principal: Principal, selector: SiteScoreEvidenceSelector) -> ResolvedSiteScoreEvidence:
        """Resolve actual scoped facts. This grants no export or policy approval."""
        if (not isinstance(principal, Principal) or not principal.authenticated
                or not _text(principal.subject_id) or not _text(principal.tenant_id)
                or not rbac_allows(principal, "audit", Action.EXPORT)
                or not rbac_allows(principal, "operator_console", Action.VIEW)
                or not principal.scope.permits_module("operator_console")
                or not rbac_allows(principal, "sitescore", Action.VIEW)):
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SCOPE_DENIED")
        if not isinstance(selector, SiteScoreEvidenceSelector):
            raise ValueError("Typed SiteScore evidence selector required")
        tenant = principal.tenant_id
        engine = self._binding(tenant)
        with engine.transaction():
            decision = self.decisions.get_decision(selector.decision_id)
            if not isinstance(decision, SiteScoreDecision) or decision.tenant_id != tenant:
                raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SOURCE_NOT_FOUND")
            self._authorize(principal, decision.candidate_site_id)
            if decision.version != selector.expected_decision_version:
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_VERSION_CHANGED")
            report = self.decisions.get_report(decision.decision_id)
            self._require_report(decision, report)
            try:
                self.review_owner._decision_workflow._validate_existing_round(decision)
            except SiteScoreDecisionError as exc:
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_REVIEW_ROUND_REQUIRED") from exc
            approval = self.approvals.get(decision.approval_id) if decision.approval_id else None
            self._require_signature(decision, approval)
            report_digest = _digest(report.to_dict())
            events = self._events(tenant, decision)
            snapshots = report.source_snapshot_ids
            if (not isinstance(snapshots, (tuple, list))
                    or any(not _text(value) for value in snapshots)
                    or len(set(snapshots)) != len(snapshots)):
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_SOURCE_REFERENCES_INVALID")
            # Freshness is a current ABAC check, not a new scoring/model gate.
            # Reads cannot reuse a former candidate grant after revocation.
            self._authorize(principal, decision.candidate_site_id)
            if (self.decisions.get_decision(decision.decision_id) != decision
                    or self.decisions.get_report(decision.decision_id) != report
                    or self.approvals.get(approval.approval_id) != approval):
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_VERSION_CHANGED")
            reference_facts = {
                "tenant": tenant, "decision": decision.to_dict(), "report_sha256": report_digest,
                "approval": {**vars(approval), "approved_at": approval.approved_at.isoformat()},
                "events": [{**vars(event), "occurred_at": event.occurred_at.isoformat()} for event in events],
            }
            return ResolvedSiteScoreEvidence(
                tenant_id=tenant, candidate_id=decision.candidate_site_id,
                decision_id=decision.decision_id, decision_version=decision.version,
                report_id=report.report_id, report_version=report.report_version, report_sha256=report_digest,
                source_snapshot_ids=tuple(snapshots), recorded_model_reference=report.model_version,
                recorded_feature_reference=report.feature_version, recorded_policy_reference=decision.policy_version,
                previous_decision_id=getattr(decision, "previous_decision_id", None),
                approval_id=approval.approval_id, approval_status=approval.approval_status,
                decision_actor=approval.approver_id, decided_at=approval.approved_at,
                recorded_network_action=decision.network_action,
                rationale_sha256=_digest(decision.history[-1].reason), events=events,
                sections=selector.sections, source_digest=_digest(reference_facts),
            )

    def _authorize(self, principal: Principal, candidate_id: str) -> None:
        try:
            self.review_owner.authorize_native_report_candidate(
                candidate_id=candidate_id, principal=principal, report_repository=self.reports,
            )
        except (NetworkReviewNotFound, NetworkReviewRoleError) as exc:
            raise SiteScoreEvidenceDenied("CANONICAL_EVIDENCE_SCOPE_DENIED") from exc
        except (NetworkReviewConflict, NetworkReviewRuntimeUnavailable) as exc:
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_OWNER_BINDING_REQUIRED") from exc

    @staticmethod
    def require_export_admission(_source: ResolvedSiteScoreEvidence) -> None:
        # No approved program/classification/retention/field authority port is
        # installed in this first source resolver. Never promote references to
        # an export-ready claim, even for a valid canonical source resolution.
        raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_INPUT_REQUIRED")

    @staticmethod
    def _require_report(decision: SiteScoreDecision, report: object) -> None:
        if (not isinstance(report, SiteScoreReport)
                or not all(_text(value) for value in (report.report_id, report.model_version,
                    report.feature_version, decision.policy_version))
                or type(report.report_version) is not int or report.report_version < 1
                or (report.candidate_site_id, report.report_id, report.report_version, report.model_version)
                != (decision.candidate_site_id, decision.report_id, decision.report_version, decision.model_version)):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_RETAINED_REPORT_REQUIRED")

    @staticmethod
    def _require_signature(decision: SiteScoreDecision, approval: object) -> None:
        if (not isinstance(decision.history, (tuple, list)) or not decision.history
                or any(not isinstance(item, DecisionTransition) for item in decision.history)):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_RECORDED_DECISION_REQUIRED")
        tail = decision.history[-1]
        status = {"APPROVE": (DecisionStatus.APPROVED, "approved"),
                  "REJECT": (DecisionStatus.REJECTED, "rejected"),
                  "REQUEST_REVISION": (DecisionStatus.DRAFT, "returned")}.get(tail.action)
        if (status is None or not isinstance(approval, Approval) or not _text(approval.approval_id)
                or approval.approval_id == decision.decision_id
                or (approval.approval_id, approval.decision_id, approval.approval_status)
                != (decision.approval_id, decision.decision_id, status[1])
                or not _text(approval.approver_id) or not isinstance(approval.approved_at, datetime)
                or approval.approved_at.tzinfo is None
                or (decision.status, tail.to_status, tail.version, tail.tenant_id, tail.report_id,
                    tail.report_version, tail.model_version, tail.approval_id, tail.actor, tail.at, tail.reason)
                != (status[0], status[0], decision.version, decision.tenant_id, decision.report_id,
                    decision.report_version, decision.model_version, approval.approval_id,
                    approval.approver_id, approval.approved_at, approval.comment)):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_SIGNATURE_REQUIRED")
        keys = ("network_action", "conditions", "required_data", "override_ack", "actor_role_id",
                "submitted_by", "submission_actor_verified", "submission_summary", "reviewer_group_id",
                "previous_decision_id")
        if any(getattr(tail, key, None) != getattr(decision, key, None) for key in keys):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_SIGNATURE_REQUIRED")

    @staticmethod
    def _require_history(decision: SiteScoreDecision) -> None:
        """Read the existing writer's grammar; do not invent transitions."""
        if (type(decision.version) is not int or decision.version < 3
                or [item.version for item in decision.history] != list(range(2, decision.version + 1))):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_TRANSITION_HISTORY_REQUIRED")
        previous = DecisionStatus.SYSTEM_RECOMMENDED
        for transition in decision.history:
            if (not isinstance(transition.action, str)
                    or not isinstance(transition.from_status, DecisionStatus)
                    or not isinstance(transition.to_status, DecisionStatus)
                    or type(transition.version) is not int
                    or type(transition.report_version) is not int):
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_TRANSITION_HISTORY_REQUIRED")
            expected = {
                "submit": DecisionStatus.PENDING_REVIEW,
                "APPROVE": DecisionStatus.APPROVED,
                "REJECT": DecisionStatus.REJECTED,
                "REQUEST_REVISION": DecisionStatus.DRAFT,
            }.get(transition.action)
            allowed_from = ({DecisionStatus.SYSTEM_RECOMMENDED, DecisionStatus.DRAFT}
                            if transition.action == "submit" else {DecisionStatus.PENDING_REVIEW})
            if (expected is None or transition.from_status is not previous
                    or transition.from_status not in allowed_from or transition.to_status is not expected
                    or (transition.tenant_id, transition.report_id, transition.report_version,
                        transition.model_version, transition.previous_decision_id)
                    != (decision.tenant_id, decision.report_id, decision.report_version,
                        decision.model_version, decision.previous_decision_id)):
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_TRANSITION_HISTORY_REQUIRED")
            previous = transition.to_status
        if previous is not decision.status:
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_TRANSITION_HISTORY_REQUIRED")

    def _events(self, tenant: str, decision: SiteScoreDecision) -> tuple[BoundSiteScoreAuditEvent, ...]:
        self._require_history(decision)
        events = self.audit_log.list_resource_events(
            tenant_id=tenant, resource=f"sitescore/decision/{decision.decision_id}",
            event_type="sitescore.decision.v1",
        )
        matches = []
        created = [event for event in events if self._matches_create(event, decision)]
        if len(created) != 1:
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_EXACT_AUDIT_REQUIRED")
        matches.extend(created)
        for transition in decision.history:
            bound = [event for event in events if self._matches_transition(event, decision, transition)]
            if len(bound) != 1:
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_EXACT_AUDIT_REQUIRED")
            matches.extend(bound)
        # An unjoined row under the same decision resource is not silently
        # copied into evidence or silently explained as a known transition.
        if len(matches) != len(events) or len({event.event_id for event in matches}) != len(matches):
            raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_UNBOUND_AUDIT")
        result = []
        for event in matches:
            exact = self.audit_log.get_event(event.event_id, tenant_id=tenant)
            if exact != event or not _text(event.correlation_id):
                raise SiteScoreEvidenceUnavailable("CANONICAL_EVIDENCE_EXACT_AUDIT_REQUIRED")
            result.append(BoundSiteScoreAuditEvent(
                event_id=event.event_id, event_hash=event.event_hash, action=event.action,
                outcome=event.outcome, actor=event.actor, correlation_id=event.correlation_id,
                occurred_at=event.occurred_at, decision_version=event.metadata["version"],
                content_sha256=_digest(event.to_dict()),
            ))
        return tuple(result)

    @staticmethod
    def _matches_create(event: AuditEvent, decision: SiteScoreDecision) -> bool:
        return (event.action == "create" and event.outcome == "system_recommended"
                and event.actor == decision.created_by
                and event.metadata.get("version") == 1
                and event.metadata.get("approval_id") is None
                and event.metadata.get("reason") == f"system recommendation {decision.recommendation.value}"
                and SiteScoreCanonicalEvidenceResolver._matches_identity(event, decision))

    @staticmethod
    def _matches_identity(event: AuditEvent, decision: SiteScoreDecision) -> bool:
        return (event.metadata.get("tenant_id") == decision.tenant_id
                and (event.metadata.get("candidate_site_id"), event.metadata.get("report_id"),
                     event.metadata.get("report_version"), event.metadata.get("model_version"),
                     event.metadata.get("policy_version"))
                == (decision.candidate_site_id, decision.report_id, decision.report_version,
                    decision.model_version, decision.policy_version))

    @staticmethod
    def _matches_transition(event: AuditEvent, decision: SiteScoreDecision, transition: DecisionTransition) -> bool:
        action = {"APPROVE": "approve", "REJECT": "reject", "REQUEST_REVISION": "return"}.get(
            transition.action, transition.action,
        )
        expected = {
            "version": transition.version, "approval_id": transition.approval_id,
            "reason": transition.reason, "network_action": transition.network_action,
            "conditions": transition.conditions, "required_data": (
                list(transition.required_data) if transition.required_data is not None else None),
            "override_ack": transition.override_ack, "actor_role_id": transition.actor_role_id,
            "submitted_by": transition.submitted_by,
            "submission_actor_verified": transition.submission_actor_verified,
            "submission_summary": transition.submission_summary,
            "reviewer_group_id": transition.reviewer_group_id,
            "previous_decision_id": transition.previous_decision_id,
        }
        return (SiteScoreCanonicalEvidenceResolver._matches_identity(event, decision)
                and event.actor == transition.actor and event.action == action
                and event.outcome == transition.to_status.value.lower()
                and event.correlation_id == transition.correlation_id
                and all(event.metadata.get(key) == value for key, value in expected.items()))
